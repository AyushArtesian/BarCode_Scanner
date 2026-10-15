#!/usr/bin/env python3
"""Scan one PDF from Azure Blob Storage and store results in Azure Table Storage.

Flow:
  download PDF -> scan every page at every DPI -> upsert one table entity
  -> print the result

Environment variables (see .env)
  AZURE_STORAGE_CONNECTION_STRING  access to the source blob and table service
  AZURE_STORAGE_AUTH_MODE         connection_string, azure_cli, or managed_identity
  AZURE_STORAGE_ACCOUNT_URL       Blob account endpoint for identity authentication
  INPUT_PDF_URL                   optional fallback when no URL argument is passed
  LOCAL_INPUT                     optional local PDF path for development/testing
  LOCAL_OUTPUT_DIR                optional folder for a local JSON copy
  SCAN_DPIS                       default "150,170,200,250"
  WORKERS                         default available CPU count - 1
  FALLBACK                        "1" enables the slow full-page decode

The source Blob metadata and PDF bytes are not changed. The table entity stores
barcode page/value data and the derived names for the would-be split files.

Output
  stdout  the response: one JSON object per run
  stderr  logs: one JSON object per line

Exit codes
  0  ok (includes partial results)
  1  download failed or the file is not a PDF
  2  scan failed, or every page errored (response still printed)
  3  writing the table/local result failed (response still printed)
  4  configuration error
"""
import argparse
import json
import logging
import os
import signal
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import unquote, urlsplit

from azure.core.exceptions import AzureError
from azure.identity import AzureCliCredential, ManagedIdentityCredential
from azure.storage.blob import BlobClient, BlobProperties
from dotenv import load_dotenv

# Load .env for local runs. Real environment variables take precedence.
# Azure has no .env file, so this does nothing there.
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

from barcodescan.config import DEFAULT_DPIS, Settings  # noqa: E402
from barcodescan.results import build_result           # noqa: E402
from barcodescan.scan_api import scan_pdf              # noqa: E402
from barcodescan.storage import (  # noqa: E402
    create_blob_client,
    create_blob_client_with_credential,
    download_blob_to,
    redact,
    validate_storage_account_url,
)
from barcodescan.table_storage import (  # noqa: E402
    build_result_entity,
    update_execution_timestamps,
    write_result_entity,
)

EXIT_OK, EXIT_DOWNLOAD, EXIT_SCAN, EXIT_WRITE, EXIT_CONFIG = range(5)
JOB_ID = os.environ.get("CONTAINER_APP_JOB_EXECUTION_NAME", "local")
log = logging.getLogger("barcode-job")


class ConfigError(Exception):
    pass


class JsonFormatter(logging.Formatter):
    def format(self, record):
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
               "level": record.levelname, "job": JOB_ID, "msg": record.getMessage()}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def setup_logging():
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


@dataclass(frozen=True)
class JobConfig:
    input_url: Optional[str]
    local_input: Optional[Path]
    blob_client: Optional[BlobClient]
    local_output_dir: Optional[Path]
    dpis: Tuple[int, ...]
    workers: int
    fallback: bool
    file_name: Optional[str] = None
    store_code: Optional[str] = None
    blob_uri: Optional[str] = None


def parse_dpis(text: str) -> Tuple[int, ...]:
    try:
        dpis = tuple(int(x.strip()) for x in text.split(",") if x.strip())
    except ValueError as e:
        raise ConfigError("SCAN_DPIS must be comma-separated positive integers") from e
    if not dpis or any(dpi <= 0 for dpi in dpis):
        raise ConfigError("SCAN_DPIS must contain positive integers")
    return dpis


def create_job_blob_client(input_url: str) -> BlobClient:
    env = os.environ.get
    auth_mode = (env("AZURE_STORAGE_AUTH_MODE") or "connection_string").strip().lower()

    if auth_mode == "connection_string":
        connection_string = (env("AZURE_STORAGE_CONNECTION_STRING") or "").strip()
        if not connection_string:
            raise ConfigError("AZURE_STORAGE_CONNECTION_STRING is required for blob URLs")
        try:
            return create_blob_client(connection_string, input_url, ".pdf")
        except (TypeError, ValueError) as e:
            raise ConfigError(f"INPUT_PDF_URL: {e}") from e

    if auth_mode not in {"azure_cli", "managed_identity"}:
        raise ConfigError(
            "AZURE_STORAGE_AUTH_MODE must be connection_string, azure_cli, "
            "or managed_identity"
        )

    account_url = (env("AZURE_STORAGE_ACCOUNT_URL") or "").strip()
    if not account_url:
        raise ConfigError("AZURE_STORAGE_ACCOUNT_URL is required for identity auth")
    try:
        account_url = validate_storage_account_url(account_url)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"AZURE_STORAGE_ACCOUNT_URL: {e}") from e

    credential = (
        ManagedIdentityCredential()
        if auth_mode == "managed_identity"
        else AzureCliCredential()
    )
    try:
        return create_blob_client_with_credential(
            account_url,
            input_url,
            ".pdf",
            credential,
        )
    except (TypeError, ValueError) as e:
        raise ConfigError(f"INPUT_PDF_URL: {e}") from e


