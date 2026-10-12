"""Chapter structure detection and running-text cleanup.

Strategy, in order of reliability:

1. PDF bookmarks / outline (most reliable).
2. Heading lines detected per page, using pattern matching plus a font-size
   signal (a line noticeably larger than the page body is a heading).
3. Fixed-size page windows as a last resort, flagged so the UI can ask the user
   to split the book manually.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import config
from .pdf_parser import Page, ParsedPdf
from .textutil import BACK_MATTER_RE, TOC_LINE_RE, clean_line

CHAPTER_RE = re.compile(
    r"^\s*(?:chapter|unit|lesson|module|section|part|"
    r"第\s*[0-9一二三四五六七八九十]+\s*[章单元课])\s*"
    r"([0-9]{1,3}|[ivxlcdm]{1,6}|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|一|二|三|四|五|六|七|八|九|十)?\b[\s:.\-—]*",
    re.IGNORECASE,
)

NUMBERED_HEADING_RE = re.compile(r"^\s*(\d{1,2})(?:\.(\d{1,2}))?\s+([A-Z][^.]{2,60})$")
DOT_LEADER_RE = re.compile(r"\.{2,}\s*\d+\s*$")
# Part-of-speech markers, used to tell a real subtitle from entry text.
POS_RE = re.compile(r"\b(n|v|vt|vi|adj|adv|prep|conj|pron|num|art|interj|abbr)\.", re.IGNORECASE)


@dataclass
class DetectedChapter:
    title: str
    start_page: int
    end_page: int
    source: str = "heading"
    subsections: list[str] = field(default_factory=list)
    number: int | None = None       # the Chapter/Unit number when known
    # Confidence is NEVER a default: it starts as "unknown" and is assigned by
    # evaluate_chapters() from actual evidence.  "manual" means the user fixed
    # the page range themselves.
    confidence: str = "unknown"     # unknown | high | medium | low | manual
    evidence: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)   # content-level warnings

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "source": self.source,
            "subsections": self.subsections,
            "number": self.number,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "alerts": self.alerts,
        }


CONFIDENCE_LEVELS = ("unknown", "high", "medium", "low", "manual")


def note(chapter: DetectedChapter, text: str) -> None:
    """Append an evidence note once (keeps re-evaluation idempotent)."""
    if text and text not in chapter.evidence:
        chapter.evidence.append(text)


def alert(chapter: DetectedChapter, text: str) -> None:
    if text and text not in chapter.alerts:
        chapter.alerts.append(text)


def has_note(chapter: DetectedChapter, prefix: str) -> bool:
    return any(note.startswith(prefix) for note in chapter.evidence)


CHAPTER_LABEL_RE = re.compile(r"^\s*chapter\s*(\d{1,3})\b", re.IGNORECASE)


def chapter_markers(
    parsed: ParsedPdf,
    toc_pages: set[int] | None = None,
) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """Pages carrying an explicit "Chapter N" marker.

    Returns ``(top_markers, bottom_markers)``: a marker at the top of a page is
    real chapter-start evidence, while one at the bottom is usually the running
    footer of a vocabulary page and must not open a new chapter.
    """
    top: dict[int, set[int]] = {}
    bottom: dict[int, set[int]] = {}
    TOP_ROWS = 8          # a marker in the top rows can open a chapter
    FOOTER_ROWS = 2       # ...while one in the last rows is a running footer
    for page in parsed.pages:
        # A contents page lists every chapter; its lines are not chapter-start
        # evidence (the printed page numbers there are handled separately).
        if toc_pages and page.number in toc_pages:
            continue
        lines = page.lines
        for index, line in enumerate(lines):
            # "last rows" only means something on a normal page; a short page
            # (a chapter opener, for instance) has no footer area.
            in_footer = len(lines) >= 5 and index >= len(lines) - FOOTER_ROWS
            in_header = index < TOP_ROWS
            if not in_footer and not in_header:
                continue
            match = CHAPTER_LABEL_RE.match(line.text.strip())
            if match:
                bucket = bottom if in_footer else top
                bucket.setdefault(page.number, set()).add(int(match.group(1)))
                continue
            # Scanned openers often break "Chapter" and "1" onto separate lines.
            if not in_footer and re.fullmatch(r"(?i)chapter", line.text.strip()):
                for following in lines[index + 1:index + 3]:
                    number = re.fullmatch(r"\s*(\d{1,3})\s*", following.text)
                    if number:
                        top.setdefault(page.number, set()).add(int(number.group(1)))
                        break
    return top, bottom


def collect_evidence(
    parsed: ParsedPdf,
    toc_pages: set[int] | None = None,
    toc_entries: dict[int, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Collect every piece of chapter evidence available in the document."""
    toc_pages = toc_pages or set()
    top, bottom = chapter_markers(parsed, toc_pages)
    return {
        "toc_pages": sorted(toc_pages),
        "toc": toc_entries or {},
        "top": top,
        "bottom": bottom,
    }


