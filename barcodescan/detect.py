import os
import re

import cv2
import numpy as np

from .config import (DET_MAX_SIDE, GRAD_THRESH, MIN_BOX, MAX_REGIONS,
                     PAD_FRAC, PAD_MIN)


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
        (cv2.subtract(gx, gy), (21, 5)),    # upright 1D barcode
        (cv2.subtract(gy, gx), (5, 21)),    # 1D barcode rotated 90 degrees
        (cv2.add(gx, gy), (13, 13)),        # 2D codes
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
            if bw * bh > 0.4 * w * h:           # photo or table, not a barcode
                continue
            fill = cv2.contourArea(c) / float(bw * bh)
            if fill < 0.5:
                continue
            cands.append((fill, x, y, bw, bh))

    cands.sort(reverse=True)
    kept = []
    for _, x, y, bw, bh in cands:
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


def save_debug(gray, boxes, found, name, page, debug_dir):
    """Red = candidate regions, green = decoded barcodes."""
    s = min(1.0, 1400 / max(gray.shape))
    vis = cv2.cvtColor(cv2.resize(gray, None, fx=s, fy=s) if s < 1 else gray,
                       cv2.COLOR_GRAY2BGR)
    h, w = vis.shape[:2]
    for x0, y0, x1, y1 in boxes:
        cv2.rectangle(vis, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (0, 0, 255), 2)
    for b in found:
        x0, y0, x1, y1 = b["bbox"]
        cv2.rectangle(vis, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), (0, 200, 0), 2)
    os.makedirs(debug_dir, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", name)
    cv2.imwrite(os.path.join(debug_dir, f"{safe}_p{page}.png"), vis)