def load_config(
    input_argument: Optional[str] = None,
    file_name_argument: Optional[str] = None,
    store_code_argument: Optional[str] = None,
    blob_uri_argument: Optional[str] = None,
) -> JobConfig:
    env = os.environ.get
    input_url = (input_argument or env("INPUT_PDF_URL") or "").strip() or None
    local_input = (env("LOCAL_INPUT") or "").strip() or None
    local_out = (env("LOCAL_OUTPUT_DIR") or "").strip() or None

    if input_url and local_input:
        raise ConfigError("set INPUT_PDF_URL or LOCAL_INPUT, not both")
    if not input_url and not local_input:
        raise ConfigError("pass a PDF URL or set INPUT_PDF_URL / LOCAL_INPUT")
    blob = None
    if input_url:
        blob = create_job_blob_client(input_url)
    if local_input and not Path(local_input).is_file():
        raise ConfigError(f"LOCAL_INPUT not found: {local_input}")

    dpis = parse_dpis(env("SCAN_DPIS", ",".join(map(str, DEFAULT_DPIS))))
    try:
        available_cpus = os.process_cpu_count() or os.cpu_count() or 2
        default_workers = (
            available_cpus
            if env("CONTAINER_APP_JOB_EXECUTION_NAME")
            else max(1, available_cpus - 1)
        )
        workers = int(env("WORKERS") or default_workers)
    except ValueError:
        raise ConfigError("WORKERS must be an integer")
    if workers < 1:
        raise ConfigError("WORKERS must be a positive integer")

    return JobConfig(
        input_url=input_url,
        local_input=Path(local_input) if local_input else None,
        blob_client=blob,
        local_output_dir=Path(local_out) if local_out else None,
        dpis=dpis,
        workers=workers,
        fallback=env("FALLBACK") == "1",
        file_name=file_name_argument or None,
        store_code=store_code_argument,
        blob_uri=blob_uri_argument,
    )


def input_name(cfg: JobConfig) -> str:
    if cfg.local_input:
        return cfg.local_input.name
    return unquote(Path(urlsplit(cfg.input_url or "").path).name)


def failure(stage: str, err, name: str = None) -> dict:
    return {"file": name, "status": "failed", "stage": stage, "error": str(err)}


