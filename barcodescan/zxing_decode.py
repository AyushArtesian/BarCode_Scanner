import cv2
import zxingcpp

from .config import MAX_UPSCALED_PIXELS


def read_codes(img):
    try:
        return [r for r in zxingcpp.read_barcodes(img) if r.valid and r.text]
    except Exception:
        return []


def capped_scales(h, w, scales):
    """Clamp each scale so the upscaled crop stays under MAX_UPSCALED_PIXELS.
    Duplicates produced by the clamp are removed so the same scale is never decoded twice."""
    limit = max(1.0, (MAX_UPSCALED_PIXELS / (h * w)) ** 0.5)
    out = []
    for s in scales:
        s = min(s, limit)
        if s not in out:
            out.append(s)
    return out


def upscale(img, s):
    if s == 1:
        return img
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)


def try_zxing(crop, scales):
    h, w = crop.shape
    for s in capped_scales(h, w, scales):
        res = read_codes(upscale(crop, s))
        if res:
            return [(r.format.name, r.text, "zxing", "") for r in res]
    return []


def adaptive_zxing(crop):
    """Adaptive threshold for uneven or tinted backgrounds."""
    h, w = crop.shape
    s = capped_scales(h, w, (3,))[0]
    up = upscale(crop, s)
    up = cv2.adaptiveThreshold(up, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 51, 15)
    return [(r.format.name, r.text, "zxing", "") for r in read_codes(up)]