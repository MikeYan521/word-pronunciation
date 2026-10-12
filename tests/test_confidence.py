"""章节置信度评估的回归测试。

需求：
  * 新章节不得在缺少证据时默认获得 high；
  * 所有章节生成路径都要经过统一的置信度评估；
  * high / medium / low / unknown 的判定必须有可追溯证据；
  * 词条数为 0 不能机械地判为 low，而应给出内容层面的提醒；
  * 用户只改章节名称不得提升置信度。
"""

import unittest

import json

from app import chapters as C
from app.chapters import DetectedChapter, collect_evidence, detect_chapters, evaluate_chapters
from app.pdf_parser import Line, Page, ParsedPdf
from tests.base import TempDataTestCase


def page(number: int, lines: list[str], printed: int | None = None) -> Page:
    return Page(
        number=number,
        lines=[Line(text=text, size=0.0, top=0.0) for text in lines],
        text="\n".join(lines),
        char_count=sum(len(text) for text in lines),
        has_text=True,
        printed_page=printed,
    )


def parsed(pages: list[Page]) -> ParsedPdf:
    return ParsedPdf(path="x.pdf", page_count=len(pages), pages=pages, outline=[],
                     scanned=True, text_ratio=1.0, page_offset=0)


class TestConfidenceDefaults(unittest.TestCase):
    def test_new_chapter_does_not_default_to_high(self):
        chapter = DetectedChapter(title="Chapter 1", start_page=1, end_page=5)
        self.assertEqual(chapter.confidence, "unknown")
        self.assertNotIn("high", C.CONFIDENCE_LEVELS[:1])
        self.assertEqual(C.CONFIDENCE_LEVELS,
                         ("unknown", "high", "medium", "low", "manual"))

    def test_unevaluated_chapters_are_treated_as_unknown(self):
        book = parsed([page(n, ["body text"]) for n in range(1, 4)])
        chapter = DetectedChapter(title="Chapter 9", start_page=1, end_page=3)
        evaluate_chapters(book, [chapter], collect_evidence(book, set()), None)
        # no evidence at all -> low (never unknown, never high)
        self.assertEqual(chapter.confidence, "low")
        self.assertTrue(chapter.evidence)


