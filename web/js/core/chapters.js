// 章节识别：书签 / 目录页+印刷页码 / 页首标识 / 兜底划分 + 置信度评估
// —— app/chapters.py 的浏览器移植版（规则与输出字段保持一致）

const CHAPTER_RE = /^\s*(?:chapter|unit|lesson|module|section|part|第\s*[0-9一二三四五六七八九十]+\s*[章单元课])\s*([0-9]{1,3}|[ivxlcdm]{1,6}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|一|二|三|四|五|六|七|八|九|十)?\b[\s:.\-—]*/i;
const CHAPTER_LABEL_RE = /^\s*chapter\s*(\d{1,3})\b/i;
const NUMBERED_HEADING_RE = /^\s*(\d{1,2})(?:\.(\d{1,2}))?\s+([A-Z][^.]{2,60})$/;
const DOT_LEADER_RE = /\.{2,}\s*\d+\s*$/;
const POS_RE = /\b(n|v|vt|vi|adj|adv|prep|conj|pron|num|art|interj|abbr)\./i;
const BACK_MATTER_RE = /^\s*(references|bibliography|further\s+reading|appendix|appendices|index|glossary|acknowledge?ments?|answer\s+key|answers|word\s+list|table\s+of\s+contents|contents|preface|foreword|copyright|about\s+the\s+authors?)\s*$/i;
const RUNNING_HEAD_MIN_RATIO = 0.35;
const SCANNED_PAGE_CHAR_THRESHOLD = 25;
const SCANNED_PDF_RATIO = 0.6;
const FALLBACK_CHAPTER_PAGES = 12;

const note = (chapter, text) => {
  if (text && !chapter.evidence.includes(text)) chapter.evidence.push(text);
};
const alert = (chapter, text) => {
  if (text && !chapter.alerts.includes(text)) chapter.alerts.push(text);
};
const hasNote = (chapter, prefix) => chapter.evidence.some((item) => item.startsWith(prefix));

export function isPageNumberLine(text) {
  const stripped = String(text).trim();
  if (!stripped || stripped.length > 24) return false;
  if (/^[-–—\s]*\d{1,4}[-–—\s]*$/.test(stripped)) return true;
  if (/^(page\s*)?\d{1,4}\s*(of\s*\d{1,4})?$/i.test(stripped)) return true;
  if (/^[ivxlcdm]{1,8}$/i.test(stripped) && stripped.length <= 6) return true;
  return false;
}

export const looksLikeTocLine = (text) => DOT_LEADER_RE.test(text);

export function findRunningLines(pages) {
  const counter = new Map();
  for (const page of pages) {
    const seen = new Set((page.lines || []).map((line) => line.text).filter((text) => text.length <= 80));
    for (const text of seen) counter.set(text, (counter.get(text) || 0) + 1);
  }
  const threshold = Math.max(3, Math.floor(pages.length * RUNNING_HEAD_MIN_RATIO));
  return new Set([...counter.entries()].filter(([, count]) => count >= threshold).map(([text]) => text));
}

export function detectTocPages(pages) {
  const toc = new Set();
  for (const page of pages) {
    const lines = page.lines || [];
    if (!lines.length) continue;
    const texts = lines.map((line) => line.text);
    const tocLines = texts.filter(looksLikeTocLine).length;
    const tocHit = texts.slice(0, 6).some((text) => /^\s*(contents|table of contents|目\s*录)/i.test(text));
    const labelLines = texts.filter((text) => /^\s*chapter\s*\d{1,3}\s*$/i.test(text)).length;
    const numberLines = texts.filter((text) => /^\d{1,4}$/.test(text.trim())).length;
    if (tocLines >= 4 || (tocHit && tocLines >= 2)) toc.add(page.number);
    else if (labelLines >= 4 && numberLines >= 3) toc.add(page.number);
    else if (tocHit && labelLines >= 3) toc.add(page.number);
    else if (labelLines >= 8) toc.add(page.number);
  }
  const extended = new Set(toc);
  for (const number of toc) if (toc.has(number + 1)) extended.add(number + 1);
  return extended;
}

