"""Barcode validation rules for supported folder detail types."""
import re

_RO_VALUE_PATTERN = re.compile(r"^(?!^.{17}$)\d{5,}$")
_DEAL_VALUE_PATTERN = re.compile(
    r"^[schoSCHO]+-[A-Za-z0-9]+(-[A-Za-z0-9]+)"
)
_INT32_PATTERN = re.compile(r"[+-]?[0-9]+", re.ASCII)
_INT32_MAX_TEXT = str(2**31 - 1)
_INT32_MIN_MAGNITUDE_TEXT = str(2**31)


def normalize_folder_detail_type(value: str) -> str:
    """Return the canonical folder detail type or reject unsupported values."""
    normalized = value.strip().casefold()
    if normalized == "ro":
        return "RO"
    if normalized == "deal":
        return "Deal"
    raise ValueError("FolderDetailType must be RO or Deal")


def _is_int32(value: str) -> bool:
    stripped = value.strip()
    if _INT32_PATTERN.fullmatch(stripped) is None:
        return False

    negative = stripped.startswith("-")
    digits = stripped.lstrip("+-").lstrip("0") or "0"
    limit = _INT32_MIN_MAGNITUDE_TEXT if negative else _INT32_MAX_TEXT
    return len(digits) < len(limit) or (
        len(digits) == len(limit) and digits <= limit
    )


def is_valid_barcode(value: object, folder_detail_type: str) -> bool:
    """Validate a decoded barcode using the selected folder's rules."""
    if value is None:
        return False
    text = str(value)
    if not text.strip():
        return False

    folder_type = normalize_folder_detail_type(folder_detail_type)
    if folder_type == "RO":
        match = _RO_VALUE_PATTERN.match(text)
        if match is None:
            return False
        digits = match.group()
        if not digits.isascii():
            return False
        significant_digits = digits.lstrip("0") or "0"
        return (
            len(significant_digits) < len(_INT32_MAX_TEXT)
            or (
                len(significant_digits) == len(_INT32_MAX_TEXT)
                and significant_digits <= _INT32_MAX_TEXT
            )
        )
    if _DEAL_VALUE_PATTERN.match(text) is None:
        return False

    details = text.split("-")
    return not (
        len(details) == 4
        and details[2].casefold() == "d"
        and not _is_int32(details[3])
    )


def deal_record_key(value: str) -> str:
    """Extract the record key using the .NET DealFileProcessor split rules."""
    details = value.split("-")
    if len(details) > 3:
        code = details[2].casefold()
        if code == "v":
            return details[3]
        if code in {"d", "dd"}:
            return "-" + details[4] if len(details) > 4 else details[3]
    return details[2]
