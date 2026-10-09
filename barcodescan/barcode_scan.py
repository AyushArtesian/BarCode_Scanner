#!/usr/bin/env python3
"""
Find + decode barcodes anywhere on PDF pages / images (low memory, low CPU).
Tuned for low-quality scans (thin bars, ~1px modules, noisy, tinted paper).

Pipeline per page:
  1. render page once (grayscale, RENDER_DPI)
  2. LOCATE barcode-like regions (gradient + morphology, OpenCV)
  3. crop each region, UPSCALE it, decode only that crop (zxing-cpp)
  4. still nothing and it looks like a 1D barcode: Code128 module-sampling decoder
     (soft pattern matching + checksum) - rescues blurry ~1px/module scans
  5. if the page still has nothing: one full-page decode as a safety net (skip with --fast)

Usage:
    python barcode_scan.py INPUT [-o OUTPUT] [--workers N] [--fast] [--debug DIR]

INPUT   PDF, image, folder (recursive) or .zip
OUTPUT  .csv (default) or .json
"""
import argparse
import csv
import json
import os
import re
import sys
import tempfile
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium
import zxingcpp
from PIL import Image

# ---- tunables ---------------------------------------------------------------
RENDER_DPI = 200         # page render resolution (one render per page)
RENDER_MAX_SIDE = 4000   # cap so huge pages can't blow up memory
DET_MAX_SIDE = 1600      # detection works on a copy no larger than this
GRAD_THRESH = 40         # lower = more sensitive detection
MIN_BOX = 18             # min region size (detection pixels)
MAX_REGIONS = 12         # decode at most this many candidates per page
PAD_FRAC = 0.20          # padding around a region (keeps the quiet zone)
PAD_MIN = 12             # ...and at least this many detection pixels
DIRECTIONAL_MIN = 2.5            # edge-direction ratio needed for the expensive 1D rescue paths
UPSCALES_FAST = (3, 1)           # tried on every candidate crop
UPSCALES_MORE = (2, 4, 2.5, 3.5) # extra sweep only for crops that look barcode-ish (decoding is scale-sensitive on ~1px bars)
CHUNK_PAGES = 50
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
FIELDS = ["file", "page", "status", "barcode_type", "value", "method", "confidence", "x0", "y0", "x1", "y1"]

Image.MAX_IMAGE_PIXELS = None


# ---- step 1: locate barcode regions -------------------------------------------
def detect_regions(gray):
    """gray: uint8 image. Returns boxes as fractions (x0, y0, x1, y1), best first."""
    h0, w0 = gray.shape
    s = min(1.0, DET_MAX_SIDE / max(h0, w0))
    det = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    h, w = det.shape

    blur = cv2.GaussianBlur(det, (3, 3), 0)
    gx = cv2.convertScaleAbs(cv2.Scharr(blur, cv2.CV_16S, 1, 0))
    gy = cv2.convertScaleAbs(cv2.Scharr(blur, cv2.CV_16S, 0, 1))

    masks = [
        (cv2.subtract(gx, gy), (21, 5)),    # upright 1D barcode (vertical bars)
        (cv2.subtract(gy, gx), (5, 21)),    # 1D barcode rotated 90 degrees
        (cv2.add(gx, gy), (13, 13)),        # 2D codes (QR, DataMatrix, ...)
    ]

    cands = []
    for m, kernel in masks:
        m = cv2.blur(m, (9, 9))
        _, m = cv2.threshold(m, GRAD_THRESH, 255, cv2.THRESH_BINARY)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
        m = cv2.erode(m, None, iterations=3)
        m = cv2.dilate(m, None, iterations=3)
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            x, y, bw, bh = cv2.boundingRect(c)
            if bw < MIN_BOX or bh < MIN_BOX:
                continue
            if bw * bh > 0.4 * w * h:           # too big: photo / table, not a barcode
                continue
            fill = cv2.contourArea(c) / float(bw * bh)
            if fill < 0.5:
                continue
            cands.append((fill, x, y, bw, bh))

    cands.sort(reverse=True)
    kept = []
    for fill, x, y, bw, bh in cands:
        dup = False
        for kx, ky, kw, kh in kept:
            ix = max(0, min(x + bw, kx + kw) - max(x, kx))
            iy = max(0, min(y + bh, ky + kh) - max(y, ky))
            if ix * iy > 0.6 * bw * bh:
                dup = True
                break
        if not dup:
            kept.append((x, y, bw, bh))
        if len(kept) >= MAX_REGIONS:
            break

    boxes = []
    for x, y, bw, bh in kept:
        px, py = max(PAD_MIN, bw * PAD_FRAC), max(PAD_MIN, bh * PAD_FRAC)
        boxes.append((max(0.0, (x - px) / w), max(0.0, (y - py) / h),
                      min(1.0, (x + bw + px) / w), min(1.0, (y + bh + py) / h)))
    return boxes