def run(cfg: JobConfig, started: float, started_at: datetime | None = None):
    """Returns (exit_code, response_dict). Always returns a response."""
    job_started_at = started_at or datetime.now(timezone.utc)
    name = input_name(cfg)
    settings = Settings(dpis=cfg.dpis, fallback=cfg.fallback)
    source = str(cfg.local_input) if cfg.local_input else redact(cfg.input_url or "")
    log.info("start file=%s source=%s dpis=%s workers=%d",
             name, source, list(cfg.dpis), cfg.workers)

    with tempfile.TemporaryDirectory(prefix="barcode-") as tmp:
        pdf_path = Path(tmp) / "input.pdf"
        blob_properties: Optional[BlobProperties] = None

        try:
            if cfg.local_input:
                shutil.copy(cfg.local_input, pdf_path)
            else:
                if cfg.blob_client is None:
                    raise RuntimeError("blob client is not configured")
                blob_properties = download_blob_to(cfg.blob_client, pdf_path)
            with open(pdf_path, "rb") as fh:
                if fh.read(5) != b"%PDF-":
                    raise ValueError("downloaded file is not a PDF")
            log.info("downloaded %.1f MB to temp folder", pdf_path.stat().st_size / 1e6)
        except Exception as e:
            log.error("download failed: %s", e)
            return EXIT_DOWNLOAD, failure("download", e, name)

        def on_progress(done, total):
            log.info("progress %d/%d tasks, %.0fs elapsed",
                     done, total, time.perf_counter() - started)

        try:
            merger = scan_pdf(pdf_path, cfg.workers, settings,
                              name=name, progress=on_progress)
        except Exception as e:
            log.exception("scan failed")
            return EXIT_SCAN, failure("scan", e, name)

        elapsed = time.perf_counter() - started
        result = build_result(merger, name, settings, elapsed)
        if result["status"] == "error":
            log.error("every page failed; source PDF was not changed")
            return EXIT_SCAN, result

        try:
            if cfg.blob_client is not None:
                if blob_properties is None or cfg.input_url is None:
                    raise RuntimeError("source blob properties are not available")
                entity = build_result_entity(
                    cfg.input_url,
                    name,
                    blob_properties.metadata,
                    result,
                    created_at=job_started_at,
                    original_file_name=cfg.file_name,
                    store_code=cfg.store_code,
                    updated_at=datetime.now(timezone.utc),
                    blob_uri=cfg.input_url,
                )
                write_result_entity(entity)
                log.info("upserted scan results in Azure Table %s",
                         "BarcodeScanResults")
            if cfg.local_output_dir:
                cfg.local_output_dir.mkdir(parents=True, exist_ok=True)
                target = cfg.local_output_dir / (Path(name).stem + ".json")
                target.write_text(
                    json.dumps(result, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                log.info("saved local JSON result to %s", target)
        except Exception as e:
            log.exception("writing table or local result failed")
            return EXIT_WRITE, failure("write", e, name)

    log.info("temp folder deleted")
    return EXIT_OK, result


def handle_sigterm(signum, frame):
    log.warning("SIGTERM received, stopping")
    sys.exit(143)


def main(argv=None) -> int:
    setup_logging()
    signal.signal(signal.SIGTERM, handle_sigterm)
    started = time.perf_counter()
    job_started_at = datetime.now(timezone.utc)

    parser = argparse.ArgumentParser(
        description="Scan an Azure PDF blob or finalize its ACA execution timestamps."
    )
    parser.add_argument(
        "input_pdf_url",
        nargs="?",
        help="HTTPS Azure Blob URL of a PDF (query parameters are ignored)",
    )
    parser.add_argument(
        "file_name",
        nargs="?",
        help="Original file name supplied by the Logic App",
    )
    parser.add_argument(
        "store_code",
        nargs="?",
        help="Store code supplied by the Logic App",
    )
    parser.add_argument(
        "blob_uri",
        nargs="?",
        help="Blob URI value supplied by the Logic App for BlobBackupFilePath",
    )
    parser.add_argument(
        "--finalize-execution",
        action="store_true",
        help="update an existing result row with the completed ACA execution times",
    )
    parser.add_argument(
        "--execution-start-time",
        help="ACA execution properties.startTime in ISO 8601 format",
    )
    parser.add_argument(
        "--execution-end-time",
        help="ACA execution properties.endTime in ISO 8601 format",
    )
    args = parser.parse_args(argv)

    if args.finalize_execution:
        if not args.input_pdf_url:
            error = ConfigError("a source PDF URL is required to finalize timestamps")
            print(json.dumps(failure("config", error)), flush=True)
            return EXIT_CONFIG
        if not args.execution_start_time or not args.execution_end_time:
            error = ConfigError(
                "--execution-start-time and --execution-end-time are required"
            )
            print(json.dumps(failure("config", error)), flush=True)
            return EXIT_CONFIG
        source_name = unquote(Path(urlsplit(args.input_pdf_url).path).name)
        try:
            update_execution_timestamps(
                args.input_pdf_url,
                args.execution_start_time,
                args.execution_end_time,
            )
        except (AzureError, ValueError) as error:
            log.exception("finalizing ACA execution timestamps failed")
            print(
                json.dumps(failure("finalize", error, source_name)),
                flush=True,
            )
            return EXIT_WRITE
        print(
            json.dumps(
                {
                    "status": "completed",
                    "file": source_name,
                    "created_at": args.execution_start_time,
                    "updated_at": args.execution_end_time,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return EXIT_OK

    try:
        cfg = load_config(
            args.input_pdf_url,
            args.file_name,
            args.store_code,
            args.blob_uri,
        )
    except ConfigError as e:
        log.error("configuration error: %s", e)
        print(json.dumps(failure("config", e)), flush=True)
        return EXIT_CONFIG

    code, result = run(cfg, started, started_at=job_started_at)
    print(json.dumps(result, ensure_ascii=False), flush=True)   # the response
    log.info("finished status=%s exit=%d", result.get("status"), code)
    return code


if __name__ == "__main__":
    sys.exit(main())