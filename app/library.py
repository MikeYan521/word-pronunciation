"""Textbook library: parsing pipeline and CRUD over textbooks/chapters/words.

The expensive part (PDF text extraction) is done once and cached as JSON under
``data/parsed/<textbook_id>.json``.  Everything after that - chapter edits, word
extraction, re-extraction - is pure text processing on that cached structure,
so the preview/confirm step and manual chapter editing are instant.
"""

from __future__ import annotations

import json
import shutil
import threading
import traceback
from pathlib import Path
from typing import Any

from . import chapters as chapter_mod
from . import config, db, ipa, pdf_parser, vocab


def parsed_dir() -> Path:
    path = config.DATA_DIR / "parsed"
    path.mkdir(parents=True, exist_ok=True)
    return path


def parsed_path(textbook_id: int) -> Path:
    return parsed_dir() / f"{textbook_id}.json"


# --------------------------------------------------------------------------
# Parse progress (a scanned book can take minutes, so parsing runs in the
# background and the UI polls this registry)
# --------------------------------------------------------------------------

_PROGRESS: dict[int, dict[str, Any]] = {}
_PROGRESS_LOCK = threading.Lock()


def set_progress(textbook_id: int, *, stage: str, done: int = 0, total: int = 0,
                 message: str = "", extra: dict[str, Any] | None = None) -> None:
    with _PROGRESS_LOCK:
        entry = _PROGRESS.get(textbook_id, {})
        entry.update({
            "stage": stage,
            "done": done,
            "total": total,
            "message": message,
            "updated_at": db.now(),
            "percent": round(done / total * 100) if total else (100 if stage == "done" else 0),
        })
        if extra:
            entry.update(extra)
        _PROGRESS[textbook_id] = entry


def get_progress(textbook_id: int) -> dict[str, Any] | None:
    with _PROGRESS_LOCK:
        entry = _PROGRESS.get(textbook_id)
        return dict(entry) if entry else None


def parse_textbook_async(textbook_id: int, use_ocr: bool = False) -> None:
    """Run :func:`parse_textbook` in a daemon thread, tracking progress."""
    update_textbook(textbook_id, status="parsing")
    set_progress(textbook_id, stage="start", message="开始解析…")

    def run() -> None:
        try:
            result = parse_textbook(textbook_id, use_ocr=use_ocr)
            set_progress(textbook_id, stage="done", done=1, total=1, message="解析完成",
                         extra={"chapters": len(result.get("chapters", []))})
        except Exception as exc:  # keep the server alive and surface the reason
            traceback.print_exc()
            update_textbook(textbook_id, status="failed")
            set_progress(textbook_id, stage="failed", message=f"解析失败：{exc}")

    threading.Thread(target=run, name=f"parse-{textbook_id}", daemon=True).start()


# --------------------------------------------------------------------------
# Textbooks
# --------------------------------------------------------------------------

def create_textbook(title: str, original_filename: str, source_path: Path) -> int:
    stored = config.PDF_DIR / f"{db.now().replace(':', '').replace(' ', '_')}_{Path(original_filename).name}"
    shutil.copy2(source_path, stored)
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO textbooks(title, original_filename, stored_path, status, created_at) "
            "VALUES(?,?,?,?,?)",
            (title, original_filename, str(stored), "parsing", db.now()),
        )
        return int(cur.lastrowid)


def parse_textbook(textbook_id: int, use_ocr: bool = False, max_pages: int | None = None,
                   progress=None) -> dict[str, Any]:
    """Extract text, detect chapters, build vocabulary - the preview stage."""
    with db.session() as conn:
        row = conn.execute("SELECT * FROM textbooks WHERE id = ?", (textbook_id,)).fetchone()
    if row is None:
        raise KeyError("textbook not found")

    path = Path(row["stored_path"])
    callback = progress or (
        lambda stage, done, total, message: set_progress(
            textbook_id, stage=stage, done=done, total=total, message=message
        )
    )
    parsed = pdf_parser.parse_pdf(
        path,
        use_ocr=use_ocr,
        max_pages=max_pages,
        progress=callback,
        ocr_dpi=config.OCR_DPI,
        ocr_workers=config.OCR_WORKERS,
        ocr_language=config.OCR_LANGUAGE,
    )
    detected, report = chapter_mod.detect_chapters(parsed)
    page_info = chapter_mod.build_page_texts(parsed, detected)
    cleaned = page_info["cleaned_lines"]
    report["unassigned_pages"] = _unassigned_pages(
        detected, parsed.page_count, report.get("toc_pages") or []
    )

    skip = set(report.get("toc_pages") or [])
    back = report.get("back_matter_start")
    if back:
        skip |= {p for p in range(int(back), parsed.page_count + 1)}

    vocabulary, vocabulary_report = vocab.build_vocabulary_ex(
        cleaned, detected, ipa.lexicon(), skip_pages=skip
    )

    payload = {
        "textbook_id": textbook_id,
        "page_count": parsed.page_count,
        "scanned": parsed.scanned,
        "text_ratio": parsed.text_ratio,
        "pre_ocr_text_ratio": parsed.pre_ocr_text_ratio,
        "ocr_page_count": parsed.ocr_page_count,
        "warnings": parsed.warnings,
        "outline": parsed.outline[:200],
        "cleaned_lines": {str(k): v for k, v in cleaned.items()},
        # Raw per-page lines (before header/footer filtering).  Kept so chapter
        # re-detection can re-apply improved filtering without re-running OCR.
        "raw_lines": {str(page.number): [line.text for line in page.lines]
                      for page in parsed.pages},
        "chapters": vocabulary,
        "vocabulary_report": vocabulary_report,
        "report": report,
        "page_offset": parsed.page_offset,
        "printed_pages": {str(p.number): p.printed_page for p in parsed.pages if p.printed_page},
        "ocr": pdf_parser.ocr_status(),
    }
    parsed_path(textbook_id).write_text(json.dumps(payload, ensure_ascii=False), "utf-8")

    with db.session() as conn:
        conn.execute(
            "UPDATE textbooks SET page_count = ?, status = ?, parse_report = ?, title = COALESCE(NULLIF(title,''), ?) WHERE id = ?",
            (parsed.page_count, "needs_review", json.dumps(report, ensure_ascii=False),
             path.stem, textbook_id),
        )
        _replace_chapters(conn, textbook_id, [
            {"title": c["title"], "start_page": c["start_page"], "end_page": c["end_page"],
             "source": c["source"], "extraction_mode": vocabulary_report.get("mode"),
             "number": c.get("number"), "confidence": c.get("confidence"),
             "evidence": c.get("evidence")}
            for c in vocabulary
        ])
        conn.commit()

    return preview(textbook_id)


