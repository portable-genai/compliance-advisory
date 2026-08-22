"""Page-level plain-text extraction shared by the pipeline and the local parser.

One place answers "turn these fetched bytes into per-page text". The corpus pipeline
uses it BEFORE redaction (so DLP scans real text, never a binary stream), and the local
document parser delegates to it so an uploaded PDF and a fetched PDF read identically.

Pages travel between pipeline stages as a single string joined with :data:`PAGE_BREAK`
(form feed, the character printers used for exactly this). That keeps the
``FetchedDocument.content: bytes`` contract intact across redaction while preserving
the page boundaries that page-level citations require.

``pypdf`` is an optional import: without it a PDF yields no pages and callers fall back
to treating the bytes as one page of text, mirroring the previous parser behaviour.
"""

from __future__ import annotations

PAGE_BREAK = "\f"

#: MIME recorded on a document whose content has been flattened to page-broken text.
PAGED_TEXT_MIME = "text/plain"


def looks_like_pdf(content: bytes, mime_type: str = "") -> bool:
    if "pdf" in (mime_type or "").lower():
        return True
    return isinstance(content, bytes) and content[:5] == b"%PDF-"


def extract_pdf_pages(content: bytes) -> list[str]:
    """Per-page text via pypdf; empty list when pypdf is missing or the PDF is bad."""
    try:
        import io

        from pypdf import PdfReader  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - pypdf is optional; caller falls back
        return []
    try:
        reader = PdfReader(io.BytesIO(content))
        return [(page.extract_text() or "") for page in reader.pages]
    except Exception:  # noqa: BLE001 - a malformed PDF falls back to text decode
        return []


def to_paged_text(content: bytes, mime_type: str = "") -> str:
    """Flatten fetched bytes into one PAGE_BREAK-joined plain-text string.

    PDFs become one page per PDF page. Anything else decodes as UTF-8 into a single
    page. The result is what redaction scans and what the ingestion adapters split
    back into cited pages.
    """
    if looks_like_pdf(content, mime_type):
        pages = extract_pdf_pages(content)
        if pages:
            return PAGE_BREAK.join(pages)
    return content.decode("utf-8", errors="replace")


def split_pages(text: str) -> tuple[str, ...]:
    """Split a PAGE_BREAK-joined string back into its pages."""
    return tuple(text.split(PAGE_BREAK))
