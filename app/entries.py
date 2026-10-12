"""Structured vocabulary-entry extraction for word books.

Word books (e.g. 《雅思词汇真经》/《单词树》) are not prose: every target word is
layed out as an entry — headword, IPA in slashes, part of speech, Chinese
definition, then example sentences and notes.  Extracting "all English tokens"
from such a page pulls in hundreds of example-sentence words, which is useless
for study.

The layout rules this module encodes, verified against real scanned pages:

* an entry **starts a line** with the headword followed immediately by an IPA
  block: ``mishap /lmshæp/``, ``breeze [bri:z/`` (OCR often turns one delimiter
  into ``[`` or ``l``), ``El Niño /el Ini:njou/``;
* example sentences start with a marker (【例】, OCR-mangled to ``[VIJI``) or are
  ordinary prose lines and therefore never match;
* the page footer repeats the page's entry words, one per line, without IPA —
  that list is used as a *cross-check* (recall/coverage) and to fill gaps where
  an entry line was mis-OCR-ed, never as independent vocabulary.

The part of speech / Chinese text next to an entry is kept as an OCR *hint*
(``hint``); authoritative IPA and meanings are resolved from ipa-dict and the
online dictionary, because OCR of IPA and Chinese is unreliable.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from typing import Any, Iterable

from .textutil import STOPWORDS

# Latin letters, including accented ones (El Niño, café).
WORD = r"[A-Za-z\u00c0-\u024f]"
HEADWORD = rf"{WORD}[A-Za-z\u00c0-\u024f'\-]*"

# IPA blocks are bracketed by slashes/brackets, but OCR frequently damages one
# of the delimiters, so the opening may be /, [ or | and the closing /, ] or |.
ENTRY_RE = re.compile(
    rf"^\s*(?P<word>{HEADWORD}(?:\s+(?:of|the|to|and|in|on|up|out)?\s*{HEADWORD}){{0,2}})"
    rf"\s*(?:\(\s*=\s*[A-Za-z\-]+\s*\))?\s*"
    rf"(?P<open>[\/\[\|])\s*(?P<ipa>[^\/\]\|]{{2,45}}?)\s*(?P<close>[\/\]\|])"
)

POS_RE = re.compile(r"\b(n|v|vt|vi|adj|adv|prep|conj|pron|num|art|interj|abbr)\.", re.IGNORECASE)
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
SENTENCE_CHARS = re.compile(r"[.?!;,]\s")

# Lines that may sit between the footer word list and the bottom of the page:
# the page number, a running head ("Chapter 5") and the chapter's Chinese name.
TAIL_NOISE_RE = re.compile(
    r"^\s*(?:\d{1,4}|[ivxlcdm]{1,6}|chapter\s*\d*|[\u3400-\u4dbf\u4e00-\u9fff\s]+)\s*$",
    re.IGNORECASE,
)

# Shortest plausible IPA body (OCR noise such as `[E]` is only two chars).
MIN_IPA_LEN = 2


@dataclass
class Entry:
    headword: str
    page: int
    order: int = 0
    ipa_hint: str = ""
    pos: str = ""
    hint: str = ""
    source: str = "entry"          # entry | summary
    in_dict: bool = True
    extra_pages: list[int] = field(default_factory=list)

    def as_dict(self, ordinal: int) -> dict[str, Any]:
        return {
            "lemma": self.headword.lower(),
            "display": self.headword,
            "headword": self.headword,
            "in_dict": self.in_dict,
            "count": 1,
            "pages": [self.page],
            "forms": {self.headword: 1},
            "pos": self.pos or None,
            "ocr_meaning": self.hint or None,
            "ordinal": ordinal,
            "entry_source": self.source,
            "proper": False,
        }


def _plausible_headword(word: str) -> bool:
    tokens = word.split()
    if not tokens or len(tokens) > 3:
        return False
    if any(re.search(r"\d", token) for token in tokens):
        return False
    if tokens[0].lower() in STOPWORDS:
        return False
    if len(tokens[0]) < 2:
        return False
    # A single-letter or all-consonant headword is OCR noise.
    if not re.search(r"[aeiouy]", tokens[0], re.IGNORECASE) and len(tokens) == 1:
        return False
    return True


def _clean_headword(raw: str) -> str:
    word = re.sub(r"\s+", " ", raw).strip(" -'\u2019")
    return word


def match_entry(line: str) -> tuple[str, str] | None:
    """Return (headword, ipa_hint) when *line* starts a vocabulary entry."""
    match = ENTRY_RE.match(line)
    if not match:
        return None
    headword = _clean_headword(match.group("word"))
    ipa = match.group("ipa").strip()
    if len(ipa) < MIN_IPA_LEN:
        return None
    if not _plausible_headword(headword):
        return None
    # An IPA block never contains a sentence-ending period.
    if "." in ipa and not re.match(r"^[a-zɑɒɔəɜɛɪʊʌæʤʧθðʃʒŋɹɫˈˌː' ]+$", ipa, re.IGNORECASE):
        return None
    return headword, ipa


def _collect_details(lines: list[str], start_index: int) -> tuple[str, str]:
    """Part of speech + OCR definition hint for the entry at *start_index*."""
    # text after the IPA on the headword line
    first = ENTRY_RE.sub("", lines[start_index], count=1).strip()
    parts: list[str] = [first] if first else []
    for line in lines[start_index + 1:start_index + 5]:
        if match_entry(line):
            break
        text = line.strip()
        if not text:
            continue
        # stop at example sentences / notes / etymology lines
        if re.match(r"^[\[\(【]?\s*(例|VIJ|记|搭|E|BJ|5?1J)", text):
            break
        parts.append(text)
        if POS_RE.search(text) and CJK_RE.search(text):
            break
    blob = " ".join(parts).strip()
    pos_match = POS_RE.search(blob)
    pos = pos_match.group(1).lower() + "." if pos_match else ""
    cjk = " ".join(part for part in CJK_RE.findall(blob))
    hint = cjk.strip() if cjk else ""
    if not hint:
        # keep a latin fallback so the preview can still show something
        latin = re.sub(r"\s+", " ", blob).strip()
        if latin and len(latin) <= 60 and not SENTENCE_CHARS.search(latin):
            hint = latin
    return pos, hint


def summary_words(lines: list[str], lexicon: set[str] | None = None) -> list[str]:
    """The page footer list of entry words (one word per line, no IPA).

    Returns an empty list when the tail of the page does not look like such a
    summary (fewer than 4 words, or words that are not in the lexicon).
    """
    collected: list[str] = []
    skipped = 0
    for line in reversed(lines):
        text = line.strip()
        if not text:
            continue
        if TAIL_NOISE_RE.match(text) and skipped < 6:
            skipped += 1
            continue
        if match_entry(text):
            break                       # reached the last real entry
        if re.fullmatch(rf"{HEADWORD}(?:\s+{HEADWORD})?", text) and text.lower() not in STOPWORDS:
            collected.append(text)
            continue
        break
    words = list(reversed(collected))
    if len(words) < 4:
        return []
    if lexicon:
        known = sum(1 for word in words if word.lower() in lexicon)
        if known / len(words) < 0.5:
            return []
    return words


def extract_entries(
    lines: Iterable[str],
    page: int,
    lexicon: set[str] | None = None,
    fill_from_summary: bool = True,
) -> tuple[list[Entry], dict[str, Any]]:
    """Entries on one page, in printed order, plus a small quality report."""
    lines = [line for line in lines]
    entries: list[Entry] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = match_entry(line)
        if not match:
            continue
        headword, ipa = match
        key = headword.lower()
        if key in seen:
            continue
        pos, hint = _collect_details(lines, index)
        entries.append(Entry(headword=headword, page=page, ipa_hint=ipa, pos=pos,
                             hint=hint, source="entry",
                             in_dict=_known(headword, lexicon)))
        seen.add(key)

    report: dict[str, Any] = {"page": page, "entries": len(entries),
                              "summary": 0, "filled": 0, "missing": []}
    summary = summary_words(lines, lexicon)
    report["summary"] = len(summary)
    if summary:
        summary_set = {word.lower() for word in summary}
        found = {entry.headword.lower() for entry in entries}
        matched, _mapping = _match_summary(summary, entries)
        overlap = len(matched)
        report["coverage"] = round(overlap / len(summary_set), 3)
        if fill_from_summary and overlap / len(summary_set) >= 0.5:
            merged, filled = _merge_with_summary(summary, entries, page, lexicon)
            report["filled"] = filled
            entries = merged
        report["missing"] = sorted(summary_set - matched)
    return entries, report


def _known(headword: str, lexicon: set[str] | None) -> bool:
    if not lexicon:
        return True
    word = headword.lower().strip()
    if word in lexicon:
        return True
    # multi-word entries: accept when the first token is known ("El Niño")
    first = word.split()[0]
    return first in lexicon


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 2:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.78


def _match_summary(summary: list[str], entries: list[Entry]) -> tuple[set[str], dict[str, Entry]]:
    """Map each summary word onto the detected entry it refers to.

    OCR frequently mangles a headword in one of the two places ("learn" in the
    entry vs "leam" in the footer), so fuzzy matching avoids counting the same
    word twice.
    """
    matched: set[str] = set()
    mapping: dict[str, Entry] = {}
    for word in summary:
        key = word.lower()
        for entry in entries:
            if entry.headword.lower() == key or _similar(entry.headword.lower(), key):
                matched.add(key)
                mapping[key] = entry
                break
    return matched, mapping


def _merge_with_summary(
    summary: list[str],
    entries: list[Entry],
    page: int,
    lexicon: set[str] | None,
) -> tuple[list[Entry], int]:
    """Order the page's entries the way the book prints them.

    The footer list mirrors the page's entry order, so it is used as the
    ordering skeleton; detected entries supply the IPA/POS hints.
    """
    _matched, mapping = _match_summary(summary, entries)
    ordered: list[Entry] = []
    used: set[int] = set()
    filled = 0
    for word in summary:
        key = word.lower()
        entry = mapping.get(key)
        if entry is not None:
            if id(entry) in used:
                continue
            ordered.append(entry)
            used.add(id(entry))
            continue
        new_entry = Entry(headword=word, page=page, source="summary",
                          in_dict=_known(word, lexicon))
        ordered.append(new_entry)
        filled += 1
    # entries the footer does not mention (or does not cover) keep their own order
    for entry in entries:
        if id(entry) not in used:
            ordered.append(entry)
    return ordered, filled
