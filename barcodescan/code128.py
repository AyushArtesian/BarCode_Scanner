"""Module-sampling Code 128 decoder for blurry ~1px/module 1D barcodes."""
import numpy as np

from .config import (SAMPLER_GOOD_CORR, SAMPLER_JITTER, SAMPLER_K_SPREAD,
                     SAMPLER_MIN_CORR, SAMPLER_SUB)
from .tables import PAT, START_V, STOP_V, decode_values


def profile(band):
    """1D darkness profile (rows averaged). ~0 = dark bar, ~1 = light space."""
    p = band.astype(np.float32).mean(axis=0)
    lo, hi = np.percentile(p, 3), np.percentile(p, 97)
    return np.clip((p - lo) / max(hi - lo, 1e-6), 0.0, 1.0)


def module_means(p, left, mod, first, count):
    """Mean profile value of `count` modules starting at module index `first`."""
    xs = left + (first + (np.arange(count * SAMPLER_SUB) + 0.5) / SAMPLER_SUB) * mod
    vals = np.interp(xs, np.arange(len(p)) + 0.5, p)
    return vals.reshape(count, SAMPLER_SUB).mean(axis=1)


def estimate_symbol_counts(p, left, right):
    """Estimate the number of symbols from the narrowest bar runs, then return
    a small window of candidates instead of looping over every possible count."""
    bits = (p < 0.5).astype(np.int8)
    runs = np.diff(np.flatnonzero(np.diff(bits)))
    if runs.size < 4:
        return []
    mod = max(float(np.percentile(runs, 10)), 0.7)   # narrowest elements ~ 1 module
    k0 = round(((right - left) / mod - 13) / 11)
    return [k for k in range(k0 - SAMPLER_K_SPREAD, k0 + SAMPLER_K_SPREAD + 1)
            if 4 <= k <= 40]


def decode_band(band):
    """band: grayscale crop with only the bars (plus a small margin).
    Returns (text, correlation) or None. Only checksum-valid decodes count."""
    p = profile(band)
    if len(p) < 40:
        return None
    dark = np.flatnonzero(p < 0.5)
    if dark.size < 10:
        return None
    left0, right0 = float(dark[0]), float(dark[-1] + 1)

    best = None
    for k in estimate_symbol_counts(p, left0, right0):
        N = 11 * k + 13
        for jl in SAMPLER_JITTER:
            for jr in SAMPLER_JITTER:
                l, r = left0 + jl, right0 + jr
                mod = (r - l) / N
                if not 0.7 <= mod <= 8:
                    continue
                # Cheap stage: start and stop patterns only.
                head = module_means(p, l, mod, 0, 11)
                if ((START_V - head) ** 2).sum(axis=1).min() > 1.7:
                    continue
                tail = module_means(p, l, mod, N - 13, 13)
                if ((tail - STOP_V) ** 2).sum() > 1.7:
                    continue
                # Full stage.
                v = module_means(p, l, mod, 0, N)
                g = v[:k * 11].reshape(k, 11)
                vals = ((g[:, None, :] - PAT[None, :, :]) ** 2).sum(axis=2).argmin(axis=1).tolist()
                txt = decode_values(vals)
                if txt is None:
                    continue
                recon = np.concatenate([PAT[x] for x in vals] + [STOP_V])
                corr = float(np.corrcoef(v, recon)[0, 1])
                if corr >= SAMPLER_MIN_CORR and (best is None or corr > best[1]):
                    best = (txt, corr)
                    if corr >= SAMPLER_GOOD_CORR:
                        return best          # good enough, stop searching
    return best


def bars_only(crop, frac=0.6):
    """Tighten a crop to the rows that contain bars (drops text and handwriting)."""
    e = np.abs(np.diff(crop.astype(np.int16), axis=1)).mean(axis=1)
    if e.size == 0 or e.max() < 8:
        return None
    rows = np.where(e >= frac * e.max())[0]
    runs = np.split(rows, np.where(np.diff(rows) != 1)[0] + 1)
    r = max(runs, key=len)
    band = crop[r[0] + 1:r[-1]]
    return band if band.shape[0] >= 6 and band.shape[1] >= 60 else None


def many_edges(img, min_edges=24):
    """Cheap check: enough bar/space transitions along rows or columns."""
    for a in (img, img.T):
        p = a.astype(np.float32).mean(axis=0)
        if p.max() - p.min() < 30:
            continue
        d = p < (p.min() + p.max()) / 2
        if int(np.count_nonzero(d[1:] != d[:-1])) >= min_edges:
            return True
    return False