# ---- Code128 module-sampling fallback ----------------------------------------
_W = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
      "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
      "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
      "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
      "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
      "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
      "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
      "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
      "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
      "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
      "114131 311141 411131 211412 211214 211232").split()
STOP = "2331112"


def _bits(widths):
    out, bar = [], 1
    for ch in widths:
        out += [bar] * int(ch)
        bar ^= 1
    return tuple(out)


PATTERNS = {_bits(w): v for v, w in enumerate(_W)}
STOP_BITS = _bits(STOP)


def _decode_values(values):
    """values: [start, d1..dn, check, ] (stop already removed). Returns text or None."""
    start, data, check = values[0], values[1:-1], values[-1]
    if start not in (103, 104, 105):
        return None
    if (start + sum((i + 1) * v for i, v in enumerate(data))) % 103 != check:
        return None
    code = {103: "A", 104: "B", 105: "C"}[start]
    text = []
    shift = False
    for v in data:
        if code == "C":
            if v < 100:
                text.append(f"{v:02d}")
            elif v == 100:
                code = "B"
            elif v == 101:
                code = "A"
            elif v == 102:
                pass  # FNC1
        else:
            if v == 99:
                code = "C"
            elif v == 100 and code == "A":
                code = "B"
            elif v == 101 and code == "B":
                code = "A"
            elif v in (98,):
                shift = not shift
            elif v >= 96:
                pass  # FNC / shift codes: ignore
            else:
                c = v + 32 if code == "B" else (v + 32 if v < 64 else v - 64)
                text.append(chr(c))
    return "".join(text)


def _profile(band):
    """1D darkness profile from a bars-only crop (rows averaged)."""
    p = band.astype(np.float32).mean(axis=0)
    lo, hi = np.percentile(p, 3), np.percentile(p, 97)
    return (p - lo) / max(hi - lo, 1e-6)          # ~0 = dark bar, ~1 = light space


def _interp(p, x):
    return np.interp(x, np.arange(len(p)) + 0.5, p)


# Pattern matrix for soft (nearest-pattern) decoding: light=1, dark=0
_PAT = np.array([[1 - b for b in _bits(w)] for w in _W], dtype=np.float32)   # (107, 11)
_STOP_V = np.array([1 - b for b in STOP_BITS], dtype=np.float32)              # (13,)


_START_V = np.array([[1 - x for x in _bits(_W[v])] for v in (103, 104, 105)], dtype=np.float32)  # (3, 11)
_SUB = 5                                                                                       # samples per module


def _module_means(p, l, m, first, count):
    """Mean profile value of `count` modules starting at module index `first`."""
    xs = l + (first + (np.arange(count * _SUB) + 0.5) / _SUB) * m
    return _interp(p, xs).reshape(count, _SUB).mean(axis=1)