export function backMatterStart(pages) {
  for (const page of pages) {
    for (const line of (page.lines || []).slice(0, 4)) {
      const text = line.text.trim();
      if (!BACK_MATTER_RE.test(text)) continue;
      if (/^\s*(contents|table of contents|目\s*录)/i.test(text)) continue;
      return page.number;
    }
  }
  return null;
}

export function estimatePageOffset(pages) {
  const offsets = new Map();
  pages.forEach((page) => {
    const printed = page.printed_page;
    if (!printed) return;
    const offset = page.number - printed;
    offsets.set(offset, (offsets.get(offset) || 0) + 1);
  });
  if (!offsets.size) return 0;
  const [offset, count] = [...offsets.entries()].sort((a, b) => b[1] - a[1])[0];
  return count >= Math.max(3, Math.floor(pages.length * 0.15)) ? offset : 0;
}

export function chapterMarkers(pages, tocPages = new Set()) {
  const top = new Map();
  const bottom = new Map();
  const TOP_ROWS = 8;
  const FOOTER_ROWS = 2;
  const add = (map, page, number) => {
    if (!map.has(page)) map.set(page, new Set());
    map.get(page).add(number);
  };
  for (const page of pages) {
    if (tocPages.has(page.number)) continue;
    const lines = page.lines || [];
    lines.forEach((line, index) => {
      const inFooter = lines.length >= 5 && index >= lines.length - FOOTER_ROWS;
      const inHeader = index < TOP_ROWS;
      if (!inFooter && !inHeader) return;
      const text = line.text.trim();
      const match = CHAPTER_LABEL_RE.exec(text);
      if (match) { add(inFooter ? bottom : top, page.number, Number(match[1])); return; }
      if (!inFooter && /^chapter$/i.test(text)) {
        for (const following of lines.slice(index + 1, index + 3)) {
          const number = /^\s*(\d{1,3})\s*$/.exec(following.text);
          if (number) { add(top, page.number, Number(number[1])); break; }
        }
      }
    });
  }
  return { top, bottom };
}

export function collectEvidence(pages, tocPages, tocEntries = {}) {
  const { top, bottom } = chapterMarkers(pages, tocPages);
  return { toc_pages: [...tocPages].sort((a, b) => a - b), toc: tocEntries, top, bottom };
}

function numbersOutside(text, spans) {
  const out = [];
  for (const match of text.matchAll(/\d{1,4}/g)) {
    const start = match.index;
    const end = start + match[0].length;
    if (spans.some(([spanStart, spanEnd]) => start < spanEnd && end > spanStart)) continue;
    out.push(Number(match[0]));
  }
  return out;
}

export function tocChapters(pages, offset, backStart) {
  let best = null;
  for (const page of pages) {
    const text = (page.lines || []).map((line) => line.text).join("\n");
    if (text.length > 20000) continue;
    const spans = [];
    const labels = [];
    for (const match of text.matchAll(/chapter\s*(\d{1,3})/gi)) {
      labels.push(Number(match[1]));
      spans.push([match.index, match.index + match[0].length]);
    }
    const numbers = numbersOutside(text, spans);
    if (labels.length < 3 || numbers.length < labels.length) continue;
    const cleaned = [];
    for (const value of numbers) {
      if (value < 1 || value > 2000) continue;
      if (cleaned.length && value <= cleaned[cleaned.length - 1]) continue;
      cleaned.push(value);
    }
    if (cleaned.length < labels.length) continue;
    if (!best || labels.length > best.labels.length) best = { page, labels, numbers: cleaned };
  }
  if (!best) return { chapters: [], tocEntries: {} };

  const starts = best.numbers.slice(0, best.labels.length)
    .map((value) => Math.max(1, Math.min(pages.length, value + offset)));
  const extraStart = best.numbers.length > best.labels.length ? best.numbers[best.labels.length] + offset : null;
  const chapters = [];
  const tocEntries = {};
  best.labels.forEach((label, index) => {
    const start = starts[index];
    const end = index + 1 < starts.length ? starts[index + 1] - 1
      : (extraStart ? extraStart - 1 : (backStart ? backStart - 1 : pages.length));
    const chapter = {
      title: `Chapter ${label}`, start_page: start, end_page: Math.min(Math.max(end, start), pages.length),
      source: "toc", number: label, subsections: [], confidence: "unknown", evidence: [], alerts: [],
    };
    note(chapter, `目录页 p${best.page.number} 记录 Chapter ${label} 起始印刷页 ${best.numbers[index]}`
      + `（offset ${offset} → 物理页 ${start}）`);
    tocEntries[label] = { toc_page: best.page.number, printed: best.numbers[index], start_page: start };
    chapters.push(chapter);
  });
  if (extraStart && extraStart <= pages.length) {
    const appendix = {
      title: "附录 / 索引", start_page: extraStart,
      end_page: backStart ? backStart - 1 : pages.length, source: "toc", number: null,
      subsections: [], confidence: "unknown", evidence: [], alerts: [],
    };
    note(appendix, `目录页 p${best.page.number} 中位于各章之后的条目（印刷页 `
      + `${best.numbers[best.labels.length]} → 物理页 ${extraStart}）`);
    chapters.push(appendix);
  }
  for (let i = 1; i < chapters.length; i += 1) {
    if (chapters[i].start_page <= chapters[i - 1].start_page) return { chapters: [], tocEntries: {} };
  }
  return { chapters, tocEntries };
}