def preview(textbook_id: int) -> dict[str, Any]:
    data = load_parsed(textbook_id)
    with db.session() as conn:
        book = conn.execute("SELECT * FROM textbooks WHERE id = ?", (textbook_id,)).fetchone()
    if book is None:
        raise KeyError("textbook not found")
    # The database is the source of truth after any user edit (page ranges,
    # renamed chapters, manual confirmations), so merge its rows into the
    # cached preview - including the chapter ids the UI needs for editing.
    stored = list_chapters(textbook_id)
    for index, chapter in enumerate(data.get("chapters") or []):
        if index >= len(stored):
            break
        row = stored[index]
        chapter["id"] = row["id"]
        chapter["title"] = row["title"]
        chapter["start_page"] = row["start_page"]
        chapter["end_page"] = row["end_page"]
        chapter["source"] = row.get("source") or chapter.get("source")
        chapter["number"] = row.get("number")
        chapter["confidence"] = row.get("confidence") or "unknown"
        chapter["evidence"] = row.get("evidence") or []
        chapter["alerts"] = row.get("alerts") or []
    return {
        "textbook": dict(book),
        "page_count": data.get("page_count", 0),
        "scanned": data.get("scanned", False),
        "text_ratio": data.get("text_ratio", 1.0),
        "pre_ocr_text_ratio": data.get("pre_ocr_text_ratio", 1.0),
        "ocr_page_count": data.get("ocr_page_count", 0),
        "warnings": data.get("warnings", []),
        "report": data.get("report", {}),
        "vocabulary_report": data.get("vocabulary_report", {}),
        "ocr": data.get("ocr", {}),
        "chapters": data.get("chapters", []),
        "outline": data.get("outline", []),
    }


def load_parsed(textbook_id: int) -> dict[str, Any]:
    path = parsed_path(textbook_id)
    if not path.exists():
        raise KeyError("no parse cache for textbook")
    return json.loads(path.read_text("utf-8"))


def parsed_pdf_from_cache(textbook_id: int) -> tuple[Any, dict[str, Any]]:
    """Rebuild a :class:`ParsedPdf` from the cached text (no re-parsing).

    Used for chapter re-detection and for re-evaluating chapter confidence when
    the user confirms chapters, so both paths see exactly the same evidence.
    """
    data = load_parsed(textbook_id)
    cleaned = {int(k): v for k, v in (data.get("cleaned_lines") or {}).items()}
    raw_lines = {int(k): v for k, v in (data.get("raw_lines") or {}).items()}
    printed = {int(k): v for k, v in (data.get("printed_pages") or {}).items()}
    page_count = int(data.get("page_count") or 0)
    source_lines = raw_lines or cleaned
    pages = [
        pdf_parser.Page(
            number=number,
            lines=[pdf_parser.Line(text=text, size=0.0, top=0.0)
                   for text in source_lines.get(number, [])],
            text="\n".join(source_lines.get(number, [])),
            char_count=sum(len(t) for t in source_lines.get(number, [])),
            has_text=bool(source_lines.get(number)),
            printed_page=printed.get(number),
        )
        for number in range(1, page_count + 1)
    ]
    parsed = pdf_parser.ParsedPdf(
        path=data.get("path", ""), page_count=page_count, pages=pages,
        outline=data.get("outline") or [], scanned=bool(data.get("scanned")),
        text_ratio=float(data.get("text_ratio") or 1.0),
        page_offset=int(data.get("page_offset") or 0),
    )
    return parsed, data


