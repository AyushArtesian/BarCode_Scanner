"""Per-page work that runs inside worker processes."""
import cv2
import numpy as np
from PIL import Image

from .config import Settings
from .decode import decode_full, decode_regions, dedupe
from .detect import detect_regions, save_debug
from .sources import pdf_page, render_page


def init_worker():
    """Runs once in each worker process. OpenCV threads are set to 1
    because the process pool already provides the parallelism."""
    cv2.setNumThreads(1)
    Image.MAX_IMAGE_PIXELS = None


def scan_gray(gray, name, page, dpi, cfg: Settings):
    if float(gray.std()) < cfg.blank_std:
        return {"file": name, "page": page, "dpi": dpi, "barcodes": []}

    boxes = detect_regions(gray)
    found = decode_regions(gray, boxes) if boxes else []
    if not found and cfg.fallback:
        found = decode_full(gray)
    found = dedupe(found)

    if cfg.debug_dir:
        save_debug(gray, boxes, found, f"{name}_{dpi}dpi", page, cfg.debug_dir)
    return {"file": name, "page": page, "dpi": dpi, "barcodes": found}


def process_task(task, cfg: Settings):
    """Worker entry point. task = (kind, path, name, index, dpi).
    Always returns one record, even on error."""
    kind, path, name, index, dpi = task
    page_no = index + 1
    try:
        if kind == "pdf":
            page = pdf_page(path, index)
            try:
                gray = render_page(page, dpi)
            finally:
                page.close()
        else:
            with Image.open(path) as im:
                im.seek(index)
                gray = np.ascontiguousarray(np.asarray(im.convert("L")))
        return scan_gray(gray, name, page_no, dpi, cfg)
    except Exception as e:
        return {"file": name, "page": page_no, "dpi": dpi, "error": str(e)}