function completeTitle(page, index, base) {
  if (/\d/.test(base) || POS_RE.test(base)) return base;
  const extra = [];
  for (const line of (page.lines || []).slice(index + 1, index + 4)) {
    const text = line.text.trim();
    if (!text || text.length > 24) break;
    if (text.length > 12 && /[.?!;,]/.test(text)) break;
    if (POS_RE.test(text) || /[/[\]]/.test(text)) break;
    extra.push(text);
    if (extra.length >= 2 && !/^\d{1,2}$/.test(text)) break;
  }
  return extra.length ? `${base} ${extra.join(" ")}`.trim() : base;
}

function lineIsHeading(text, size, bodySize) {
  if (!text || text.length > 90) return false;
  const words = text.split(/\s+/);
  if (words.length > 12) return false;
  if (/[,;:]$/.test(text) && !CHAPTER_RE.test(text)) return false;
  const big = bodySize > 0 && size >= bodySize * 1.18;
  if (CHAPTER_RE.test(text) || NUMBERED_HEADING_RE.test(text)) return true;
  if (big && !text.endsWith(".")) return true;
  const letters = text.replace(/[^A-Za-z]/g, "");
  if (letters.length >= 4 && text === text.toUpperCase() && words.length <= 8 && bodySize > 0) return true;
  return false;
}

function headingRank(text) {
  if (CHAPTER_RE.test(text)) return 0;
  if (NUMBERED_HEADING_RE.test(text)) return 1;
  return 2;
}

export function outlineChapters(outline, pageCount) {
  const entries = (outline || []).filter((entry) => entry.page);
  if (entries.length < 2) return [];
  const pageLike = entries.filter((entry) => /(页面|page)\s*[_\-\s]*\d+/i.test(entry.title || "")
    || /^\d+$/.test(String(entry.title || "").trim())).length;
  if (pageLike / entries.length > 0.3 || entries.length > pageCount * 0.5) return [];
  const depths = [...new Set(entries.map((entry) => entry.depth || 0))].sort((a, b) => a - b);
  const keep = new Set(depths.slice(0, 2));
  const top = entries.filter((entry) => keep.has(entry.depth || 0));
  const chapters = [];
  top.forEach((entry, index) => {
    const start = Math.max(1, Number(entry.page));
    const end = index + 1 < top.length ? Number(top[index + 1].page) - 1 : pageCount;
    const chapter = {
      title: entry.title, start_page: start, end_page: Math.min(Math.max(end, start), pageCount),
      source: "outline", number: null, subsections: [], confidence: "unknown", evidence: [], alerts: [],
    };
    note(chapter, `PDF 书签“${entry.title}”指向第 ${start} 页`);
    chapters.push(chapter);
  });
  return chapters;
}