def decode_band(band, max_symbols=40, min_corr=0.75):
    """
    band: grayscale crop containing ONLY the bars (+ small margin).
    Returns (text, quality) or None. Accepts only checksum-valid decodes whose
    reconstruction correlates >= min_corr with the measured profile.
    Cheap rejection first (start + stop pattern on a handful of modules), so
    non-barcode regions cost almost nothing.
    """
    p = np.clip(_profile(band), 0.0, 1.0)
    if len(p) < 40:
        return None
    idx = np.where(p < 0.5)[0]
    if len(idx) < 10:
        return None
    left0, right0 = float(idx[0]), float(idx[-1] + 1)
    jit = np.arange(-1.5, 1.51, 0.5)
    best = None
    for k in range(4, max_symbols + 1):              # k = start + data + check symbols
        N = 11 * k + 13
        mod = (right0 - left0) / N
        if mod < 0.7 or mod > 8:
            continue
        for jl in jit:
            for jr in jit:
                l, r = left0 + jl, right0 + jr
                m = (r - l) / N
                # --- cheap stage: start pattern + stop pattern only
                head = _module_means(p, l, m, 0, 11)
                if ((_START_V - head) ** 2).sum(axis=1).min() > 1.7:
                    continue
                tail = _module_means(p, l, m, N - 13, 13)
                if ((tail - _STOP_V) ** 2).sum() > 1.7:
                    continue
                # --- full stage
                v = _module_means(p, l, m, 0, N)
                g = v[:k * 11].reshape(k, 11)
                d = ((g[:, None, :] - _PAT[None, :, :]) ** 2).sum(axis=2)     # (k, 107)
                vals = d.argmin(axis=1).tolist()
                txt = _decode_values(vals)
                if txt is None:
                    continue
                recon = np.concatenate([_PAT[x] for x in vals] + [_STOP_V])
                corr = float(np.corrcoef(v, recon)[0, 1])
                if corr >= min_corr and (best is None or corr > best[1]):
                    best = (txt, corr)
    return best


# ---- step 2: decode ------------------------------------------------------------
def _read(img):
    try:
        return [r for r in zxingcpp.read_barcodes(img) if r.valid and r.text]
    except Exception:
        return []


def _bars_only(crop, frac=0.6):
    """Tighten a crop to the rows that actually contain bars (drops handwriting, text, margins)."""
    e = np.abs(np.diff(crop.astype(np.int16), axis=1)).mean(axis=1)
    if e.size == 0 or e.max() < 8:
        return None
    rows = np.where(e >= frac * e.max())[0]
    runs = np.split(rows, np.where(np.diff(rows) != 1)[0] + 1)
    r = max(runs, key=len)
    band = crop[r[0] + 1:r[-1]]
    return band if band.shape[0] >= 6 and band.shape[1] >= 60 else None


def _try_zxing(crop, scales):
    for sc in scales:
        up = cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC) if sc != 1 else crop
        res = _read(up)
        if res:
            return [(r.format.name, r.text, "zxing", "") for r in res]
    return []


def _many_edges(img, min_edges=24):
    """Cheap check: enough bar/space transitions along rows or columns to be a barcode candidate."""
    for a in (img, img.T):
        p = a.astype(np.float32).mean(axis=0)
        if p.max() - p.min() < 30:
            continue
        d = p < (p.min() + p.max()) / 2
        if int(np.count_nonzero(d[1:] != d[:-1])) >= min_edges:
            return True
    return False


def _direction_ratio(crop):
    """>>1 means strongly one-directional edges (a 1D barcode); text is ~1-2. Real codes measured 3.3-7."""
    c = crop.astype(np.float32)
    if c.shape[0] < 3 or c.shape[1] < 3:
        return 1.0
    gx = np.abs(np.diff(c, axis=1)).mean()
    gy = np.abs(np.diff(c, axis=0)).mean()
    return float(max(gx, gy) / (min(gx, gy) + 1e-3))


