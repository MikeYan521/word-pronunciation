"""PDF text extraction.

Primary engine is ``pdfplumber`` (layout aware, gives per-line font sizes which
the chapter detector needs).  ``pypdf`` is used for the document outline
(bookmarks) and ``pypdfium2`` for fast page rendering.

Scanned PDFs (no text layer) are detected page by page and, when an OCR engine
is available (Windows built-in OCR or Tesseract - see :mod:`app.ocr`), the image
pages are rendered in parallel and OCR-ed.  OCR is never required for the app to
run; without an engine the user gets an explicit message instead of a silently
empty word list.
"""

from __future__ import annotations

import statistics
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config
from . import ocr as ocr_module
from .textutil import clean_block, clean_line, dehyphenate


@dataclass
class Line:
    text: str
    size: float
    top: float


@dataclass
class Page:
    number: int                 # 1-based physical page index
    lines: list[Line] = field(default_factory=list)
    text: str = ""
    char_count: int = 0
    body_size: float = 0.0      # median font size, used as heading baseline
    has_text: bool = True
    ocr_used: bool = False
    printed_page: int | None = None   # page number printed on the page itself


@dataclass
class ParsedPdf:
    path: str
    page_count: int
    pages: list[Page]
    outline: list[dict[str, Any]]
    scanned: bool
    text_ratio: float
    engine: str = "pdfplumber"
    warnings: list[str] = field(default_factory=list)
    ocr_page_count: int = 0          # pages that needed and received OCR
    pre_ocr_text_ratio: float = 1.0  # text-layer coverage before OCR
    page_offset: int = 0             # printed page number -> physical index


def ocr_status() -> dict[str, Any]:
    """Report whether OCR can be used on this machine."""
    return ocr_module.ocr_status_for_api()


def _read_outline(path: Path) -> list[dict[str, Any]]:
    """Flatten the PDF bookmark tree into [{title, page}] entries."""
    try:
        from pypdf import PdfReader
    except Exception:
        return []
    try:
        reader = PdfReader(str(path))
        raw = reader.outline
    except Exception:
        return []
    out: list[dict[str, Any]] = []

    def walk(items: list[Any], depth: int = 0) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = clean_line(getattr(item, "title", "") or "")
            if not title:
                continue
            page_no = None
            try:
                page_no = reader.get_destination_page_number(item) + 1
            except Exception:
                page_no = None
            out.append({"title": title, "page": page_no, "depth": depth})

    walk(raw)
    return out


class _ThreadDocuments:
    """One pypdfium2 document per worker thread (pdfium handles are not
    shareable across threads)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.local = threading.local()

    def get(self):
        import pypdfium2 as pdfium

        doc = getattr(self.local, "doc", None)
        if doc is None:
            doc = pdfium.PdfDocument(self.path)
            self.local.doc = doc
        return doc


def _render_page_png(documents: _ThreadDocuments, index: int, dpi: int) -> bytes:
    import io
    import pypdfium2 as pdfium  # noqa: F401  (import check)

    doc = documents.get()
    page = doc[index]
    bitmap = page.render(scale=dpi / 72.0)
    image = bitmap.to_pil().convert("L")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _ocr_pages(
    path: str,
    page_numbers: list[int],
    dpi: int,
    workers: int,
    progress: Any = None,
    language: str = "en",
) -> dict[int, str]:
    """OCR the given 0-based page indexes; returns {index: text}.

    Windows' OCR engine occasionally returns nothing when it is driven from
    several threads at once, so empty results are retried serially before being
    accepted.  That keeps multi-hundred-page books fast without losing pages.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not page_numbers:
        return {}
    documents = _ThreadDocuments(path)
    results: dict[int, str] = {}
    done = 0
    total = len(page_numbers)

    def render_and_ocr(index: int) -> str:
        png = _render_page_png(documents, index, dpi)
        return ocr_module.recognize(png, language=language)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for index, text in pool.map(lambda i: (i, render_and_ocr(i)), page_numbers):
            results[index] = text
            done += 1
            if progress:
                progress("ocr", done, total, f"OCR 识别中（{done}/{total} 页）")

    empty = [index for index, text in results.items() if not text.strip()]
    if empty and workers > 1:
        # Serial retry (fresh render each time).
        retried = 0
        for index in empty:
            retried += 1
            for attempt in range(2):
                text = render_and_ocr(index)
                if text.strip():
                    results[index] = text
                    break
            if progress:
                progress("ocr_retry", retried, len(empty),
                         f"重试未识别的页面（{retried}/{len(empty)}）")
    return results