function headingChapters(pages, tocPages, backStart) {
  const hits = [];
  for (const page of pages) {
    if (tocPages.has(page.number)) continue;
    if (backStart && page.number >= backStart) continue;
    const lines = page.lines || [];
    for (let index = 0; index < Math.min(8, lines.length); index += 1) {
      const line = lines[index];
      const text = line.text.trim();
      if (!lineIsHeading(text, line.size || 0, page.body_size || 0)) continue;
      if (headingRank(text) > 1) continue;
      hits.push({ page: page.number, title: completeTitle(page, index, text), model: page });
      break;
    }
  }
  if (hits.length < 2) return [];
  const chapters = [];
  hits.forEach((hit, index) => {
    const end = index + 1 < hits.length ? hits[index + 1].page - 1
      : (backStart ? backStart - 1 : pages.length);
    if (chapters.length && chapters[chapters.length - 1].title === hit.title
        && hit.page - chapters[chapters.length - 1].start_page <= 1) {
      chapters[chapters.length - 1].end_page = Math.max(chapters[chapters.length - 1].end_page, end);
      return;
    }
    const chapter = {
      title: hit.title, start_page: hit.page, end_page: Math.max(end, hit.page),
      source: "heading", number: null, subsections: [], confidence: "unknown", evidence: [], alerts: [],
    };
    note(chapter, `p${hit.page} 页首按标题样式识别为“${hit.title}”`);
    chapters.push(chapter);
  });
  return chapters;
}

function fallbackChapters(pages, tocPages, backStart) {
  const last = backStart ? backStart - 1 : pages.length;
  let first = 1;
  if (tocPages.size) first = Math.min(last, Math.max(...tocPages) + 1);
  const chapters = [];
  let page = first;
  let index = 1;
  while (page <= last) {
    const end = Math.min(page + FALLBACK_CHAPTER_PAGES - 1, last);
    const chapter = {
      title: `第 ${index} 部分（第 ${page}-${end} 页）`, start_page: page, end_page: end,
      source: "fallback", number: null, subsections: [], confidence: "unknown", evidence: [], alerts: [],
    };
    note(chapter, "未找到目录或标题结构，按固定页数自动划分");
    chapters.push(chapter);
    page = end + 1;
    index += 1;
  }
  return chapters;
}

