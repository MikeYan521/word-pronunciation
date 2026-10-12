import unittest

from app import textutil


class TestTextUtil(unittest.TestCase):
    def test_tokenize_keeps_internal_punctuation(self):
        self.assertEqual(
            textutil.tokens("The well-known student's book, 2024 edition."),
            ["the", "well-known", "student's", "book", "edition"],
        )

    def test_noise_filter(self):
        for word in ["a", "the", "of", "xzq", "ii"]:
            self.assertTrue(textutil.is_noise_token(word), word)
        for word in ["environment", "government", "technology"]:
            self.assertFalse(textutil.is_noise_token(word), word)

    def test_dehyphenate_joins_split_words(self):
        self.assertEqual(textutil.dehyphenate("environ-\nment"), "environment")
        self.assertEqual(textutil.dehyphenate("well-\nknown"), "wellknown")

    def test_lemmatize_with_lexicon_never_invents_words(self):
        lexicon = {"study", "go", "child", "happy", "run", "environment", "try"}
        cases = {
            "studies": "study",
            "studied": "study",
            "went": "go",
            "children": "child",
            "happier": "happy",
            "running": "run",
            "environments": "environment",
            "tried": "try",
            "bus": "bus",          # must NOT become "bu"
            "gas": "gas",
        }
        for word, expected in cases.items():
            self.assertEqual(textutil.lemmatize(word, lexicon), expected, word)

    def test_irregular_verbs(self):
        lexicon = {"write", "take", "think", "be", "have"}
        self.assertEqual(textutil.lemmatize("written", lexicon), "write")
        self.assertEqual(textutil.lemmatize("took", lexicon), "take")
        self.assertEqual(textutil.lemmatize("thought", lexicon), "think")
        self.assertEqual(textutil.lemmatize("was", lexicon), "be")

    def test_reduces_inflected_forms_even_when_present_in_lexicon(self):
        # ipa-dict lists inflected forms as entries, so the lemmatiser must try
        # candidates before accepting the surface form.
        lexicon = {"begin", "begins", "study", "studies", "environment", "environments",
                   "run", "running", "child", "children"}
        self.assertEqual(textutil.lemmatize("begins", lexicon), "begin")
        self.assertEqual(textutil.lemmatize("studies", lexicon), "study")
        self.assertEqual(textutil.lemmatize("environments", lexicon), "environment")
        self.assertEqual(textutil.lemmatize("running", lexicon), "run")
        self.assertEqual(textutil.lemmatize("children", lexicon), "child")

    def test_derivations_are_not_applied_to_real_words(self):
        # "teacher" is a word, so it must not be reduced to "teach".
        lexicon = {"teach", "teacher", "work", "worker", "small", "smaller", "talk", "talker"}
        self.assertEqual(textutil.lemmatize("teacher", lexicon), "teacher")
        self.assertEqual(textutil.lemmatize("worker", lexicon), "worker")
        self.assertEqual(textutil.lemmatize("smaller", lexicon), "smaller")
        # ...but an unknown surface form may still be reduced
        self.assertEqual(textutil.lemmatize("talkers", {"talk", "talker"}), "talker")

    def test_protected_plurals_are_not_reduced(self):
        lexicon = {"new", "news", "politic", "politics", "physic", "physics", "series", "sery"}
        for word in ["news", "politics", "physics", "series"]:
            self.assertEqual(textutil.lemmatize(word, lexicon), word)

    def test_plural_rules_keep_dictionary_validity(self):
        lexicon = {"class", "classes", "bus", "gas", "study"}
        self.assertEqual(textutil.lemmatize("classes", lexicon), "class")
        self.assertEqual(textutil.lemmatize("bus", lexicon), "bus")
        self.assertEqual(textutil.lemmatize("gas", lexicon), "gas")

    def test_normalize_unicode(self):
        self.assertEqual(textutil.normalize("caf\u00e9\u00a0bar"), "café bar")
        self.assertEqual(textutil.normalize("don\u2019t"), "don't")

    def test_clean_block_keeps_line_structure(self):
        raw = "Chapter\r\n1   自然地理\r\nmagma   n. 岩浆\r\n\r\n"
        self.assertEqual(textutil.clean_block(raw),
                         "Chapter\n1 自然地理\nmagma n. 岩浆")
        # clean_line intentionally collapses everything (single-line input)
        self.assertEqual(textutil.clean_line("a  b"), "a b")


if __name__ == "__main__":
    unittest.main()