def chapter_evidence_from_cache(textbook_id: int) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """(parsed, report, evidence) for a textbook that has been parsed."""
    parsed, data = parsed_pdf_from_cache(textbook_id)
    report = data.get("report") or {}
    toc_entries = {int(k): v for k, v in (report.get("toc_entries") or {}).items()}
    evidence = chapter_mod.collect_evidence(
        parsed, set(report.get("toc_pages") or []), toc_entries
    )
    return parsed, report, evidence


def redetect_chapters(textbook_id: int) -> dict[str, Any]:
    """Re-run chapter detection on the cached OCR text (no re-OCR needed).

    Useful after the extractor or the detection rules change, and as a
    "重新识别章节" action for books whose outline was unusable.
    """
    parsed, data = parsed_pdf_from_cache(textbook_id)
    page_count = parsed.page_count
    detected, report = chapter_mod.detect_chapters(parsed)
    # rebuild the cleaned page text with the current filtering rules
    page_info = chapter_mod.build_page_texts(parsed, detected)
    data["cleaned_lines"] = {str(k): v for k, v in page_info["cleaned_lines"].items()}
    report["unassigned_pages"] = _unassigned_pages(detected, page_count,
                                                   report.get("toc_pages") or [])
    data["report"] = report
    parsed_path(textbook_id).write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return apply_chapters(textbook_id, [
        {"title": c.title, "start_page": c.start_page, "end_page": c.end_page,
         "source": c.source, "number": c.number, "confidence": c.confidence,
         "evidence": c.evidence, "alerts": c.alerts}
        for c in detected
    ])


def _unassigned_pages(chapters, page_count: int, toc_pages: list[int]) -> list[int]:
    """Pages that no chapter covers (front matter, gaps) - shown as 待确认."""
    covered: set[int] = set()
    for chapter in chapters:
        covered.update(range(chapter.start_page, chapter.end_page + 1))
    return [page for page in range(1, page_count + 1)
            if page not in covered and page not in set(toc_pages)]


def update_chapter_range(chapter_id: int, start_page: int, end_page: int,
                         title: str | None = None) -> dict[str, Any]:
    """Manually fix one chapter's page range and re-extract the affected range.

    Boundaries are kept contiguous: the previous chapter stops one page before
    this chapter starts, and the next chapter starts one page after it ends.
    """
    with db.session() as conn:
        row = conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
        if row is None:
            raise KeyError("chapter not found")
        textbook_id = row["textbook_id"]
        siblings = db.rows_to_dicts(conn.execute(
            "SELECT * FROM chapters WHERE textbook_id = ? ORDER BY position", (textbook_id,)
        ).fetchall())

    index = next(i for i, item in enumerate(siblings) if item["id"] == chapter_id)
    start = max(1, int(start_page))
    end = max(start, int(end_page))
    range_changed = (start != int(siblings[index]["start_page"])
                     or end != int(siblings[index]["end_page"]))
    payload = [
        {
            "title": item["title"],
            "start_page": item["start_page"],
            "end_page": item["end_page"],
            "source": item.get("source"),
            "number": item.get("number"),
            "confidence": item.get("confidence"),
            "evidence": db.json_load(item.get("evidence"), []),
            "alerts": db.json_load(item.get("alerts"), []),
        }
        for item in siblings
    ]
    payload[index]["start_page"] = start
    payload[index]["end_page"] = end
    if title:
        payload[index]["title"] = title
    if range_changed:
        # The user decided where this chapter starts and ends: that is the
        # strongest evidence there is, so the chapter becomes "manual".
        payload[index]["source"] = "manual"
        payload[index]["confidence"] = "manual"
        payload[index]["evidence"] = ["用户手动指定页码范围"]
    else:
        # Renaming must NOT upgrade confidence; keep the evaluated state.
        if title and title != siblings[index]["title"]:
            note = f"用户修改了章节名称（不影响边界置信度）"
            if note not in payload[index]["evidence"]:
                payload[index]["evidence"].append(note)

    # keep neighbours contiguous (chapter i-1 ends before chapter i starts)
    if index > 0:
        payload[index - 1]["end_page"] = max(payload[index - 1]["start_page"], start - 1)
    if index + 1 < len(payload):
        payload[index + 1]["start_page"] = min(payload[index + 1]["end_page"], end + 1)
    payload.sort(key=lambda item: item["start_page"])

    apply_chapters(textbook_id, payload)
    with db.session() as conn:
        # Re-extraction re-creates the chapter rows, so look the chapter up
        # again by its (textbook, range, title) identity.
        updated = conn.execute(
            "SELECT * FROM chapters WHERE textbook_id = ? AND start_page = ? AND title = ? "
            "ORDER BY position LIMIT 1",
            (textbook_id, start, payload[index]["title"]),
        ).fetchone()
        if updated is None:
            updated = conn.execute(
                "SELECT * FROM chapters WHERE textbook_id = ? ORDER BY position", (textbook_id,)
            ).fetchall()
            updated = updated[min(index, len(updated) - 1)] if updated else None
    return {
        "chapter": dict(updated) if updated else None,
        "chapter_id": updated["id"] if updated else None,
        "textbook_id": textbook_id,
    }


