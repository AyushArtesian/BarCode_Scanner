# barcodescan/config.py
from dataclasses import dataclass
from typing import Optional, Tuple

# ---- rendering -------------------------------------------------------------
RENDER_MAX_SIDE = 4000           # cap so huge pages can't blow up memory
BLANK_STD = 3.0                  # pages with less grey variation are skipped

# ---- detection -------------------------------------------------------------
DET_MAX_SIDE = 1600              # detection works on a copy no larger than this
GRAD_THRESH = 40
MIN_BOX = 18
MAX_REGIONS = 12
PAD_FRAC = 0.20
PAD_MIN = 12

# ---- decoding --------------------------------------------------------------
DIRECTIONAL_MIN = 2.5            # edge-direction ratio needed for 1D rescue paths
UPSCALE_FAST = (3, 1)            # tried on every candidate crop
UPSCALE_MORE = (2, 4, 2.5, 3.5)  # only for 1D-looking crops that failed the fast pass
MAX_UPSCALED_PIXELS = 4_000_000  # hard cap on any upscaled crop

# ---- Code 128 sampler ------------------------------------------------------
SAMPLER_MIN_CORR = 0.75          # accept a decode above this correlation
SAMPLER_GOOD_CORR = 0.92         # stop searching once a decode is this good
SAMPLER_SUB = 5                  # samples per module
SAMPLER_K_SPREAD = 2             # symbol counts tested around the estimate
SAMPLER_JITTER = (-1.0, 0.0, 1.0)

# ---- scheduling ------------------------------------------------------------
DEFAULT_DPIS = (150, 170, 200, 250)   # render passes, run in this order for every page
CHUNKSIZE = 2                         # tasks sent to a worker at once
PROGRESS_EVERY = 10                   # print progress every N finished tasks

# ---- files -----------------------------------------------------------------
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
FIELDS = ["file", "page", "status", "barcode_type", "value", "method",
          "dpi", "confidence", "x0", "y0", "x1", "y1"]


@dataclass(frozen=True)
class Settings:
    """Per-run options. Frozen and picklable so it can be sent to worker processes."""
    dpis: Tuple[int, ...] = DEFAULT_DPIS    # every page is scanned at each of these
    fallback: bool = False                  # full-page decode when nothing is found
    debug_dir: Optional[str] = None         # save detection overlays here
    blank_std: float = BLANK_STD