"""Vocabulary extraction.

Two strategies, chosen automatically per book:

``entries``
    For word books: only the words that are laid out as vocabulary entries
    (headword + IPA, see :mod:`app.entries`).  Example sentences and running
    text are ignored, and the printed order is preserved.
``tokens``
    For prose textbooks: every content word in the chapter, lemmatised and
    deduplicated, with occurrence counts and page numbers.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from .chapters import DetectedChapter
from .entries import extract_entries
from .textutil import is_noise_token, lemmatize, tokens


@dataclass
class WordEntry:
    lemma: str
    display: str
    count: int = 0
    in_dict: bool = False
    proper: bool = False
    pages: list[int] = field(default_factory=list)
    forms: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lemma": self.lemma,
            "display": self.display,
            "count": self.count,
            "in_dict": self.in_dict,
            "proper": self.proper,
            "pages": sorted(self.pages),
            "forms": dict(sorted(self.forms.items(), key=lambda kv: -kv[1])),
        }


def extract_words(
    lines_by_page: dict[int, list[str]],
    start_page: int,
    end_page: int,
    lexicon: set[str],
    skip_pages: Iterable[int] = (),
) -> list[WordEntry]:
    """Collect vocabulary from page range [start_page, end_page]."""
    skip = set(skip_pages)
    entries: dict[str, WordEntry] = {}
    lower_seen: Counter[str] = Counter()
    upper_seen: Counter[str] = Counter()
    surface_first: dict[str, str] = {}

    for page_no in range(start_page, end_page + 1):
        if page_no in skip:
            continue
        for line in lines_by_page.get(page_no, []):
            for match in _iter_tokens(line):
                surface, lower = match
                if is_noise_token(lower):
                    continue
                if surface[:1].isupper():
                    upper_seen[lower] += 1
                else:
                    lower_seen[lower] += 1
                surface_first.setdefault(lower, lower)

                lemma = lemmatize(lower, lexicon)
                entry = entries.get(lemma)
                if entry is None:
                    entry = WordEntry(lemma=lemma, display=lemma, in_dict=lemma in lexicon)
                    entries[lemma] = entry
                entry.count += 1
                entry.forms[lower] = entry.forms.get(lower, 0) + 1
                if page_no not in entry.pages:
                    entry.pages.append(page_no)

    result: list[WordEntry] = []
    for entry in entries.values():
        # A token that is only ever capitalised and unknown to the lexicon is
        # almost certainly a name/place/abbreviation, not textbook vocabulary.
        entry.proper = (not entry.in_dict) and lower_seen[entry.lemma] == 0 and upper_seen[entry.lemma] > 0
        result.append(entry)
    return result


def _iter_tokens(line: str) -> Iterable[tuple[str, str]]:
    from .textutil import TOKEN_RE

    for match in TOKEN_RE.finditer(line):
        surface = match.group(0)
        yield surface, surface.lower()


def build_vocabulary(
    lines_by_page: dict[int, list[str]],
    chapters: list[DetectedChapter],
    lexicon: set[str],
    skip_pages: Iterable[int] = (),
    include_proper: bool = False,
    mode: str = "auto",
) -> list[dict[str, Any]]:
    """Word list for every chapter (see :func:`build_vocabulary_ex`)."""
    result, _report = build_vocabulary_ex(
        lines_by_page, chapters, lexicon, skip_pages=skip_pages,
        include_proper=include_proper, mode=mode,
    )
    return result


def build_vocabulary_ex(
    lines_by_page: dict[int, list[str]],
    chapters: list[DetectedChapter],
    lexicon: set[str],
    skip_pages: Iterable[int] = (),
    include_proper: bool = False,
    mode: str = "auto",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the word list plus a report describing how it was produced."""
    skip = set(skip_pages)
    chosen = mode
    report: dict[str, Any] = {"requested_mode": mode, "mode": mode}
    if mode == "auto":
        chosen, probe = detect_extraction_mode(lines_by_page, chapters, lexicon, skip)
        report.update(probe)
        report["mode"] = chosen

    if chosen == "entries":
        result, details = build_entry_vocabulary(lines_by_page, chapters, lexicon, skip)
        report.update(details)
        report["mode"] = "entries"
    else:
        result = build_token_vocabulary(lines_by_page, chapters, lexicon, skip, include_proper)
        report["mode"] = "tokens"
    report["stats"] = chapter_stats(result)
    return result, report


