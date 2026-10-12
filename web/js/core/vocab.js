// 词表构建：词条模式（单词书）与全词模式（正文）—— app/vocab.py 的移植版

import { extractEntries, detectEntryMode } from "./entries.js";
import { isNoiseToken, lemmatize, tokens } from "./text.js";

function chapterBase(chapter) {
  return {
    title: chapter.title,
    start_page: chapter.start_page,
    end_page: chapter.end_page,
    source: chapter.source,
    subsections: chapter.subsections || [],
    number: chapter.number ?? null,
    confidence: chapter.confidence || "unknown",
    evidence: chapter.evidence || [],
    alerts: chapter.alerts || [],
  };
}

function extractTokenWords(linesByPage, startPage, endPage, lexicon, skip) {
  const entries = new Map();
  const lowerSeen = new Map();
  const upperSeen = new Map();
  for (let page = startPage; page <= endPage; page += 1) {
    if (skip.has(page)) continue;
    for (const line of linesByPage[page] || []) {
      for (const surface of line.match(/[A-Za-z][A-Za-z'\-]*/g) || []) {
        const lower = surface.toLowerCase();
        if (isNoiseToken(lower)) continue;
        if (surface[0] === surface[0].toUpperCase()) upperSeen.set(lower, (upperSeen.get(lower) || 0) + 1);
        else lowerSeen.set(lower, (lowerSeen.get(lower) || 0) + 1);
        const lemma = lemmatize(lower, lexicon);
        if (!entries.has(lemma)) {
          entries.set(lemma, { lemma, display: lemma, count: 0, in_dict: lexicon.has(lemma),
                               pages: [], forms: {} });
        }
        const entry = entries.get(lemma);
        entry.count += 1;
        entry.forms[lower] = (entry.forms[lower] || 0) + 1;
        if (!entry.pages.includes(page)) entry.pages.push(page);
      }
    }
  }
  const out = [];
  for (const entry of entries.values()) {
    const proper = !entry.in_dict && !lowerSeen.get(entry.lemma) && upperSeen.get(entry.lemma);
    if (proper) continue;
    out.push({
      lemma: entry.lemma, display: entry.display, in_dict: entry.in_dict,
      count: entry.count, pages: entry.pages.sort((a, b) => a - b), forms: entry.forms,
      pos: null, ocr_meaning: null, ordinal: 0, proper: false,
    });
  }
  return out.sort((a, b) => b.count - a.count || a.lemma.localeCompare(b.lemma));
}

function buildEntryVocabulary(linesByPage, chapters, lexicon, skip) {
  const result = [];
  const pageReports = [];
  for (const chapter of chapters) {
    const entries = [];
    const seen = new Set();
    for (let page = chapter.start_page; page <= chapter.end_page; page += 1) {
      if (skip.has(page)) continue;
      const { entries: found, report } = extractEntries(linesByPage[page] || [], page, lexicon);
      pageReports.push(report);
      for (const entry of found) {
        const key = entry.headword.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        entries.push(entry);
      }
    }
    result.push({
      ...chapterBase(chapter),
      extraction_mode: "entries",
      words: entries.map((entry, index) => ({
        lemma: entry.headword.toLowerCase(),
        display: entry.headword,
        in_dict: entry.in_dict,
        count: 1,
        pages: [entry.page],
        forms: { [entry.headword]: 1 },
        pos: entry.pos || null,
        ocr_meaning: entry.hint || null,
        ordinal: index + 1,
        proper: false,
      })),
      word_count: entries.length,
    });
  }
  const covered = pageReports.filter((report) => report.summary);
  const details = {
    pages_scanned: pageReports.length,
    pages_with_entries: pageReports.filter((report) => report.entries).length,
    entries_total: pageReports.reduce((sum, report) => sum + report.entries, 0),
    entries_from_footer: pageReports.reduce((sum, report) => sum + report.filled, 0),
    footer_coverage: covered.length
      ? Number((covered.reduce((sum, report) => sum + (report.coverage || 0), 0) / covered.length).toFixed(3))
      : null,
  };
  return { chapters: result, details };
}

function buildTokenVocabulary(linesByPage, chapters, lexicon, skip) {
  return chapters.map((chapter) => {
    const words = extractTokenWords(linesByPage, chapter.start_page, chapter.end_page, lexicon, skip);
    return { ...chapterBase(chapter), extraction_mode: "tokens", words, word_count: words.length };
  });
}

/** @returns {{chapters: Array, report: Object}} */
export function buildVocabulary(linesByPage, chapters, lexicon, { skipPages = [], mode = "auto" } = {}) {
  const skip = new Set(skipPages);
  let chosen = mode;
  const report = { requested_mode: mode, mode };
  if (mode === "auto") {
    const detected = detectEntryMode(linesByPage, chapters, skip);
    chosen = detected.mode;
    Object.assign(report, detected.probe, { mode: chosen });
  }
  if (chosen === "entries") {
    const { chapters: result, details } = buildEntryVocabulary(linesByPage, chapters, lexicon, skip);
    Object.assign(report, details, { mode: "entries" });
    return { chapters: result, report };
  }
  report.mode = "tokens";
  return { chapters: buildTokenVocabulary(linesByPage, chapters, lexicon, skip), report };
}
