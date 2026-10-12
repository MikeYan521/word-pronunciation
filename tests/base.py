"""Shared test scaffolding: every test runs against an isolated data directory
so the real ``data/app.sqlite3`` and audio cache are never touched.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from app import config, db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_TMP_ROOT = PROJECT_ROOT / "work" / "testtmp"


class TempDataTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._original = {
            "DATA_DIR": config.DATA_DIR,
            "DB_PATH": config.DB_PATH,
            "PDF_DIR": config.PDF_DIR,
            "AUDIO_CACHE_DIR": config.AUDIO_CACHE_DIR,
        }
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = TEST_TMP_ROOT / f"vocabtest_{uuid.uuid4().hex[:10]}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        config.DATA_DIR = self.tmp
        config.DB_PATH = self.tmp / "test.sqlite3"
        config.PDF_DIR = self.tmp / "pdfs"
        config.AUDIO_CACHE_DIR = self.tmp / "audio"
        config.ensure_dirs()
        db.init_db()

    def tearDown(self) -> None:
        for key, value in self._original.items():
            setattr(config, key, value)
        shutil.rmtree(self.tmp, ignore_errors=True)
