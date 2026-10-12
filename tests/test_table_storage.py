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
                    {"page": 7, "value": "RONO-2"},
                    {"page": 3, "value": "RONO-1"},
                    {"page": 3, "value": "RONO-1B"},
                ]
            },
            created_at=datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(entity["BlobBackupFilePath"],
                         "https://alinkdev.blob.core.windows.net/"
                         "barcodescanner/incoming/invoice.pdf")
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
                {"page": 3, "rono": "RONO-1"},
                {"page": 3, "rono": "RONO-1B"},
                {"page": 7, "rono": "RONO-2"},
            ],
        )
        self.assertEqual(entity["CreatedAt"], "2026-10-12T10:00:00Z")

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
