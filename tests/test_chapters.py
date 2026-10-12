import unittest

from app.chapters import (
    detect_chapters,
    detect_toc_pages,
    find_running_lines,
    is_page_number_line,
    looks_like_toc_line,
)
from app.pdf_parser import Line, Page, ParsedPdf


def make_page(number, lines, sizes=None):
    sizes = sizes or [10.0] * len(lines)
    return Page(
        number=number,
        lines=[Line(text=text, size=size, top=0.0) for text, size in zip(lines, sizes)],
        text="\n".join(lines),
        char_count=sum(len(line) for line in lines),
        body_size=10.0,
    )


class TestChapterDetection(unittest.TestCase):
    def test_page_number_and_running_head(self):
        self.assertTrue(is_page_number_line("34"))
        self.assertTrue(is_page_number_line("- 12 -"))
        self.assertTrue(is_page_number_line("Page 7 of 90"))
        self.assertFalse(is_page_number_line("Chapter 1"))
        self.assertTrue(looks_like_toc_line("Chapter 1  Introduction ......... 12"))
        self.assertFalse(looks_like_toc_line("This is normal body text."))

    def test_running_lines_across_pages(self):
        pages = [
            make_page(n, ["Unit Book Level 3", f"unique body line for page {n}"]) for n in range(1, 6)
        ]
        running = find_running_lines(pages)
        self.assertIn("Unit Book Level 3", running)
        self.assertNotIn("unique body line for page 3", running)

    def test_toc_pages_detected(self):
        pages = [
            make_page(1, ["Contents", "Chapter 1 Something ......... 3",
                          "Chapter 2 Other ......... 7", "Chapter 3 Third ......... 11",
                          "Chapter 4 Fourth ......... 15"]),
            make_page(2, ["Contents", "Chapter 5 Fifth ......... 19",
                          "Chapter 6 Sixth ......... 23", "Chapter 7 Seventh ......... 27",
                          "Chapter 8 Eighth ......... 31"]),
            make_page(3, ["Chapter 1 Real content", "The body text starts here."]),
        ]
        self.assertEqual(detect_toc_pages(pages), {1, 2})

    def test_heading_based_chapters(self):
        pages = [
            make_page(1, ["English Book", "Some intro text"]),
            make_page(2, ["Chapter 1  Welcome", "body one"]),
            make_page(3, ["more body"]),
            make_page(4, ["Chapter 2  Animals", "body two"]),
            make_page(5, ["more body"]),
            make_page(6, ["References", "Doe, J. 2020"]),
        ]
        parsed = ParsedPdf(path="x.pdf", page_count=6, pages=pages, outline=[],
                           scanned=False, text_ratio=1.0)
        chapters, report = detect_chapters(parsed)
        titles = [c.title for c in chapters]
        self.assertEqual(titles, ["Chapter 1  Welcome", "Chapter 2  Animals"])
        self.assertEqual(report["strategy"], "heading")
        self.assertEqual(chapters[0].start_page, 2)
        self.assertEqual(chapters[0].end_page, 3)
        self.assertEqual(chapters[1].end_page, 5)   # References page excluded
        self.assertEqual(report["back_matter_start"], 6)

    def test_outline_strategy_wins(self):
        pages = [make_page(n, [f"page {n} body"]) for n in range(1, 8)]
        parsed = ParsedPdf(
            path="x.pdf", page_count=7, pages=pages,
            outline=[{"title": "Chapter 1", "page": 2, "depth": 0},
                     {"title": "Chapter 2", "page": 5, "depth": 0}],
            scanned=False, text_ratio=1.0,
        )
        chapters, report = detect_chapters(parsed)
        self.assertEqual(report["strategy"], "outline")
        self.assertEqual([(c.start_page, c.end_page) for c in chapters], [(2, 4), (5, 7)])

    def test_fallback_split_when_nothing_detected(self):
        pages = [make_page(n, [f"plain body line {n}", "another line"]) for n in range(1, 26)]
        parsed = ParsedPdf(path="x.pdf", page_count=25, pages=pages, outline=[],
                           scanned=False, text_ratio=1.0)
        chapters, report = detect_chapters(parsed)
        self.assertEqual(report["strategy"], "fallback")
        self.assertGreater(len(chapters), 1)
        self.assertEqual(chapters[0].start_page, 1)
        self.assertEqual(chapters[-1].end_page, 25)

    def test_ocr_split_heading_is_rebuilt(self):
        """Scanned openers put 'Chapter' / '1' / '自然地理' on separate lines."""
        pages = [
            make_page(1, ["VOCABULARY FOR IELTS"]),
            make_page(2, ["We cannot command nature except by obeying her. -Francis Bacon",
                          "Chapter", "1", "自然地理", "magma n. 岩浆"]),
            make_page(3, ["ore /o:/ This mountain area is rich in iron ore."]),
            make_page(4, ["Chapter", "2", "人类与自然", "environment n. 环境"]),
            make_page(5, ["The environment matters."]),
        ]
        parsed = ParsedPdf(path="x.pdf", page_count=5, pages=pages, outline=[],
                           scanned=False, text_ratio=1.0)
        chapters, _report = detect_chapters(parsed)
        titles = [c.title for c in chapters]
        self.assertEqual(titles, ["Chapter 1 自然地理", "Chapter 2 人类与自然"])

    def test_toc_strategy_uses_printed_page_numbers(self):
        """Scanned word books: contents page + printed page numbers."""
        pages = [
            make_page(1, ["Contents", "Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4",
                          "001", "011", "021", "031"]),
        ]
        pages += [make_page(number, [f"body line {number}", f"{number - 1:03d}"])
                  for number in range(2, 40)]
        parsed = ParsedPdf(path="x.pdf", page_count=39, pages=pages, outline=[],
                           scanned=True, text_ratio=1.0, page_offset=1)
        chapters, report = detect_chapters(parsed)
        self.assertEqual(report["strategy"], "toc")
        self.assertEqual([c.start_page for c in chapters], [2, 12, 22, 32])
        self.assertEqual([c.end_page for c in chapters], [11, 21, 31, 39])
        self.assertEqual(chapters[0].title, "Chapter 1")

    def test_toc_page_keeps_its_page_numbers(self):
        from app.chapters import build_page_texts

        pages = [
            make_page(1, ["Contents", "Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4",
                          "001", "011", "021", "031"]),
            make_page(2, ["body text", "005"]),
        ]
        parsed = ParsedPdf(path="x.pdf", page_count=2, pages=pages, outline=[],
                           scanned=True, text_ratio=1.0)
        cleaned = build_page_texts(parsed, [])["cleaned_lines"]
        self.assertIn("001", cleaned[1])           # kept: it is a chapter start page
        self.assertNotIn("005", cleaned[2])        # dropped: it is a page number

    def test_page_bookmark_outline_is_ignored(self):
        """PDFs with one bookmark per page must not become one chapter per page."""
        pages = [make_page(number, ["body"]) for number in range(1, 12)]
        parsed = ParsedPdf(
            path="x.pdf", page_count=11, pages=pages,
            outline=[{"title": f"雅思词汇真经_页面_{n:03d}", "page": n, "depth": 0}
                     for n in range(1, 12)],
            scanned=True, text_ratio=1.0,
        )
        chapters, report = detect_chapters(parsed)
        self.assertNotEqual(report["strategy"], "outline")
        self.assertNotEqual(len(chapters), 11)


if __name__ == "__main__":
    unittest.main()