export function evaluateChapters(pages, chapters, evidence, backStart, { allowSnap = true, allowAdditions = true } = {}) {
  if (!chapters.length) return chapters;
  const top = evidence.top || new Map();
  const bottom = evidence.bottom || new Map();
  const toc = evidence.toc || {};
  const lastPage = backStart ? backStart - 1 : pages.length;

  for (const chapter of chapters) {
    if (chapter.number === null || chapter.number === undefined) {
      const match = CHAPTER_LABEL_RE.exec(chapter.title || "");
      if (match) chapter.number = Number(match[1]);
    }
    if (!chapter.evidence.length) chapter.confidence = "unknown";
  }
  const numberIn = (map, page, number) => number !== null && number !== undefined
    && map.has(page) && map.get(page).has(number);

  if (allowSnap) {
    for (const chapter of chapters) {
      if (chapter.number === null || chapter.number === undefined || chapter.confidence === "manual") continue;
      const bodyPages = [...top.entries()].filter(([, numbers]) => numbers.has(chapter.number))
        .map(([page]) => page).sort((a, b) => a - b);
      if (!bodyPages.length || bodyPages.includes(chapter.start_page)) continue;
      const nearby = bodyPages.filter((page) => page >= chapter.start_page - 4);
      const pool = nearby.length ? nearby : bodyPages;
      const target = pool.reduce((best, page) => (
        Math.abs(page - chapter.start_page) < Math.abs(best - chapter.start_page) ? page : best), pool[0]);
      const delta = target - chapter.start_page;
      chapter.start_page = target;
      note(chapter, Math.abs(delta) <= 4
        ? `边界由推算的 p${target - delta} 校正到 p${target}（页首 Chapter ${chapter.number}）`
        : `目录/页码推算与正文标识相差 ${Math.abs(delta)} 页，已按正文 p${target} 的 Chapter ${chapter.number} 为准`);
    }
  }

  if (allowAdditions) {
    const covered = new Set(chapters.map((chapter) => chapter.number).filter((n) => n !== null && n !== undefined));
    const claimed = new Set();
    for (const chapter of chapters) {
      for (let page = chapter.start_page; page <= chapter.end_page; page += 1) claimed.add(page);
    }
    for (const page of [...top.keys()].sort((a, b) => a - b)) {
      for (const number of [...top.get(page)].sort((a, b) => a - b)) {
        if (covered.has(number) || claimed.has(page)) continue;
        chapters.push({
          title: `Chapter ${number}`, start_page: page, end_page: page, source: "marker",
          number, subsections: [], confidence: "unknown",
          evidence: [`p${page} 页首发现目录未记录的 Chapter ${number}`], alerts: [],
        });
        covered.add(number);
      }
    }
  }

  chapters.sort((a, b) => a.start_page - b.start_page || (a.number || 0) - (b.number || 0));
  chapters.forEach((chapter, index) => {
    chapter.start_page = Math.max(1, Math.min(chapter.start_page, pages.length));
    const end = index + 1 < chapters.length ? chapters[index + 1].start_page - 1 : lastPage;
    chapter.end_page = Math.max(chapter.start_page, Math.min(end, lastPage));
  });

  const numbers = chapters.map((chapter) => chapter.number).filter((n) => n !== null && n !== undefined);
  const monotonic = numbers.every((value, index) => index === 0 || numbers[index - 1] < value);
  if (!monotonic) {
    for (const chapter of chapters) if (chapter.number !== null) alert(chapter, "章节编号顺序与页码顺序不一致，请确认");
  }

  for (const chapter of chapters) {
    if (chapter.confidence === "manual") continue;
    const number = chapter.number;
    const startTop = top.get(chapter.start_page);
    const startHasTop = Boolean(startTop && number !== null && number !== undefined && startTop.has(number));
    const ambiguousStart = Boolean(startTop && startTop.size > 1);
    let hasFooterMarker = false;
    for (let page = chapter.start_page; page <= chapter.end_page; page += 1) {
      if (numberIn(bottom, page, number)) { hasFooterMarker = true; break; }
    }
    const tocEntry = number !== null && number !== undefined ? toc[number] : null;
    const span = chapter.end_page - chapter.start_page + 1;
    if (startHasTop && !ambiguousStart && monotonic) {
      chapter.confidence = "high";
      note(chapter, `p${chapter.start_page} 页首有正文标识 Chapter ${number}`);
      if (tocEntry && !hasNote(chapter, "目录页")) {
        note(chapter, `目录页 p${tocEntry.toc_page} 记录其起始印刷页 ${tocEntry.printed}（换算物理页 ${tocEntry.start_page}）`);
      }
    } else if (startHasTop && (ambiguousStart || !monotonic)) {
      chapter.confidence = "low";
      note(chapter, `p${chapter.start_page} 页首出现多个章节标识 ${[...startTop].sort()}，无法确认边界`);
    } else if (hasFooterMarker) {
      chapter.confidence = "medium";
      note(chapter, `章节内页脚出现 Chapter ${number} 标识（首页缺正文标识）`);
      if (tocEntry && !hasNote(chapter, "目录页")) {
        note(chapter, `目录页 p${tocEntry.toc_page} 记录起始印刷页 ${tocEntry.printed}`);
      }
    } else if (tocEntry) {
      chapter.confidence = "medium";
      if (!hasNote(chapter, "目录页")) {
        note(chapter, `边界来自目录页 p${tocEntry.toc_page} 的 Chapter ${number}`
          + `（印刷页 ${tocEntry.printed} → 物理页 ${tocEntry.start_page}），正文未找到该标识`);
      } else {
        note(chapter, "正文首页与页脚均无该章标识，边界仅由目录页码推算");
      }
    } else if (span <= 2) {
      chapter.confidence = "low";
      note(chapter, `页码范围仅 ${span} 页且缺少章节标识`);
    } else {
      chapter.confidence = "low";
      note(chapter, "正文首页、页脚均无该章标识，且缺少目录页码，无法确认边界");
    }
  }
  return chapters;
}

