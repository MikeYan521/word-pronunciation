"""Entry extraction for word books (the 《单词树》/《雅思词汇真经》 layout)."""

import unittest

from app.chapters import DetectedChapter
from app.entries import extract_entries, match_entry, summary_words
from app.vocab import build_vocabulary_ex

LEXICON = {
    "recruit", "literate", "illiteracy", "numerate", "problem", "issue", "affair",
    "controversial", "puzzle", "riddle", "obscure", "instil", "cram", "learn",
    "study", "knowledge", "environment", "government", "technology",
}

# A page in the style of the textbook (OCR output, including its quirks).
PAGE = [
    "recruit /rllkru:t/",
    "v. 吸收（新成员）",
    "[VIJI The country's first act would be to recruit for the navy.",
    "literate /llltmåt/",
    "adj. 有读写能力的",
    "[VIJI A quarter of the adult population are not fully literate.",
    "illiteracy /1'11tårosi/",
    "n. 文盲",
    "issue /'1Ju:/",
    "n. 重要问题；发行 v. 公布，发出",
    "[VIJI On this issue, they thought as one.",
    "affair /olfeo/",
    "n. 事情；公共事务，政治事务",
    "controversial /lkontrolva'fal/",
    "adj. 有争议的",
    "[ % ] a controversial issue 一件有争议的事",
    "puzzle /lpAzal/",
    "n. 难题，谜；使迷惑",
    "riddle /lrldl/",
    "n. 谜，谜语",
    "obscure /oblskjuo/",
    "adj. 难以理解的",
    "instil /lnlstll/",
    "( = instill) v. 逐渐灌输",
    "cram /kræm/",
    "v. 把……塞满；（为应考）临时死记硬背",
    # the footer summary of this page (no IPA!)
    "recruit",
    "literate",
    "illiteracy",
    "numerate",
    "problem",
    "issue",
    "affair",
    "controversial",
    "puzzle",
    "riddle",
    "obscure",
    "instil",
    "cram",
    "055",
    "Chapter 5",
    "学校教育",
]


class TestEntryExtraction(unittest.TestCase):
    def test_match_entry_accepts_real_headword_lines(self):
        for line, expected in [
            ("mishap /lmshæp/", "mishap"),
            ("breeze [bri:z/", "breeze"),            # OCR damaged the delimiter
            ("El Niño /el Ini:njou/", "El Niño"),
            ("cram /kræm/", "cram"),
        ]:
            result = match_entry(line)
            self.assertIsNotNone(result, line)
            self.assertEqual(result[0], expected)

    def test_match_entry_rejects_sentences_and_notes(self):
        for line in [
            "[VIJI A mishap prevented him from attending the",
            "The country's first act would be to recruit for the navy.",
            "mis +hap",
            "dis ( + aster (",
            "the /ðə/",                             # stop word, not an entry
            "Rä mine ($111)",
            "[ % ] a controversial issue",
            "Chapter 1",
        ]:
            self.assertIsNone(match_entry(line), line)

    def test_extract_entries_in_original_order_and_without_example_words(self):
        entries, report = extract_entries(PAGE, 66, LEXICON)
        words = [entry.headword.lower() for entry in entries]
        # exactly the words circled in the textbook page
        for expected in ["recruit", "literate", "illiteracy", "numerate", "problem",
                         "issue", "affair", "controversial", "puzzle", "riddle",
                         "obscure", "instil", "cram"]:
            self.assertIn(expected, words)
        # example-sentence words must not appear
        for unwanted in ["country", "act", "navy", "adult", "population", "quarter",
                         "thought", "bought", "money", "school"]:
            self.assertNotIn(unwanted, words)
        # printed order is preserved
        self.assertEqual(words[:8], ["recruit", "literate", "illiteracy", "numerate",
                                     "problem", "issue", "affair", "controversial"])
        self.assertEqual(len(words), 13)
        # Two words (numerate, problem) only exist in the footer on this page,
        # which is exactly the case the footer cross-check exists for.
        self.assertGreaterEqual(report["coverage"], 0.8)

    def test_pos_and_meaning_hints_are_captured(self):
        entries, _report = extract_entries(PAGE, 66, LEXICON)
        by_word = {entry.headword.lower(): entry for entry in entries}
        self.assertEqual(by_word["recruit"].pos, "v.")
        self.assertIn("吸收", by_word["recruit"].hint)
        self.assertEqual(by_word["literate"].pos, "adj.")

    def test_footer_summary_is_detected_but_not_double_counted(self):
        words = summary_words(PAGE, LEXICON)
        self.assertIn("numerate", words)
        self.assertNotIn("Chapter 5", words)
        entries, _report = extract_entries(PAGE, 66, LEXICON)
        headwords = [entry.headword.lower() for entry in entries]
        self.assertEqual(len(headwords), len(set(headwords)))

    def test_missing_entry_is_filled_from_the_footer_in_place(self):
        # Drop the illiteracy entry line: it must come back from the footer.
        page = [line for line in PAGE if not line.startswith("illiteracy /")]
        entries, report = extract_entries(page, 66, LEXICON)
        words = [entry.headword.lower() for entry in entries]
        self.assertIn("numerate", words)
        self.assertIn("illiteracy", words)
        self.assertGreater(report["filled"], 0)

    def test_fuzzy_match_avoids_ocr_duplicates(self):
        page = ["learn /l3:n/", "v. 学习", "leam", "study"]
        page += ["study", "learn"]           # footer (leam is OCR noise for learn)
        entries, _report = extract_entries(page, 10, LEXICON)
        words = [entry.headword.lower() for entry in entries]
        self.assertEqual(words.count("learn"), 1)
        self.assertNotIn("leam", words)

    def test_vocabulary_uses_entries_mode_for_word_books(self):
        lines = {66: PAGE}
        chapters, report = build_vocabulary_ex(
            lines, [DetectedChapter("Chapter 5", 66, 66)], LEXICON
        )
        self.assertEqual(report["mode"], "entries")
        self.assertEqual(chapters[0]["word_count"], 13)
        self.assertEqual(chapters[0]["words"][0]["display"].lower(), "recruit")
        self.assertEqual(chapters[0]["words"][0]["ordinal"], 1)

    def test_vocabulary_uses_tokens_mode_for_prose(self):
        prose = {1: ["The environment is important for every government worldwide.",
                     "Technology continues to improve the quality of education."]},
        chapters, report = build_vocabulary_ex(
            {1: prose[0][1]}, [DetectedChapter("Chapter 1", 1, 1)], LEXICON
        )
        self.assertEqual(report["mode"], "tokens")


if __name__ == "__main__":
    unittest.main()
