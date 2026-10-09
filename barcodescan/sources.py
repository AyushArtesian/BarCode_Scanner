"""File discovery, PDF page access and render, and task creation."""
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from .config import IMG_EXT, RENDER_MAX_SIDE

# Each worker keeps one PDF open. Tasks for the same file tend to run
# consecutively, so this avoids reopening the document for every page.
_open_pdf = {"path": None, "doc": None}


def pdf_page(path, index):
    """Return page `index` of the PDF at `path`, reusing the open document when possible."""
    if _open_pdf["path"] != path:
        if _open_pdf["doc"] is not None:
            _open_pdf["doc"].close()
        _open_pdf["doc"] = pdfium.PdfDocument(path)
        _open_pdf["path"] = path
    return _open_pdf["doc"][index]


def render_page(page, dpi):
    """Render one PDF page as an 8-bit grayscale array."""
    w_pt, h_pt = page.get_size()
    scale = min(dpi / 72, RENDER_MAX_SIDE / max(w_pt, h_pt))
    bitmap = page.render(scale=scale, grayscale=True,
                         draw_annots=False, may_draw_forms=False)
    return np.ascontiguousarray(np.squeeze(bitmap.to_numpy()))


def collect_files(root):
    """Yields (path, display_name) for PDFs and images under root."""
    root = Path(root)
    if root.is_file():
        yield str(root), root.name
        return
    for p in sorted(root.rglob("*")):
        if p.is_file() and (p.suffix.lower() == ".pdf" or p.suffix.lower() in IMG_EXT):
            yield str(p), str(p.relative_to(root))


def build_tasks(files):
    """One task per page or image frame, so every page can run on any core.
    Task = (kind, path, display_name, index)."""
    for path, name in files:
        ext = Path(path).suffix.lower()
        if ext == ".pdf":
            try:
                doc = pdfium.PdfDocument(path)
                n = len(doc)
                doc.close()
            except Exception:
                n = 1                       # the worker will report the open error
            for i in range(n):
                yield ("pdf", path, name, i)
        elif ext in IMG_EXT:
            try:
                with Image.open(path) as im:
                    n = getattr(im, "n_frames", 1)
            except Exception:
                n = 1
            for i in range(n):
                yield ("image", path, name, i)