def list_textbooks() -> list[dict[str, Any]]:
    with db.session() as conn:
        books = db.rows_to_dicts(conn.execute(
            "SELECT * FROM textbooks ORDER BY id DESC"
        ).fetchall())
        for book in books:
            stats = conn.execute(
                "SELECT COUNT(*) AS chapters FROM chapters WHERE textbook_id = ?",
                (book["id"],),
            ).fetchone()
            words = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN mastered = 1 THEN 1 ELSE 0 END) AS mastered, "
                "SUM(CASE WHEN favorite = 1 THEN 1 ELSE 0 END) AS favorite, "
                "SUM(CASE WHEN meaning_cn IS NOT NULL OR uk_ipa IS NOT NULL THEN 1 ELSE 0 END) AS learned "
                "FROM words WHERE textbook_id = ? "
                "AND EXISTS (SELECT 1 FROM chapter_words cw WHERE cw.word_id = words.id)",
                (book["id"],),
            ).fetchone()
            book["chapter_count"] = stats["chapters"] if stats else 0
            book["word_count"] = (words["total"] or 0) if words else 0
            book["mastered_count"] = (words["mastered"] or 0) if words else 0
            book["favorite_count"] = (words["favorite"] or 0) if words else 0
            book["learned_count"] = (words["learned"] or 0) if words else 0
    return books


def get_textbook(textbook_id: int) -> dict[str, Any]:
    with db.session() as conn:
        book = conn.execute("SELECT * FROM textbooks WHERE id = ?", (textbook_id,)).fetchone()
        if book is None:
            raise KeyError("textbook not found")
        result = dict(book)
        result["chapters"] = list_chapters(textbook_id, conn=conn)
    return result


def update_textbook(textbook_id: int, title: str | None = None, status: str | None = None) -> None:
    fields, values = [], []
    if title:
        fields.append("title = ?")
        values.append(title)
    if status:
        fields.append("status = ?")
        values.append(status)
    if not fields:
        return
    values.append(textbook_id)
    with db.session() as conn:
        conn.execute(f"UPDATE textbooks SET {', '.join(fields)} WHERE id = ?", values)


def delete_textbook(textbook_id: int) -> None:
    with db.session() as conn:
        conn.execute("DELETE FROM textbooks WHERE id = ?", (textbook_id,))
    path = parsed_path(textbook_id)
    if path.exists():
        path.unlink()


# --------------------------------------------------------------------------
# Chapters
# --------------------------------------------------------------------------

def _replace_chapters(conn, textbook_id: int, chapter_list: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM chapters WHERE textbook_id = ?", (textbook_id,))
    for index, chapter in enumerate(chapter_list):
        conn.execute(
            "INSERT INTO chapters(textbook_id, position, title, start_page, end_page, source, "
            "extraction_mode, number, confidence, evidence, alerts) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                textbook_id,
                index,
                chapter["title"],
                int(chapter["start_page"]),
                int(chapter["end_page"]),
                chapter.get("source", "manual"),
                chapter.get("extraction_mode"),
                chapter.get("number"),
                chapter.get("confidence") or "unknown",
                json.dumps(chapter.get("evidence") or [], ensure_ascii=False),
                json.dumps(chapter.get("alerts") or [], ensure_ascii=False),
            ),
        )


def list_chapters(textbook_id: int, conn=None) -> list[dict[str, Any]]:
    own = conn is None
    conn = conn or db.connect()
    try:
        rows = db.rows_to_dicts(conn.execute(
            "SELECT * FROM chapters WHERE textbook_id = ? ORDER BY position", (textbook_id,)
        ).fetchall())
        for chapter in rows:
            chapter["evidence"] = db.json_load(chapter.get("evidence"), [])
            chapter["alerts"] = db.json_load(chapter.get("alerts"), [])
            agg = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN w.mastered = 1 THEN 1 ELSE 0 END) AS mastered, "
                "SUM(CASE WHEN w.meaning_cn IS NOT NULL OR w.uk_ipa IS NOT NULL THEN 1 ELSE 0 END) AS learned "
                "FROM chapter_words cw JOIN words w ON w.id = cw.word_id WHERE cw.chapter_id = ?",
                (chapter["id"],),
            ).fetchone()
            chapter["word_count"] = agg["total"] or 0
            chapter["mastered_count"] = agg["mastered"] or 0
            chapter["learned_count"] = agg["learned"] or 0
        return rows
    finally:
        if own:
            conn.close()


def get_chapter(chapter_id: int) -> dict[str, Any]:
    with db.session() as conn:
        chapter = conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone()
        if chapter is None:
            raise KeyError("chapter not found")
        result = dict(chapter)
        result["evidence"] = db.json_load(result.get("evidence"), [])
        result["alerts"] = db.json_load(result.get("alerts"), [])
        book = conn.execute("SELECT * FROM textbooks WHERE id = ?", (chapter["textbook_id"],)).fetchone()
        result["textbook"] = dict(book) if book else None
        words = db.rows_to_dicts(conn.execute(
            "SELECT w.*, cw.count AS chapter_count, cw.pages AS chapter_pages, "
            "cw.forms AS chapter_forms, cw.ordinal AS chapter_ordinal "
            "FROM chapter_words cw JOIN words w ON w.id = cw.word_id "
            "WHERE cw.chapter_id = ? "
            "ORDER BY cw.ordinal, cw.count DESC, w.lemma",
            (chapter_id,),
        ).fetchall())
        result["words"] = [db.word_to_api(w) for w in words]
    return result