export function enrichTitlesFromOpeners(pages, chapters) {
  const cleanExtra = /^[\dA-Za-z\u4e00-\u9fff \-']+$/;
  const byPage = new Map(pages.map((page) => [page.number, page]));
  for (const chapter of chapters) {
    const page = byPage.get(chapter.start_page);
    if (!page) continue;
    const lines = page.lines || [];
    for (let index = 0; index < Math.min(8, lines.length); index += 1) {
      const text = lines[index].text.trim();
      if (!CHAPTER_RE.test(text)) continue;
      const title = completeTitle(page, index, text);
      const extra = title.slice(text.length).trim();
      if (title.length > chapter.title.length && extra && cleanExtra.test(extra)) chapter.title = title;
      break;
    }
  }
}

export function buildPageTexts(pages, tocPages) {
  const running = findRunningLines(pages);
  const cleaned = new Map();
  for (const page of pages) {
    const lines = [];
    let previous = "";
    for (const line of page.lines || []) {
      const text = line.text;
      if (running.has(text)) continue;
      if (isPageNumberLine(text) && !tocPages.has(page.number)) {
        const keepForHeading = /^chapter$/i.test(previous.trim()) && /^\d{1,3}$/.test(text.trim());
        if (!keepForHeading) continue;
      }
      if (looksLikeTocLine(text)) continue;
      const value = text.trim().replace(/\s{2,}/g, " ");
      if (value) { lines.push(value); previous = value; }
    }
    cleaned.set(page.number, lines);
  }
  return { cleaned, running };
}

/** 完整流程：证据收集 → 各策略 → 统一置信度评估 */
export function detectChapters(pages, outline = [], pageOffset = 0) {
  const tocPages = detectTocPages(pages);
  const backStart = backMatterStart(pages);
  const offset = pageOffset || estimatePageOffset(pages);
  const report = {
    toc_pages: [...tocPages].sort((a, b) => a - b),
    back_matter_start: backStart,
    page_offset: offset,
    running_lines: [...findRunningLines(pages)].slice(0, 25),
    strategy: null,
  };

  let tocEntries = {};
  let chapters = outlineChapters(outline, pages.length);
  if (chapters.length >= 2) {
    report.strategy = "outline";
  } else {
    const tocResult = tocChapters(pages, offset, backStart);
    chapters = tocResult.chapters;
    tocEntries = tocResult.tocEntries;
    if (chapters.length >= 3) {
      enrichTitlesFromOpeners(pages, chapters);
      report.strategy = "toc";
    } else {
      chapters = headingChapters(pages, tocPages, backStart);
      if (chapters.length >= 2) report.strategy = "heading";
      else { chapters = fallbackChapters(pages, tocPages, backStart); report.strategy = "fallback"; }
      tocEntries = {};
    }
  }

  const evidence = collectEvidence(pages, tocPages, tocEntries);
  chapters = evaluateChapters(pages, chapters, evidence, backStart);
  report.marker_pages = Object.fromEntries([...evidence.top.entries()].sort((a, b) => a[0] - b[0])
    .map(([page, numbers]) => [page, [...numbers].sort((a, b) => a - b)]));
  report.footer_marker_pages = Object.fromEntries([...evidence.bottom.entries()].sort((a, b) => a[0] - b[0])
    .map(([page, numbers]) => [page, [...numbers].sort((a, b) => a - b)]));
  report.toc_entries = Object.fromEntries(Object.entries(tocEntries).sort((a, b) => Number(a[0]) - Number(b[0])));
  report.chapters_needing_review = chapters.filter((c) => ["low", "unknown", "medium"].includes(c.confidence))
    .map((c) => ({ title: c.title, start_page: c.start_page, end_page: c.end_page,
                   confidence: c.confidence, evidence: c.evidence, alerts: c.alerts }));
  report.chapter_count = chapters.length;
  return { chapters, report, tocPages };
}

export function unassignedPages(chapters, pageCount, tocPages = []) {
  const covered = new Set();
  for (const chapter of chapters) {
    for (let page = chapter.start_page; page <= chapter.end_page; page += 1) covered.add(page);
  }
  const toc = new Set(tocPages);
  const out = [];
  for (let page = 1; page <= pageCount; page += 1) if (!covered.has(page) && !toc.has(page)) out.push(page);
  return out;
}

export { SCANNED_PAGE_CHAR_THRESHOLD, SCANNED_PDF_RATIO, note, alert };