def decode_crop(crop):
    """Return [(type, value, method, confidence)] for one candidate crop."""
    res = _try_zxing(crop, UPSCALES_FAST)                 # cheap; also covers 2D codes
    if res:
        return res
    if _direction_ratio(crop) < DIRECTIONAL_MIN:          # not a 1D barcode candidate -> stop here
        return []
    res = _try_zxing(crop, UPSCALES_MORE)
    if res:
        return res
    # adaptive threshold (uneven / tinted background)
    up = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    up = cv2.adaptiveThreshold(up, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 15)
    res = [(r.format.name, r.text, "zxing", "") for r in _read(up)]
    if res:
        return res
    # blurry 1D Code128: sample the bars directly (upright, mirrored, rotated 90 deg)
    for img in (crop, np.ascontiguousarray(np.rot90(crop))):
        band = _bars_only(img)
        if band is None or not _many_edges(band):
            continue
        for cand in (band, np.ascontiguousarray(band[:, ::-1])):
            out = decode_band(cand)
            if out:
                return [("Code128", out[0], "code128-sampler", round(out[1], 3))]
    return []


def decode_regions(img, boxes):
    h, w = img.shape
    out = []
    for box in boxes:
        x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
        crop = np.ascontiguousarray(img[y0:y1, x0:x1])
        if crop.size:
            for t, v, m, c in decode_crop(crop):
                out.append({"type": t, "value": v, "method": m, "confidence": c,
                            "bbox": [round(b, 4) for b in box]})
    return out


def decode_full(img):
    h, w = img.shape
    out = []
    for r in _read(img):
        p = r.position
        xs = [p.top_left.x, p.top_right.x, p.bottom_right.x, p.bottom_left.x]
        ys = [p.top_left.y, p.top_right.y, p.bottom_right.y, p.bottom_left.y]
        out.append({"type": r.format.name, "value": r.text, "method": "zxing-fullpage", "confidence": "",
                    "bbox": [round(min(xs) / w, 4), round(min(ys) / h, 4),
                             round(max(xs) / w, 4), round(max(ys) / h, 4)]})
    return out


def dedupe(found):
    seen, out = set(), []
    for b in found:
        k = (b["type"], b["value"])
        if k not in seen:
            seen.add(k)
            out.append(b)
    return out


def save_debug(gray, boxes, found, name, page, debug_dir):
    s = min(1.0, 1400 / max(gray.shape))
    vis = cv2.cvtColor(cv2.resize(gray, None, fx=s, fy=s) if s < 1 else gray, cv2.COLOR_GRAY2BGR)
    h, w = vis.shape[:2]
    for x0, y0, x1, y1 in boxes:
        cv2.rectangle(vis, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (0, 0, 255), 2)
    for b in found:
        x0, y0, x1, y1 = b["bbox"]
        cv2.rectangle(vis, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (0, 200, 0), 2)
    os.makedirs(debug_dir, exist_ok=True)
    cv2.imwrite(os.path.join(debug_dir, f"{re.sub(r'[^\w.-]', '_', name)}_p{page}.png"), vis)


# ---- per-page pipeline ------------------------------------------------------------
def scan_gray(gray, name, page, cfg):
    boxes = detect_regions(gray)
    found = decode_regions(gray, boxes) if boxes else []
    if not found and cfg["fallback"]:
        found = decode_full(gray)
    found = dedupe(found)
    if cfg["debug"]:
        save_debug(gray, boxes, found, name, page, cfg["debug"])
    return {"file": name, "page": page, "barcodes": found}


def scan_pdf_chunk(path, name, start, end, cfg):
    records = []
    try:
        pdf = pdfium.PdfDocument(path)
    except Exception as e:
        return [{"file": name, "page": 0, "error": f"cannot open PDF: {e}"}]
    try:
        for i in range(start, min(end, len(pdf))):
            try:
                page = pdf[i]
                w_pt, h_pt = page.get_size()
                scale = min(RENDER_DPI / 72, RENDER_MAX_SIDE / max(w_pt, h_pt))
                gray = np.ascontiguousarray(np.squeeze(page.render(scale=scale, grayscale=True).to_numpy()))
                page.close()
                records.append(scan_gray(gray, name, i + 1, cfg))
                del gray
            except Exception as e:
                records.append({"file": name, "page": i + 1, "error": str(e)})
    finally:
        pdf.close()
    return records


