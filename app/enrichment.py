"""Enrichment: attach UK/US IPA + Chinese meaning to stored words.

IPA priority is deliberately: local ``ipa-dict`` first (offline, MIT, stable),
remote dictionary IPA only as a gap filler.  Meanings come from the dictionary
module.  Results are cached in SQLite - a word is enriched once and then read
from the database, so the app keeps working offline after the first pass.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable

from . import db, dictionary, ipa


def enrich_word(row: dict[str, Any], force: bool = False) -> dict[str, Any]:
    """Return an update payload for one ``words`` row."""
    word = row["lemma"]
    already = bool(row.get("uk_ipa") or row.get("us_ipa") or row.get("meaning_cn"))
    if already and not force:
        return {}

    local = ipa.lookup(word)
    uk = (local.get("uk") or [None])[0]
    us = (local.get("us") or [None])[0]
    ipa_source = "ipa-dict" if (uk or us) else None

    info = dictionary.lookup(word)
    if not uk and info.uk_ipa:
        uk, ipa_source = info.uk_ipa, info.ipa_source or ipa_source
    if not us and info.us_ipa:
        us, ipa_source = info.us_ipa, info.ipa_source or ipa_source

    update: dict[str, Any] = {
        "uk_ipa": uk,
        "us_ipa": us,
        "ipa_source": ipa_source,
        "meaning_cn": info.meaning_cn,
        "pos": info.pos,
        "meaning_source": info.meaning_source,
        "enriched_at": db.now(),
    }
    return update


def get_words(conn, word_ids: Iterable[int]) -> list[dict[str, Any]]:
    ids = [int(i) for i in word_ids]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM words WHERE id IN ({placeholders})", ids
    ).fetchall()
    return [dict(r) for r in rows]


def enrich_batch(conn, word_ids: Iterable[int], force: bool = False, workers: int = 6) -> list[dict[str, Any]]:
    rows = get_words(conn, word_ids)
    if not rows:
        return []

    def work(row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            return row["id"], enrich_word(row, force=force)
        except Exception as exc:  # never let one word break the batch
            return row["id"], {"error": str(exc)}

    updates: list[tuple[int, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for item in pool.map(work, rows):
            updates.append(item)

    for word_id, update in updates:
        if not update:
            continue
        update.pop("error", None)
        columns = [k for k in update if k != "id"]
        if not columns:
            continue
        assignments = ", ".join(f"{col} = ?" for col in columns)
        conn.execute(
            f"UPDATE words SET {assignments} WHERE id = ?",
            [update[col] for col in columns] + [word_id],
        )
    conn.commit()
    refreshed = get_words(conn, [row["id"] for row in rows])
    return [db.word_to_api(r) for r in refreshed]
