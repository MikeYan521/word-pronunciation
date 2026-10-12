"""Central configuration: filesystem layout and tunable constants.

All paths are derived from the project root so the app can be started from
anywhere.  Runtime data (uploaded PDFs, SQLite database, cached audio) lives
under ``data/`` which is safe to delete to reset the app.
"""

from __future__ import annotations

import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent

DATA_DIR = Path(os.environ.get("VOCAB_DATA_DIR", PROJECT_ROOT / "data"))
PDF_DIR = DATA_DIR / "pdfs"
AUDIO_CACHE_DIR = DATA_DIR / "audio"
# 音标数据只保留一份：Python 版与浏览器版共用 web/data/ipa（随仓库发布）
IPA_DIR = Path(os.environ.get("VOCAB_IPA_DIR", PROJECT_ROOT / "web" / "data" / "ipa"))
DB_PATH = DATA_DIR / "app.sqlite3"
WEB_DIR = PROJECT_ROOT / "web"

HOST = os.environ.get("VOCAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("VOCAB_PORT", "8000"))

# --- PDF parsing ---------------------------------------------------------
# A page with fewer than this many characters is treated as "no text layer".
SCANNED_PAGE_CHAR_THRESHOLD = 25
# Fraction of pages that must look image-only before we call the PDF scanned.
SCANNED_PDF_RATIO = 0.6
# Lines repeated on at least this fraction of pages are running heads/footers.
RUNNING_HEAD_MIN_RATIO = 0.35
# Fallback chapter length (in pages) when no headings can be detected.
FALLBACK_CHAPTER_PAGES = 12

# OCR settings for scanned PDFs.
# Windows' OCR engine is reliable when driven from a single thread; parallel
# calls can return empty results, so 1 is the safe default. Increase it via
# VOCAB_OCR_WORKERS if you need raw speed and your engine tolerates it.
OCR_DPI = int(os.environ.get("VOCAB_OCR_DPI", "300"))
OCR_WORKERS = int(os.environ.get("VOCAB_OCR_WORKERS", "1"))
OCR_LANGUAGE = os.environ.get("VOCAB_OCR_LANG", "en")

# --- Network -------------------------------------------------------------
HTTP_TIMEOUT = float(os.environ.get("VOCAB_HTTP_TIMEOUT", "10"))
HTTP_RETRIES = int(os.environ.get("VOCAB_HTTP_RETRIES", "2"))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Audio files smaller than this are treated as broken/placeholder responses.
MIN_AUDIO_BYTES = 1200
# Ignore audio larger than this (guards against error pages / wrong payloads).
MAX_AUDIO_BYTES = 3_000_000


def ensure_dirs() -> None:
    for path in (DATA_DIR, PDF_DIR, AUDIO_CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)
