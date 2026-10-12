// ipa-dict 音标查询（数据来自 MIT 许可的 open-dict-data/ipa-dict，见 /data/ipa/）
// 数据文件按需加载一次：英式 65k 条 / 美式 126k 条，加载后建立内存索引。

const state = { tables: null, loading: null };

// 数据文件用相对模块的路径解析，子目录部署也能找到
const IPA_BASE = new URL("../../data/ipa/", import.meta.url);

// 随项目发布的 ipa-dict 数据规模（来源：data/ipa 下的两个文件，导入时核对过）。
// 首页状态栏直接用它，避免每次打开页面都去解析 4.6MB 文本 —— 手机上那是一段
// 明显的主线程卡顿。真正的索引只在需要查询时才建立（loadIpa）。
export const SHIPPED_STATS = { uk_entries: 65119, us_entries: 125927 };

async function loadText(path) {
  const res = await fetch(path, { cache: "force-cache" });
  if (!res.ok) throw new Error(`加载 ${path} 失败：HTTP ${res.status}`);
  return res.text();
}

function parseTable(text) {
  const table = new Map();
  for (const raw of text.split("\n")) {
    if (!raw || raw[0] === "#") continue;
    const tab = raw.indexOf("\t");
    if (tab < 0) continue;
    const word = raw.slice(0, tab).trim().toLowerCase();
    if (!word) continue;
    const values = raw.slice(tab + 1).split(",")
      .map((part) => part.trim().replace(/^\/|\/$/g, ""))
      .filter(Boolean);
    if (values.length) table.set(word, values);
  }
  return table;
}

export function loadIpa() {
  if (state.tables) return Promise.resolve(state.tables);
  if (!state.loading) {
    state.loading = (async () => {
      const [ukText, usText] = await Promise.all([
        loadText(new URL("en_UK.txt", IPA_BASE).href),
        loadText(new URL("en_US.txt", IPA_BASE).href),
      ]);
      const uk = parseTable(ukText);
      const us = parseTable(usText);
      const lexicon = new Set(uk.keys());
      for (const word of us.keys()) lexicon.add(word);
      state.tables = { uk, us, lexicon };
      return state.tables;
    })();
  }
  return state.loading;
}

function variants(word) {
  const w = String(word).toLowerCase().trim();
  const out = [w];
  if (w.endsWith("'s")) out.push(w.slice(0, -2));
  if (w.includes("-")) {
    out.push(w.split("-")[0]);
    out.push(w.split("-").slice(-1)[0]);
  }
  if (w.endsWith("s") && w.length > 3) out.push(w.slice(0, -1));
  return out;
}

export async function lexicon() {
  return (await loadIpa()).lexicon;
}

export async function lookup(word) {
  const tables = await loadIpa();
  const result = {};
  for (const accent of ["uk", "us"]) {
    for (const variant of variants(word)) {
      const hit = tables[accent].get(variant);
      if (hit) { result[accent] = hit; break; }
    }
  }
  return result;
}

export async function stats() {
  const tables = await loadIpa();
  return { uk_entries: tables.uk.size, us_entries: tables.us.size, lexicon: tables.lexicon.size };
}
