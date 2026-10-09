"""
Code 128 symbol table (ISO/IEC 15417). Each symbol is six bar/space widths
in modules; the table is fixed by the standard, so it must be a constant.
"""
import numpy as np

CODE128_WIDTHS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 "
    "221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 "
    "221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 "
    "212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 "
    "231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 "
    "231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 "
    "314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
    "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 "
    "111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
    "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 "
    "114131 311141 411131 211412 211214 211232"
).split()
STOP_WIDTHS = "2331112"

# Sanity checks: run at import, cheap, catch a mistyped table immediately.
# Sanity checks: run at import, cheap, catch a mistyped table immediately.
assert len(CODE128_WIDTHS) == 106                            # symbols 0..105
assert all(len(w) == 6 for w in CODE128_WIDTHS)              # six elements each
assert all(sum(map(int, w)) == 11 for w in CODE128_WIDTHS)   # every symbol is 11 modules
assert len(set(CODE128_WIDTHS)) == 106                       # all patterns are unique
assert len(STOP_WIDTHS) == 7 and sum(map(int, STOP_WIDTHS)) == 13   # stop is 13 modules


def _bits(widths):
    out, bar = [], 1
    for ch in widths:
        out += [bar] * int(ch)
        bar ^= 1
    return tuple(out)


# Soft-matching templates: light = 1, dark = 0 (matches the profile convention).
PAT = np.array([[1 - b for b in _bits(w)] for w in CODE128_WIDTHS], dtype=np.float32)
STOP_V = np.array([1 - b for b in _bits(STOP_WIDTHS)], dtype=np.float32)
START_V = PAT[[103, 104, 105]]


def decode_values(values):
    """values = [start, data..., check]. Returns text, or None if the checksum fails."""
    if len(values) < 3:
        return None
    start, data, check = values[0], values[1:-1], values[-1]
    if start not in (103, 104, 105):
        return None
    if (start + sum((i + 1) * v for i, v in enumerate(data))) % 103 != check:
        return None

    code = {103: "A", 104: "B", 105: "C"}[start]
    out = []
    for v in data:
        if code == "C":
            if v < 100:
                out.append(f"{v:02d}")
            elif v == 100:
                code = "B"
            elif v == 101:
                code = "A"
            # 102 = FNC1, ignored
        else:
            if v == 99:
                code = "C"
            elif v == 100 and code == "A":
                code = "B"
            elif v == 101 and code == "B":
                code = "A"
            elif v >= 96:
                pass                       # FNC / shift codes
            else:
                out.append(chr(v + 32 if code == "B" or v < 64 else v - 64))
    return "".join(out)