"""Shared scan-result serialization."""

from .config import Settings
from .output import PageMerger


def build_result(
    merger: PageMerger,
    name: str,
    settings: Settings,
    seconds: float,
) -> dict:
    stats = merger.summary()
    rows = list(merger.rows())
    barcodes = [
        {
            "page": row["page"],
            "type": row["barcode_type"],
            "value": row["value"],
            "dpi": row["dpi"],
            "bbox": [row["x0"], row["y0"], row["x1"], row["y1"]],
        }
        for row in rows
        if row["status"] == "found"
    ]
    error_pages = [
        {"page": row["page"], "error": row["value"]}
        for row in rows
        if row["status"] == "error"
    ]

    if stats["errors"] == 0:
        status = "ok"
    elif stats["errors"] < stats["pages"]:
        status = "partial"
    else:
        status = "error"

    return {
        "file": name,
        "status": status,
        "dpis": list(settings.dpis),
        "pages_total": stats["pages"],
        "pages_with_barcodes": stats["found_pages"],
        "barcode_count": stats["codes"],
        "error_count": stats["errors"],
        "seconds": round(seconds, 1),
        "barcodes": barcodes,
        "error_pages": error_pages,
    }
