"""Azure Blob Storage access using connection strings or token credentials."""
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from azure.core import MatchConditions
from azure.core.credentials import TokenCredential
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


def validate_storage_account_url(account_url: str) -> str:
    """Validate and normalize an Azure Blob Storage account endpoint."""
    parsed = urlsplit(account_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
        hostname.endswith(host_suffix) for host_suffix in AZURE_BLOB_HOST_SUFFIXES
    ):
        raise ValueError("Configured storage account URL must be an HTTPS Azure Blob endpoint")
    if (
        parsed.username
        or parsed.password
        or parsed.port
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Configured storage account URL must contain only the account endpoint")
    return f"https://{hostname}"


def validate_blob_url_for_account(
    account_url: str,
    blob_url: str,
    suffix: str,
) -> tuple[str, str]:
    """Validate the blob URL and return its path only if it matches the configured account."""
    normalized_account_url = validate_storage_account_url(account_url)
    container, blob_name = parse_blob_url(blob_url, suffix)
    url_host = (urlsplit(blob_url).hostname or "").lower()
    account_host = (urlsplit(normalized_account_url).hostname or "").lower()
    if url_host != account_host:
        raise ValueError("PDF URL storage account does not match the configured account")
    return container, blob_name


def create_blob_client_with_credential(
    account_url: str,
    blob_url: str,
    suffix: str,
    credential: TokenCredential,
) -> BlobClient:
    """Create a blob client using a token credential and an allowlisted account endpoint."""
    normalized_account_url = validate_storage_account_url(account_url)
    container, blob_name = validate_blob_url_for_account(
        normalized_account_url,
        blob_url,
        suffix,
    )
    return BlobClient(
        account_url=normalized_account_url,
        container_name=container,
        blob_name=blob_name,
        credential=credential,
    )


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
