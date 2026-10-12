// 浏览器端流水线：上传 → 解析 → 章节识别 → 词表 → 释义/发音（全部本地或走轻量代理）

import * as store from "./store.js";
import { extractPdf } from "./pdftext.js";
import { detectChapters, buildPageTexts, evaluateChapters, collectEvidence,
         detectTocPages, backMatterStart, unassignedPages } from "./chapters.js";
import { buildVocabulary } from "./vocab.js";
import { lexicon as ipaLexicon, lookup as ipaLookup } from "./ipa.js";

const progress = new Map();
const setProgress = (bookId, patch) => progress.set(bookId, { ...(progress.get(bookId) || {}), ...patch, updated_at: Date.now() });
export const getProgress = (bookId) => progress.get(bookId) || null;

const SKIP_STATUS = new Set(["parsed", "needs_review"]);

async function linesByPage(book) {
  const out = {};
  for (const [page, lines] of Object.entries(book.raw_lines || {})) out[Number(page)] = lines;
  return out;
}

/** 上传 + 解析 + 章节识别（保存原始行，供后续"重新识别章节"用，无需重新解析 PDF） */
export async function createBookFromFile(file, { title, useOcr = false, onProgress, bookId, ocrMaxPages = 20 } = {}) {
  const book = bookId
    ? await store.getBook(bookId)
    : await store.createBook({
        title: (title || file.name.replace(/\.pdf$/i, "")).trim(),
        filename: file.name,
        status: "parsing",
      });
  if (!book) throw new Error("单词书不存在");
  setProgress(book.id, { stage: "start", percent: 2, message: "开始解析…" });
  try {
    const parsed = await extractPdf(file, {
      useOcr,
      ocrMaxPages,
      onProgress: (done, total, label) => {
        const percent = Math.round((done / Math.max(1, total)) * 80) + 5;
        setProgress(book.id, { stage: useOcr ? "ocr" : "text", percent,
                               message: `${label}（${done}/${total} 页）` });
        if (onProgress) onProgress(done, total, label);
      },
    });
    const noTextAtAll = parsed.page_count > 0 && parsed.pages.every((page) => page.char_count < 25);
    if (noTextAtAll) {
      // 扫描版且没有任何 OCR 结果：给出可操作的提示，而不是静默产出"0 个单词"
      await store.updateBook(book.id, {
        status: "needs_ocr", page_count: parsed.page_count, scanned: true,
        text_ratio: 0, pre_ocr_text_ratio: parsed.pre_ocr_text_ratio,
        ocr_page_count: parsed.ocr_page_count, warnings: parsed.warnings, ocr: parsed.ocr,
        report: { strategy: null, toc_pages: [], unassigned_pages: [] },
      });
      setProgress(book.id, { stage: "needs_ocr", percent: 100,
                             message: "这是扫描版 PDF：需要开启 OCR 才能识别文字" });
      return preview(book.id);
    }
    setProgress(book.id, { stage: "chapters", percent: 88, message: "识别章节…" });
    const { chapters, report } = detectChapters(parsed.pages, parsed.outline, parsed.page_offset);
    report.unassigned_pages = unassignedPages(chapters, parsed.page_count, report.toc_pages);
    // 与 Python 版一致：识别出的章节先落库，预览页据此展示与编辑
    await store.replaceChapters(book.id, chapters);

    const rawLines = Object.fromEntries(parsed.pages.map((page) => [page.number, page.lines.map((line) => line.text)]));
    const printedPages = Object.fromEntries(parsed.pages.filter((page) => page.printed_page)
      .map((page) => [page.number, page.printed_page]));
    await store.updateBook(book.id, {
      status: "needs_review",
      page_count: parsed.page_count,
      scanned: parsed.scanned,
      text_ratio: parsed.text_ratio,
      pre_ocr_text_ratio: parsed.pre_ocr_text_ratio,
      ocr_page_count: parsed.ocr_page_count,
      page_offset: parsed.page_offset,
      warnings: parsed.warnings,
      report,
      raw_lines: rawLines,
      printed_pages: printedPages,
      outline: parsed.outline,
      ocr: parsed.ocr,
    });
    setProgress(book.id, { stage: "done", percent: 100, message: "解析完成" });
    return preview(book.id);
  } catch (err) {
    await store.updateBook(book.id, { status: "failed" });
    setProgress(book.id, { stage: "failed", percent: 100, message: `解析失败：${err.message}` });
    throw err;
  }
}

