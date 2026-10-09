import numpy as np

from .code128 import bars_only, decode_band, many_edges
from .config import DIRECTIONAL_MIN, UPSCALE_FAST, UPSCALE_MORE
from .zxing_decode import adaptive_zxing, read_codes, try_zxing


def direction_ratio(crop):
    """Much greater than 1 means one-directional edges (a 1D barcode).
    Text is roughly 1 to 2, real codes measured 3.3 to 7."""
    c = crop.astype(np.float32)
    if c.shape[0] < 3 or c.shape[1] < 3:
        return 1.0
    gx = np.abs(np.diff(c, axis=1)).mean()
    gy = np.abs(np.diff(c, axis=0)).mean()
    return float(max(gx, gy) / (min(gx, gy) + 1e-3))


def sample_code128(crop):
    for img in (crop, np.ascontiguousarray(np.rot90(crop))):
        band = bars_only(img)
        if band is None or not many_edges(band):
            continue
        for cand in (band, np.ascontiguousarray(band[:, ::-1])):
            out = decode_band(cand)
            if out:
                return [("Code128", out[0], "code128-sampler", round(out[1], 3))]
    return []


def decode_crop(crop):
    """Returns [(type, value, method, confidence)] for one candidate crop.
    Cheapest method first; each step runs only if the previous one failed."""
    res = try_zxing(crop, UPSCALE_FAST)
    if res:
        return res
    if direction_ratio(crop) < DIRECTIONAL_MIN:      # not 1D-like, stop early
        return []
    res = try_zxing(crop, UPSCALE_MORE)
    if res:
        return res
    res = adaptive_zxing(crop)
    if res:
        return res
    return sample_code128(crop)


def decode_regions(img, boxes):
    h, w = img.shape
    out = []
    for box in boxes:
        x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)
        crop = np.ascontiguousarray(img[y0:y1, x0:x1])
        if crop.size == 0:
            continue
        for t, v, m, c in decode_crop(crop):
            out.append({"type": t, "value": v, "method": m, "confidence": c,
                        "bbox": [round(b, 4) for b in box]})
    return out


def decode_full(img):
    """Safety net: one full-page decode. Off by default because it is slow."""
    h, w = img.shape
    out = []
    for r in read_codes(img):
        p = r.position
        xs = [p.top_left.x, p.top_right.x, p.bottom_right.x, p.bottom_left.x]
        ys = [p.top_left.y, p.top_right.y, p.bottom_right.y, p.bottom_left.y]
        out.append({"type": r.format.name, "value": r.text,
                    "method": "zxing-fullpage", "confidence": "",
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