"""Barcode validation rules for supported folder detail types."""
import re

_RO_VALUE_PATTERN = re.compile(r"^(?!^.{17}$)\d{5,}$")
_DEAL_VALUE_PATTERN = re.compile(
    r"^[schoSCHO]+-[A-Za-z0-9]+(-[A-Za-z0-9]+)"
)
_DEAL_NUMBER_PATTERN = re.compile(r"[+-]?[0-9]+")
_INT32_MAX_TEXT = str(2**31 - 1)
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


def normalize_folder_detail_type(value: str) -> str:
    """Return the canonical folder detail type or reject unsupported values."""
    normalized = value.strip().casefold()
    if normalized == "ro":
        return "RO"
    if normalized == "deal":
        return "Deal"
    raise ValueError("FolderDetailType must be RO or Deal")


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
    if len(details) == 4 and details[2].casefold() == "d":
        number_text = details[3].strip()
        if _DEAL_NUMBER_PATTERN.fullmatch(number_text) is None:
            return False
        try:
            deal_number = int(number_text)
        except ValueError:
            return False
        if not _INT32_MIN <= deal_number <= _INT32_MAX:
            return False
    return True
