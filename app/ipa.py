"""IPA lookup built on the ``ipa-dict`` dataset (MIT, open-dict-data).

Data: ``web/data/ipa/en_UK.txt`` (Received Pronunciation) and
``web/data/ipa/en_US.txt`` (General American), each a tab separated
``word<TAB>/ipa/[, /ipa/]`` file.  Loading is lazy and cached.

Attribution: ipa-dict by dohliam / open-dict-data, MIT licence,
https://github.com/open-dict-data/ipa-dict
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from . import config

_LOCK = threading.Lock()
_CACHE: dict[str, dict[str, list[str]]] = {}
_LEXICON: set[str] | None = None

IPA_SPLIT_RE = re.compile(r"\s*,\s*")
SLASH_RE = re.compile(r"^/|/$")


def _load(path: Path) -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    if not path.exists():
        return table
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if not line or "\t" not in line:
                continue
            word, ipa = line.split("\t", 1)
            word = word.strip().lower()
            if not word:
                continue
            parts = [SLASH_RE.sub("", p.strip()) for p in IPA_SPLIT_RE.split(ipa.strip()) if p.strip()]
            if parts:
                table[word] = parts
    return table


def _tables() -> dict[str, dict[str, list[str]]]:
    if not _CACHE:
        with _LOCK:
            if not _CACHE:
                _CACHE["uk"] = _load(config.IPA_DIR / "en_UK.txt")
                _CACHE["us"] = _load(config.IPA_DIR / "en_US.txt")
    return _CACHE


def lexicon() -> set[str]:
    """All known English headwords (used to validate lemmatisation)."""
    global _LEXICON
    if _LEXICON is None:
        tables = _tables()
        _LEXICON = set(tables["uk"]) | set(tables["us"])
    return _LEXICON


def _variants(word: str) -> list[str]:
    word = word.lower().strip()
    out = [word]
    if word.endswith("'s"):
        out.append(word[:-2])
    if "-" in word:
        out.append(word.split("-")[0])
        out.append(word.split("-")[-1])
    if word.endswith("s") and len(word) > 3:
        out.append(word[:-1])
    return out


def lookup(word: str) -> dict[str, list[str]]:
    """Return ``{"uk": [...], "us": [...]}``; missing keys mean no data."""
    tables = _tables()
    result: dict[str, list[str]] = {}
    for accent in ("uk", "us"):
        for variant in _variants(word):
            hit = tables[accent].get(variant)
            if hit:
                result[accent] = hit
                break
    return result


def primary(word: str, accent: str) -> str | None:
    values = lookup(word).get(accent)
    return values[0] if values else None


def stats() -> dict[str, int]:
    tables = _tables()
    return {"uk_entries": len(tables["uk"]), "us_entries": len(tables["us"]),
            "lexicon": len(lexicon())}
