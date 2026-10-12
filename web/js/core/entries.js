// 词条识别（单词书排版规则）—— app/entries.py 的浏览器移植版。
//
// 规则：行首"单词 + /音标/"才算词条；例句/正文/搭配不计入；页脚词汇汇总只用于
// 交叉校验与补全漏识词条。

import { STOPWORDS } from "./text.js";

const WORD = "[A-Za-z\\u00c0-\\u024f]";
const HEADWORD = `${WORD}[A-Za-z\\u00c0-\\u024f'\\-]*`;

const ENTRY_RE = new RegExp(
  `^\\s*(?<word>${HEADWORD}(?:\\s+(?:of|the|to|and|in|on|up|out)?\\s*${HEADWORD}){0,2})`
  + "\\s*(?:\\(\\s*=\\s*[A-Za-z\\-]+\\s*\\))?\\s*"
  + "(?<open>[/\\[|])\\s*(?<ipa>[^/\\]|]{2,45}?)\\s*(?<close>[/\\]|])",
);
const POS_RE = /\b(n|v|vt|vi|adj|adv|prep|conj|pron|num|art|interj|abbr)\./i;
const CJK_RE = /[\u3400-\u4dbf\u4e00-\u9fff]+/g;
const TAIL_NOISE_RE = /^\s*(?:\d{1,4}|[ivxlcdm]{1,6}|chapter\s*\d*|[\u3400-\u4dbf\u4e00-\u9fff\s]+)\s*$/i;

function plausiheadwordPlausible(word) {
  const parts = word.split(/\s+/);
  if (!parts.length || parts.length > 3) return false;
  if (parts.some((part) => /\d/.test(part))) return false;
  if (STOPWORDS.has(parts[0].toLowerCase())) return false;
  if (parts[0].length < 2) return false;
  if (!/[aeiouy]/i.test(parts[0]) && parts.length === 1) return false;
  return true;
}