/** 预览负载：与后端 /preview 返回结构一致 */
export async function preview(bookId) {
  const book = await store.getBook(bookId);
  if (!book) throw new Error("单词书不存在");
  let chapters = await store.listChapters(bookId);
  const report = book.report || {};
  const tocPages = new Set(report.toc_pages || []);
  const { cleaned } = buildPageTexts(rehydratePages(book), tocPages);
  const lines = Object.fromEntries(cleaned);
  const lexicon = await ipaLexicon();
  const skip = new Set([...tocPages]);
  if (report.back_matter_start) {
    for (let page = report.back_matter_start; page <= book.page_count; page += 1) skip.add(page);
  }
  const { chapters: vocabulary, report: vocabularyReport } = buildVocabulary(
    lines, chapters, lexicon, { skipPages: skip },
  );

  // 把词表统计回填到章节（预览页要显示"词条 N 个"）
  chapters = chapters.map((chapter, index) => {
    const words = vocabulary[index] ? vocabulary[index].words : [];
    return { ...chapter, words, word_count: words.length,
             extraction_mode: vocabulary[index] ? vocabulary[index].extraction_mode : null };
  });
  return {
    textbook: book,
    page_count: book.page_count || 0,
    scanned: book.scanned || false,
    text_ratio: book.text_ratio || 1,
    pre_ocr_text_ratio: book.pre_ocr_text_ratio ?? 1,
    ocr_page_count: book.ocr_page_count || 0,
    warnings: book.warnings || [],
    report,
    vocabulary_report: vocabularyReport,
    ocr: book.ocr || { available: false, reason: "" },
    chapters,
    outline: book.outline || [],
  };
}

function rehydratePages(book) {
  const pages = [];
  for (let number = 1; number <= (book.page_count || 0); number += 1) {
    const raw = (book.raw_lines || {})[number] || [];
    pages.push({
      number,
      lines: raw.map((text) => ({ text, size: 0, top: 0 })),
      printed_page: (book.printed_pages || {})[number] || null,
    });
  }
  return pages;
}

/** 用户确认章节 → 生成正式词表（写入 words/links） */
export async function applyChapters(bookId, chapterList) {
  const book = await store.getBook(bookId);
  if (!book) throw new Error("单词书不存在");
  const pages = rehydratePages(book);
  const report = book.report || {};
  const tocPages = new Set(report.toc_pages || []);
  const evidence = collectEvidence(pages, tocPages, report.toc_entries || {});

  const chapters = chapterList.map((chapter, index) => ({
    title: (chapter.title || `第 ${index + 1} 章`).trim(),
    start_page: Math.max(1, Number(chapter.start_page) || 1),
    end_page: Math.min(book.page_count || 1, Number(chapter.end_page) || 1),
    source: chapter.source || "manual",
    number: chapter.number ?? null,
    confidence: chapter.confidence || "unknown",
    evidence: chapter.evidence || [],
    alerts: chapter.alerts || [],
    subsections: chapter.subsections || [],
  }));
  chapters.forEach((chapter) => { if (chapter.end_page < chapter.start_page) chapter.end_page = chapter.start_page; });
  evaluateChapters(pages, chapters, evidence, report.back_matter_start,
                   { allowSnap: false, allowAdditions: false });

  const { cleaned } = buildPageTexts(pages, tocPages);
  const lines = Object.fromEntries(cleaned);
  const lexicon = await ipaLexicon();
  const skip = new Set([...tocPages]);
  if (report.back_matter_start) {
    for (let page = report.back_matter_start; page <= book.page_count; page += 1) skip.add(page);
  }
  const { chapters: vocabulary, report: vocabularyReport } = buildVocabulary(lines, chapters, lexicon, { skipPages: skip });

  const stored = await store.replaceChapters(bookId, chapters.map((chapter, index) => ({
    ...chapter,
    extraction_mode: vocabulary[index] ? vocabulary[index].extraction_mode : vocabularyReport.mode,
  })));

  for (let index = 0; index < stored.length; index += 1) {
    const chapter = stored[index];
    const words = (vocabulary[index] || {}).words || [];
    const links = [];
    for (const word of words) {
      const row = await store.upsertWord(bookId, {
        lemma: word.lemma,
        display: word.display,
        in_dict: word.in_dict ? 1 : 0,
        pos: word.pos || null,
        ocr_meaning: word.ocr_meaning || null,
      });
      links.push({ word_id: row.id, count: word.count, pages: word.pages, forms: word.forms, ordinal: word.ordinal });
    }
    await store.replaceLinks(chapter.id, links);
    if (!words.length) chapter.alerts = [...(chapter.alerts || []), "本章未识别到词条：可能是附录/索引，或未识别出内容"];
  }
  await store.updateBook(bookId, { status: "parsed", vocabulary_report: vocabularyReport, report });
  const allWords = await store.listBookWords(bookId);
  await store.updateBookCounters(bookId, {
    chapter_count: chapters.length,
    word_count: allWords.length,
    learned_count: allWords.filter((word) => word.meaning_cn || word.uk_ipa).length,
    mastered_count: allWords.filter((word) => word.mastered).length,
    favorite_count: allWords.filter((word) => word.favorite).length,
  });
  const book2 = await store.getBook(bookId);
  return { textbook: { ...book2, chapters: await store.listChapters(bookId) },
           stats: { chapters: chapters.length,
                    unique_words: new Set(vocabulary.flatMap((chapter) => chapter.words.map((word) => word.lemma))).size },
           vocabulary_report: vocabularyReport };
}

