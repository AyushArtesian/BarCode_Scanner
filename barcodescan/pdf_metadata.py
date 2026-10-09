"""Write barcode scan results into PDF document information metadata."""
from pathlib import Path

from pypdf import PdfWriter


def format_barcode_metadata(result: dict) -> str:
    """Format detected values as one page-number/value entry per line."""
    entries = [
        f"Page {barcode['page']}: {barcode['value']}"
        for barcode in result["barcodes"]
    ]
    return "\n".join(entries) if entries else "No barcodes found"


def write_barcode_metadata(source: Path, destination: Path, result: dict) -> None:
    """Copy a PDF and update its custom BarcodeResults metadata field."""
    writer = PdfWriter(clone_from=Path(source))
    writer.add_metadata({"/BarcodeResults": format_barcode_metadata(result)})
    writer.write(Path(destination))