export function matchEntry(line) {
  const match = ENTRY_RE.exec(line);
  if (!match) return null;
  const headword = match.groups.word.replace(/\s+/g, " ").replace(/^[ \-']+|[ \-']+$/g, "");
  const ipa = (match.groups.ipa || "").trim();
  if (ipa.length < 2) return null;
  if (!plausiheadwordPlausible(headword)) return null;
  if (ipa.includes(".") && !/^[a-zɑɒɔəɜɛɪʊʌæʤʧθðʃʒŋɹɫˈˌː' ]+$/i.test(ipa)) return null;
  return { headword, ipa };
}

function collectDetails(lines, startIndex) {
  const first = lines[startIndex].replace(ENTRY_RE, "").trim();
  const parts = first ? [first] : [];
  for (const line of lines.slice(startIndex + 1, startIndex + 5)) {
    if (matchEntry(line)) break;
    const text = line.trim();
    if (!text) continue;
    if (/^[[(【]?\s*(例|VIJ|记|搭|E|BJ|5?1J)/.test(text)) break;
    parts.push(text);
    if (POS_RE.test(text) && CJK_RE.test(text)) break;
  }
  const blob = parts.join(" ").trim();
  const posMatch = POS_RE.exec(blob);
  const pos = posMatch ? `${posMatch[1].toLowerCase()}.` : "";
  const cjk = (blob.match(CJK_RE) || []).join(" ").trim();
  let hint = cjk;
  if (!hint) {
    const latin = blob.replace(/\s+/g, " ").trim();
    if (latin && latin.length <= 60 && !/[.?!;,]\s/.test(latin)) hint = latin;
  }
  return { pos, hint };
}

export function summaryWords(lines, lexicon) {
  const collected = [];
  let skipped = 0;
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const text = lines[i].trim();
    if (!text) continue;
    if (TAIL_NOISE_RE.test(text) && skipped < 6) { skipped += 1; continue; }
    if (matchEntry(text)) break;
    if (new RegExp(`^${HEADWORD}(?:\\s+${HEADWORD})?$`).test(text) && !STOPWORDS.has(text.toLowerCase())) {
      collected.push(text);
      continue;
    }
    break;
  }
  const words = collected.reverse();
  if (words.length < 4) return [];
  if (lexicon) {
    const known = words.filter((word) => lexicon.has(word.toLowerCase())).length;
    if (known / words.length < 0.5) return [];
  }
  return words;
}

function known(headword, lexicon) {
  if (!lexicon) return true;
  const word = headword.toLowerCase().trim();
  if (lexicon.has(word)) return true;
  return lexicon.has(word.split(/\s+/)[0]);
}

function similar(a, b) {
  if (a === b) return true;
  if (Math.abs(a.length - b.length) > 2) return false;
  // 简化版相似度：编辑距离 / 较长长度 <= 0.22
  const longer = a.length >= b.length ? a : b;
  const shorter = a.length >= b.length ? b : a;
  let diff = longer.length - shorter.length;
  for (let i = 0; i < shorter.length && diff <= 2; i += 1) if (longer[i] !== shorter[i]) diff += 1;
  return diff <= Math.max(2, Math.round(longer.length * 0.22));
}

function matchSummary(summary, entries) {
  const matched = new Set();
  const mapping = new Map();
  for (const word of summary) {
    const key = word.toLowerCase();
    for (const entry of entries) {
      if (entry.headword.toLowerCase() === key || similar(entry.headword.toLowerCase(), key)) {
        matched.add(key);
        mapping.set(key, entry);
        break;
      }
    }
  }
  return { matched, mapping };
}

/** 一页的词条（按原书顺序）+ 质量报告 */
export function extractEntries(lines, page, lexicon, fillFromSummary = true) {
  let entries = [];
  const seen = new Set();
  lines.forEach((line, index) => {
    const match = matchEntry(line);
    if (!match) return;
    const key = match.headword.toLowerCase();
    if (seen.has(key)) return;
    const { pos, hint } = collectDetails(lines, index);
    entries.push({ headword: match.headword, page, ipa_hint: match.ipa, pos, hint,
                   source: "entry", in_dict: known(match.headword, lexicon) });
    seen.add(key);
  });

  const report = { page, entries: entries.length, summary: 0, filled: 0, missing: [] };
  const summary = summaryWords(lines, lexicon);
  report.summary = summary.length;
  if (summary.length) {
    const { matched, mapping } = matchSummary(summary, entries);
    report.coverage = Number((matched.size / summary.length).toFixed(3));
    if (fillFromSummary && matched.size / summary.length >= 0.5) {
      const ordered = [];
      const used = new Set();
      let filled = 0;
      for (const word of summary) {
        const key = word.toLowerCase();
        const entry = mapping.get(key);
        if (entry) {
          if (!used.has(entry)) { ordered.push(entry); used.add(entry); }
          continue;
        }
        ordered.push({ headword: word, page, ipa_hint: "", pos: "", hint: "",
                       source: "summary", in_dict: known(word, lexicon) });
        filled += 1;
      }
      for (const entry of entries) if (!used.has(entry)) ordered.push(entry);
      entries = ordered;
      report.filled = filled;
    }
    report.missing = [...new Set(summary.map((word) => word.toLowerCase()))]
      .filter((word) => !matched.has(word)).sort();
  }
  return { entries, report };
}

/** 整本书是否为"单词书排版"：目标页中出现词条的比例 */
export function detectEntryMode(linesByPage, chapters, skipPages = new Set()) {
  let considered = 0;
  let withEntries = 0;
  let total = 0;
  for (const chapter of chapters) {
    for (let page = chapter.start_page; page <= chapter.end_page; page += 1) {
      if (skipPages.has(page)) continue;
      considered += 1;
      const lines = linesByPage[page] || [];
      const found = lines.filter((line) => matchEntry(line)).length;
      if (found) { withEntries += 1; total += found; }
    }
  }
  const ratio = considered ? withEntries / considered : 0;
  return {
    mode: ratio >= 0.05 && total >= 10 ? "entries" : "tokens",
    probe: { pages_considered: considered, pages_with_entries: withEntries,
             entry_ratio: Number(ratio.toFixed(3)), entries_detected: total },
  };
}