/** 用已保存的原始文本重新识别章节（不重新解析 PDF） */
export async function redetectChapters(bookId) {
  const book = await store.getBook(bookId);
  if (!book) throw new Error("单词书不存在");
  const pages = rehydratePages(book);
  const { chapters, report } = detectChapters(pages, book.outline || [], book.page_offset || 0);
  report.unassigned_pages = unassignedPages(chapters, book.page_count, report.toc_pages);
  await store.updateBook(bookId, { report });
  return applyChapters(bookId, chapters.map((chapter) => ({
    title: chapter.title, start_page: chapter.start_page, end_page: chapter.end_page,
    source: chapter.source, number: chapter.number, confidence: chapter.confidence,
    evidence: chapter.evidence, alerts: chapter.alerts, subsections: chapter.subsections,
  })));
}

/** 手动指定某一章的页码范围，只重新计算该范围 */
export async function updateChapterRange(chapterId, { start_page, end_page, title }) {
  const chapter = await store.getChapterRow(chapterId);
  if (!chapter) throw new Error("章节不存在");
  const book = await store.getBook(chapter.book_id);
  const others = (await store.listChapters(book.id)).filter((item) => item.id !== chapterId);
  const siblings = [...others, { ...chapter, start_page: Number(start_page), end_page: Number(end_page),
                                 source: "manual", confidence: "manual", evidence: ["用户手动指定页码范围"],
                                 title: title || chapter.title }]
    .sort((a, b) => a.start_page - b.start_page);
  const index = siblings.findIndex((item) => item.id === chapterId);
  if (index > 0) siblings[index - 1].end_page = Math.max(siblings[index - 1].start_page, siblings[index].start_page - 1);
  if (index + 1 < siblings.length) siblings[index + 1].start_page = Math.min(siblings[index + 1].end_page, siblings[index].end_page + 1);
  const result = await applyChapters(book.id, siblings);
  return { chapter: (result.textbook.chapters || []).find((item) => item.title === siblings[index].title && item.start_page === siblings[index].start_page)
      || result.textbook.chapters[index], textbook_id: book.id };
}

/** 批量补释义/音标（走 /api/dict 代理，带并发与本地缓存） */
export async function enrichWords(wordIds, { force = false, concurrency = 4 } = {}) {
  const out = [];
  const queue = [...wordIds];
  const worker = async () => {
    while (queue.length) {
      const id = queue.shift();
      const word = await store.getWord(id);
      if (!word) continue;
      if (!force && (word.meaning_cn || word.uk_ipa)) { out.push(store.wordApi(word)); continue; }
      const info = await fetchDict(word.lemma);
      const local = await ipaLookup(word.lemma);
      const uk = (local.uk && local.uk[0]) || (info && info.uk_ipa) || null;
      const us = (local.us && local.us[0]) || (info && info.us_ipa) || null;
      const updated = await store.updateWord(id, {
        uk_ipa: uk,
        us_ipa: us,
        ipa_source: (local.uk || local.us) ? "ipa-dict" : (info && info.ipa_source) || null,
        meaning_cn: (info && info.meaning_cn) || word.meaning_cn || null,
        pos: (info && info.pos) || word.pos || null,
        meaning_source: (info && info.meaning_source) || word.meaning_source || null,
        enriched_at: new Date().toISOString(),
      });
      out.push(store.wordApi(updated));
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, queue.length || 1) }, worker));
  return out;
}

/**
 * 释义查询：优先走站内代理（/api/dict，可以有中文释义）；
 * 如果站点没有部署函数（例如只拖了静态文件），退化为浏览器直连
 * dictionaryapi.dev（它允许跨域，但只有英文释义）。
 */
