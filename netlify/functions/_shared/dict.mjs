// 词典代理：中文释义 + 英美音标（有道 jsonapi，失败回退 dictionaryapi.dev）

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

const POS_RE = /^\s*([a-z]{1,5})\./i;

async function fetchJson(url, timeoutMs = 7000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { headers: { "User-Agent": UA, Accept: "application/json" } });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function cleanTr(text) {
  const stripped = String(text).replace(/<[^>]+>/g, "").trim();
  const match = POS_RE.exec(stripped);
  return { pos: match ? match[1].toLowerCase() : null, text: stripped };
}

export async function lookupWord(word) {
  const out = { word, meaning_cn: null, pos: null, uk_ipa: null, us_ipa: null,
                meaning_source: null, ipa_source: null, definitions_en: [] };

  const data = await fetchJson(`https://dict.youdao.com/jsonapi?q=${encodeURIComponent(word)}`);
  if (data) {
    const entry = (((data.ec || {}).word) || [])[0] || {};
    const simple = (((data.simple || {}).word) || [])[0] || {};
    const pick = (field) => entry[field] || simple[field] || null;
    out.uk_ipa = pick("ukphone");
    out.us_ipa = pick("usphone");
    if (out.uk_ipa || out.us_ipa) out.ipa_source = "youdao";
    const translations = [];
    const posSeen = [];
    for (const block of entry.trs || []) {
      for (const tr of block.tr || []) {
        for (const item of ((tr.l || {}).i) || []) {
          const { pos, text } = cleanTr(item);
          if (text) {
            translations.push(text);
            if (pos && !posSeen.includes(pos)) posSeen.push(pos);
          }
        }
      }
    }
    if (translations.length) {
      out.meaning_cn = translations.slice(0, 6).join("；");
      out.pos = posSeen.slice(0, 4).join("/") || null;
      out.meaning_source = "youdao";
    }
  }

  if (!out.meaning_cn || !out.uk_ipa || !out.us_ipa) {
    const entries = await fetchJson(`https://api.dictionaryapi.dev/api/v2/entries/en/${encodeURIComponent(word)}`);
    if (Array.isArray(entries)) {
      const defs = [];
      for (const entry of entries.slice(0, 3)) {
        for (const meaning of (entry.meanings || []).slice(0, 4)) {
          for (const definition of (meaning.definitions || []).slice(0, 2)) {
            const text = String(definition.definition || "").trim();
            if (text) defs.push(meaning.partOfSpeech ? `(${meaning.partOfSpeech}) ${text}` : text);
          }
        }
        for (const phonetic of entry.phonetics || []) {
          const audio = String(phonetic.audio || "").toLowerCase();
          const text = String(phonetic.text || "").replace(/^\/|\/$/g, "").trim();
          if (!text) continue;
          if (audio.includes("-uk") && !out.uk_ipa) { out.uk_ipa = text; out.ipa_source = out.ipa_source || "dictionaryapi"; }
          else if (audio.includes("-us") && !out.us_ipa) { out.us_ipa = text; out.ipa_source = out.ipa_source || "dictionaryapi"; }
        }
      }
      out.definitions_en = defs.slice(0, 6);
      if (defs.length && !out.meaning_cn) out.meaning_source = "dictionaryapi(en)";
    }
  }
  return out;
}
