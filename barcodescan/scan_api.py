"""Library entry point: scan one PDF and return merged results. Writes no files."""
from concurrent.futures import ProcessPoolExecutor
from itertools import repeat
from pathlib import Path
from typing import Callable, Optional

from .config import CHUNKSIZE, Settings
from .output import PageMerger
from .pipeline import init_worker, process_page
from .sources import build_tasks

PROGRESS_STEP = 50   # call the progress callback every N finished pages


def scan_pdf(pdf_path, workers: int, settings: Settings, name: Optional[str] = None,
             progress: Optional[Callable[[int, int], None]] = None) -> PageMerger:
    """Scan pages through settings.dpis until each page gets a barcode or runs out.
    progress(done, total) is called periodically if given."""
    pdf_path = Path(pdf_path)
    name = name or pdf_path.name
    pages = list(build_tasks([(str(pdf_path), name)]))
    tasks = [(*t, settings.dpis) for t in pages]

    merger = PageMerger()
    total = len(tasks)
    if total == 0:
        return merger

    with ProcessPoolExecutor(workers, initializer=init_worker) as ex:
        for done, records in enumerate(
                ex.map(process_page, tasks, repeat(settings), chunksize=CHUNKSIZE), 1):
            for rec in records:
                merger.add(rec)
            if progress and (done % PROGRESS_STEP == 0 or done == total):
                progress(done, total)
    return merger