def apply_chapters(textbook_id: int, chapter_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist user-edited chapter definitions and re-extract vocabulary."""
    data = load_parsed(textbook_id)
    page_count = int(data.get("page_count") or 0)
    cleaned = {int(k): v for k, v in (data.get("cleaned_lines") or {}).items()}
    report = data.get("report") or {}
    skip = set(report.get("toc_pages") or [])
    back = report.get("back_matter_start")
    if back:
        skip |= set(range(int(back), page_count + 1))

    cleaned_input: list[dict[str, Any]] = []
    for index, chapter in enumerate(chapter_list):
        start = max(1, int(chapter.get("start_page") or 1))
        end = min(page_count or start, int(chapter.get("end_page") or start))
        if end < start:
            end = start
        cleaned_input.append({
            "title": (chapter.get("title") or f"第 {index + 1} 章").strip(),
            "start_page": start,
            "end_page": end,
            "source": chapter.get("source") or "manual",
            "subsections": chapter.get("subsections") or [],
            "number": chapter.get("number"),
            "confidence": chapter.get("confidence") or "unknown",
            "evidence": chapter.get("evidence") or [],
        })

    detected = [
        chapter_mod.DetectedChapter(
            title=c["title"], start_page=c["start_page"], end_page=c["end_page"],
            source=c["source"], subsections=c["subsections"],
            number=c.get("number"),
            confidence=c.get("confidence") or "unknown",
            evidence=list(c.get("evidence") or []),
            alerts=list(c.get("alerts") or []),
        )
        for c in cleaned_input
    ]
    # Re-evaluate confidence from the cached evidence for every chapter that the
    # user has not fixed manually.  This is the same entry point the detector
    # uses, so confirming chapters can never leave a stale default behind.
    _parsed, report_cached, evidence = chapter_evidence_from_cache(textbook_id)
    chapter_mod.evaluate_chapters(
        _parsed, detected, evidence, report_cached.get("back_matter_start"),
        allow_snap=False, allow_additions=False,
    )
    cleaned_input = [
        {
            "title": chapter.title, "start_page": chapter.start_page,
            "end_page": chapter.end_page, "source": chapter.source,
            "number": chapter.number, "confidence": chapter.confidence,
            "evidence": chapter.evidence, "alerts": list(chapter.alerts),
        }
        for chapter in detected
    ]
    vocabulary, vocabulary_report = vocab.build_vocabulary_ex(
        cleaned, detected, ipa.lexicon(), skip_pages=skip
    )
    # Content-level warnings never change confidence on their own: a chapter can
    # legitimately have zero entries (an appendix) while its boundary is fine.
    for chapter, payload, chapter_input in zip(detected, vocabulary, cleaned_input):
        if payload["word_count"] == 0:
            chapter_mod.alert(chapter, "本章未识别到词条：可能是附录/索引，或 OCR 未识别出内容")
        elif chapter.confidence in ("low", "unknown"):
            chapter_mod.alert(chapter, "章节边界依据不足，词条归属可能不准确")
        chapter_input["alerts"] = list(chapter.alerts)

    data["chapters"] = vocabulary
    data["vocabulary_report"] = vocabulary_report
    parsed_path(textbook_id).write_text(json.dumps(data, ensure_ascii=False), "utf-8")

    with db.session() as conn:
        for chapter in cleaned_input:
            chapter["extraction_mode"] = vocabulary_report.get("mode")
        _replace_chapters(conn, textbook_id, cleaned_input)
        _sync_words(conn, textbook_id, vocabulary)
        conn.execute("UPDATE textbooks SET status = 'parsed' WHERE id = ?", (textbook_id,))
        conn.commit()
    return {
        "textbook": get_textbook(textbook_id),
        "stats": vocab.chapter_stats(vocabulary),
        "vocabulary_report": vocabulary_report,
    }


def _sync_words(conn, textbook_id: int, vocabulary: list[dict[str, Any]]) -> None:
    """Create/refresh ``words`` and ``chapter_words`` rows for the book."""
    # remove links that point at chapters which no longer exist
    conn.execute(
        "DELETE FROM chapter_words WHERE chapter_id NOT IN (SELECT id FROM chapters)"
    )
    chapter_ids = {
        row["position"]: row["id"]
        for row in conn.execute(
            "SELECT id, position FROM chapters WHERE textbook_id = ?", (textbook_id,)
        ).fetchall()
    }
    conn.execute(
        "DELETE FROM chapter_words WHERE chapter_id IN (SELECT id FROM chapters WHERE textbook_id = ?)",
        (textbook_id,),
    )
    for index, chapter in enumerate(vocabulary):
        chapter_id = chapter_ids.get(index)
        if chapter_id is None:
            continue
        for word in chapter["words"]:
            row = conn.execute(
                "SELECT id FROM words WHERE textbook_id = ? AND lemma = ?",
                (textbook_id, word["lemma"]),
            ).fetchone()
            if row:
                word_id = row["id"]
                conn.execute(
                    "UPDATE words SET display = ?, in_dict = ?, pos = COALESCE(?, pos), "
                    "ocr_meaning = COALESCE(?, ocr_meaning) WHERE id = ?",
                    (word["display"], 1 if word["in_dict"] else 0,
                     word.get("pos"), word.get("ocr_meaning"), word_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO words(textbook_id, lemma, display, in_dict, pos, ocr_meaning, created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (textbook_id, word["lemma"], word["display"], 1 if word["in_dict"] else 0,
                     word.get("pos"), word.get("ocr_meaning"), db.now()),
                )
                word_id = int(cur.lastrowid)
            conn.execute(
                "INSERT OR REPLACE INTO chapter_words(chapter_id, word_id, count, pages, forms, ordinal) "
                "VALUES(?,?,?,?,?,?)",
                (
                    chapter_id,
                    word_id,
                    int(word["count"]),
                    json.dumps(word["pages"]),
                    json.dumps(word["forms"], ensure_ascii=False),
                    int(word.get("ordinal") or 0),
                ),
            )

    # Words no longer belonging to any chapter would only inflate the
    # statistics; keep the ones the user marked (收藏/已掌握) so no progress is
    # lost, drop the rest.
    conn.execute(
        "DELETE FROM words WHERE textbook_id = ? AND favorite = 0 AND mastered = 0 "
        "AND NOT EXISTS (SELECT 1 FROM chapter_words cw WHERE cw.word_id = words.id)",
        (textbook_id,),
    )


def add_word(textbook_id: int, chapter_id: int, word: str) -> dict[str, Any]:
    lemma = word.strip().lower()
    if not lemma:
        raise ValueError("empty word")
    from .textutil import lemmatize

    base = lemmatize(lemma, ipa.lexicon())
    with db.session() as conn:
        row = conn.execute(
            "SELECT * FROM words WHERE textbook_id = ? AND lemma = ?", (textbook_id, base)
        ).fetchone()
        if row:
            word_id = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO words(textbook_id, lemma, display, in_dict, created_at) VALUES(?,?,?,?,?)",
                (textbook_id, base, base, 1 if base in ipa.lexicon() else 0, db.now()),
            )
            word_id = int(cur.lastrowid)
        conn.execute(
            "INSERT OR IGNORE INTO chapter_words(chapter_id, word_id, count, pages, forms) VALUES(?,?,0,'[]','{}')",
            (chapter_id, word_id),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
    return db.word_to_api(updated)


def remove_chapter_word(chapter_id: int, word_id: int) -> None:
    with db.session() as conn:
        conn.execute(
            "DELETE FROM chapter_words WHERE chapter_id = ? AND word_id = ?", (chapter_id, word_id)
        )


def mark_audio(word_id: int, accent: str, cached) -> None:
    """Remember which cache file / provider supplied an accent's audio."""
    if accent not in ("uk", "us"):
        return
    with db.session() as conn:
        conn.execute(
            f"UPDATE words SET {accent}_audio = ?, {accent}_audio_source = ?, "
            f"{accent}_audio_missing = 0, audio_checked_at = ? WHERE id = ?",
            (cached.path.name, cached.provider, db.now(), word_id),
        )


def mark_audio_missing(word_id: int, accent: str, missing: bool = True) -> None:
    """Record that the provider chain definitively has no recording.

    Only called for non-retryable failures, so a network hiccup never turns a
    word into a permanent "无音频".
    """
    if accent not in ("uk", "us"):
        return
    with db.session() as conn:
        conn.execute(
            f"UPDATE words SET {accent}_audio_missing = ?, audio_checked_at = ? WHERE id = ?",
            (1 if missing else 0, db.now(), word_id),
        )


def audio_probe_required(word_id: int) -> bool:
    with db.session() as conn:
        row = conn.execute(
            "SELECT uk_audio, us_audio, audio_checked_at FROM words WHERE id = ?", (word_id,)
        ).fetchone()
    if row is None:
        return False
    return not (row["audio_checked_at"] and (row["uk_audio"] or row["us_audio"]))


def list_words(
    textbook_id: int,
    favorite: bool | None = None,
    mastered: bool | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    query = ["SELECT DISTINCT w.* FROM words w JOIN chapter_words cw ON cw.word_id = w.id "
             "JOIN chapters c ON c.id = cw.chapter_id WHERE c.textbook_id = ?"]
    params: list[Any] = [textbook_id]
    if favorite is True:
        query.append("AND w.favorite = 1")
    if mastered is not None:
        query.append("AND w.mastered = ?")
        params.append(1 if mastered else 0)
    if search:
        query.append("AND w.lemma LIKE ?")
        params.append(f"%{search.lower()}%")
    query.append("ORDER BY w.lemma")
    with db.session() as conn:
        rows = db.rows_to_dicts(conn.execute(" ".join(query), params).fetchall())
    return [db.word_to_api(r) for r in rows]


def update_word(word_id: int, favorite: bool | None = None, mastered: bool | None = None,
                meaning_cn: str | None = None) -> dict[str, Any]:
    fields, values = [], []
    if favorite is not None:
        fields.append("favorite = ?")
        values.append(1 if favorite else 0)
    if mastered is not None:
        fields.append("mastered = ?")
        values.append(1 if mastered else 0)
    if meaning_cn is not None:
        fields.append("meaning_cn = ?")
        values.append(meaning_cn)
    if fields:
        values.append(word_id)
        with db.session() as conn:
            conn.execute(f"UPDATE words SET {', '.join(fields)} WHERE id = ?", values)
    with db.session() as conn:
        row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
    if row is None:
        raise KeyError("word not found")
    return db.word_to_api(row)


def get_word(word_id: int) -> dict[str, Any]:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
        if row is None:
            raise KeyError("word not found")
        result = db.word_to_api(row)
        result["chapters"] = db.rows_to_dicts(conn.execute(
            "SELECT c.id, c.title, c.position, c.textbook_id, cw.count, cw.pages "
            "FROM chapter_words cw JOIN chapters c ON c.id = cw.chapter_id "
            "WHERE cw.word_id = ? ORDER BY c.textbook_id, c.position",
            (word_id,),
        ).fetchall())
    return result


def stats(textbook_id: int | None = None) -> dict[str, Any]:
    """Vocabulary progress across the library (or one book)."""
    params: list[Any] = []
    where = ""
    if textbook_id:
        where = "AND textbook_id = ?"
        params.append(textbook_id)
    with db.session() as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(mastered), 0) AS mastered, "
            "COALESCE(SUM(favorite), 0) AS favorite, "
            "COALESCE(SUM(CASE WHEN meaning_cn IS NOT NULL OR uk_ipa IS NOT NULL THEN 1 ELSE 0 END), 0) AS learned, "
            "COALESCE(SUM(CASE WHEN uk_audio_source IS NOT NULL THEN 1 ELSE 0 END), 0) AS uk_audio, "
            "COALESCE(SUM(CASE WHEN us_audio_source IS NOT NULL THEN 1 ELSE 0 END), 0) AS us_audio "
            "FROM words WHERE EXISTS (SELECT 1 FROM chapter_words cw WHERE cw.word_id = words.id) "
            + where,
            params,
        ).fetchone()
        books = conn.execute(
            "SELECT t.id, t.title, t.page_count, "
            "(SELECT COUNT(*) FROM chapters c WHERE c.textbook_id = t.id) AS chapter_count, "
            "(SELECT COUNT(*) FROM words w WHERE w.textbook_id = t.id "
            "   AND EXISTS (SELECT 1 FROM chapter_words cw WHERE cw.word_id = w.id)) AS word_count, "
            "(SELECT COUNT(*) FROM words w WHERE w.textbook_id = t.id AND w.mastered = 1 "
            "   AND EXISTS (SELECT 1 FROM chapter_words cw WHERE cw.word_id = w.id)) AS mastered_count, "
            "(SELECT COUNT(*) FROM words w WHERE w.textbook_id = t.id AND w.favorite = 1 "
            "   AND EXISTS (SELECT 1 FROM chapter_words cw WHERE cw.word_id = w.id)) AS favorite_count "
            "FROM textbooks t " + ("WHERE t.id = ? " if textbook_id else "") + "ORDER BY t.id DESC",
            params,
        ).fetchall()
        wrong_free = db.rows_to_dicts(conn.execute(
            "SELECT w.id, w.lemma, w.meaning_cn, w.uk_ipa, w.us_ipa FROM words w "
            "WHERE w.mastered = 0 "
            "AND EXISTS (SELECT 1 FROM chapter_words cw WHERE cw.word_id = w.id) "
            + ("AND w.textbook_id = ? " if textbook_id else "")
            + "ORDER BY w.lemma LIMIT 30",
            params,
        ).fetchall())
    return {
        "books": len(books),
        "book_list": [dict(row) for row in books],
        "words": {
            "total": totals["total"] or 0,
            "learned": totals["learned"] or 0,
            "mastered": totals["mastered"] or 0,
            "favorite": totals["favorite"] or 0,
            "uk_audio": totals["uk_audio"] or 0,
            "us_audio": totals["us_audio"] or 0,
        },
        # words still waiting to be learned (used as a "to review" list)
        "unmastered": wrong_free,
    }


