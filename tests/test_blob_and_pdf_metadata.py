import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pypdf import PdfWriter
from azure.core import MatchConditions
from barcodescan.output import PageMerger
from barcodescan.storage import (
    create_blob_client,
    download_blob_to,
    parse_blob_url,
)
from job import (
    ConfigError,
    EXIT_OK,
    EXIT_SCAN,
    EXIT_WRITE,
    JobConfig,
    load_config,
    main,
    run,
)

DUMMY_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=https;AccountName=alinkdev;"
    "AccountKey=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=;"
    "EndpointSuffix=core.windows.net"
)


class BlobUrlTests(unittest.TestCase):
    CONNECTION_STRING = DUMMY_CONNECTION_STRING

    def test_extracts_container_and_blob_without_using_query_string(self):
        container, blob = parse_blob_url(
            "https://alinkdev.blob.core.windows.net/barcodescanner/"
            "folder%20one/part.pdf?sv=ignored&sig=ignored",
            ".pdf",
        )

        self.assertEqual(container, "barcodescanner")
        self.assertEqual(blob, "folder one/part.pdf")

    def test_rejects_non_https_and_non_pdf_urls(self):
        for url in (
            "http://account.blob.core.windows.net/container/file.pdf",
            "https://example.com/container/file.pdf",
            "https://account.blob.core.windows.net/container/file.json",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_blob_url(url, ".pdf")

    def test_rejects_url_from_a_different_storage_account(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            create_blob_client(
                self.CONNECTION_STRING,
                "https://other.blob.core.windows.net/barcodescanner/file.pdf",
                ".pdf",
            )

    def test_creates_client_for_account_named_by_connection_string(self):
        blob = create_blob_client(
            self.CONNECTION_STRING,
            "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf",
            ".pdf",
        )

        self.assertEqual(
            blob.url,
            "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf",
        )

    @patch.dict(
        os.environ,
        {
            "AZURE_STORAGE_CONNECTION_STRING": DUMMY_CONNECTION_STRING,
            "SCAN_DPIS": "150,200",
            "WORKERS": "1",
        },
        clear=True,
    )
    def test_load_config_accepts_url_and_connection_string_without_sas(self):
        url = "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf"

        config = load_config(
            url,
            "invoice.pdf",
            "North",
            "FileBunkerRawFiles/incoming/invoice.pdf",
        )

        self.assertEqual(config.input_url, url)
        self.assertEqual(config.blob_client.url, url)
        self.assertEqual(config.dpis, (150, 200))
        self.assertEqual(config.file_name, "invoice.pdf")
        self.assertEqual(config.store_code, "North")
        self.assertEqual(
            config.blob_uri,
            "FileBunkerRawFiles/incoming/invoice.pdf",
        )

    @patch.dict(
        os.environ,
        {
            "AZURE_STORAGE_CONNECTION_STRING": DUMMY_CONNECTION_STRING,
        },
        clear=True,
    )
    def test_load_config_defaults_to_the_seven_dpi_passes(self):
        config = load_config(
            "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf"
        )

        self.assertEqual(config.dpis, (100, 150, 170, 200, 250, 350, 500))

    @patch.dict(
        os.environ,
        {
            "AZURE_STORAGE_CONNECTION_STRING": DUMMY_CONNECTION_STRING,
            "INPUT_PDF_URL": (
                "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf"
            ),
            "CONTAINER_APP_JOB_EXECUTION_NAME": "job-execution",
        },
        clear=True,
    )
    @patch("job.os.process_cpu_count", return_value=2)
    def test_container_app_default_workers_use_all_allocated_cpus(self, _cpu_count):
        self.assertEqual(load_config().workers, 2)

    @patch.dict(os.environ, {}, clear=True)
    def test_load_config_requires_a_blob_connection_string_for_url_input(self):
        with self.assertRaisesRegex(ConfigError, "AZURE_STORAGE_CONNECTION_STRING"):
            load_config("https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf")

    @patch.dict(
        os.environ,
        {
            "AZURE_STORAGE_AUTH_MODE": "managed_identity",
            "AZURE_STORAGE_ACCOUNT_URL": "https://alinkdev.blob.core.windows.net",
            "INPUT_PDF_URL": (
                "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf"
                "?sv=ignored"
            ),
        },
        clear=True,
    )
    @patch("job.ManagedIdentityCredential", create=True)
    def test_load_config_accepts_blob_url_with_managed_identity(self, credential):
        config = load_config()

        self.assertEqual(
            config.blob_client.url,
            "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf",
        )
        credential.assert_called_once_with()

    @patch.dict(
        os.environ,
        {
            "AZURE_STORAGE_AUTH_MODE": "managed_identity",
            "AZURE_STORAGE_ACCOUNT_URL": "https://alinkdev.blob.core.windows.net",
            "INPUT_PDF_URL": (
                "https://other.blob.core.windows.net/barcodescanner/file.pdf"
            ),
        },
        clear=True,
    )
    @patch("job.ManagedIdentityCredential")
    def test_managed_identity_mode_rejects_a_different_storage_account(self, _credential):
        with self.assertRaisesRegex(ConfigError, "does not match"):
            load_config()

    @patch.dict(
        os.environ,
        {
            "AZURE_STORAGE_AUTH_MODE": "azure_cli",
            "AZURE_STORAGE_ACCOUNT_URL": "https://alinkdev.blob.core.windows.net",
            "INPUT_PDF_URL": (
                "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf"
            ),
        },
        clear=True,
    )
    @patch("job.AzureCliCredential")
    def test_load_config_accepts_blob_url_with_azure_cli_identity(self, credential):
        config = load_config()

        self.assertEqual(
            config.blob_client.url,
            "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf",
        )
        credential.assert_called_once_with()

    @patch.dict(
        os.environ,
        {
            "AZURE_STORAGE_AUTH_MODE": "managed_identity",
            "INPUT_PDF_URL": (
                "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf"
            ),
        },
        clear=True,
    )
    def test_managed_identity_mode_requires_account_endpoint(self):
        with self.assertRaisesRegex(ConfigError, "AZURE_STORAGE_ACCOUNT_URL"):
            load_config()

    @patch.dict(
        os.environ,
        {
            "AZURE_STORAGE_AUTH_MODE": "invalid",
            "INPUT_PDF_URL": (
                "https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf"
            ),
        },
        clear=True,
    )
    def test_load_config_rejects_unknown_storage_auth_mode(self):
        with self.assertRaisesRegex(ConfigError, "AZURE_STORAGE_AUTH_MODE"):
            load_config()

    def test_download_is_conditional_on_the_blob_version_read(self):
        class FakeDownloader:
            def readinto(self, stream):
                stream.write(b"source PDF")

        class FakeBlob:
            def __init__(self):
                self.download_options = None
                self.properties = SimpleNamespace(etag='"etag-2"')

            def get_blob_properties(self):
                return self.properties

            def download_blob(self, **kwargs):
                self.download_options = kwargs
                return FakeDownloader()

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "source.pdf"
            blob = FakeBlob()

            properties = download_blob_to(blob, destination)

            self.assertEqual(destination.read_bytes(), b"source PDF")
            self.assertEqual(properties.etag, '"etag-2"')
            self.assertEqual(blob.download_options["etag"], '"etag-2"')
            self.assertEqual(
                blob.download_options["match_condition"],
                MatchConditions.IfNotModified,
            )


class JobWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "source.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_metadata({"/Title": "Original"})
        writer.write(self.source)

    def config(self):
        return JobConfig(
            input_url=None,
            local_input=self.source,
            blob_client=None,
            local_output_dir=None,
            dpis=(72,),
            workers=1,
            fallback=False,
        )

    def test_successful_local_scan_does_not_modify_source_pdf_or_write_table(self):
        merger = PageMerger()
        merger.add({
            "file": self.source.name,
            "page": 1,
            "dpi": 72,
            "barcodes": [{
                "type": "Code128",
                "value": "12345",
                "method": "test",
                "confidence": 1.0,
                "bbox": [0.1, 0.2, 0.8, 0.9],
            }],
        })

        source_bytes = self.source.read_bytes()
        with (
            patch("job.scan_pdf", return_value=merger),
            patch("job.write_result_entity") as write_result_entity,
        ):
            code, result = run(self.config(), time.perf_counter())

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(result["barcode_count"], 1)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        write_result_entity.assert_not_called()

    def test_successful_blob_scan_writes_table_row_without_mutating_blob_metadata(self):
        class FakeDownloader:
            def readinto(self, stream):
                stream.write(self.source_bytes)

            def __init__(self, source_bytes):
                self.source_bytes = source_bytes

        class FakeBlob:
            def __init__(self, source_bytes):
                self.source_bytes = source_bytes
                self.updated_metadata = None
                self.properties = SimpleNamespace(
                    etag='"blob-etag"',
                    metadata={"department": "shipping", "Store": "North"},
                )

            def get_blob_properties(self):
                return self.properties

            def download_blob(self, **kwargs):
                return FakeDownloader(self.source_bytes)

            def set_blob_metadata(self, **kwargs):
                self.updated_metadata = kwargs["metadata"]

        merger = PageMerger()
        merger.add({
            "file": self.source.name,
            "page": 1,
            "dpi": 72,
            "barcodes": [{
                "type": "Code128",
                "value": "12345",
                "method": "test",
                "confidence": 1.0,
                "bbox": [0.1, 0.2, 0.8, 0.9],
            }],
        })
        blob = FakeBlob(self.source.read_bytes())
        config = JobConfig(
            input_url="https://alinkdev.blob.core.windows.net/barcodescanner/source.pdf",
            local_input=None,
            blob_client=blob,
            local_output_dir=None,
            dpis=(72,),
            workers=1,
            fallback=False,
            blob_uri="FileBunkerRawFiles/source.pdf",
        )

        with (
            patch("job.scan_pdf", return_value=merger),
            patch("job.write_result_entity") as write_result_entity,
        ):
            job_started_at = datetime.now(timezone.utc)
            code, result = run(
                config,
                time.perf_counter(),
                started_at=job_started_at,
            )

        self.assertEqual(code, EXIT_OK)
        self.assertEqual(result["barcode_count"], 1)
        blob_entity = write_result_entity.call_args.args[0]
        self.assertEqual(
            blob_entity["BlobBackupFilePath"],
            "FileBunkerRawFiles/source.pdf",
        )
        self.assertEqual(
            blob_entity["CreatedAt"],
            job_started_at.isoformat().replace("+00:00", "Z"),
        )
        self.assertGreaterEqual(
            datetime.fromisoformat(blob_entity["UpdatedAt"].replace("Z", "+00:00")),
            job_started_at,
        )
        self.assertEqual(blob_entity["Store"], "North")
        self.assertEqual(json.loads(blob_entity["pagenumber"]),
                         [{"page": 1, "rono": "12345"}])
        self.assertEqual(json.loads(blob_entity["OriginalFileName"]),
                         ["source.pdf", "source_1_1.pdf"])
        self.assertIsNone(blob.updated_metadata)

        with (
            self.assertLogs("barcode-job", level="ERROR"),
            patch("job.scan_pdf", return_value=merger),
            patch("job.write_result_entity", side_effect=OSError("table unavailable")),
        ):
            code, write_failure = run(config, time.perf_counter())

        self.assertEqual(code, EXIT_WRITE)
        self.assertEqual(write_failure["stage"], "write")
        self.assertEqual(blob.updated_metadata, None)

    def test_scan_failure_does_not_write_a_table_entity_or_change_local_pdf(self):
        source_bytes = self.source.read_bytes()
        merger = PageMerger()
        merger.add({
            "file": self.source.name,
            "page": 1,
            "dpi": 72,
            "error": "test scan error",
        })

        with (
            patch("job.scan_pdf", return_value=merger),
            patch("job.write_result_entity") as write_result_entity,
        ):
            code, result = run(self.config(), time.perf_counter())

        self.assertEqual(code, EXIT_SCAN)
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.source.read_bytes(), source_bytes)
        write_result_entity.assert_not_called()


    def test_finalize_execution_command_updates_timestamps_without_scanning(self):
        blob_url = (
            "https://alinkprodstorage.blob.core.windows.net/"
            "FileBunkerRawFiles/source.pdf"
        )
        started_at = "2026-10-14T10:37:57Z"
        ended_at = "2026-10-14T10:38:30Z"
        output = StringIO()

        with (
            redirect_stdout(output),
            patch("job.setup_logging"),
            patch("job.signal.signal"),
            patch("job.update_execution_timestamps") as finalize,
            patch("job.scan_pdf") as scan,
        ):
            code = main([
                "--finalize-execution",
                "--execution-start-time", started_at,
                "--execution-end-time", ended_at,
                blob_url,
            ])

        self.assertEqual(code, EXIT_OK)
        finalize.assert_called_once_with(blob_url, started_at, ended_at)
        scan.assert_not_called()
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "status": "completed",
                "file": "source.pdf",
                "created_at": started_at,
                "updated_at": ended_at,
            },
        )

    def test_scan_command_passes_fourth_positional_file_path_to_config(self):
        input_values = [
            "https://alinkprodstorage.blob.core.windows.net/raw/source.pdf",
            "source.pdf",
            "SC1",
            "FileBunkerRawFiles/2026/source.pdf",
        ]
        output = StringIO()

        with (
            redirect_stdout(output),
            patch("job.setup_logging"),
            patch("job.signal.signal"),
            patch("job.load_config", return_value=Mock()) as load_config_mock,
            patch("job.run", return_value=(EXIT_OK, {"status": "ok"})),
        ):
            code = main(input_values)

        self.assertEqual(code, EXIT_OK)
        load_config_mock.assert_called_once_with(*input_values)


if __name__ == "__main__":
    unittest.main()
