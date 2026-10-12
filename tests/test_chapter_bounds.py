"""章节识别验收测试（对应需求 五、测试与验收 的 6 项检查）。

使用一本合成的《单词书》结构：

* p1  目录页（Chapter 1–3 + 印刷页码 001/004/007）
* p2  Chapter 1 首页（Chapter / 1 / 自然地理 + 词条）
* p3  普通词汇页（词条 + 页脚 002 + Chapter 1）
* p4  Chapter 1 最后一页（页脚 003）
* p5  Chapter 2 首页（Chapter / 2 / 人类与自然 + 词条）
* p6  Chapter 2 普通词汇页
* p7  Chapter 2 最后一页
* p8  Chapter 3 首页
* p9  Chapter 3 普通词汇页
* p10 Chapter 3 最后一页
"""

import unittest

from app import ipa
from app.chapters import build_page_texts, detect_chapters
from app.pdf_parser import Line, Page, ParsedPdf
from app.vocab import build_vocabulary_ex

LEXICON = ipa.lexicon()


def page(number: int, lines: list[str]) -> Page:
    return Page(
        number=number,
        lines=[Line(text=text, size=0.0, top=0.0) for text in lines],
        text="\n".join(lines),
        char_count=sum(len(text) for text in lines),
        has_text=True,
    )


def build_book() -> ParsedPdf:
    pages = [
        page(1, ["Contents", "Chapter 1", "Chapter 2", "Chapter 3",
                 "001", "004", "007"]),
        page(2, ["Chapter", "1", "自然地理",
                 "atmosphere /'ætmosfio/", "n. 大气", "hydrosphere /'haidrosfio/", "n. 水圈"]),
        page(3, ["lithosphere /'liθosfio/", "n. 岩石圈", "oxide /'oksaid/", "n. 氧化物",
                 "hydrosphere /'haidrosfio/", "n. 水圈",
                 "hydrogen /'haidrodʒon/", "n. 氢",
                 "002", "Chapter 1"]),
        page(4, ["carbon /'ka:bon/", "n. 碳", "dioxide /dai'oksaid/", "n. 二氧化物",
                 "003", "Chapter 1"]),
        page(5, ["Chapter", "2", "人类与自然",
                 "species /'spi:ʃi:z/", "n. 物种", "evolve /i'volv/", "v. 进化"]),
        page(6, ["extinct /ik'stiŋkt/", "adj. 灭绝的", "habitat /'hæbitæt/", "n. 栖息地",
                 "005", "Chapter 2"]),
        page(7, ["ecology /i'kolodʒi/", "n. 生态学", "006", "Chapter 2"]),
        page(8, ["Chapter", "3", "科技",
                 "technology /tek'nolodʒi/", "n. 技术"]),
        page(9, ["engineer /,endʒi'niə/", "n. 工程师", "mechanic /mi'kænik/", "n. 机械师",
                 "008", "Chapter 3"]),
        page(10, ["software /'softweə/", "n. 软件", "009", "Chapter 3"]),
    ]
    return ParsedPdf(path="synthetic.pdf", page_count=10, pages=pages, outline=[],
                     scanned=True, text_ratio=1.0)


class TestChapterBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = build_book()
        cls.chapters, cls.report = detect_chapters(cls.parsed)
        cleaned = build_page_texts(cls.parsed, cls.chapters)["cleaned_lines"]
        cls.vocabulary, cls.vocabulary_report = build_vocabulary_ex(
            cleaned, cls.chapters, LEXICON,
            skip_pages=set(cls.report.get("toc_pages") or []),
        )
        cls.cleaned = cleaned
        cls.lemmas = [
            [word["lemma"] for word in chapter["words"]] for chapter in cls.vocabulary
        ]

    # 1. 章节首页是否正确识别 ---------------------------------------------
    def test_01_chapter_first_pages_are_detected(self):
        self.assertEqual(self.report["strategy"], "toc")
        self.assertEqual([c.start_page for c in self.chapters], [2, 5, 8])
        self.assertEqual([c.number for c in self.chapters], [1, 2, 3])
        # 页首的 Chapter 标识使边界达到最高置信度
        self.assertTrue(all(c.confidence == "high" for c in self.chapters),
                        [(c.title, c.confidence, c.evidence) for c in self.chapters])
        self.assertTrue(
            any("页首有正文标识 Chapter 1" in note for note in self.chapters[0].evidence),
            self.chapters[0].evidence,
        )

    # 2. 普通词汇页是否继承正确的章节归属 ---------------------------------
    def test_02_content_pages_inherit_their_chapter(self):
        chapter_one = self.lemmas[0]
        # p3 是普通词汇页，其词条属于 Chapter 1
        for lemma in ["lithosphere", "oxide", "hydrogen"]:
            self.assertIn(lemma, chapter_one)
        page_of = {word["lemma"]: word["pages"] for word in self.vocabulary[0]["words"]}
        self.assertEqual(page_of["lithosphere"], [3])
        self.assertEqual(page_of["oxide"], [3])
        # 页脚重复出现的 Chapter 1 不会被当成新的章节
        self.assertEqual(len(self.chapters), 3)
        # 首页标题由 "Chapter" / "1" / "自然地理" 三行重组而成
        self.assertEqual(self.vocabulary[0]["title"], "Chapter 1 自然地理")

    # 3. 上一章最后一页不应被划入下一章 -----------------------------------
    def test_03_previous_chapter_keeps_its_last_page(self):
        self.assertEqual(self.chapters[0].end_page, 4)
        self.assertEqual(self.chapters[1].start_page, 5)
        self.assertIn("carbon", self.lemmas[0])
        self.assertNotIn("carbon", self.lemmas[1])

    # 4. 下一章第一页应及时切换章节 ---------------------------------------
    def test_04_next_chapter_starts_immediately(self):
        self.assertEqual(self.chapters[1].start_page, 5)
        self.assertIn("species", self.lemmas[1])
        self.assertIn("evolve", self.lemmas[1])
        # 首页词条不会落到上一章
        self.assertNotIn("species", self.lemmas[0])
        self.assertEqual([c.start_page for c in self.chapters], [2, 5, 8])
        self.assertEqual([c.end_page for c in self.chapters], [4, 7, 10])

    # 5. 页码 / 章节编号 / 词条编号要区分开 --------------------------------
    def test_05_page_numbers_chapter_numbers_and_ordinals_are_distinct(self):
        # 页脚页码不会进入词表
        for unwanted in ["002", "003", "005", "006", "008", "009", "001"]:
            self.assertNotIn(unwanted, self.cleaned[3])
        all_words = [lemma for words in self.lemmas for lemma in words]
        self.assertNotIn("chapter", all_words)
        self.assertFalse([w for w in all_words if w.isdigit()])
        # 目录页的页码用于换算章节起始页，而不是成为词条
        self.assertEqual(self.report["page_offset"], 1)
        self.assertIn(1, self.report["toc_pages"])
        self.assertEqual(self.vocabulary[0]["words"][0]["pages"], [2])
        # 词条编号在每章内从 1 开始，且按原书顺序
        for chapter in self.vocabulary:
            ordinals = [word["ordinal"] for word in chapter["words"]]
            self.assertEqual(ordinals, list(range(1, len(ordinals) + 1)))
        self.assertEqual(
            [w["lemma"] for w in self.vocabulary[1]["words"]][:2], ["species", "evolve"]
        )

    # 6. 每个词条可追溯到原书页面 ------------------------------------------
    def test_06_every_entry_is_traceable_to_a_page(self):
        for chapter in self.vocabulary:
            for word in chapter["words"]:
                self.assertTrue(word["pages"], word)
                page = word["pages"][0]
                self.assertGreaterEqual(page, chapter["start_page"])
                self.assertLessEqual(page, chapter["end_page"])
        # 具体页面映射
        mapping = {w["lemma"]: w["pages"][0] for w in self.vocabulary[2]["words"]}
        self.assertEqual(mapping["technology"], 8)
        self.assertEqual(mapping["engineer"], 9)
        self.assertEqual(mapping["software"], 10)


if __name__ == "__main__":
    unittest.main()
