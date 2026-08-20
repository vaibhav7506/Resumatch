"""
PDF text extraction — carried over from ResumeAnalyzer v1's approach:
pdfplumber first, OCR fallback via pdf2image + pytesseract for scanned PDFs.

v1 difference: this always ran OCR as part of the flow. Here, OCR only
kicks in when pdfplumber's extraction looks too sparse to be a real
text-based PDF (below OCR_FALLBACK_THRESHOLD_CHARS per page) — most
resumes are text-based PDFs, so this skips the slow OCR path in the
common case while still handling scanned resumes correctly.
"""

from __future__ import annotations

import io

import pdfplumber

from app.core.logging import get_logger

logger = get_logger(__name__)

OCR_FALLBACK_THRESHOLD_CHARS = 40  # per page; below this, assume it's scanned


def extract_text_pdfplumber(pdf_bytes: bytes) -> str:
    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts)


def extract_text_ocr(pdf_bytes: bytes) -> str:
    """OCR fallback for scanned/image-based PDFs. Requires system deps:
    tesseract-ocr and poppler-utils (see README setup)."""
    from pdf2image import convert_from_bytes
    import pytesseract

    images = convert_from_bytes(pdf_bytes)
    text_parts = [pytesseract.image_to_string(img) for img in images]
    return "\n".join(text_parts)


def extract_text(pdf_bytes: bytes) -> tuple[str, str]:
    """Returns (text, method_used). Tries pdfplumber first; falls back to
    OCR only if the extracted text looks too sparse to be real content."""
    text = extract_text_pdfplumber(pdf_bytes)

    # crude but effective: average chars per page too low = likely scanned
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_count = max(len(pdf.pages), 1)

    if len(text.strip()) / page_count < OCR_FALLBACK_THRESHOLD_CHARS:
        logger.info("pdfplumber_extraction_sparse falling_back_to_ocr")
        text = extract_text_ocr(pdf_bytes)
        return text, "ocr"

    return text, "pdfplumber"
