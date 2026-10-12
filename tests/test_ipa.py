import unittest

from app import ipa


class TestIpaDict(unittest.TestCase):
    def test_dataset_loaded(self):
        stats = ipa.stats()
        self.assertGreater(stats["uk_entries"], 50000)
        self.assertGreater(stats["us_entries"], 100000)

    def test_known_pronunciations(self):
        result = ipa.lookup("schedule")
        self.assertIn("ʃ", result["uk"][0])       # British /ˈʃedjuːl/
        self.assertIn("sk", result["us"][0])      # American /ˈskedʒuːl/
        self.assertNotEqual(result["uk"][0], result["us"][0])

    def test_uk_and_us_differ_for_some_words(self):
        uk = ipa.primary("water", "uk")
        us = ipa.primary("water", "us")
        self.assertTrue(uk and us)
        self.assertNotEqual(uk, us)

    def test_multiple_pronunciations_are_preserved(self):
        values = ipa.lookup("tomato")["us"]
        self.assertGreaterEqual(len(values), 2)

    def test_unknown_word_returns_empty(self):
        self.assertEqual(ipa.lookup("zzzqqqxyz"), {})

    def test_lexicon_is_a_superset(self):
        self.assertIn("environment", ipa.lexicon())
        self.assertNotIn("asdkjfhqwer", ipa.lexicon())


if __name__ == "__main__":
    unittest.main()