def scan_image(path, name, cfg):
    records = []
    try:
        with Image.open(path) as im:
            for f in range(getattr(im, "n_frames", 1)):
                try:
                    im.seek(f)
                    gray = np.ascontiguousarray(np.asarray(im.convert("L")))
                    records.append(scan_gray(gray, name, f + 1, cfg))
                except Exception as e:
                    records.append({"file": name, "page": f + 1, "error": str(e)})
    except Exception as e:
        records.append({"file": name, "page": 0, "error": f"cannot open image: {e}"})
    return records


# ---- input handling / output ----------------------------------------------------------
def collect_files(root):
    root = Path(root)
    if root.is_file():
        yield str(root), root.name
        return
    for p in sorted(root.rglob("*")):
        if p.is_file() and (p.suffix.lower() == ".pdf" or p.suffix.lower() in IMG_EXT):
            yield str(p), str(p.relative_to(root))


def build_tasks(files, cfg):
    for path, name in files:
        ext = Path(path).suffix.lower()
        if ext == ".pdf":
            try:
                n = len(pdfium.PdfDocument(path))
            except Exception:
                n = 1                      # worker will report the open error
            for s in range(0, n, CHUNK_PAGES):
                yield (scan_pdf_chunk, path, name, s, s + CHUNK_PAGES, cfg)
        elif ext in IMG_EXT:
            yield (scan_image, path, name, cfg)


def to_rows(rec):
    blank = dict(barcode_type="", value="", method="", confidence="", x0="", y0="", x1="", y1="")
    if "error" in rec:
        return [dict(file=rec["file"], page=rec["page"], status="error", **{**blank, "value": rec["error"]})]
    if not rec["barcodes"]:
        return [dict(file=rec["file"], page=rec["page"], status="none", **blank)]
    return [dict(file=rec["file"], page=rec["page"], status="found",
                 barcode_type=b["type"], value=b["value"],
                 method=b["method"], confidence=b["confidence"],
                 x0=b["bbox"][0], y0=b["bbox"][1], x1=b["bbox"][2], y1=b["bbox"][3])
            for b in rec["barcodes"]]


def run(input_path, output_path, workers, cfg):
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(input_path)
        if src.suffix.lower() == ".zip":
            with zipfile.ZipFile(src) as z:
                z.extractall(tmp)
            src = Path(tmp)

        files = list(collect_files(src))
        if not files:
            sys.exit("No PDF or image files found in input.")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = output_path.with_suffix(output_path.suffix + ".tmp")
        is_json = output_path.suffix.lower() == ".json"

        pages = found_pages = n_codes = n_err = 0
        json_rows = []

        with ProcessPoolExecutor(workers) as ex, \
                open(tmp_out, "w", newline="", encoding="utf-8") as fh:
            writer = None
            if not is_json:
                writer = csv.DictWriter(fh, fieldnames=FIELDS)
                writer.writeheader()

            futures = [ex.submit(t[0], *t[1:]) for t in build_tasks(files, cfg)]
            for fut in futures:                      # submission order => sorted output
                for rec in fut.result():
                    pages += 1
                    if "error" in rec:
                        n_err += 1
                    elif rec["barcodes"]:
                        found_pages += 1
                        n_codes += len(rec["barcodes"])
                    for row in to_rows(rec):
                        if is_json:
                            json_rows.append(row)
                        else:
                            writer.writerow(row)
                print(f"\rpages processed: {pages}", end="", file=sys.stderr, flush=True)

            if is_json:
                json.dump(json_rows, fh, indent=2, ensure_ascii=False)

        os.replace(tmp_out, output_path)             # atomic: never a half-written file

    print(f"\nDone. {pages} pages, {found_pages} with barcodes, {n_codes} barcodes, "
          f"{n_err} errors -> {output_path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Find and decode barcodes in PDFs/images.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("-w", "--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--fast", action="store_true", help="skip full-page fallback decode")
    ap.add_argument("--debug", metavar="DIR", help="save detection overlays (red=candidates, green=decoded)")
    a = ap.parse_args()

    out = a.output or str(Path(a.input).with_suffix("")) + "_barcodes.csv"
    run(a.input, out, a.workers, {"fallback": not a.fast, "debug": a.debug})


if __name__ == "__main__":
    main()