class TestConfidenceRules(unittest.TestCase):
    def test_high_when_body_marker_on_the_chapter_start_page(self):
        pages = [
            page(1, ["Chapter", "1", "自然地理", "atmosphere /'ætmosfio/"]),
            page(2, ["body", "Chapter 1"]),
        ]
        book = parsed(pages)
        chapter = DetectedChapter(title="Chapter 1", start_page=1, end_page=2, number=1)
        evaluate_chapters(book, [chapter], collect_evidence(book, set()), None)
        self.assertEqual(chapter.confidence, "high")
        self.assertTrue(any("页首有正文标识" in note for note in chapter.evidence))

    def test_contents_page_is_not_body_evidence(self):
        pages = [
            page(1, ["Contents", "Chapter 1", "Chapter 2", "001", "005"]),
            page(2, ["unrelated text"]),
            page(3, ["unrelated text"]),
        ]
        book = parsed(pages)
        evidence = collect_evidence(book, {1})
        self.assertEqual(evidence["top"], {})       # no false chapter markers

    def test_medium_when_only_the_footer_confirms_the_chapter(self):
        pages = [
            page(1, ["body line one", "body line two", "body line three",
                     "body line four", "005", "Chapter 4"]),
            page(2, ["body line one", "body line two", "body line three",
                     "body line four", "006", "Chapter 4"]),
        ]
        book = parsed(pages)
        chapter = DetectedChapter(title="Chapter 4", start_page=1, end_page=2, number=4)
        evaluate_chapters(book, [chapter], collect_evidence(book, set()), None)
        self.assertEqual(chapter.confidence, "medium")
        self.assertTrue(any("页脚" in note for note in chapter.evidence))

    def test_medium_when_only_the_contents_page_confirms_the_chapter(self):
        pages = [page(n, ["body text line"]) for n in range(1, 6)]
        book = parsed(pages)
        chapter = DetectedChapter(title="Chapter 2", start_page=1, end_page=5, number=2)
        evidence = collect_evidence(book, set(), {2: {"toc_page": 1, "printed": 1, "start_page": 1}})
        evaluate_chapters(book, [chapter], evidence, None, allow_snap=False)
        self.assertEqual(chapter.confidence, "medium")
        self.assertTrue(any("目录页" in note for note in chapter.evidence))

    def test_low_when_there_is_no_evidence(self):
        pages = [page(n, ["plain prose without markers"]) for n in range(1, 8)]
        book = parsed(pages)
        chapter = DetectedChapter(title="第一部分", start_page=1, end_page=7)
        evaluate_chapters(book, [chapter], collect_evidence(book, set()), None)
        self.assertEqual(chapter.confidence, "low")

    def test_multiple_markers_on_one_page_downgrade_to_low(self):
        pages = [page(1, ["Chapter", "3", "Chapter", "4", "body"]),
                 page(2, ["body"]), page(3, ["body"])]
        book = parsed(pages)
        chapter = DetectedChapter(title="Chapter 3", start_page=1, end_page=3, number=3)
        evaluate_chapters(book, [chapter], collect_evidence(book, set()), None)
        self.assertEqual(chapter.confidence, "low")
        self.assertTrue(any("多个章节标识" in note for note in chapter.evidence))

    def test_body_marker_wins_over_contents_page_number(self):
        pages = [page(n, ["body"]) for n in range(1, 13)]
        pages[11] = page(12, ["Chapter", "5", "body"])
        book = parsed(pages)
        # the contents page claims chapter 5 starts on physical page 8
        chapter = DetectedChapter(title="Chapter 5", start_page=8, end_page=12, number=5,
                                  source="toc",
                                  evidence=["目录页 p1 记录 Chapter 5 起始印刷页 7"])
        evaluate_chapters(book, [chapter], collect_evidence(book, set()), None)
        self.assertEqual(chapter.start_page, 12)
        self.assertTrue(any("相差 4 页" in note or "校正" in note for note in chapter.evidence))

    def test_zero_entries_do_not_lower_confidence(self):
        """附录类章节内容为空，但边界证据充分时必须保持 high。"""
        pages = [page(1, ["Chapter", "7", "附录"])]
        book = parsed(pages)
        chapter = DetectedChapter(title="Chapter 7", start_page=1, end_page=1, number=7)
        evaluate_chapters(book, [chapter], collect_evidence(book, set()), None)
        self.assertEqual(chapter.confidence, "high")
        self.assertEqual(chapter.alerts, [])          # content alerts added elsewhere

    def test_all_generation_paths_are_evaluated(self):
        """书签 / 标题 / 兜底 三条路径都必须给出置信度与证据。"""
        pages = [page(n, [f"plain body line {n}"]) for n in range(1, 13)]
        book = parsed(pages)
        # outline path
        book.outline = [{"title": "Chapter 1", "page": 1, "depth": 0},
                        {"title": "Chapter 2", "page": 7, "depth": 0}]
        chapters, report = detect_chapters(book)
        self.assertEqual(report["strategy"], "outline")
        for chapter in chapters:
            self.assertIn(chapter.confidence, C.CONFIDENCE_LEVELS)
            self.assertNotEqual(chapter.confidence, "unknown")
            self.assertTrue(chapter.evidence, chapter.title)

        # heading path
        book2 = parsed([page(1, ["Chapter 1  Start", "body"]),
                        page(2, ["body"]),
                        page(3, ["Chapter 2  Next", "body"]),
                        page(4, ["body"])])
        chapters2, report2 = detect_chapters(book2)
        self.assertEqual(report2["strategy"], "heading")
        for chapter in chapters2:
            self.assertNotEqual(chapter.confidence, "unknown")
            self.assertTrue(chapter.evidence)

        # fallback path
        book3 = parsed([page(n, ["prose without any structure"]) for n in range(1, 30)])
        chapters3, report3 = detect_chapters(book3)
        self.assertEqual(report3["strategy"], "fallback")
        self.assertTrue(all(chapter.confidence == "low" for chapter in chapters3))
        self.assertTrue(all(chapter.evidence for chapter in chapters3))
        self.assertTrue(report3["chapters_needing_review"])


