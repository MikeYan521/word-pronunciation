"""End-to-end test of the Phase-1 pipeline using a real (generated) PDF."""

import unittest
from pathlib import Path

from app import library
from tests.base import TempDataTestCase
from tools.make_sample_pdf import build_pdf


class TestPdfPipeline(TempDataTestCase):
    @classmethod
    def setUpClass(cls):
        cls.sample = Path(__file__).resolve().parent / "_sample.pdf"
        build_pdf(cls.sample)

    @classmethod
    def tearDownClass(cls):
        if cls.sample.exists():
            cls.sample.unlink()

    def upload(self):
        textbook_id = library.create_textbook("Sample Book", "sample.pdf", self.sample)
        preview = library.parse_textbook(textbook_id)
        return textbook_id, preview

    def test_pdf_text_extraction(self):
        textbook_id, preview = self.upload()
        # cover + contents + 3 chapters x 2 pages + references
        self.assertEqual(preview["page_count"], 9)
        self.assertFalse(preview["scanned"])
        self.assertGreater(preview["text_ratio"], 0.9)

    def confirm(self, textbook_id, preview):
        """Run the preview -> confirm step that creates the word list."""
        return library.apply_chapters(textbook_id, [
            {"title": c["title"], "start_page": c["start_page"], "end_page": c["end_page"],
             "source": c["source"]}
            for c in preview["chapters"]
        ])

    def test_preview_has_no_words_until_confirmed(self):
        textbook_id, preview = self.upload()
        self.assertEqual(preview["textbook"]["status"], "needs_review")
        self.assertEqual(library.list_words(textbook_id), [])
        self.confirm(textbook_id, preview)
        self.assertTrue(library.list_words(textbook_id))

    def test_chapters_detected_and_references_excluded(self):
        _textbook_id, preview = self.upload()
        titles = [c["title"] for c in preview["chapters"]]
        self.assertEqual(len(titles), 3, titles)
        self.assertTrue(all("Chapter" in title for title in titles), titles)
        # The References page must not contribute vocabulary.
        all_words = {w["lemma"] for c in preview["chapters"] for w in c["words"]}
        self.assertNotIn("oxford", all_words)
        self.assertNotIn("doe", all_words)

    def test_vocabulary_contents(self):
        _textbook_id, preview = self.upload()
        chapter_one = {w["lemma"] for w in preview["chapters"][0]["words"]}
        for expected in ["environment", "government", "opportunity", "schedule", "semester"]:
            self.assertIn(expected, chapter_one, expected)
        # stop words must be gone
        for stop in ["the", "and", "of", "a"]:
            self.assertNotIn(stop, chapter_one)
        # words shared by chapters keep separate records
        chapter_two = {w["lemma"] for w in preview["chapters"][1]["words"]}
        self.assertIn("environment", chapter_two)

    def test_confirm_chapters_recomputes_words(self):
        textbook_id, preview = self.upload()
        chapters = [
            {"title": "第一单元", "start_page": 3, "end_page": 4},
            {"title": "第二单元", "start_page": 5, "end_page": 8},
        ]
        library.apply_chapters(textbook_id, chapters)
        book = library.get_textbook(textbook_id)
        self.assertEqual([c["title"] for c in book["chapters"]], ["第一单元", "第二单元"])
        self.assertEqual(book["chapters"][0]["start_page"], 3)
        words = library.get_chapter(book["chapters"][0]["id"])["words"]
        lemmas = {w["lemma"] for w in words}
        self.assertIn("environment", lemmas)
        self.assertIn("semester", lemmas)
        # chapter 3 content is now inside "第二单元"
        second = {w["lemma"] for w in library.get_chapter(book["chapters"][1]["id"])["words"]}
        self.assertIn("technology", second)

    def test_recompute_does_not_duplicate_words(self):
        textbook_id, preview = self.upload()
        chapters = [{"title": "全部", "start_page": 1, "end_page": 8}]
        library.apply_chapters(textbook_id, chapters)
        first = library.list_words(textbook_id)
        library.apply_chapters(textbook_id, chapters)
        second = library.list_words(textbook_id)
        self.assertEqual(len(first), len(second))
        self.assertEqual(len(first), len({w["id"] for w in first}))

    def test_manual_word_add_and_remove(self):
        textbook_id, preview = self.upload()
        chapter = library.get_textbook(textbook_id)["chapters"][0]
        added = library.add_word(textbook_id, chapter["id"], "photosynthesis")
        self.assertEqual(added["lemma"], "photosynthesis")
        lemmas = {w["lemma"] for w in library.get_chapter(chapter["id"])["words"]}
        self.assertIn("photosynthesis", lemmas)
        library.remove_chapter_word(chapter["id"], added["id"])
        lemmas = {w["lemma"] for w in library.get_chapter(chapter["id"])["words"]}
        self.assertNotIn("photosynthesis", lemmas)

    def test_word_filters_and_flags(self):
        textbook_id, preview = self.upload()
        self.confirm(textbook_id, preview)
        words = library.list_words(textbook_id)
        self.assertTrue(words)
        target = words[0]
        library.update_word(target["id"], favorite=True, mastered=True)
        favorites = library.list_words(textbook_id, favorite=True)
        self.assertEqual([w["id"] for w in favorites], [target["id"]])
        mastered = library.list_words(textbook_id, mastered=True)
        self.assertEqual([w["id"] for w in mastered], [target["id"]])
        search = library.list_words(textbook_id, search="environ")
        self.assertTrue(all("environ" in w["lemma"] for w in search))

    def test_stats_after_progress(self):
        textbook_id, preview = self.upload()
        self.confirm(textbook_id, preview)
        words = library.list_words(textbook_id)
        library.update_word(words[0]["id"], mastered=True)
        data = library.stats(textbook_id)
        self.assertEqual(data["words"]["mastered"], 1)
        self.assertEqual(data["books"], 1)
        self.assertEqual(data["book_list"][0]["id"], textbook_id)
        self.assertTrue(data["unmastered"])


if __name__ == "__main__":
    unittest.main()