export async function fetchDict(lemma) {
  try {
    const res = await fetch(`api/dict?word=${encodeURIComponent(lemma)}`);
    if (res.ok) {
      const data = await res.json();
      if (data && (data.meaning_cn || data.uk_ipa || data.us_ipa)) return data;
    }
  } catch { /* 继续退化 */ }
  try {
    const res = await fetch(`https://api.dictionaryapi.dev/api/v2/entries/en/${encodeURIComponent(lemma)}`);
    if (!res.ok) return null;
    const entries = await res.json();
    const out = { word: lemma, meaning_cn: null, pos: null, uk_ipa: null, us_ipa: null,
                  definitions_en: [], meaning_source: null, ipa_source: null };
    for (const entry of (Array.isArray(entries) ? entries : []).slice(0, 3)) {
      for (const meaning of (entry.meanings || []).slice(0, 3)) {
        for (const definition of (meaning.definitions || []).slice(0, 2)) {
          const text = String(definition.definition || "").trim();
          if (text) out.definitions_en.push(meaning.partOfSpeech ? `(${meaning.partOfSpeech}) ${text}` : text);
        }
      }
      for (const phonetic of entry.phonetics || []) {
        const audio = String(phonetic.audio || "").toLowerCase();
        const text = String(phonetic.text || "").replace(/^\/|\/$/g, "").trim();
        if (!text) continue;
        if (audio.includes("-uk") && !out.uk_ipa) { out.uk_ipa = text; out.ipa_source = "dictionaryapi"; }
        else if (audio.includes("-us") && !out.us_ipa) { out.us_ipa = text; out.ipa_source = "dictionaryapi"; }
      }
    }
    out.meaning_cn = out.definitions_en.slice(0, 2).join(" / ") || null;
    if (out.definitions_en.length) out.meaning_source = "dictionaryapi(en)";
    return out;
  } catch {
    return null;
  }
}

/** 校验某个词的英美发音是否真的存在（只发轻量探测请求） */
export async function probeAudio(wordIds, accents = ["uk", "us"], concurrency = 3) {
  const results = [];
  const queue = [...wordIds];
  const worker = async () => {
    while (queue.length) {
      const id = queue.shift();
      const word = await store.getWord(id);
      if (!word) continue;
      const entry = { word_id: id, lemma: word.lemma, accents: {} };
      for (const accent of accents) {
        let status = { available: false, retryable: true, provider: null, error: null };
        try {
          const res = await fetch(`api/audio?word=${encodeURIComponent(word.lemma)}&accent=${accent}`,
                                  { headers: { Range: "bytes=0-1" } });
          if (res.ok || res.status === 206) {
            status = { available: true, retryable: false, provider: res.headers.get("X-Audio-Provider"), error: null };
          } else {
            let payload = {};
            try { payload = await res.json(); } catch { /* ignore */ }
            status = { available: false, retryable: Boolean(payload.retryable),
                       provider: null, error: payload.detail || `HTTP ${res.status}` };
          }
        } catch (err) {
          status = { available: false, retryable: true, provider: null, error: err.message };
        }
        entry.accents[accent] = status;
        await store.setAudioStatus(id, accent, {
          available: status.available,
          missing: !status.available && !status.retryable,
          provider: status.provider,
        });
      }
      results.push(entry);
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, queue.length || 1) }, worker));
  return results;
}

export async function stats(bookId) {
  const books = bookId ? [await store.getBook(bookId)].filter(Boolean) : await store.listBooks();
  const bookList = [];
  let totals = { total: 0, learned: 0, mastered: 0, favorite: 0, uk_audio: 0, us_audio: 0 };
  const unmastered = [];
  for (const book of books) {
    const chapters = await store.listChapters(book.id);
    const words = await store.listBookWords(book.id);
    bookList.push({
      id: book.id, title: book.title, page_count: book.page_count || 0,
      chapter_count: chapters.length, word_count: words.length,
      mastered_count: words.filter((word) => word.mastered).length,
      favorite_count: words.filter((word) => word.favorite).length,
    });
    totals.total += words.length;
    totals.learned += words.filter((word) => word.meaning_cn || word.uk_ipa).length;
    totals.mastered += words.filter((word) => word.mastered).length;
    totals.favorite += words.filter((word) => word.favorite).length;
    totals.uk_audio += words.filter((word) => word.uk_audio_status === "available").length;
    totals.us_audio += words.filter((word) => word.us_audio_status === "available").length;
    for (const word of words) {
      if (!word.mastered && unmastered.length < 30) {
        unmastered.push({ id: word.id, lemma: word.lemma, meaning_cn: word.meaning_cn,
                          uk_ipa: word.uk_ipa, us_ipa: word.us_ipa });
      }
    }
  }
  return { books: books.length, book_list: bookList, words: totals, unmastered };
}
