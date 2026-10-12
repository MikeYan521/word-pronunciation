"""Dictionary lookups: Chinese meaning + UK/US IPA, with an English-only
fallback.

Primary source: Youdao dictionary JSON endpoint (``dict.youdao.com/jsonapi``).
It returns, for a single word, the British and American IPA, a speech handle
for each accent, and Chinese translations.

.. warning::
   This endpoint is public but **undocumented** and not guaranteed by the
   vendor.  It is used server-side, one word at a time, results are cached in
   SQLite and it can be swapped out by editing :data:`PROVIDERS` below.  Only
   individual words are ever sent - never the uploaded PDF.

Fallback source: Free Dictionary API (dictionaryapi.dev, MIT project by
meetDeveloper, data from Wiktionary) for English definitions.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config


@dataclass
class WordInfo:
    word: str
    meaning_cn: str | None = None
    pos: str | None = None
    uk_ipa: str | None = None
    us_ipa: str | None = None
    meaning_source: str | None = None
    ipa_source: str | None = None
    definitions_en: list[str] = field(default_factory=list)
    error: str | None = None


def _http_get(url: str, timeout: float | None = None) -> tuple[int, bytes]:
    timeout = timeout or config.HTTP_TIMEOUT
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://dict.youdao.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


POS_RE = re.compile(r"^\s*([a-z]{1,5})\.", re.IGNORECASE)


def _clean_tr(text: str) -> tuple[str | None, str]:
    text = re.sub(r"<[^>]+>", "", text).strip()
    match = POS_RE.match(text)
    pos = match.group(1).lower() if match else None
    return pos, text


def fetch_youdao(word: str) -> WordInfo:
    info = WordInfo(word=word)
    url = "https://dict.youdao.com/jsonapi?q=" + urllib.parse.quote(word)
    status, body = _http_get(url)
    if status != 200 or not body:
        info.error = f"youdao HTTP {status}"
        return info
    try:
        data = json.loads(body)
    except ValueError:
        info.error = "youdao 返回内容无法解析"
        return info

    entry = None
    words = (data.get("ec") or {}).get("word") or []
    if words:
        entry = words[0]
    simple = (data.get("simple") or {}).get("word") or []
    simple_entry = simple[0] if simple else {}

    def pick(field: str) -> str | None:
        if entry and entry.get(field):
            return str(entry[field]).strip()
        if simple_entry.get(field):
            return str(simple_entry[field]).strip()
        return None

    info.uk_ipa = pick("ukphone")
    info.us_ipa = pick("usphone")
    if info.uk_ipa or info.us_ipa:
        info.ipa_source = "youdao"

    translations: list[str] = []
    pos_seen: list[str] = []
    for block in (entry or {}).get("trs") or []:
        for tr in block.get("tr") or []:
            items = ((tr.get("l") or {}).get("i")) or []
            for item in items:
                text = item if isinstance(item, str) else str(item)
                pos, cleaned = _clean_tr(text)
                if cleaned:
                    translations.append(cleaned)
                    if pos and pos not in pos_seen:
                        pos_seen.append(pos)
    if translations:
        info.meaning_cn = "；".join(translations[:6])
        info.pos = "/".join(pos_seen[:4]) or None
        info.meaning_source = "youdao"
    return info


def fetch_freedict(word: str) -> WordInfo:
    """English-only fallback (definitions + Wiktionary-style IPA)."""
    info = WordInfo(word=word)
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(word)}"
    status, body = _http_get(url)
    if status != 200 or not body:
        info.error = f"dictionaryapi.dev HTTP {status}"
        return info
    try:
        data = json.loads(body)
    except ValueError:
        info.error = "dictionaryapi.dev 返回内容无法解析"
        return info
    if not isinstance(data, list):
        info.error = "unexpected payload"
        return info

    defs: list[str] = []
    for entry in data[:3]:
        for meaning in entry.get("meanings", [])[:4]:
            part = meaning.get("partOfSpeech")
            for definition in meaning.get("definitions", [])[:2]:
                text = (definition.get("definition") or "").strip()
                if text:
                    defs.append(f"({part}) {text}" if part else text)
        for phonetic in entry.get("phonetics", []):
            audio = (phonetic.get("audio") or "").lower()
            text = (phonetic.get("text") or "").strip().strip("/")
            if not text:
                continue
            if "-uk" in audio and not info.uk_ipa:
                info.uk_ipa, info.ipa_source = text, "dictionaryapi"
            elif "-us" in audio and not info.us_ipa:
                info.us_ipa, info.ipa_source = text, "dictionaryapi"
    info.definitions_en = defs[:6]
    if defs and not info.meaning_cn:
        info.meaning_source = "dictionaryapi(en)"
    return info


# Ordered provider chain.  `ipa-dict` (local, MIT) is applied on top of the
# remote IPA in the enrichment step, so it always wins when it has data.
PROVIDERS: list[tuple[str, Callable[[str], WordInfo]]] = [
    ("youdao", fetch_youdao),
    ("dictionaryapi", fetch_freedict),
]


def lookup(word: str) -> WordInfo:
    """Query providers until one returns something useful."""
    first_error: str | None = None
    merged = WordInfo(word=word)
    for name, provider in PROVIDERS:
        try:
            info = provider(word)
        except Exception as exc:  # network hiccup - keep trying
            info = WordInfo(word=word, error=f"{name}: {exc}")
        if info.error:
            first_error = first_error or info.error
        merged.meaning_cn = merged.meaning_cn or info.meaning_cn
        merged.pos = merged.pos or info.pos
        merged.uk_ipa = merged.uk_ipa or info.uk_ipa
        merged.us_ipa = merged.us_ipa or info.us_ipa
        merged.meaning_source = merged.meaning_source or info.meaning_source
        merged.ipa_source = merged.ipa_source or info.ipa_source
        merged.definitions_en = merged.definitions_en or info.definitions_en
        if merged.meaning_cn and merged.uk_ipa and merged.us_ipa:
            break
    if not (merged.meaning_cn or merged.definitions_en or merged.uk_ipa or merged.us_ipa):
        merged.error = first_error or "未找到释义"
    return merged
