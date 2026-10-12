"""Inspect what the entry extractor finds in an already-parsed book.

    python tools/inspect_extraction.py --list
    python tools/inspect_extraction.py --textbook-id 5 --pages 65-70
    python tools/inspect_extraction.py --textbook-id 5 --chapters 1-4

Uses the cached OCR text under ``data/parsed/`` so it runs instantly and never
re-OCRs the PDF.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, ipa, library, vocab  # noqa: E402
from app.chapters import DetectedChapter  # noqa: E402
from app.entries import extract_entries, summary_words  # noqa: E402


def parse_range(text: str | None) -> list[int]:
    if not text:
        return []
    out: list[int] = []
    for part in text.split(","):
        if "-" in part:
            start, end = part.split("-", 1)
            out.extend(range(int(start), int(end) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="列出已解析的单词书")
    parser.add_argument("--textbook-id", type=int)
    parser.add_argument("--pages", help="页码，例如 65-70 或 65,67")
    parser.add_argument("--chapters", help="章节序号，例如 1-4（从 1 开始）")
    parser.add_argument("--mode", choices=["auto", "entries", "tokens"], default="auto")
    args = parser.parse_args()

    if args.list or not args.textbook_id:
        with db.session() as conn:
            rows = conn.execute(
                "SELECT t.id, t.title, t.page_count, t.status, "
                "(SELECT COUNT(*) FROM chapters c WHERE c.textbook_id = t.id) AS chapters, "
                "(SELECT COUNT(*) FROM words w WHERE w.textbook_id = t.id) AS words "
                "FROM textbooks t ORDER BY t.id"
            ).fetchall()
        print(f"{'ID':>4} {'页数':>5} {'章节':>5} {'词条':>6}  标题")
        for row in rows:
            print(f"{row['id']:>4} {row['page_count']:>5} {row['chapters']:>5} {row['words']:>6}  {row['title']}")
        if not args.textbook_id:
            return 0

    data = library.load_parsed(args.textbook_id)
    cleaned = {int(k): v for k, v in (data.get("cleaned_lines") or {}).items()}
    report = data.get("report") or {}
    skip = set(report.get("toc_pages") or [])
    if report.get("back_matter_start"):
        skip |= set(range(int(report["back_matter_start"]), int(data.get("page_count") or 0) + 1))
    lexicon = ipa.lexicon()

    pages = parse_range(args.pages)
    chapter_indexes = parse_range(args.chapters)

    if pages:
        mode, probe = vocab.detect_extraction_mode(
            cleaned, [DetectedChapter("all", 1, int(data.get("page_count") or 0))], lexicon, skip
        )
        print(f"自动判定模式: {mode}  {probe}")
        for page in pages:
            lines = cleaned.get(page, [])
            entries, page_report = extract_entries(lines, page, lexicon)
            print(f"\n=== 第 {page} 页：识别 {len(entries)} 个词条 "
                  f"(页脚汇总 {page_report['summary']} 个, 覆盖 {page_report.get('coverage')}, "
                  f"由页脚补全 {page_report['filled']}) ===")
            if page_report.get("missing"):
                print(f"    页脚有但未识别: {page_report['missing']}")
            for index, entry in enumerate(entries, 1):
                tag = "" if entry.source == "entry" else "  [页脚补全]"
                print(f"  {index:3d}. {entry.headword:20s} /{entry.ipa_hint or '—':14s} "
                      f"{entry.pos:5s} {entry.hint}{tag}")
            if not entries:
                for line in lines[:12]:
                    print(f"      | {line[:100]}")
        return 0

    chapters = [
        DetectedChapter(title=f"chapter {c['position'] + 1}", start_page=c["start_page"],
                        end_page=c["end_page"])
        for c in db.rows_to_dicts(db.connect().execute(
            "SELECT * FROM chapters WHERE textbook_id = ? ORDER BY position", (args.textbook_id,)
        ).fetchall())
    ]
    if chapter_indexes:
        chapters = [chapters[i - 1] for i in chapter_indexes if 0 < i <= len(chapters)]
    result, details = vocab.build_vocabulary_ex(cleaned, chapters, lexicon, skip_pages=skip,
                                                mode=args.mode)
    print(f"模式: {details['mode']}  详情: { {k: v for k, v in details.items() if k != 'stats'} }")
    for chapter in result:
        print(f"\n=== {chapter['title'][:50]} (p{chapter['start_page']}-{chapter['end_page']}) "
              f"{chapter['word_count']} 词条 ===")
        for word in chapter["words"][:60]:
            print(f"  {word['ordinal']:3d}. {word['display']:20s} {word.get('pos') or '':5s} "
                  f"{(word.get('ocr_meaning') or '')[:40]}")
        if chapter["word_count"] > 60:
            print(f"  ... 还有 {chapter['word_count'] - 60} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
