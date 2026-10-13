"""Build and persist one Azure Table entity for each scanned source PDF."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import AzureCliCredential, ManagedIdentityCredential

from .storage import parse_blob_url, redact, validate_storage_account_url

TABLE_NAME = "BarcodeScanResults"

_TABLE_HOST_SUFFIXES = (
    (".blob.core.windows.net", ".table.core.windows.net", None),
    (".blob.core.usgovcloudapi.net", ".table.core.usgovcloudapi.net",
     "https://storage.azure.us"),
    (".blob.core.chinacloudapi.cn", ".table.core.chinacloudapi.cn",
     "https://storage.azure.cn"),
)


def table_endpoint_from_blob_url(account_url: str) -> tuple[str, str | None]:
    """Return the Table endpoint and any required sovereign-cloud audience."""
    normalized = validate_storage_account_url(account_url)
    hostname = urlsplit(normalized).hostname or ""
    for blob_suffix, table_suffix, audience in _TABLE_HOST_SUFFIXES:
        if hostname.endswith(blob_suffix):
            account_name = hostname[: -len(blob_suffix)]
            return f"https://{account_name}{table_suffix}", audience
    raise ValueError("Configured storage account has no supported Table endpoint")


def _derived_names(source_name: str, barcodes: list[dict]) -> list[str]:
    names = [source_name]
    stem = Path(source_name).stem
    next_start_page = 1
    for end_page in sorted({int(barcode["page"]) for barcode in barcodes}):
        if end_page < next_start_page:
            continue
        names.append(f"{stem}_{next_start_page}_{end_page}.pdf")
        next_start_page = end_page + 1
    return names


def build_result_entity(
    blob_url: str,
    source_name: str,
    source_metadata: dict[str, str] | None,
    result: dict,
    created_at: datetime | None = None,
    original_file_name: str | None = None,
    store_code: str | None = None,
) -> dict:
    """Build a stable, SAS-free table entity for a scanned source PDF."""
    container, blob_name = parse_blob_url(blob_url, ".pdf")
    table_file_name = original_file_name or source_name
    barcodes = sorted(
        result["barcodes"],
        key=lambda barcode: (int(barcode["page"]), str(barcode["value"])),
    )
    store = store_code
    if store is None:
        store = next(
            (
                value
                for key, value in (source_metadata or {}).items()
                if key.casefold() == "store" and value is not None
            ),
            "",
        )
    timestamp = created_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")

    return {
        "PartitionKey": container,
        "RowKey": hashlib.sha256(blob_name.encode("utf-8")).hexdigest(),
        "BlobBackupFilePath": redact(blob_url),
        "OriginalFileName": json.dumps(
            _derived_names(table_file_name, barcodes),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "Store": store,
        "pagenumber": json.dumps(
            [
                {"page": int(barcode["page"]), "rono": barcode["value"]}
                for barcode in barcodes
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "CreatedAt": timestamp.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
    }


def _create_table_service_client() -> TableServiceClient:
    env = os.environ.get
    auth_mode = (env("AZURE_STORAGE_AUTH_MODE") or "connection_string").strip().lower()
    if auth_mode == "connection_string":
        connection_string = (env("AZURE_STORAGE_CONNECTION_STRING") or "").strip()
        if not connection_string:
            raise ValueError(
                "AZURE_STORAGE_CONNECTION_STRING is required for Table Storage"
            )
        return TableServiceClient.from_connection_string(connection_string)

    if auth_mode not in {"azure_cli", "managed_identity"}:
        raise ValueError(
            "AZURE_STORAGE_AUTH_MODE must be connection_string, azure_cli, "
            "or managed_identity"
        )

    account_url = (env("AZURE_STORAGE_ACCOUNT_URL") or "").strip()
    if not account_url:
        raise ValueError("AZURE_STORAGE_ACCOUNT_URL is required for Table Storage")
    endpoint, audience = table_endpoint_from_blob_url(account_url)
    credential = (
        ManagedIdentityCredential()
        if auth_mode == "managed_identity"
        else AzureCliCredential()
    )
    client_options = {"endpoint": endpoint, "credential": credential}
    if audience:
        client_options["audience"] = audience
    return TableServiceClient(**client_options)


def write_result_entity(entity: dict) -> None:
    """Create the results table if needed and replace this source's row."""
    service_client = _create_table_service_client()
    try:
        table_client = service_client.create_table_if_not_exists(TABLE_NAME)
        table_client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)
    finally:
        service_client.close()
