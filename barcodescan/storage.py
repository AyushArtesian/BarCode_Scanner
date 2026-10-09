"""Azure Blob Storage access using a connection string."""
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from azure.core import MatchConditions
from azure.storage.blob import BlobClient, BlobProperties


AZURE_BLOB_HOST_SUFFIXES = (
    ".blob.core.windows.net",
    ".blob.core.usgovcloudapi.net",
    ".blob.core.chinacloudapi.cn",
)


def parse_blob_url(url: str, suffix: str) -> tuple[str, str]:
    """Return the container and blob name from an Azure Blob URL."""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        hostname.endswith(host_suffix) for host_suffix in AZURE_BLOB_HOST_SUFFIXES
    ):
        raise ValueError("URL must be an HTTPS Azure Blob Storage URL")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("URL must not include credentials or a port")

    path = unquote(parsed.path.lstrip("/"))
    container, separator, blob_name = path.partition("/")
    if not separator or not container or not blob_name:
        raise ValueError("URL must include a container and blob name")
    if not blob_name.lower().endswith(suffix.lower()):
        raise ValueError(f"URL must point to a {suffix} blob")
    return container, blob_name


def create_blob_client(connection_string: str, blob_url: str, suffix: str) -> BlobClient:
    """Create a blob client from the connection string and URL path.

    Query parameters are intentionally ignored, so callers do not need SAS tokens.
    """
    if not connection_string.strip():
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING is required")
    container, blob_name = parse_blob_url(blob_url, suffix)
    blob = BlobClient.from_connection_string(
        conn_str=connection_string,
        container_name=container,
        blob_name=blob_name,
    )
    url_host = (urlsplit(blob_url).hostname or "").lower()
    connection_host = (urlsplit(blob.url).hostname or "").lower()
    if url_host != connection_host:
        raise ValueError("PDF URL storage account does not match the connection string")
    return blob


def redact(url: str) -> str:
    """Return a URL without its query string before logging it."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def download_blob_to(blob: BlobClient, destination: Path) -> BlobProperties:
    """Stream a blob to a local file and return the properties it was read with."""
    properties = blob.get_blob_properties()
    with Path(destination).open("wb") as file:
        blob.download_blob(
            etag=properties.etag,
            match_condition=MatchConditions.IfNotModified,
        ).readinto(file)
    return properties


def format_blob_metadata(result: dict) -> str:
    """Serialize page/value results as compact, HTTP-header-safe JSON."""
    entries = [
        {"page": barcode["page"], "value": barcode["value"]}
        for barcode in result["barcodes"]
    ]
    return json.dumps(entries, ensure_ascii=True, separators=(",", ":"))


def update_blob_metadata(
    blob: BlobClient,
    properties: BlobProperties,
    metadata: dict[str, str],
) -> None:
    """Conditionally update metadata without changing the blob content."""
    blob_metadata = dict(properties.metadata or {})
    replacement_names = {name.lower() for name in metadata}
    blob_metadata = {
        name: value
        for name, value in blob_metadata.items()
        if name.lower() not in replacement_names
    }
    blob_metadata.update(metadata)
    blob.set_blob_metadata(
        metadata=blob_metadata,
        etag=properties.etag,
        match_condition=MatchConditions.IfNotModified,
    )
