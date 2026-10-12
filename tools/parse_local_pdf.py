"""Parse a local PDF through the real pipeline and print a summary.

    python tools/parse_local_pdf.py "C:\\path\\to\\book.pdf" --ocr
    python tools/parse_local_pdf.py book.pdf --ocr --limit 40 --enrich 20

Useful to validate a specific book (chapter detection, vocabulary quality)
before opening the web UI.  ``--limit`` parses only the first N pages, which is
handy while tuning.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, ipa, library  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", nargs="?", help="PDF 路径（用 --textbook-id 时可省略）")
    parser.add_argument("--textbook-id", type=int, help="重新解析已有的单词书（就地更新）")
    parser.add_argument("--ocr", action="store_true", help="对扫描版启用 OCR")
    parser.add_argument("--limit", type=int, default=0, help="只解析前 N 页")
    parser.add_argument("--enrich", type=int, default=10, help="额外获取多少个词的释义/音频")
    parser.add_argument("--dpi", type=int, default=config.OCR_DPI)
    parser.add_argument("--workers", type=int, default=config.OCR_WORKERS)
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    config.ensure_dirs()
    db.init_db()

    started = time.time()
    if args.textbook_id:
        with db.session() as conn:
            row = conn.execute("SELECT * FROM textbooks WHERE id = ?", (args.textbook_id,)).fetchone()
        if row is None:
            print(f"找不到 id={args.textbook_id} 的单词书")
            return 2
        textbook_id = int(row["id"])
        source = Path(row["stored_path"])
        if not source.exists():
            print(f"原始 PDF 已不存在：{source}")
            return 2
        print(f"重新解析《{row['title']}》({source.name})")
    else:
        if not args.pdf:
            print("请提供 PDF 路径或 --textbook-id")
            return 2
        source = Path(args.pdf)
        if not source.exists():
            print(f"找不到文件：{source}")
            return 2
        textbook_id = library.create_textbook(args.title or source.stem, source.name, source)

    progress_state: dict[str, str] = {}

    def progress(stage: str, done: int, total: int, message: str) -> None:
        if stage in ("text", "ocr", "ocr_retry"):
            if message != progress_state.get(stage):
                progress_state[stage] = message
                print(f"  {message}", flush=True)

    # Exactly the same code path the web app uses.
    config.OCR_DPI = args.dpi
    config.OCR_WORKERS = args.workers
    preview = library.parse_textbook(textbook_id, use_ocr=args.ocr,
                                     max_pages=args.limit or None, progress=progress)
    # Confirm the detected chapters so the word list is generated immediately.
    library.apply_chapters(textbook_id, [
        {"title": c["title"], "start_page": c["start_page"], "end_page": c["end_page"],
         "source": c["source"]}
        for c in preview["chapters"]
    ])
    book = library.get_textbook(textbook_id)
    vocabulary = [{"title": c["title"], "start_page": c["start_page"], "end_page": c["end_page"],
                   "words": library.get_chapter(c["id"])["words"]} for c in book["chapters"]]
    report = preview["report"]
    parsed = preview

    elapsed = time.time() - started
    print("=" * 70)
    print(f"单词书 ID: {textbook_id}  用时: {elapsed:.0f}s")
    print(f"页数: {parsed['page_count']}  扫描版: {parsed['scanned']}  "
          f"文本层覆盖率: {parsed['pre_ocr_text_ratio']:.0%} -> OCR 后 {parsed['text_ratio']:.0%}  "
          f"(OCR 页数 {parsed['ocr_page_count']})")
    print(f"章节识别方式: {report.get('strategy')}  章节数: {len(vocabulary)}  "
          f"目录页: {report.get('toc_pages')}  附录起始: {report.get('back_matter_start')}")
    if parsed["warnings"]:
        print("提示：")
        for warning in parsed["warnings"][:6]:
            print(f"  - {warning}")
    print("-" * 70)
    total = 0
    for chapter in vocabulary:
        total += len(chapter["words"])
        sample = " ".join(w["lemma"] for w in chapter["words"][:8])
        print(f"  [{chapter['start_page']:>3}-{chapter['end_page']:>3}] {chapter['title'][:40]:42s} "
              f"{len(chapter['words']):4d} 词 | {sample}")
    print(f"合计候选词条: {total}")

    if args.enrich:
        first = book["chapters"][0]
        words = library.get_chapter(first["id"])["words"][:args.enrich]
        from app import enrichment
        with db.session() as conn:
            enriched = enrichment.enrich_batch(conn, [w["id"] for w in words])
        print("-" * 70)
        print(f"{first['title']} 前 {len(enriched)} 个词的音标/释义：")
        for word in enriched:
            print(f"  {word['lemma']:16s} UK/{word.get('uk_ipa') or '—':16s} "
                  f"US/{word.get('us_ipa') or '—':16s} {word.get('meaning_cn') or ''}"[:150])

        from app.audio.service import AudioService
        service = AudioService()
        print("-" * 70)
        print("英美发音实测（前 5 个词）：")
        for word in enriched[:5]:
            uk, _ = service.resolve(word["lemma"], "uk")
            us, _ = service.resolve(word["lemma"], "us")
            library.mark_audio(word["id"], "uk", uk) if uk else None
            library.mark_audio(word["id"], "us", us) if us else None
            print(f"  {word['lemma']:16s} UK={('✓ ' + uk.mime + ' ' + str(round(uk.duration or 0, 2)) + 's') if uk else '✗'}   "
                  f"US={('✓ ' + us.mime + ' ' + str(round(us.duration or 0, 2)) + 's') if us else '✗'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