def evaluate_chapters(
    parsed: ParsedPdf,
    chapters: list[DetectedChapter],
    evidence: dict[str, Any],
    back_start: int | None = None,
    allow_snap: bool = True,
    allow_additions: bool = True,
) -> list[DetectedChapter]:
    """THE single place where chapter confidence is assigned.

    Every creation path (bookmarks, contents page, heading detection, fallback
    split, newly found markers) funnels through here, and so does the
    re-evaluation done when the user confirms or edits chapters, so no chapter
    can reach the UI with an unexamined default.

    Confidence rules:

    ``high``   the chapter's own start page carries a matching ``Chapter N``
               marker in the body (not the contents page, not a running footer),
               the numbering is monotonic and the range is sane;
    ``medium`` boundary derived from the contents page + printed page numbers, a
               footer marker inside the range, or a boundary snapped onto a
               nearby marker - i.e. plausible but not page-level confirmed;
    ``low``    no usable evidence, contradictory evidence, or an abnormal range;
    ``unknown``not evaluated yet (should never reach the UI);
    ``manual`` the user set the page range explicitly.
    """
    if not chapters:
        return chapters

    top: dict[int, set[int]] = evidence.get("top", {})
    bottom: dict[int, set[int]] = evidence.get("bottom", {})
    toc: dict[int, dict[str, int]] = evidence.get("toc", {})
    last_page = (back_start - 1) if back_start else parsed.page_count

    # ---- normalise numbers and drop stale conclusions ---------------------
    for chapter in chapters:
        if chapter.number is None:
            match = CHAPTER_LABEL_RE.match(chapter.title or "")
            if match:
                chapter.number = int(match.group(1))
        if chapter.confidence not in CONFIDENCE_LEVELS:
            chapter.confidence = "unknown"

    # ---- 1. resolve conflicts between the contents page and the body ------
    if allow_snap:
        for chapter in chapters:
            if chapter.number is None or chapter.confidence == "manual":
                continue
            body_pages = sorted(page for page, numbers in top.items()
                                if chapter.number in numbers)
            if not body_pages:
                continue
            if chapter.start_page in body_pages:
                continue
            nearby = [page for page in body_pages if page >= chapter.start_page - 4]
            target = min(nearby or body_pages,
                         key=lambda page: (abs(page - chapter.start_page), page))
            delta = target - chapter.start_page
            chapter.start_page = target
            if abs(delta) <= 4:
                note(chapter, f"边界由推算的 p{target - delta} 校正到 p{target}（页首 Chapter {chapter.number}）")
            else:
                note(chapter, f"目录/页码推算与正文标识相差 {abs(delta)} 页，"
                              f"已按正文 p{target} 的 Chapter {chapter.number} 为准")

    # ---- 2. markers that no chapter accounts for become new chapters ------
    covered = {chapter.number for chapter in chapters if chapter.number is not None}
    claimed: set[int] = set()
    for chapter in chapters:
        claimed.update(range(chapter.start_page, chapter.end_page + 1))
    additions: list[DetectedChapter] = []
    if allow_additions:
        for page in sorted(top):
            for number in sorted(top[page]):
                if number in covered or page in claimed:
                    continue
                additions.append(DetectedChapter(
                    title=f"Chapter {number}", start_page=page, end_page=page,
                    source="marker", number=number, confidence="unknown",
                    evidence=[f"p{page} 页首发现目录未记录的 Chapter {number}"],
                ))
                covered.add(number)
    if additions:
        chapters.extend(additions)

    # ---- 3. contiguous, monotonic ranges ---------------------------------
    chapters.sort(key=lambda c: (c.start_page, c.number or 0))
    for index, chapter in enumerate(chapters):
        chapter.start_page = max(1, min(chapter.start_page, parsed.page_count))
        end = (chapters[index + 1].start_page - 1) if index + 1 < len(chapters) else last_page
        chapter.end_page = max(chapter.start_page, min(end, last_page))

    numbers = [chapter.number for chapter in chapters if chapter.number is not None]
    monotonic = all(a < b for a, b in zip(numbers, numbers[1:]))
    if not monotonic:
        for chapter in chapters:
            if chapter.number is not None:
                alert(chapter, "章节编号顺序与页码顺序不一致，请确认")

    # ---- 4. assign a confidence justified by evidence --------------------
    for chapter in chapters:
        if chapter.confidence == "manual":
            continue
        number = chapter.number
        start_has_top = number is not None and number in top.get(chapter.start_page, set())
        # a page that carries several chapter markers cannot confirm a boundary
        start_top_markers = top.get(chapter.start_page, set())
        ambiguous_start = len(start_top_markers) > 1
        has_footer_marker = any(number is not None and number in bottom.get(page, set())
                                for page in range(chapter.start_page, chapter.end_page + 1))
        toc_entry = toc.get(number) if number is not None else None
        span = chapter.end_page - chapter.start_page + 1

        if start_has_top and not ambiguous_start and monotonic:
            chapter.confidence = "high"
            note(chapter, f"p{chapter.start_page} 页首有正文标识 Chapter {number}")
            if toc_entry and not has_note(chapter, "目录页"):
                note(chapter, f"目录页 p{toc_entry['toc_page']} 记录其起始印刷页 "
                              f"{toc_entry['printed']}（换算物理页 {toc_entry['start_page']}）")
        elif start_has_top and (ambiguous_start or not monotonic):
            chapter.confidence = "low"
            note(chapter, f"p{chapter.start_page} 页首出现多个章节标识 "
                          f"{sorted(start_top_markers)}，无法确认边界")
        elif has_footer_marker:
            chapter.confidence = "medium"
            note(chapter, f"章节内页脚出现 Chapter {number} 标识（首页缺正文标识）")
            if toc_entry and not has_note(chapter, "目录页"):
                note(chapter, f"目录页 p{toc_entry['toc_page']} 记录起始印刷页 {toc_entry['printed']}")
        elif toc_entry:
            chapter.confidence = "medium"
            if not has_note(chapter, "目录页"):
                note(chapter, f"边界来自目录页 p{toc_entry['toc_page']} 的 Chapter {number} "
                              f"（印刷页 {toc_entry['printed']} → 物理页 {toc_entry['start_page']}），"
                              f"正文未找到该标识")
            else:
                note(chapter, "正文首页与页脚均无该章标识，边界仅由目录页码推算")
        elif span <= 2:
            chapter.confidence = "low"
            note(chapter, f"页码范围仅 {span} 页且缺少章节标识")
        else:
            chapter.confidence = "low"
            note(chapter, "正文首页、页脚均无该章标识，且缺少目录页码，无法确认边界")

    return chapters