# --------------------------------------------------------------------------
# 词表导出 / 导入（在电脑上解析，在手机上学习）
# --------------------------------------------------------------------------

EXPORT_FORMAT = "wordbook-export/1"


def export_textbook(textbook_id: int) -> dict[str, Any]:
    """把一本书的词表（含音标/释义/发音状态/学习进度）导出成可移植 JSON。"""
    with db.session() as conn:
        book = conn.execute("SELECT * FROM textbooks WHERE id = ?", (textbook_id,)).fetchone()
        if book is None:
            raise KeyError("textbook not found")
        chapters = db.rows_to_dicts(conn.execute(
            "SELECT * FROM chapters WHERE textbook_id = ? ORDER BY position", (textbook_id,)
        ).fetchall())
        words = db.rows_to_dicts(conn.execute(
            "SELECT w.*, c.position AS chapter_position, cw.count AS link_count, "
            "cw.pages AS link_pages, cw.forms AS link_forms, cw.ordinal AS link_ordinal "
            "FROM words w JOIN chapter_words cw ON cw.word_id = w.id "
            "JOIN chapters c ON c.id = cw.chapter_id "
            "WHERE w.textbook_id = ? ORDER BY c.position, cw.ordinal",
            (textbook_id,),
        ).fetchall())
    return {
        "format": EXPORT_FORMAT,
        "exported_at": db.now(),
        "book": {
            "title": book["title"],
            "filename": book["original_filename"],
            "page_count": book["page_count"],
            "scanned": bool(book["parse_report"] and "scanned" in book["parse_report"]),
        },
        "chapters": [
            {
                "position": chapter["position"],
                "title": chapter["title"],
                "start_page": chapter["start_page"],
                "end_page": chapter["end_page"],
                "number": chapter["number"],
                "confidence": chapter["confidence"],
                "evidence": db.json_load(chapter.get("evidence"), []),
                "alerts": db.json_load(chapter.get("alerts"), []),
                "extraction_mode": chapter.get("extraction_mode"),
            }
            for chapter in chapters
        ],
        "words": [
            {
                "chapter_position": word["chapter_position"],
                "lemma": word["lemma"],
                "display": word["display"],
                "in_dict": bool(word["in_dict"]),
                "uk_ipa": word["uk_ipa"],
                "us_ipa": word["us_ipa"],
                "ipa_source": word["ipa_source"],
                "meaning_cn": word["meaning_cn"],
                "pos": word["pos"],
                "ocr_meaning": word.get("ocr_meaning"),
                "meaning_source": word["meaning_source"],
                "favorite": bool(word["favorite"]),
                "mastered": bool(word["mastered"]),
                "count": word["link_count"],
                "pages": db.json_load(word["link_pages"], []),
                "forms": db.json_load(word["link_forms"], {}),
                "ordinal": word["link_ordinal"],
            }
            for word in words
        ],
    }