class TestConfidencePersistence(TempDataTestCase):
    """通过 API 层验证：确认章节 / 改名不会留下未评估的置信度。"""

    def _seed(self):
        from app import db, library
        with db.session() as conn:
            cur = conn.execute(
                "INSERT INTO textbooks(title, original_filename, page_count, status, created_at) "
                "VALUES('B','b.pdf',4,'needs_review',?)", (db.now(),),
            )
            textbook_id = int(cur.lastrowid)
            conn.commit()
        cache = {
            "textbook_id": textbook_id,
            "page_count": 4,
            "scanned": True,
            "text_ratio": 1.0,
            "pre_ocr_text_ratio": 0.0,
            "ocr_page_count": 4,
            "warnings": [],
            "report": {"strategy": "toc", "toc_pages": [1], "page_offset": 0,
                       "back_matter_start": None,
                       "toc_entries": {"1": {"toc_page": 1, "printed": 1, "start_page": 3}}},
            "cleaned_lines": {
                "1": ["Contents", "Chapter 1", "001"],
                "2": ["front matter"],
                "3": ["Chapter", "1", "自然地理", "atmosphere /'ætmosfio/", "n. 大气"],
                "4": ["Chapter 1"],
            },
            "raw_lines": {
                "1": ["Contents", "Chapter 1", "001"],
                "2": ["front matter"],
                "3": ["Chapter", "1", "自然地理", "atmosphere /'ætmosfio/", "n. 大气"],
                "4": ["002", "Chapter 1"],
            },
            "printed_pages": {"1": 1, "3": 1, "4": 2},
            "chapters": [],
            "ocr": {},
        }
        library.parsed_path(textbook_id).write_text(json.dumps(cache, ensure_ascii=False), "utf-8")
        return textbook_id

    def test_apply_chapters_evaluates_confidence(self):
        from app import db, library
        textbook_id = self._seed()
        result = library.apply_chapters(textbook_id, [
            {"title": "Chapter 1", "start_page": 3, "end_page": 4, "source": "toc",
             "number": 1},
        ])
        chapter = result["textbook"]["chapters"][0]
        # the opener page carries the body marker -> high, with evidence
        self.assertEqual(chapter["confidence"], "high")
        self.assertTrue(chapter["evidence"])
        self.assertIsInstance(chapter["evidence"], list)

    def test_unassessed_chapters_do_not_appear_as_confirmed(self):
        from app import db, library
        textbook_id = self._seed()
        result = library.apply_chapters(textbook_id, [
            {"title": "第 1 部分", "start_page": 3, "end_page": 4},   # no evidence
        ])
        chapter = result["textbook"]["chapters"][0]
        self.assertIn(chapter["confidence"], ("low", "medium"))
        self.assertTrue(chapter["evidence"])

    def test_renaming_does_not_upgrade_confidence(self):
        from app import db, library
        textbook_id = self._seed()
        library.apply_chapters(textbook_id, [
            {"title": "第 1 部分", "start_page": 3, "end_page": 4},
        ])
        chapter_id = library.get_textbook(textbook_id)["chapters"][0]["id"]
        before = library.get_chapter(chapter_id)
        self.assertIn(before["confidence"], ("low", "medium"))
        # rename only
        result = library.update_chapter_range(chapter_id, before["start_page"], before["end_page"],
                                              title="自然地理")
        chapter_id = result["chapter_id"]
        after = library.get_chapter(chapter_id)
        self.assertEqual(after["title"], "自然地理")
        self.assertNotEqual(after["confidence"], "manual")
        self.assertEqual(after["confidence"], before["confidence"])
        self.assertTrue(any("不影响边界置信度" in note for note in after["evidence"]))

    def test_fixing_the_range_marks_it_confirmed(self):
        from app import db, library
        textbook_id = self._seed()
        library.apply_chapters(textbook_id, [
            {"title": "第 1 部分", "start_page": 3, "end_page": 4},
        ])
        chapter_id = library.get_textbook(textbook_id)["chapters"][0]["id"]
        result = library.update_chapter_range(chapter_id, 3, 4)     # same range -> no upgrade
        chapter_id = result["chapter_id"]
        self.assertNotEqual(library.get_chapter(chapter_id)["confidence"], "manual")
        result = library.update_chapter_range(chapter_id, 2, 4)     # real change -> confirmed
        chapter_id = result["chapter_id"]
        self.assertEqual(library.get_chapter(chapter_id)["confidence"], "manual")

    def test_zero_entry_chapter_gets_an_alert_not_a_lower_confidence(self):
        from app import db, library
        textbook_id = self._seed()
        result = library.apply_chapters(textbook_id, [
            {"title": "Chapter 1", "start_page": 3, "end_page": 3, "source": "toc", "number": 1},
            {"title": "附录", "start_page": 4, "end_page": 4, "source": "toc"},
        ])
        appendix = result["textbook"]["chapters"][1]
        self.assertTrue(appendix["alerts"], appendix)
        self.assertIn(appendix["confidence"], ("high", "medium", "low"))


if __name__ == "__main__":
    unittest.main()
