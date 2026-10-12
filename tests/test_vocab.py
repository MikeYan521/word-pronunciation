import unittest

from app.chapters import DetectedChapter
from app.vocab import build_vocabulary, extract_words

LEXICON = {
    "environment", "government", "study", "child", "run", "go", "book",
    "knowledge", "improve", "technology", "schedule",
}


class TestVocabulary(unittest.TestCase):
    def test_dedupe_keeps_forms_and_pages(self):
        lines = {
            1: ["The environment is important.", "We study the environment daily."],
            2: ["Studies of environments continue."],
        }
        words = extract_words(lines, 1, 2, LEXICON)
        by_lemma = {w.lemma: w for w in words}
        self.assertIn("environment", by_lemma)
        self.assertEqual(by_lemma["environment"].count, 3)
        self.assertEqual(by_lemma["environment"].pages, [1, 2])
        self.assertEqual(by_lemma["environment"].forms["environment"], 2)
        self.assertEqual(by_lemma["environment"].forms["environments"], 1)
        self.assertEqual(by_lemma["study"].count, 2)   # study + Studies

    def test_proper_nouns_are_flagged_and_filtered(self):
        lines = {1: ["Shanghai is a big city.", "The city has many districts."]}
        words = {w.lemma: w for w in extract_words(lines, 1, 1, LEXICON)}
        self.assertIn("shanghai", words)
        self.assertTrue(words["shanghai"].proper)
        chapters = build_vocabulary(lines, [DetectedChapter("c", 1, 1)], LEXICON)
        self.assertNotIn("shanghai", [w["lemma"] for w in chapters[0]["words"]])

    def test_known_word_that_is_capitalised_is_not_proper(self):
        lines = {1: ["Knowledge is power.", "knowledge grows"]}
        words = {w.lemma: w for w in extract_words(lines, 1, 1, LEXICON)}
        self.assertFalse(words["knowledge"].proper)

    def test_skip_pages_excludes_toc(self):
        lines = {1: ["environment"], 2: ["government"], 3: ["technology"]}
        words = {w.lemma for w in extract_words(lines, 1, 3, LEXICON, skip_pages={2})}
        self.assertNotIn("government", words)
        self.assertIn("technology", words)

    def test_sorted_by_frequency(self):
        lines = {1: ["technology technology technology", "culture culture", "knowledge"]}
        lexicon = LEXICON | {"culture"}
        chapters = build_vocabulary(lines, [DetectedChapter("c", 1, 1)], lexicon)
        lemmas = [w["lemma"] for w in chapters[0]["words"]]
        self.assertEqual(lemmas[0], "technology")


if __name__ == "__main__":
    unittest.main()