def parse_pdf(
    path: str | Path,
    use_ocr: bool = False,
    max_pages: int | None = None,
    progress: Any = None,
    ocr_dpi: int = 300,
    ocr_workers: int = 4,
    ocr_language: str = "en",
) -> ParsedPdf:
    """Extract per-page lines and text from *path*."""
    path = Path(path)
    warnings: list[str] = []

    try:
        import pdfplumber
    except Exception as exc:  # pragma: no cover - environment issue
        raise RuntimeError(
            "需要 pdfplumber 才能解析 PDF（pip install pdfplumber）"
        ) from exc

    pages: list[Page] = []
    outline = _read_outline(path)

    ocr = ocr_status()
    if use_ocr and not ocr["available"]:
        warnings.append(f"OCR 不可用：{ocr['reason']}")
    elif use_ocr:
        warnings.append(f"已启用 OCR（引擎：{ocr['engine']}，语言：{', '.join(ocr['languages']) or '默认'}）")

    with pdfplumber.open(str(path)) as pdf:
        total = len(pdf.pages)
        limit = min(total, max_pages) if max_pages else total
        for index in range(limit):
            raw_page = pdf.pages[index]
            page = Page(number=index + 1)
            try:
                raw_lines = raw_page.extract_text_lines(layout=False, strip=True) or []
            except Exception:
                raw_lines = []

            for item in raw_lines:
                text = clean_line(item.get("text", ""))
                if not text:
                    continue
                sizes = [c.get("size", 0) for c in (item.get("chars") or []) if c.get("size")]
                size = statistics.median(sizes) if sizes else 0.0
                page.lines.append(Line(text=text, size=float(size), top=float(item.get("top", 0) or 0)))

            page.text = dehyphenate("\n".join(line.text for line in page.lines))
            page.char_count = len(page.text)
            page.has_text = page.char_count >= config.SCANNED_PAGE_CHAR_THRESHOLD
            page.printed_page = _detect_printed_page(page.lines)
            if page.lines:
                body = [line.size for line in page.lines if line.size > 0]
                page.body_size = statistics.median(body) if body else 0.0

            pages.append(page)
            if progress:
                progress("text", index + 1, limit, f"提取文本层（{index + 1}/{limit} 页）")

    # Ratio *before* OCR: this is what tells the user whether the file is a scan.
    pre_ocr_text_pages = sum(1 for p in pages if p.char_count >= config.SCANNED_PAGE_CHAR_THRESHOLD)

    # ---- OCR pass for pages without a text layer -------------------------
    pending = [p for p in pages if not p.has_text]
    if use_ocr and pending and ocr["available"]:
        indexes = [p.number - 1 for p in pending]
        try:
            texts = _ocr_pages(str(path), indexes, ocr_dpi, ocr_workers, progress, ocr_language)
        except Exception as exc:
            warnings.append(f"OCR 失败：{exc}")
            texts = {}
        by_number = {p.number: p for p in pages}
        for index, text in texts.items():
            page = by_number.get(index + 1)
            if page is None:
                continue
            cleaned = clean_block(text)
            page.ocr_used = True
            page.text = text
            page.char_count = len(text)
            page.has_text = page.char_count >= config.SCANNED_PAGE_CHAR_THRESHOLD
            page.lines = [
                Line(text=line.strip(), size=0.0, top=0.0)
                for line in cleaned.splitlines() if line.strip()
            ]
            page.printed_page = _detect_printed_page(page.lines)
            if not cleaned:
                warnings.append(f"第 {page.number} 页 OCR 未识别出文字")

    page_count = len(pages)
    text_pages = sum(1 for p in pages if p.has_text)
    ratio_before = (pre_ocr_text_pages / page_count) if page_count else 0.0
    ratio_after = (text_pages / page_count) if page_count else 0.0
    scanned = page_count > 0 and ratio_before < (1 - config.SCANNED_PDF_RATIO)
    if scanned and not use_ocr:
        warnings.append("检测到扫描版 PDF（缺少文本层），可勾选“尝试 OCR”后重新解析。")
    elif pending and not ocr["available"]:
        warnings.append(
            "检测到扫描版 PDF 且本机没有可用 OCR 引擎："
            "① pip install winsdk（Windows 内置 OCR，推荐）；"
            "② winget install UB-Mannheim.TesseractOCR + pip install pytesseract pillow。"
        )

    return ParsedPdf(
        path=str(path),
        page_count=page_count,
        pages=pages,
        outline=outline,
        scanned=scanned,
        text_ratio=round(ratio_after, 3),
        ocr_page_count=sum(1 for p in pages if p.ocr_used),
        pre_ocr_text_ratio=round(ratio_before, 3),
        page_offset=_estimate_offset(pages),
        warnings=warnings,
    )


def _detect_printed_page(lines: list[Line]) -> int | None:
    """The page number printed in the header/footer of a page, if any."""
    candidates = [line.text.strip() for line in lines[:3]]
    candidates += [line.text.strip() for line in lines[-3:]]
    for text in candidates:
        if len(text) > 6:
            continue
        match = fullmatch_page_number(text)
        if match is not None:
            return match
    return None


def fullmatch_page_number(text: str) -> int | None:
    import re

    match = re.fullmatch(r"[-–—\s]*(\d{1,4})[-–—\s]*", text)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 2000 else None


def _estimate_offset(pages: list[Page]) -> int:
    """Most common difference between physical page index and printed number."""
    from collections import Counter

    offsets: Counter[int] = Counter()
    for page in pages:
        if page.printed_page:
            offsets[page.number - page.printed_page] += 1
    if not offsets:
        return 0
    offset, count = offsets.most_common(1)[0]
    return offset if count >= max(3, int(len(pages) * 0.15)) else 0
