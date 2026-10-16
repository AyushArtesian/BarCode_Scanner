import unittest
from unittest.mock import patch

from barcodescan.config import Settings
from barcodescan.pipeline import process_page


class ProcessPageTests(unittest.TestCase):
    def test_stops_at_first_dpi_that_finds_a_barcode(self):
        page_task = ("pdf", "source.pdf", "source.pdf", 0, (100, 150, 170))

        def scan_at_dpi(task, _settings):
            dpi = task[-1]
            barcodes = [{"type": "Code128", "value": "12345"}] if dpi == 150 else []
            return {
                "file": "source.pdf",
                "page": 1,
                "dpi": dpi,
                "barcodes": barcodes,
            }

        with patch("barcodescan.pipeline.process_task", side_effect=scan_at_dpi) as scan:
            records = process_page(page_task, Settings())

        self.assertEqual([record["dpi"] for record in records], [100, 150])
        self.assertEqual(scan.call_count, 2)

    def test_checks_every_dpi_when_no_barcode_is_found(self):
        page_task = ("pdf", "source.pdf", "source.pdf", 0, (100, 150, 170))

        def scan_at_dpi(task, _settings):
            dpi = task[-1]
            return {
                "file": "source.pdf",
                "page": 1,
                "dpi": dpi,
                "barcodes": [],
            }

        with patch("barcodescan.pipeline.process_task", side_effect=scan_at_dpi) as scan:
            records = process_page(page_task, Settings())

        self.assertEqual([record["dpi"] for record in records], [100, 150, 170])
        self.assertEqual(scan.call_count, 3)

    def test_invalid_barcode_does_not_stop_higher_dpi_passes(self):
        page_task = ("pdf", "source.pdf", "source.pdf", 0, (100, 150, 170))

        def scan_at_dpi(task, _settings):
            dpi = task[-1]
            value = "ABC" if dpi == 100 else "12345"
            return {
                "file": "source.pdf",
                "page": 1,
                "dpi": dpi,
                "barcodes": [{"type": "Code128", "value": value}],
            }

        with patch("barcodescan.pipeline.process_task", side_effect=scan_at_dpi) as scan:
            records = process_page(
                page_task,
                Settings(folder_detail_type="RO"),
            )

        self.assertEqual([record["dpi"] for record in records], [100, 150])
        self.assertEqual(records[0]["barcodes"], [])
        self.assertEqual(records[1]["barcodes"][0]["value"], "12345")
        self.assertEqual(scan.call_count, 2)

    def test_continues_after_a_dpi_pass_error(self):
        page_task = ("pdf", "source.pdf", "source.pdf", 0, (100, 150, 170))

        def scan_at_dpi(task, _settings):
            dpi = task[-1]
            if dpi == 100:
                return {
                    "file": "source.pdf",
                    "page": 1,
                    "dpi": dpi,
                    "error": "render failed",
                }
            return {
                "file": "source.pdf",
                "page": 1,
                "dpi": dpi,
                "barcodes": (
                    [{"type": "Code128", "value": "12345"}] if dpi == 150 else []
                ),
            }

        with patch("barcodescan.pipeline.process_task", side_effect=scan_at_dpi) as scan:
            records = process_page(page_task, Settings())

        self.assertEqual([record["dpi"] for record in records], [100, 150])
        self.assertIn("error", records[0])
        self.assertEqual(scan.call_count, 2)
