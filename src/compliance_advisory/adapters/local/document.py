"""Local document parser — the ``local`` profile's stand-in for Document AI.

SDK-free, deterministic plain-text extraction. If ``pypdf`` is importable and the bytes
look like a PDF, each PDF page becomes one page of text; otherwise the bytes are decoded
as UTF-8 text and returned as a single page. There is no Google emulator for Document
AI, so this path is unconditional and imports no google-cloud package.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...config import Settings
from ...domain.models import FetchedDocument
from ...pipelines import textract


@dataclass(frozen=True, slots=True)
class DocumentExtract:
    """Plain-text extraction of a fetched document, split into pages."""

    text: str
    pages: tuple[str, ...]
    mime_type: str


class LocalDocumentParser:
    """Parse a :class:`FetchedDocument` into page-level plain text, no SDK required."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse(self, document: FetchedDocument) -> DocumentExtract:
        content = document.content
        if textract.looks_like_pdf(content, document.mime_type):
            pages = textract.extract_pdf_pages(content)
            if pages:
                return DocumentExtract(
                    text="\n\n".join(pages),
                    pages=tuple(pages),
                    mime_type="application/pdf",
                )
        # Text passthrough. The corpus pipeline flattens PDFs to PAGE_BREAK-joined text
        # BEFORE redaction, so a page-broken body must split back into its real pages
        # here or every citation would say p.1.
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
        split = textract.split_pages(text)
        return DocumentExtract(text=text, pages=split, mime_type=document.mime_type or "text/plain")
