import csv
import json
import os
from collections import defaultdict
from pathlib import Path

from .config import FIELDS


class PageMerger:
    """Collects attempted DPI results for each page until its first barcode hit."""

    def __init__(self):
        self.pages = {}                       # (file, page) -> state
        self.new_by_dpi = defaultdict(int)    # barcodes first seen in each pass

    def add(self, rec):
        key = (rec["file"], rec["page"])
        st = self.pages.setdefault(key, {"found": {}, "error": None, "dpis": []})
        dpi = rec.get("dpi")
        st["dpis"].append(dpi)

        if "error" in rec:
            st["error"] = st["error"] or rec["error"]
            return
        for b in rec["barcodes"]:
            k = (b["type"], b["value"])
            if k not in st["found"]:
                st["found"][k] = (b, dpi)
                self.new_by_dpi[dpi] += 1

    def summary(self):
        found_pages = sum(1 for st in self.pages.values() if st["found"])
        errors = sum(1 for st in self.pages.values() if not st["found"] and st["error"])
        codes = sum(len(st["found"]) for st in self.pages.values())
        return {"pages": len(self.pages), "found_pages": found_pages,
                "codes": codes, "errors": errors, "new_by_dpi": dict(self.new_by_dpi)}

    def rows(self):
        blank = dict(barcode_type="", value="", method="", confidence="",
                     dpi="", x0="", y0="", x1="", y1="")
        for (file, page), st in self.pages.items():
            if st["found"]:
                for (_, _), (b, dpi) in st["found"].items():
                    yield dict(blank, file=file, page=page, status="found",
                               barcode_type=b["type"], value=b["value"],
                               method=b["method"], confidence=b["confidence"], dpi=dpi,
                               x0=b["bbox"][0], y0=b["bbox"][1],
                               x1=b["bbox"][2], y1=b["bbox"][3])
            elif st["error"]:
                yield dict(blank, file=file, page=page, status="error", value=st["error"])
            else:
                yield dict(blank, file=file, page=page, status="none",
                           dpi=" ".join(str(d) for d in st["dpis"]))


def write_results(output_path: Path, merger: PageMerger):
    """Write the merged result. Goes to a .tmp file first, then renamed,
    so the output is never half-written."""
    output_path = Path(output_path)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    is_json = output_path.suffix.lower() == ".json"
    rows = list(merger.rows())

    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        if is_json:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
        else:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    os.replace(tmp, output_path)