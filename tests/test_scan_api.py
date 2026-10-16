import unittest
from pathlib import Path
from unittest.mock import patch

from barcodescan.config import Settings
from barcodescan.scan_api import scan_pdf


class ScanApiTests(unittest.TestCase):
    def test_first_page_only_limits_the_scheduled_pages(self):
        pages = [
            ("pdf", "source.pdf", "source.pdf", 0),
            ("pdf", "source.pdf", "source.pdf", 1),
            ("pdf", "source.pdf", "source.pdf", 2),
        ]
        scheduled = []

        class CapturingExecutor:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def map(self, _function, tasks, _settings, chunksize):
                self.tasks = list(tasks)
                scheduled.extend(self.tasks)
                return [
                    [{
                        "file": task[2],
                        "page": task[3] + 1,
                        "dpi": task[4][0],
                        "barcodes": [],
                    }]
                    for task in self.tasks
                ]

        with (
            patch("barcodescan.scan_api.build_tasks", return_value=pages),
            patch("barcodescan.scan_api.ProcessPoolExecutor", CapturingExecutor),
        ):
            merger = scan_pdf(
                Path("source.pdf"),
                workers=1,
                settings=Settings(dpis=(100, 150)),
                first_page_only=True,
            )

        self.assertEqual([task[3] for task in scheduled], [0])
        self.assertEqual(merger.summary()["pages"], 1)

    def test_scans_all_pages_when_first_page_only_is_not_requested(self):
        pages = [
            ("pdf", "source.pdf", "source.pdf", 0),
            ("pdf", "source.pdf", "source.pdf", 1),
        ]
        scheduled = []

        class CapturingExecutor:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def map(self, _function, tasks, _settings, chunksize):
                scheduled.extend(list(tasks))
                return [
                    [{
                        "file": task[2],
                        "page": task[3] + 1,
                        "dpi": task[4][0],
                        "barcodes": [],
                    }]
                    for task in scheduled
                ]

        with (
            patch("barcodescan.scan_api.build_tasks", return_value=pages),
            patch("barcodescan.scan_api.ProcessPoolExecutor", CapturingExecutor),
        ):
            merger = scan_pdf(
                Path("source.pdf"),
                workers=1,
                settings=Settings(dpis=(100,)),
            )

        self.assertEqual([task[3] for task in scheduled], [0, 1])
        self.assertEqual(merger.summary()["pages"], 2)
