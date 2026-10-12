"""SQLite persistence layer.

Deliberately a thin wrapper over :mod:`sqlite3` (standard library) so the app
has no database dependency and the whole state is one portable file.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS textbooks (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT NOT NULL,
    original_filename TEXT,
    stored_path       TEXT,
    page_count        INTEGER DEFAULT 0,
    status            TEXT DEFAULT 'parsed',      -- parsed | needs_review | failed
    parse_report      TEXT,                        -- JSON blob
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    textbook_id INTEGER NOT NULL REFERENCES textbooks(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    title       TEXT NOT NULL,
    start_page  INTEGER NOT NULL,
    end_page    INTEGER NOT NULL,
    source      TEXT DEFAULT 'auto'                -- outline | heading | fallback | manual
);

CREATE TABLE IF NOT EXISTS words (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    textbook_id   INTEGER NOT NULL REFERENCES textbooks(id) ON DELETE CASCADE,
    lemma         TEXT NOT NULL,
    display       TEXT NOT NULL,
    in_dict       INTEGER DEFAULT 0,
    uk_ipa        TEXT,
    us_ipa        TEXT,
    ipa_source    TEXT,
    meaning_cn    TEXT,
    pos           TEXT,
    meaning_source TEXT,
    uk_audio      TEXT,          -- cache file name relative to data/audio
    us_audio      TEXT,
    uk_audio_source TEXT,
    us_audio_source TEXT,
    audio_checked_at TEXT,       -- last time audio availability was probed
    favorite      INTEGER DEFAULT 0,
    mastered      INTEGER DEFAULT 0,
    enriched_at   TEXT,          -- last time IPA/meaning was fetched
    created_at    TEXT NOT NULL,
    UNIQUE (textbook_id, lemma)
);

CREATE TABLE IF NOT EXISTS chapter_words (
    chapter_id INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    word_id    INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    count      INTEGER DEFAULT 1,
    pages      TEXT DEFAULT '[]',   -- JSON list of source page numbers
    forms      TEXT DEFAULT '{}',   -- JSON map surface form -> count
    PRIMARY KEY (chapter_id, word_id)
);

CREATE INDEX IF NOT EXISTS idx_chapter_words_word ON chapter_words(word_id);
CREATE INDEX IF NOT EXISTS idx_words_book ON words(textbook_id);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns added after the first release; applied to existing databases so an
# upgrade never requires deleting the user's data.
MIGRATIONS: dict[str, dict[str, str]] = {
    "words": {
        "enriched_at": "TEXT",
        "audio_checked_at": "TEXT",
        "uk_audio_source": "TEXT",
        "us_audio_source": "TEXT",
        "ocr_meaning": "TEXT",
        # 1 = we asked the providers and they definitively have no recording for
        # this word (as opposed to "never checked" / "network hiccup").
        "uk_audio_missing": "INTEGER DEFAULT 0",
        "us_audio_missing": "INTEGER DEFAULT 0",
    },
    "textbooks": {
        "parse_report": "TEXT",
    },
    "chapters": {
        "extraction_mode": "TEXT",
        "number": "INTEGER",
        "confidence": "TEXT",
        "evidence": "TEXT",
        "alerts": "TEXT",
    },
    "chapter_words": {
        "ordinal": "INTEGER DEFAULT 0",
    },
}


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # PRAGMA settings are per connection: without this, ON DELETE CASCADE on
    # chapters/words silently does nothing on later connections.
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        _purge_orphans(conn)


def _purge_orphans(conn: sqlite3.Connection) -> None:
    """Drop rows whose parent row no longer exists.

    Older versions of this app opened connections without
    ``PRAGMA foreign_keys=ON``, so deletes did not cascade.  This sweep keeps
    existing databases consistent (and is a no-op on healthy ones).
    """
    conn.execute("DELETE FROM chapter_words WHERE chapter_id NOT IN (SELECT id FROM chapters)")
    conn.execute("DELETE FROM chapter_words WHERE word_id NOT IN (SELECT id FROM words)")
    conn.execute("DELETE FROM chapters WHERE textbook_id NOT IN (SELECT id FROM textbooks)")
    conn.execute(
        "DELETE FROM words WHERE textbook_id NOT IN (SELECT id FROM textbooks) "
        "OR (favorite = 0 AND mastered = 0 "
        "AND NOT EXISTS (SELECT 1 FROM chapter_words cw WHERE cw.word_id = words.id))"
    )


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue
        for column, declaration in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def get_kv(key: str, default: str | None = None) -> str | None:
    with session() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_kv(key: str, value: str) -> None:
    with session() as conn:
        conn.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def word_to_api(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """Shape a ``words`` row for the JSON API."""
    d = dict(row)
    d["in_dict"] = bool(d.get("in_dict"))
    d["favorite"] = bool(d.get("favorite"))
    d["mastered"] = bool(d.get("mastered"))
    for accent in ("uk", "us"):
        source = d.get(f"{accent}_audio_source")
        verified = source in ("cache", "youdao", "freedict") and bool(d.get(f"{accent}_audio"))
        if verified:
            status = "available"
        elif d.get(f"{accent}_audio_missing"):
            status = "missing"
        else:
            status = "unknown"      # not probed yet - the UI keeps the button live
        d[f"{accent}_audio_status"] = status
        d[f"has_{accent}_audio"] = verified
    return d


def json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
