// 纯前端（Netlify）实现：不依赖任何常驻后端。
// PDF 解析 / 章节识别 / 词条提取在浏览器完成，数据存 IndexedDB，
// 只有"释义"和"发音"走 Netlify 上的两个无状态函数（/api/dict、/api/audio）。

import * as store from "./core/store.js";
import * as pipeline from "./core/pipeline.js";
import { lookup as ipaLookup, SHIPPED_STATS } from "./core/ipa.js";
import { exportBook, importBook } from "./core/transfer.js";

async function bookPayload(bookId) {
  const book = await store.getBook(bookId);
  if (!book) throw new Error("单词书不存在");
  const chapters = await store.listChapters(bookId);
  return { ...book, chapters };
}

export const staticApi = {
  mode: "static",

  health: async () => ({
    status: "ok",
    mode: "static (browser)",
    // 不做全量解析：音标表按需加载，这里只报告发布数据规模
    ipa: { ...SHIPPED_STATS, lazy: true },
    // 也不要在启动时去 CDN 拉 OCR 引擎（手机上可能要等很久才超时）：
    // 真正的检测发生在用户勾选「尝试 OCR」时。
    ocr: { available: null, engine: "tesseract.js", reason: "按需加载（勾选 OCR 时检测）" },
    audio_providers: [
      { name: "youdao", note: "有道词典发音接口（真实录音，type=1 英式 / type=2 美式）" },
      { name: "freedict", note: "Free Dictionary API（dictionaryapi.dev，MIT，音频来自维基媒体）" },
    ],
    tts_fallback: { mode: "browser_speech_synthesis",
                   note: "备用方案：仅在系统确有 en-GB / en-US 语音时启用，界面标注为“合成语音”。" },
    data_dir: "浏览器 IndexedDB（不上传 PDF）",
  }),

  // ------------------------------------------------------------ 单词书
  listTextbooks: async () => {
    const books = await store.listBooks();
    const out = [];
    for (const book of books) {
      if (typeof book.word_count === "number") {
        // 直接读计数：首页保持毫秒级
        out.push({
          ...book,
          chapter_count: book.chapter_count || 0,
          word_count: book.word_count || 0,
          mastered_count: book.mastered_count || 0,
          favorite_count: book.favorite_count || 0,
          learned_count: book.learned_count || 0,
        });
        continue;
      }
      // 旧数据（升级前导入的书）：算一次并写入缓存
      const chapters = await store.listChapters(book.id);
      const words = await store.listBookWords(book.id);
      const counters = {
        chapter_count: chapters.length,
        word_count: words.length,
        mastered_count: words.filter((word) => word.mastered).length,
        favorite_count: words.filter((word) => word.favorite).length,
        learned_count: words.filter((word) => word.meaning_cn || word.uk_ipa).length,
      };
      await store.updateBookCounters(book.id, counters);
      out.push({ ...book, ...counters });
    }
    return { textbooks: out };
  },

  getTextbook: bookPayload,

  preview: (id) => pipeline.preview(id),

  textbookStatus: async (id) => {
    const book = await store.getBook(id);
    if (!book) throw new Error("单词书不存在");
    const progress = pipeline.getProgress(id) || {};
    return { id: book.id, title: book.title, status: book.status,
             page_count: book.page_count || 0, progress,
             // needs_ocr 也是终态：需要用户操作，不能再让首页一直等下去
             ready: ["parsed", "needs_review", "needs_ocr"].includes(book.status) };
  },

  renameTextbook: async (id, title) => store.updateBook(id, { title }),

  deleteTextbook: (id) => store.deleteBook(id),

  exportTextbook: (id) => exportBook(id),

  importTextbook: (payload) => importBook(payload),

  applyChapters: (id, chapters) => pipeline.applyChapters(id, chapters),

  redetectChapters: (id) => pipeline.redetectChapters(id),

  reparse: async (id, useOcr) => {
    const book = await store.getBook(id);
    if (!book || !book.file_blob) {
      throw new Error("浏览器版没有保留 PDF 原件（大文件不缓存）。请重新上传该 PDF，并勾选「尝试 OCR」。");
    }
    await store.updateBook(id, { status: "parsing" });
    await pipeline.createBookFromFile(book.file_blob, { title: book.title, useOcr });
    return { textbook_id: id, status: "parsing" };
  },

  upload: async (file, { title, useOcr, ocrMaxPages = 20 } = {}) => {
    const blob = file.size <= 30 * 1024 * 1024 ? file : null;   // 小文件留原件，方便重新 OCR
    const book = await store.createBook({ title: (title || file.name).trim(), filename: file.name,
                                          status: "parsing", file_blob: blob });
    pipeline.createBookFromFile(file, { title: book.title, useOcr, ocrMaxPages, bookId: book.id })
      .catch(() => { /* 失败状态已写入 store */ });
    return { textbook_id: book.id, status: "parsing", message: "已开始解析，请稍候…" };
  },

  // ------------------------------------------------------------ 章节 / 词条
  getChapter: async (chapterId) => {
    const chapter = await store.getChapterRow(chapterId);
    if (!chapter) throw new Error("章节不存在");
    const textbook = await store.getBook(chapter.book_id);
    const words = await store.getChapterWords(chapterId);
    return { ...chapter, textbook, words };
  },

  addWord: async (chapterId, word) => {
    const chapter = await store.getChapterRow(chapterId);
    if (!chapter) throw new Error("章节不存在");
    return store.addWordToChapter(chapter.book_id, chapterId, word);
  },

  removeWord: (chapterId, wordId) => store.removeWordFromChapter(chapterId, wordId),

  listWords: async (bookId, params = {}) => {
    let words = await store.listBookWords(bookId);
    if (params.q) words = words.filter((word) => word.lemma.includes(String(params.q).toLowerCase())
      || (word.meaning_cn || "").includes(params.q));
    if (params.favorite === "1") words = words.filter((word) => word.favorite);
    if (params.mastered !== undefined && params.mastered !== "") {
      words = words.filter((word) => word.mastered === (params.mastered === "1"));
    }
    return { words };
  },

  getWord: async (id) => {
    const word = await store.getWord(id);
    if (!word) throw new Error("词条不存在");
    return store.wordApi(word, { chapters: [] });
  },

  updateWord: async (id, patch) => {
    const before = await store.getWord(id);
    const updated = await store.updateWord(id, patch);
    // 收藏 / 掌握数量随操作增量维护，避免首页重新全量统计
    const delta = {};
    if ("favorite" in patch) delta.favorite_count = (patch.favorite ? 1 : 0) - (before.favorite ? 1 : 0);
    if ("mastered" in patch) delta.mastered_count = (patch.mastered ? 1 : 0) - (before.mastered ? 1 : 0);
    if (Object.keys(delta).length && updated.book_id) {
      await store.adjustBookCounters(updated.book_id, delta);
    }
    return store.wordApi(updated);
  },

  enrich: async (wordIds, force = false) => ({ words: await pipeline.enrichWords(wordIds, { force }) }),

  // ------------------------------------------------------------ 发音
  probeAudio: async (wordIds, accents = ["uk", "us"]) => (
    { results: await pipeline.probeAudio(wordIds, accents) }
  ),

  audioSources: async () => ({
    providers: [
      { name: "youdao", note: "有道词典发音接口" },
      { name: "freedict", note: "Free Dictionary API（dictionaryapi.dev）" },
    ],
    policy: "英式(type=1/-uk)与美式(type=2/-us)分别获取并验证；缺失时明确标记，不互相替代。",
    cache_dir: "浏览器 HTTP 缓存 + 站点 CDN 缓存",
  }),

  // ------------------------------------------------------------ 其它
  chapterRange: (chapterId, payload) => pipeline.updateChapterRange(chapterId, payload),

  stats: (bookId) => pipeline.stats(bookId),

  lookup: async (word) => {
    const info = await pipeline.fetchDict(word);
    if (!info) throw new Error("词典查询失败（站点未部署函数且直连词典不可用）");
    const local = await ipaLookup(word);
    return {
      word,
      meaning_cn: info.meaning_cn,
      pos: info.pos,
      uk_ipa: (local.uk && local.uk[0]) || info.uk_ipa,
      us_ipa: (local.us && local.us[0]) || info.us_ipa,
      definitions_en: info.definitions_en || [],
      sources: { meaning: info.meaning_source, ipa: (local.uk || local.us) ? "ipa-dict" : info.ipa_source },
      error: null,
    };
  },
};
