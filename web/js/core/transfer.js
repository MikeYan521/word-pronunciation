// 词表导出 / 导入：把电脑上解析好的书搬到手机上学习。
// 格式与 Python 版 `library.export_textbook()` 完全一致（wordbook-export/1），
// 因此两个版本之间可以互相导入导出。

import * as store from "./store.js";

export const EXPORT_FORMAT = "wordbook-export/1";

export async function exportBook(bookId) {
  const book = await store.getBook(bookId);
  if (!book) throw new Error("单词书不存在");
  const chapters = await store.listChapters(bookId);
  const words = [];
  const positionById = new Map(chapters.map((chapter) => [chapter.id, chapter.position]));
  for (const chapter of chapters) {
    const rows = await store.getChapterWords(chapter.id);
    for (const word of rows) {
      words.push({
        chapter_position: positionById.get(chapter.id) || 0,
        lemma: word.lemma,
        display: word.display,
        in_dict: word.in_dict,
        uk_ipa: word.uk_ipa || null,
        us_ipa: word.us_ipa || null,
        ipa_source: word.ipa_source || null,
        meaning_cn: word.meaning_cn || null,
        pos: word.pos || null,
        ocr_meaning: word.ocr_meaning || null,
        meaning_source: word.meaning_source || null,
        favorite: Boolean(word.favorite),
        mastered: Boolean(word.mastered),
        count: word.chapter_count || 1,
        pages: word.chapter_pages || [],
        forms: word.chapter_forms || {},
        ordinal: word.chapter_ordinal || 0,
      });
    }
  }
  return {
    format: EXPORT_FORMAT,
    exported_at: new Date().toISOString().slice(0, 19).replace("T", " "),
    source: "browser",
    book: { title: book.title, filename: book.filename || "", page_count: book.page_count || 0,
            scanned: Boolean(book.scanned) },
    chapters: chapters.map((chapter) => ({
      position: chapter.position, title: chapter.title,
      start_page: chapter.start_page, end_page: chapter.end_page,
      number: chapter.number ?? null, confidence: chapter.confidence || "unknown",
      evidence: chapter.evidence || [], alerts: chapter.alerts || [],
      extraction_mode: chapter.extraction_mode || null,
    })),
    words,
  };
}

export async function importBook(payload) {
  if (!payload || payload.format !== EXPORT_FORMAT) {
    throw new Error("不是有效的词表文件（缺少 wordbook-export/1 标记）");
  }
  const meta = payload.book || {};
  const book = await store.createBook({
    title: (meta.title || "导入的词表").trim(),
    filename: meta.filename || "imported.json",
    page_count: Number(meta.page_count) || 0,
    scanned: Boolean(meta.scanned),
    status: "parsed",
  });
  const chapters = await store.replaceChapters(book.id, (payload.chapters || []).map((chapter) => ({
    title: chapter.title, start_page: chapter.start_page, end_page: chapter.end_page,
    source: "imported", number: chapter.number ?? null,
    confidence: chapter.confidence || "unknown",
    evidence: chapter.evidence || [], alerts: chapter.alerts || [],
    extraction_mode: chapter.extraction_mode || null,
  })));
  const positionToId = new Map(chapters.map((chapter, index) => [
    (payload.chapters || [])[index] ? (payload.chapters[index].position ?? index) : index,
    chapter.id,
  ]));
  const links = new Map();
  const seen = new Map();
  for (const word of payload.words || []) {
    const lemma = String(word.lemma || word.display || "").trim().toLowerCase();
    if (!lemma) continue;
    let row = seen.get(lemma);
    if (!row) {
      row = await store.upsertWord(book.id, {
        lemma,
        display: word.display || lemma,
        in_dict: word.in_dict === false ? 0 : 1,
        uk_ipa: word.uk_ipa || null,
        us_ipa: word.us_ipa || null,
        ipa_source: word.ipa_source || null,
        meaning_cn: word.meaning_cn || null,
        pos: word.pos || null,
        ocr_meaning: word.ocr_meaning || null,
        meaning_source: word.meaning_source || null,
        favorite: word.favorite ? 1 : 0,
        mastered: word.mastered ? 1 : 0,
      });
      seen.set(lemma, row);
    }
    const chapterId = positionToId.get(Number(word.chapter_position || 0));
    if (chapterId === undefined) continue;
    if (!links.has(chapterId)) links.set(chapterId, []);
    links.get(chapterId).push({
      word_id: row.id,
      count: Number(word.count) || 1,
      pages: word.pages || [],
      forms: word.forms || {},
      ordinal: Number(word.ordinal) || 0,
    });
  }
  for (const [chapterId, rows] of links) await store.replaceLinks(chapterId, rows);
  const allWords = await store.listBookWords(book.id);
  await store.updateBookCounters(book.id, {
    chapter_count: chapters.length,
    word_count: allWords.length,
    learned_count: allWords.filter((word) => word.meaning_cn || word.uk_ipa).length,
    mastered_count: allWords.filter((word) => word.mastered).length,
    favorite_count: allWords.filter((word) => word.favorite).length,
  });
  return { textbook: { ...book, chapters: await store.listChapters(book.id) }, imported: true };
}