# --------------------------------------------------------------------------
# Running heads / footers, TOC pages, back matter
# --------------------------------------------------------------------------

def find_running_lines(pages: list[Page]) -> set[str]:
    """Lines that repeat on many pages are headers/footers, not content."""
    counter: Counter[str] = Counter()
    for page in pages:
        seen = {line.text for line in page.lines if len(line.text) <= 80}
        counter.update(seen)
    threshold = max(3, int(len(pages) * config.RUNNING_HEAD_MIN_RATIO))
    return {text for text, count in counter.items() if count >= threshold}


def is_page_number_line(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 24:
        return False
    if re.fullmatch(r"[-–—\s]*\d{1,4}[-–—\s]*", stripped):
        return True
    if re.fullmatch(r"(page\s*)?\d{1,4}\s*(of\s*\d{1,4})?", stripped, re.IGNORECASE):
        return True
    if re.fullmatch(r"[ivxlcdm]{1,8}", stripped, re.IGNORECASE) and len(stripped) <= 6:
        return True
    return False


def looks_like_toc_line(text: str) -> bool:
    return bool(DOT_LEADER_RE.search(text) or TOC_LINE_RE.search(text))


def detect_toc_pages(pages: list[Page]) -> set[int]:
    """Pages that are a table of contents (dot leaders / dense numbering)."""
    toc: set[int] = set()
    chapter_label_re = re.compile(r"^\s*chapter\s*\d{1,3}\s*$", re.IGNORECASE)
    for page in pages:
        if not page.lines:
            continue
        toc_lines = sum(1 for line in page.lines if looks_like_toc_line(line.text))
        toc_hit = any(
            re.match(r"^\s*(contents|table of contents|目\s*录)", line.text, re.IGNORECASE)
            for line in page.lines[:6]
        )
        # Scanned contents pages OCR without dot leaders, but keep one
        # "Chapter N" per line plus a column of printed page numbers.
        label_lines = sum(1 for line in page.lines if chapter_label_re.match(line.text))
        number_lines = sum(1 for line in page.lines if re.fullmatch(r"\d{1,4}", line.text.strip()))
        if toc_lines >= 4 or (toc_hit and toc_lines >= 2):
            toc.add(page.number)
        elif label_lines >= 4 and number_lines >= 3:
            toc.add(page.number)
        elif toc_hit and label_lines >= 3:
            # scanned contents page: a "Contents" heading plus chapter labels
            toc.add(page.number)
        elif label_lines >= 8:
            toc.add(page.number)
    # a TOC page numbered N usually continues on N+1
    extended = set(toc)
    for number in toc:
        if (number + 1) in toc:
            extended.add(number + 1)
    return extended


def forward_page_offset(pages: list[Page], outline: list[dict[str, Any]]) -> int:
    """Many PDFs number pages differently from the physical index.

    Uses the first outline entry to estimate the offset between printed page
    numbers and physical ones (best effort, 0 when unknown).
    """
    del pages
    del outline
    return 0


def estimate_page_offset(pages: list[Page]) -> int:
    """Offset between the printed page number and the physical page index.

    Scanned books print "003" on physical page 14, so a table of contents that
    lists printed page numbers can be mapped onto real pages.  The offset is the
    most common difference between a footer number and its physical page.
    """
    offsets: Counter[int] = Counter()
    for page in pages:
        candidates = [line.text.strip() for line in page.lines[:3]]
        candidates += [line.text.strip() for line in page.lines[-3:]]
        for text in candidates:
            match = re.fullmatch(r"(\d{1,4})", text)
            if not match:
                continue
            printed = int(match.group(1))
            if 1 <= printed <= 2000:
                offsets[page.number - printed] += 1
    if not offsets:
        return 0
    offset, count = offsets.most_common(1)[0]
    # only trust it when a meaningful number of pages agree
    return offset if count >= max(3, int(len(pages) * 0.15)) else 0


def _numbers_outside(text: str, spans: list[tuple[int, int]]) -> list[int]:
    out: list[int] = []
    for match in re.finditer(r"\d{1,4}", text):
        start, end = match.span()
        if any(start < span_end and end > span_start for span_start, span_end in spans):
            continue
        out.append(int(match.group(0)))
    return out


def toc_chapters(
    parsed: ParsedPdf,
    offset: int,
    back_start: int | None,
) -> tuple[list[DetectedChapter], dict[int, dict[str, int]]]:
    """Chapters from the printed table of contents.

    This is the most reliable strategy for scanned word books, whose chapter
    opener pages often OCR badly: the contents page lists every chapter and its
    printed start page, and the printed page numbers can be mapped onto the
    physical page index with :func:`estimate_page_offset`.
    """
    best: tuple[int, list[int], list[int], Page] | None = None
    for page in parsed.pages:
        text = "\n".join(line.text for line in page.lines)
        if len(text) > 20000:
            continue
        spans: list[tuple[int, int]] = []
        labels: list[int] = []
        for match in re.finditer(r"chapter\s*(\d{1,3})", text, re.IGNORECASE):
            labels.append(int(match.group(1)))
            spans.append(match.span())
        numbers = _numbers_outside(text, spans)
        if len(labels) < 3 or len(numbers) < len(labels):
            continue
        # keep only the plausible, strictly increasing prefix
        cleaned: list[int] = []
        for value in numbers:
            if not (1 <= value <= 2000):
                continue
            if cleaned and value <= cleaned[-1]:
                continue
            cleaned.append(value)
        if len(cleaned) < len(labels):
            continue
        if best is None or len(labels) > len(best[1]):
            best = (page.number, labels, cleaned, page)
    if best is None:
        return [], {}

    toc_page, labels, numbers, _page = best
    starts = [max(1, min(parsed.page_count, value + offset)) for value in numbers[:len(labels)]]
    extra_start = numbers[len(labels)] + offset if len(numbers) > len(labels) else None

    chapters: list[DetectedChapter] = []
    toc_entries: dict[int, dict[str, int]] = {}
    for index, (label, start) in enumerate(zip(labels, starts)):
        end = (starts[index + 1] - 1) if index + 1 < len(starts) else (
            (extra_start - 1) if extra_start else ((back_start - 1) if back_start else parsed.page_count)
        )
        end = min(max(end, start), parsed.page_count)
        printed = numbers[index]
        chapter = DetectedChapter(title=f"Chapter {label}", start_page=start,
                                  end_page=end, source="toc", number=label)
        note(chapter, f"目录页 p{toc_page} 记录 Chapter {label} 起始印刷页 {printed}"
                      f"（offset {offset} → 物理页 {start}）")
        toc_entries[label] = {"toc_page": toc_page, "printed": printed, "start_page": start}
        chapters.append(chapter)
    if extra_start and extra_start <= parsed.page_count:
        appendix = DetectedChapter(
            title="附录 / 索引", start_page=extra_start,
            end_page=(back_start - 1) if back_start else parsed.page_count, source="toc",
            number=None,
        )
        note(appendix, f"目录页 p{toc_page} 中位于各章之后的条目（印刷页 "
                       f"{numbers[len(labels)]} → 物理页 {extra_start}）")
        chapters.append(appendix)
    # sanity: chapters must not overlap and must cover increasing pages
    for index in range(1, len(chapters)):
        if chapters[index].start_page <= chapters[index - 1].start_page:
            return [], {}
    return chapters, toc_entries


def enrich_titles_from_openers(parsed: ParsedPdf, chapters: list[DetectedChapter]) -> None:
    """Give "Chapter N" a subtitle when the opener page has one."""
    clean_extra = re.compile(r"^[\dA-Za-z\u4e00-\u9fff \-']+$")
    by_page = {page.number: page for page in parsed.pages}
    for chapter in chapters:
        page = by_page.get(chapter.start_page)
        if page is None:
            continue
        for index, line in enumerate(page.lines[:8]):
            text = line.text.strip()
            if not CHAPTER_RE.match(text):
                continue
            title = _complete_title(page, index, text)
            extra = title[len(text):].strip()
            # Only accept a subtitle that is made of real words - OCR garbage
            # such as "k5€Eä)J" must not replace "Chapter 8".
            if title and len(title) > len(chapter.title) and extra and clean_extra.match(extra):
                chapter.title = title
            break


def back_matter_start(pages: list[Page]) -> int | None:
    for page in pages:
        for line in page.lines[:4]:
            text = line.text.strip()
            if BACK_MATTER_RE.match(text):
                # Ignore "Contents" that simply labels a TOC page
                if re.match(r"^\s*(contents|table of contents|目\s*录)", text, re.IGNORECASE):
                    continue
                return page.number
    return None


# --------------------------------------------------------------------------
# Heading detection
# --------------------------------------------------------------------------

def _line_is_heading(line_text: str, size: float, body_size: float) -> bool:
    text = line_text.strip()
    if not text or len(text) > 90:
        return False
    words = text.split()
    if len(words) > 12:
        return False
    if text.endswith((",", ";", ":")) and not CHAPTER_RE.match(text):
        return False
    big = body_size > 0 and size >= body_size * 1.18
    patterns = bool(CHAPTER_RE.match(text) or NUMBERED_HEADING_RE.match(text))
    if CHAPTER_RE.match(text):
        return True
    if patterns:
        return True
    if big and not text.endswith("."):
        return True
    # ALL CAPS short lines
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 4 and text.upper() == text and len(words) <= 8 and body_size > 0:
        return True
    return False


def _heading_rank(text: str) -> int:
    """Lower is more chapter-like."""
    if CHAPTER_RE.match(text):
        return 0
    if NUMBERED_HEADING_RE.match(text):
        return 1
    return 2


def _outline_chapters(parsed: ParsedPdf) -> list[DetectedChapter]:
    entries = [e for e in parsed.outline if e.get("page")]
    if len(entries) < 2:
        return []
    # Guard: some PDFs contain one bookmark per page ("..._页面_047", "Page 47").
    # Those carry no chapter meaning and would produce one chapter per page.
    page_like = sum(
        1 for e in entries
        if re.search(r"(页面|page)\s*[_\-\s]*\d+", str(e.get("title", "")), re.IGNORECASE)
        or re.fullmatch(r"\d+", str(e.get("title", "")).strip())
    )
    if page_like / len(entries) > 0.30 or len(entries) > parsed.page_count * 0.5:
        return []
    # keep the first two levels only
    dephts = sorted({e.get("depth", 0) for e in entries})
    keep = set(dephts[:2]) if dephts else {0}
    top = [e for e in entries if e.get("depth", 0) in keep]
    chapters: list[DetectedChapter] = []
    for i, entry in enumerate(top):
        start = int(entry["page"])
        end = int(top[i + 1]["page"]) - 1 if i + 1 < len(top) else parsed.page_count
        if end < start:
            end = start
        title = entry["title"]
        if BACK_MATTER_RE.match(title) and re.match(r"^\s*(contents|table of contents)", title, re.IGNORECASE):
            continue
        chapters.append(
            DetectedChapter(
                title=title,
                start_page=max(1, start),
                end_page=min(parsed.page_count, end),
                source="outline",
            )
        )
        note(chapters[-1], f"PDF 书签“{title}”指向第 {max(1, start)} 页")
    # drop leading front matter (cover/copyright/contents)
    chapters = [c for c in chapters if c.end_page - c.start_page >= 0]
    return chapters


def _heading_chapters(parsed: ParsedPdf, toc_pages: set[int], back_start: int | None) -> list[DetectedChapter]:
    hits: list[tuple[int, str, float]] = []
    for page in parsed.pages:
        if page.number in toc_pages:
            continue
        if back_start and page.number >= back_start:
            continue
        if not page.lines:
            continue
        for index, line in enumerate(page.lines[:8]):
            text = line.text.strip()
            if not _line_is_heading(text, line.size, page.body_size):
                continue
            if _heading_rank(text) > 1:
                continue
            hits.append((page.number, _complete_title(page, index, text), line.size))
            break  # one heading per page is enough

    if len(hits) < 2:
        return []

    # De-duplicate repeated "Chapter 1" spans: keep the first occurrence and
    # drop later duplicates of the very same title inside the same span.
    chapters: list[DetectedChapter] = []
    for idx, (page_no, text, _size) in enumerate(hits):
        end = hits[idx + 1][0] - 1 if idx + 1 < len(hits) else (
            (back_start - 1) if back_start else parsed.page_count
        )
        end = max(end, page_no)
        if chapters and chapters[-1].title == text and page_no - chapters[-1].start_page <= 1:
            chapters[-1].end_page = max(chapters[-1].end_page, end)
            continue
        chapters.append(
            DetectedChapter(title=text, start_page=page_no, end_page=end, source="heading")
        )
        note(chapters[-1], f"p{page_no} 页首按标题样式识别为“{text}”")
    return chapters


def _complete_title(page: Page, index: int, base: str) -> str:
    """Rebuild a title that OCR split across lines.

    Scanned pages often render a heading as ``Chapter`` / ``1`` / ``自然地理``
    on three separate lines.  Following short lines are appended so the chapter
    is named "Chapter 1 自然地理" instead of just "Chapter".
    """
    if re.search(r"\d", base):
        return base
    extra: list[str] = []
    if POS_RE.search(base):        # "Chapter n. something" - already polluted
        return base
    for line in page.lines[index + 1:index + 4]:
        text = line.text.strip()
        if not text or len(text) > 24:
            break
        if len(text) > 12 and re.search(r"[.?!;,]", text):
            break                      # looks like a sentence, not a title
        if POS_RE.search(text) or re.search(r"[/\[\]]", text):
            break                      # an entry line, not a subtitle
        extra.append(text)
        if len(extra) >= 2 and not re.fullmatch(r"\d{1,2}", text):
            break
    if not extra:
        return base
    return f"{base} {' '.join(extra)}".strip()


def _fallback_chapters(parsed: ParsedPdf, toc_pages: set[int], back_start: int | None) -> list[DetectedChapter]:
    last = (back_start - 1) if back_start else parsed.page_count
    first = 1
    # skip front matter: start at the first page after the last TOC page
    if toc_pages:
        first = min(last, max(toc_pages) + 1)
    size = config.FALLBACK_CHAPTER_PAGES
    chapters: list[DetectedChapter] = []
    page = first
    index = 1
    while page <= last:
        end = min(page + size - 1, last)
        chapters.append(
            DetectedChapter(
                title=f"第 {index} 部分（第 {page}-{end} 页）",
                start_page=page,
                end_page=end,
                source="fallback",
            )
        )
        note(chapters[-1], "未找到目录或标题结构，按固定页数自动划分")
        page = end + 1
        index += 1
    return chapters


def detect_chapters(parsed: ParsedPdf) -> tuple[list[DetectedChapter], dict[str, Any]]:
    """Return detected chapters plus a report describing how they were found."""
    toc_pages = detect_toc_pages(parsed.pages)
    back_start = back_matter_start(parsed.pages)
    running = find_running_lines(parsed.pages)
    page_offset = parsed.page_offset or estimate_page_offset(parsed.pages)

    report: dict[str, Any] = {
        "toc_pages": sorted(toc_pages),
        "back_matter_start": back_start,
        "running_lines": sorted(running)[:25],
        "running_line_count": len(running),
        "page_offset": page_offset,
        "strategy": None,
    }

    toc_entries: dict[int, dict[str, int]] = {}
    chapters = _outline_chapters(parsed)
    if len(chapters) >= 2:
        report["strategy"] = "outline"
    else:
        chapters, toc_entries = toc_chapters(parsed, page_offset, back_start)
        if len(chapters) >= 3:
            enrich_titles_from_openers(parsed, chapters)
            report["strategy"] = "toc"
        else:
            chapters = _heading_chapters(parsed, toc_pages, back_start)
            if len(chapters) >= 2:
                report["strategy"] = "heading"
            else:
                chapters = _fallback_chapters(parsed, toc_pages, back_start)
                report["strategy"] = "fallback"

    # Single evaluation entry point: every strategy goes through it, so a
    # chapter can never keep an unevaluated default confidence.
    evidence = collect_evidence(parsed, toc_pages, toc_entries)
    chapters = evaluate_chapters(parsed, chapters, evidence, back_start)
    report["marker_pages"] = {
        str(page): sorted(numbers) for page, numbers in sorted(evidence["top"].items())
    }
    report["footer_marker_pages"] = {
        str(page): sorted(numbers) for page, numbers in sorted(evidence["bottom"].items())
    }
    report["toc_entries"] = {str(number): entry for number, entry in sorted(toc_entries.items())}
    report["chapters_needing_review"] = [
        {"title": chapter.title, "start_page": chapter.start_page, "end_page": chapter.end_page,
         "confidence": chapter.confidence, "evidence": chapter.evidence, "alerts": chapter.alerts}
        for chapter in chapters
        if chapter.confidence in ("low", "unknown", "medium")
    ]

    # Attach sub-section headings (level-2 style headings inside a chapter).
    subs_by_range: dict[int, list[str]] = defaultdict(list)
    for page in parsed.pages:
        for line in page.lines[:8]:
            text = line.text.strip()
            if _heading_rank(text) == 1 and NUMBERED_HEADING_RE.match(text):
                for i, chapter in enumerate(chapters):
                    if chapter.start_page <= page.number <= chapter.end_page:
                        if text not in subs_by_range[i]:
                            subs_by_range[i].append(text)
                        break
    for i, chapter in enumerate(chapters):
        chapter.subsections = subs_by_range.get(i, [])[:20]

    report["chapter_count"] = len(chapters)
    return chapters, report


def build_page_texts(parsed: ParsedPdf, chapters: list[DetectedChapter]) -> dict[str, Any]:
    """Remove running heads and clamp pages to chapter boundaries."""
    running = find_running_lines(parsed.pages)
    toc_pages = detect_toc_pages(parsed.pages)
    cleaned: dict[int, list[str]] = {}
    for page in parsed.pages:
        lines: list[str] = []
        previous = ""
        for line in page.lines:
            text = line.text
            if text in running:
                continue
            # On a contents page the numbers are chapter start pages - keep them.
            if is_page_number_line(text) and page.number not in toc_pages:
                # ...but keep the number of a heading that OCR split into
                # "Chapter" / "7" - it is chapter evidence, not a page number.
                if not (re.fullmatch(r"(?i)chapter", previous.strip())
                        and re.fullmatch(r"\d{1,3}", text.strip())):
                    continue
            if looks_like_toc_line(text):
                continue
            cleaned_line = clean_line(text)
            if cleaned_line:
                lines.append(cleaned_line)
                previous = cleaned_line
        cleaned[page.number] = lines
    return {"cleaned_lines": cleaned, "running_lines": running}
