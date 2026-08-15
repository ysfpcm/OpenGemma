"""PDF text extraction tool — extract text from PDF files via pdfplumber."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DEFAULT_MAX_CHARS = 50_000


def _parse_pages(pages_str: str, total_pages: int) -> List[int]:
    """Parse a page range string into zero-indexed page numbers.

    Supports formats like ``"1-5"`` (range) and ``"1,3,5"`` (list).
    Page numbers in the input are 1-indexed; the returned list is 0-indexed.

    Parameters
    ----------
    pages_str:
        Page specification string.
    total_pages:
        Total number of pages in the PDF.

    Returns
    -------
    List[int]
        Sorted list of zero-indexed page numbers.
    """
    result: list[int] = []
    for part in pages_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start = max(1, int(start_str.strip()))
            end = min(total_pages, int(end_str.strip()))
            result.extend(range(start - 1, end))
        else:
            page_num = int(part)
            if 1 <= page_num <= total_pages:
                result.append(page_num - 1)
    return sorted(set(result))


@ToolRegistry.register("pdf_extract")
class PDFExtractTool(BaseTool):
    """Extract text content from PDF files using pdfplumber."""

    tool_id = "pdf_extract"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="pdf_extract",
            description=(
                "Extract text from a PDF file (either local file_path or remote URL). Returns the extracted text content."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the local PDF file.",
                    },
                    "url": {
                        "type": "string",
                        "description": "URL to the remote PDF file to download and extract directly.",
                    },
                    "pages": {
                        "type": "string",
                        "description": (
                            "Page range to extract, e.g. '1-5' or '1,3,5'."
                            " Omit to extract all pages."
                        ),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": ("Maximum characters to return. Default 50000."),
                    },
                },
                "required": [],
            },
            category="media",
            required_capabilities=["file:read", "network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        file_path = params.get("file_path", "")
        url = params.get("url", "")
        if not file_path and not url:
            return ToolResult(
                tool_name="pdf_extract",
                content="Either 'file_path' or 'url' must be provided.",
                success=False,
            )

        try:
            import pdfplumber
        except ImportError:
            return ToolResult(
                tool_name="pdf_extract",
                content=(
                    "pdfplumber package not installed."
                    " Install with: pip install pdfplumber"
                ),
                success=False,
            )

        max_chars = params.get("max_chars", _DEFAULT_MAX_CHARS)
        pages_param = params.get("pages")

        if url:
            from openjarvis.security.ssrf import check_ssrf
            ssrf_error = check_ssrf(url)
            if ssrf_error:
                return ToolResult(
                    tool_name="pdf_extract",
                    content=f"SSRF protection blocked request: {ssrf_error}",
                    success=False,
                )

            import httpx
            import io
            try:
                response = httpx.get(url, timeout=60.0)
                response.raise_for_status()
                pdf_bytes = response.content
            except Exception as e:
                return ToolResult(
                    tool_name="pdf_extract",
                    content=f"Failed to download PDF from URL {url}: {e}",
                    success=False,
                )

            try:
                with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                    total_pages = len(pdf.pages)
                    if pages_param:
                        page_indices = _parse_pages(pages_param, total_pages)
                    else:
                        page_indices = list(range(total_pages))

                    text_parts: list[str] = []
                    for idx in page_indices:
                        if 0 <= idx < total_pages:
                            page_text = pdf.pages[idx].extract_text() or ""
                            text_parts.append(page_text)

                    text = "\n\n".join(text_parts)
                    if len(text) > max_chars:
                        text = text[:max_chars] + "\n\n[Content truncated]"

                    return ToolResult(
                        tool_name="pdf_extract",
                        content=text or "No text content found in PDF.",
                        success=True,
                        metadata={
                            "url": url,
                            "total_pages": total_pages,
                            "pages_extracted": len(page_indices),
                        },
                    )
            except Exception as exc:
                return ToolResult(
                    tool_name="pdf_extract",
                    content=f"PDF extraction error: {exc}",
                    success=False,
                )

        # Local file path extraction
        path = Path(file_path)

        # Validate extension
        if path.suffix.lower() != ".pdf":
            return ToolResult(
                tool_name="pdf_extract",
                content=f"Not a PDF file: {file_path}",
                success=False,
            )

        # Check sensitive file policy
        from openjarvis.security.file_policy import is_sensitive_file

        if is_sensitive_file(path):
            return ToolResult(
                tool_name="pdf_extract",
                content=f"Access denied: {file_path} is a sensitive file.",
                success=False,
            )

        if not path.exists():
            return ToolResult(
                tool_name="pdf_extract",
                content=f"File not found: {file_path}",
                success=False,
            )

        try:
            with pdfplumber.open(str(path)) as pdf:
                total_pages = len(pdf.pages)

                if pages_param:
                    page_indices = _parse_pages(pages_param, total_pages)
                else:
                    page_indices = list(range(total_pages))

                text_parts = []
                for idx in page_indices:
                    if 0 <= idx < total_pages:
                        page_text = pdf.pages[idx].extract_text() or ""
                        text_parts.append(page_text)

                text = "\n\n".join(text_parts)
                if len(text) > max_chars:
                    text = text[:max_chars] + "\n\n[Content truncated]"

                return ToolResult(
                    tool_name="pdf_extract",
                    content=text or "No text content found in PDF.",
                    success=True,
                    metadata={
                        "file_path": str(path.resolve()),
                        "total_pages": total_pages,
                        "pages_extracted": len(page_indices),
                    },
                )
        except Exception as exc:
            return ToolResult(
                tool_name="pdf_extract",
                content=f"PDF extraction error: {exc}",
                success=False,
            )


__all__ = ["PDFExtractTool"]