def detect_extraction_mode(
    lines_by_page: dict[int, list[str]],
    chapters: list[DetectedChapter],
    lexicon: set[str],
    skip: set[int],
) -> tuple[str, dict[str, Any]]:
    """Decide between the "entries" and "tokens" strategies for a whole book.

    A word book has headword+IPA entries on nearly every content page; prose
    does not.  The threshold is deliberately low (5% of pages) because a single
    detected entry per page is already a strong signal - running prose almost
    never produces the pattern `word /ipa/` at the start of a line.
    """
    considered = 0
    with_entry = 0
    total_entries = 0
    for chapter in chapters:
        for page in range(chapter.start_page, chapter.end_page + 1):
            if page in skip:
                continue
            considered += 1
            found = 0
            for line in lines_by_page.get(page, []):
                match = _match_entry_line(line)
                if match:
                    found += 1
            if found:
                with_entry += 1
                total_entries += found
    ratio = (with_entry / considered) if considered else 0.0
    probe = {
        "pages_considered": considered,
        "pages_with_entries": with_entry,
        "entry_ratio": round(ratio, 3),
        "entries_detected": total_entries,
    }
    return ("entries" if ratio >= 0.05 and total_entries >= 10 else "tokens"), probe


def _match_entry_line(line: str):
    from .entries import match_entry

    return match_entry(line)


def build_entry_vocabulary(
    lines_by_page: dict[int, list[str]],
    chapters: list[DetectedChapter],
    lexicon: set[str],
    skip: set[int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-chapter list of printed entries, in the book's original order."""
    out: list[dict[str, Any]] = []
    page_reports: list[dict[str, Any]] = []
    for chapter in chapters:
        entries = []
        seen: set[str] = set()
        for page in range(chapter.start_page, chapter.end_page + 1):
            if page in skip:
                continue
            found, page_report = extract_entries(lines_by_page.get(page, []), page, lexicon)
            page_reports.append(page_report)
            for entry in found:
                key = entry.headword.lower()
                if key in seen:
                    continue
                seen.add(key)
                entries.append(entry)
        words = [entry.as_dict(index + 1) for index, entry in enumerate(entries)]
        payload = chapter.as_dict()
        payload["words"] = words
        payload["word_count"] = len(words)
        payload["extraction_mode"] = "entries"
        out.append(payload)

    covered = [report for report in page_reports if report.get("summary")]
    coverage = (
        round(sum(report.get("coverage", 0) for report in covered) / len(covered), 3)
        if covered else None
    )
    details = {
        "pages_scanned": len(page_reports),
        "pages_with_entries": sum(1 for report in page_reports if report["entries"]),
        "entries_total": sum(report["entries"] for report in page_reports),
        "entries_from_footer": sum(report["filled"] for report in page_reports),
        "footer_coverage": coverage,
    }
    return out, details


def build_token_vocabulary(
    lines_by_page: dict[int, list[str]],
    chapters: list[DetectedChapter],
    lexicon: set[str],
    skip: set[int],
    include_proper: bool = False,
) -> list[dict[str, Any]]:
    """Prose strategy: every content word, deduplicated per chapter."""
    out: list[dict[str, Any]] = []
    for chapter in chapters:
        words = extract_words(
            lines_by_page,
            chapter.start_page,
            chapter.end_page,
            lexicon,
            skip_pages=skip,
        )
        if not include_proper:
            words = [w for w in words if not w.proper]
        words.sort(key=lambda w: (-w.count, w.lemma))
        payload = chapter.as_dict()
        payload["words"] = [w.as_dict() for w in words]
        payload["word_count"] = len(words)
        payload["extraction_mode"] = "tokens"
        out.append(payload)
    return out


def chapter_stats(vocabulary: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(len(c["words"]) for c in vocabulary)
    unique = len({w["lemma"] for c in vocabulary for w in c["words"]})
    multiple = sum(
        1
        for c in vocabulary
        for w in c["words"]
        if any(w["lemma"] in other["words"] for other in [])  # placeholder, see below
    )
    del multiple
    shared = Counter()
    for chapter in vocabulary:
        for word in chapter["words"]:
            shared[word["lemma"]] += 1
    return {
        "chapters": len(vocabulary),
        "total_occurrences": total,
        "unique_words": unique,
        "words_in_multiple_chapters": sum(1 for _, n in shared.items() if n > 1),
    }
