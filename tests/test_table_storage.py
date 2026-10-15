import hashlib
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from azure.data.tables import UpdateMode
from barcodescan.table_storage import (
    TABLE_NAME,
    build_result_entity,
    table_endpoint_from_blob_url,
    update_execution_timestamps,
    write_result_entity,
)


class TableResultEntityTests(unittest.TestCase):
    def test_builds_source_row_with_split_names_barcode_boundaries_and_store(self):
        entity = build_result_entity(
            blob_url=(
                "https://alinkdev.blob.core.windows.net/barcodescanner/"
                "incoming/invoice.pdf?sv=secret"
            ),
            source_name="invoice.pdf",
            source_metadata={"store": "North"},
            result={
                "barcodes": [
                    {"page": 7, "value": "12347"},
                    {"page": 3, "value": "12345"},
                    {"page": 3, "value": "12346"},
                ]
            },
            created_at=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 10, 12, 10, 5, tzinfo=timezone.utc),
            blob_uri="FileBunkerRawFiles/incoming/invoice.pdf",
        )

        self.assertEqual(
            entity["BlobBackupFilePath"],
            "FileBunkerRawFiles/incoming/invoice.pdf",
        )
        self.assertEqual(
            json.loads(entity["OriginalFileName"]),
            [
                "invoice.pdf",
                "invoice_1_3.pdf",
                "invoice_4_7.pdf",
            ],
        )
        self.assertEqual(entity["Store"], "North")
        self.assertEqual(
            json.loads(entity["pagenumber"]),
            [
                {"page": 3, "rono": "12345"},
                {"page": 3, "rono": "12346"},
                {"page": 7, "rono": "12347"},
            ],
        )
        self.assertEqual(entity["CreatedAt"], "2026-10-12T10:00:00Z")
        self.assertEqual(entity["UpdatedAt"], "2026-10-12T10:05:00Z")

    def test_updated_at_must_be_timezone_aware(self):
        with self.assertRaisesRegex(ValueError, "updated_at must be timezone-aware"):
            build_result_entity(
                blob_url="https://alinkdev.blob.core.windows.net/barcodescanner/file.pdf",
                source_name="file.pdf",
                source_metadata={},
                result={"barcodes": []},
                updated_at=datetime(2026, 10, 12, 10, 5),
            )

    def test_only_stores_barcode_values_matching_validation_regex(self):
        entity = build_result_entity(
            blob_url=(
                "https://alinkdev.blob.core.windows.net/barcodescanner/"
                "validated.pdf"
            ),
            source_name="validated.pdf",
            source_metadata={},
            result={
                "barcodes": [
                    {"page": 1, "value": "12345"},
                    {"page": 2, "value": "1234"},
                    {"page": 3, "value": "ABCDEF"},
                    {"page": 4, "value": "12345678901234567"},
                    {"page": 5, "value": "123456789012345678"},
                ]
            },
        )

        self.assertEqual(
            json.loads(entity["pagenumber"]),
            [
                {"page": 1, "rono": "12345"},
                {"page": 5, "rono": "123456789012345678"},
            ],
        )
        self.assertEqual(
            json.loads(entity["OriginalFileName"]),
            ["validated.pdf", "validated_1_1.pdf", "validated_2_5.pdf"],
        )

    def test_missing_store_and_barcodes_produce_empty_values(self):
        entity = build_result_entity(
            blob_url=(
                "https://alinkdev.blob.core.windows.net/barcodescanner/empty.pdf"
            ),
            source_name="empty.pdf",
            source_metadata={},
            result={"barcodes": []},
            created_at=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(entity["Store"], "")
        self.assertEqual(entity["BlobBackupFilePath"], "")
        self.assertEqual(json.loads(entity["OriginalFileName"]), ["empty.pdf"])
        self.assertEqual(json.loads(entity["pagenumber"]), [])

    def test_same_source_has_stable_entity_keys(self):
        arguments = {
            "blob_url": (
                "https://alinkdev.blob.core.windows.net/barcodescanner/"
                "incoming/invoice.pdf"
            ),
            "source_name": "invoice.pdf",
            "source_metadata": {},
            "result": {"barcodes": []},
        }

        first = build_result_entity(**arguments)
        repeated = build_result_entity(**arguments)
        another = build_result_entity(
            **{
                **arguments,
                "blob_url": (
                    "https://alinkdev.blob.core.windows.net/barcodescanner/"
                    "incoming/another.pdf"
                ),
            }
        )

        self.assertEqual(first["PartitionKey"], repeated["PartitionKey"])
        self.assertEqual(first["RowKey"], repeated["RowKey"])
        self.assertNotEqual(first["RowKey"], another["RowKey"])


class TableStorageClientTests(unittest.TestCase):
    def test_updates_only_execution_timestamps_on_existing_source_row(self):
        table_client = Mock()
        service_client = Mock()
        service_client.create_table_if_not_exists.return_value = table_client
        table_client.get_entity.return_value = {
            "PartitionKey": "barcodescanner",
            "RowKey": "existing-row",
            "Store": "SC1",
            "pagenumber": "[]",
        }

        with (
            patch.dict(
                os.environ,
                {
                    "AZURE_STORAGE_AUTH_MODE": "managed_identity",
                    "AZURE_STORAGE_ACCOUNT_URL": (
                        "https://alinkdev.blob.core.windows.net"
                    ),
                },
                clear=True,
            ),
            patch("barcodescan.table_storage.ManagedIdentityCredential"),
            patch(
                "barcodescan.table_storage.TableServiceClient",
                return_value=service_client,
            ),
        ):
            update_execution_timestamps(
                "https://alinkdev.blob.core.windows.net/barcodescanner/invoice.pdf",
                "2026-10-14T10:37:57Z",
                "2026-10-14T10:38:30Z",
            )

        table_client.get_entity.assert_called_once_with(
            partition_key="barcodescanner",
            row_key=hashlib.sha256(b"invoice.pdf").hexdigest(),
        )
        updated_entity = table_client.update_entity.call_args.kwargs["entity"]
        self.assertEqual(updated_entity["PartitionKey"], "barcodescanner")
        self.assertEqual(
            updated_entity["RowKey"],
            hashlib.sha256(b"invoice.pdf").hexdigest(),
        )
        self.assertEqual(updated_entity["CreatedAt"], "2026-10-14T10:37:57Z")
        self.assertEqual(updated_entity["UpdatedAt"], "2026-10-14T10:38:30Z")
        table_client.update_entity.assert_called_once_with(
            entity=updated_entity,
            mode=UpdateMode.MERGE,
        )
        service_client.close.assert_called_once_with()

    def test_execution_timestamps_normalize_offsets_to_utc(self):
        table_client = Mock()
        service_client = Mock()
        service_client.create_table_if_not_exists.return_value = table_client
        table_client.get_entity.return_value = {
            "PartitionKey": "barcodescanner",
            "RowKey": "existing-row",
        }

        with (
            patch.dict(
                os.environ,
                {
                    "AZURE_STORAGE_AUTH_MODE": "managed_identity",
                    "AZURE_STORAGE_ACCOUNT_URL": (
                        "https://alinkdev.blob.core.windows.net"
                    ),
                },
                clear=True,
            ),
            patch("barcodescan.table_storage.ManagedIdentityCredential"),
            patch(
                "barcodescan.table_storage.TableServiceClient",
                return_value=service_client,
            ),
        ):
            update_execution_timestamps(
                "https://alinkdev.blob.core.windows.net/barcodescanner/invoice.pdf",
                "2026-10-14T12:37:57+02:00",
                "2026-10-14T12:38:30+02:00",
            )

        self.assertEqual(
            table_client.update_entity.call_args.kwargs["entity"]["CreatedAt"],
            "2026-10-14T10:37:57Z",
        )
        self.assertEqual(
            table_client.update_entity.call_args.kwargs["entity"]["UpdatedAt"],
            "2026-10-14T10:38:30Z",
        )

    def test_rejects_invalid_or_reversed_execution_timestamps(self):
        blob_url = (
            "https://alinkdev.blob.core.windows.net/barcodescanner/invoice.pdf"
        )
        invalid_pairs = (
            ("not-a-time", "2026-10-14T10:38:30Z"),
            ("2026-10-14T10:38:30Z", "2026-10-14T10:37:57Z"),
            ("2026-10-14T10:37:57", "2026-10-14T10:38:30Z"),
        )

        for started_at, ended_at in invalid_pairs:
            with self.subTest(started_at=started_at, ended_at=ended_at):
                with self.assertRaises(ValueError):
                    update_execution_timestamps(blob_url, started_at, ended_at)

    def test_missing_source_row_is_not_silently_created(self):
        table_client = Mock()
        service_client = Mock()
        service_client.create_table_if_not_exists.return_value = table_client
        table_client.get_entity.side_effect = RuntimeError("entity not found")

        with (
            patch.dict(
                os.environ,
                {
                    "AZURE_STORAGE_AUTH_MODE": "managed_identity",
                    "AZURE_STORAGE_ACCOUNT_URL": (
                        "https://alinkdev.blob.core.windows.net"
                    ),
                },
                clear=True,
            ),
            patch("barcodescan.table_storage.ManagedIdentityCredential"),
            patch(
                "barcodescan.table_storage.TableServiceClient",
                return_value=service_client,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "entity not found"):
                update_execution_timestamps(
                    "https://alinkdev.blob.core.windows.net/barcodescanner/invoice.pdf",
                    "2026-10-14T10:37:57Z",
                    "2026-10-14T10:38:30Z",
                )

        table_client.update_entity.assert_not_called()
        service_client.close.assert_called_once_with()

    def test_managed_identity_creates_table_and_replaces_entity(self):
        entity = {"PartitionKey": "barcodescanner", "RowKey": "stable-key"}
        table_client = Mock()
        service_client = Mock()
        service_client.create_table_if_not_exists.return_value = table_client

        with (
            patch.dict(
                os.environ,
                {
                    "AZURE_STORAGE_AUTH_MODE": "managed_identity",
                    "AZURE_STORAGE_ACCOUNT_URL": (
                        "https://alinkdev.blob.core.windows.net"
                    ),
                },
                clear=True,
            ),
            patch(
                "barcodescan.table_storage.ManagedIdentityCredential"
            ) as credential_factory,
            patch(
                "barcodescan.table_storage.TableServiceClient",
                return_value=service_client,
            ) as service_factory,
        ):
            write_result_entity(entity)

        service_factory.assert_called_once_with(
            endpoint="https://alinkdev.table.core.windows.net",
            credential=credential_factory.return_value,
        )
        service_client.create_table_if_not_exists.assert_called_once_with(TABLE_NAME)
        table_client.upsert_entity.assert_called_once_with(
            entity=entity,
            mode=UpdateMode.REPLACE,
        )
        service_client.close.assert_called_once_with()

    def test_connection_string_creates_table_service_client(self):
        service_client = Mock()
        service_client.create_table_if_not_exists.return_value = Mock()
        connection_string = "DefaultEndpointsProtocol=https;AccountName=test"

        with (
            patch.dict(
                os.environ,
                {
                    "AZURE_STORAGE_AUTH_MODE": "connection_string",
                    "AZURE_STORAGE_CONNECTION_STRING": connection_string,
                },
                clear=True,
            ),
            patch(
                "barcodescan.table_storage.TableServiceClient"
            ) as service_factory,
        ):
            service_factory.from_connection_string.return_value = service_client
            write_result_entity({"PartitionKey": "barcodescanner", "RowKey": "key"})

        service_factory.from_connection_string.assert_called_once_with(
            connection_string
        )

    def test_table_endpoint_supports_public_and_sovereign_clouds(self):
        self.assertEqual(
            table_endpoint_from_blob_url("https://account.blob.core.windows.net"),
            ("https://account.table.core.windows.net", None),
        )
        self.assertEqual(
            table_endpoint_from_blob_url(
                "https://account.blob.core.usgovcloudapi.net"
            ),
            (
                "https://account.table.core.usgovcloudapi.net",
                "https://storage.azure.us",
            ),
        )


if __name__ == "__main__":
    unittest.main()