def import_textbook(payload: dict[str, Any]) -> dict[str, Any]:
    """导入上面导出的 JSON（新建一本书）。"""
    if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT:
        raise ValueError("不是有效的词表文件（缺少 format 标记）")
    book_meta = payload.get("book") or {}
    title = (book_meta.get("title") or "导入的词表").strip()
    with db.session() as conn:
        cur = conn.execute(
            "INSERT INTO textbooks(title, original_filename, page_count, status, created_at) "
            "VALUES(?,?,?,'parsed',?)",
            (title, book_meta.get("filename") or "imported.json",
             int(book_meta.get("page_count") or 0), db.now()),
        )
        textbook_id = int(cur.lastrowid)
        chapters = payload.get("chapters") or []
        position_to_id: dict[int, int] = {}
        for chapter in chapters:
            cur = conn.execute(
                "INSERT INTO chapters(textbook_id, position, title, start_page, end_page, source, "
                "number, confidence, evidence, alerts, extraction_mode) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (textbook_id, int(chapter.get("position") or 0), chapter.get("title") or "章节",
                 int(chapter.get("start_page") or 1), int(chapter.get("end_page") or 1), "imported",
                 chapter.get("number"), chapter.get("confidence") or "unknown",
                 json.dumps(chapter.get("evidence") or [], ensure_ascii=False),
                 json.dumps(chapter.get("alerts") or [], ensure_ascii=False),
                 chapter.get("extraction_mode")),
            )
            position_to_id[int(chapter.get("position") or 0)] = int(cur.lastrowid)
        for word in payload.get("words") or []:
            lemma = (word.get("lemma") or word.get("display") or "").strip().lower()
            if not lemma:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO words(textbook_id, lemma, display, in_dict, uk_ipa, us_ipa, "
                "ipa_source, meaning_cn, pos, ocr_meaning, meaning_source, favorite, mastered, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (textbook_id, lemma, word.get("display") or lemma, 1 if word.get("in_dict", True) else 0,
                 word.get("uk_ipa"), word.get("us_ipa"), word.get("ipa_source"),
                 word.get("meaning_cn"), word.get("pos"), word.get("ocr_meaning"),
                 word.get("meaning_source"), 1 if word.get("favorite") else 0,
                 1 if word.get("mastered") else 0, db.now()),
            )
            row = conn.execute("SELECT id FROM words WHERE textbook_id = ? AND lemma = ?",
                               (textbook_id, lemma)).fetchone()
            if row is None:
                continue
            chapter_id = position_to_id.get(int(word.get("chapter_position") or 0))
            if chapter_id is None:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO chapter_words(chapter_id, word_id, count, pages, forms, ordinal) "
                "VALUES(?,?,?,?,?,?)",
                (chapter_id, row["id"], int(word.get("count") or 1),
                 json.dumps(word.get("pages") or []),
                 json.dumps(word.get("forms") or {}, ensure_ascii=False),
                 int(word.get("ordinal") or 0)),
            )
        conn.commit()
    return {"textbook": get_textbook(textbook_id), "imported": True}
