"""PDF text extraction for material ingestion (P2 #9) via PyMuPDF (``fitz``).

Kept tiny and dependency-isolated: ``fitz`` is imported lazily so importing this module (or the
ingest-disabled path) never loads PyMuPDF.
"""
import logging
from typing import Union

logger = logging.getLogger(__name__)


def extract_pages_from_pdf(data: Union[bytes, str]) -> list[tuple[int, str]]:
    """Extract plain text from a PDF given raw ``bytes`` or a file path.

    Returns (one-based page number, text) pairs, retaining empty page positions.
    Raises ``ValueError`` if the PDF cannot be opened or has no extractable text.
    """
    import fitz  # lazy: PyMuPDF only when ingesting

    try:
        if isinstance(data, bytes):
            doc = fitz.open(stream=data, filetype="pdf")
        else:
            doc = fitz.open(data)
    except Exception as e:
        raise ValueError(f"could not open PDF: {e}") from e

    try:
        pages = [(index + 1, page.get_text("text").strip()) for index, page in enumerate(doc)]
    finally:
        doc.close()

    if not any(text for _, text in pages):
        raise ValueError("PDF contained no extractable text (scanned image? needs OCR)")
    return pages


def extract_text_from_pdf(data: Union[bytes, str]) -> str:
    """Compatibility adapter; ingestion uses pages to preserve real page numbers."""
    return "\n\n".join(text for _, text in extract_pages_from_pdf(data) if text)
