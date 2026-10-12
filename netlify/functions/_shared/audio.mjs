// 发音代理：把 Python 版 app/audio/ 的"多来源 + 校验"逻辑搬到 Node。
//
// 只做两件事：向真实词典接口取回录音，并**验证它确实是音频**（HTTP 状态、
// 体积、容器魔数、时长合理性）。英式/美式分别独立获取，缺失就返回 404，
// 绝不用另一口音替代。

const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36";

export const MIN_AUDIO_BYTES = 1200;
export const MAX_AUDIO_BYTES = 3_000_000;
export const MIN_CLIP_SECONDS = 0.12;
export const MAX_CLIP_SECONDS = 5.0;

const TIMEOUT_MS = 8000;
const TRANSIENT = ["timeout", "timed out", "fetch failed", "econn", "enotfound",
  "network", "socket", "aborted", "ssl"];

export function isTransientError(error = "") {
  const text = String(error).toLowerCase();
  return TRANSIENT.some((marker) => text.includes(marker));
}

export function sniffAudio(data) {
  if (!data || data.length < 12) return null;
  const head = data.subarray(0, 16);
  if (head[0] === 0x49 && head[1] === 0x44 && head[2] === 0x33) return "audio/mpeg";       // ID3
  if (head[0] === 0xff && (head[1] & 0xe0) === 0xe0) return "audio/mpeg";                  // frame sync
  if (head[0] === 0x52 && head[1] === 0x49 && head[2] === 0x46 && head[3] === 0x46 &&
      data[8] === 0x57 && data[9] === 0x41 && data[10] === 0x56 && data[11] === 0x45) return "audio/wav";
  if (head[0] === 0x4f && head[1] === 0x67 && head[2] === 0x67 && head[3] === 0x53) return "audio/ogg";
  if (head[0] === 0x66 && head[1] === 0x4c && head[2] === 0x61 && head[3] === 0x43) return "audio/flac";
  return null;
}

const MP3_BITRATES_MPEG1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320];
const MP3_BITRATES_MPEG2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160];
const MP3_RATES = { 3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000] };

/** 估算时长（秒）。用来排除错误页/整句录音之类的异常响应。 */
export function probeDuration(data, mime = "") {
  try {
    const kind = mime || sniffAudio(data) || "";
    if (kind === "audio/wav") return wavDuration(data);
    if (kind === "audio/mpeg") return mp3Duration(data);
    if (kind === "audio/ogg") return oggDuration(data);
  } catch {
    return null;
  }
  return null;
}

function wavDuration(data) {
  if (data.length < 44) return null;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  let pos = 12;
  let rate = 0, channels = 0, bits = 0, formatTag = 1, dataSize = null;
  while (pos + 8 <= data.length) {
    const id = String.fromCharCode(data[pos], data[pos + 1], data[pos + 2], data[pos + 3]);
    const size = view.getUint32(pos + 4, true);
    const body = pos + 8;
    if (id === "fmt " && size >= 16 && body + 16 <= data.length) {
      formatTag = view.getUint16(body, true);
      channels = view.getUint16(body + 2, true);
      rate = view.getUint32(body + 4, true);
      bits = view.getUint16(body + 14, true);
    } else if (id === "data") {
      dataSize = size || data.length - body;
      break;
    }
    pos = body + size + (size % 2);
  }
  if ((formatTag === 1 || formatTag === 3 || formatTag === 0xfffe) && rate && channels && bits && dataSize) {
    return dataSize / (rate * channels * bits / 8);
  }
  return null;
}

function mp3Duration(data) {
  let start = 0;
  if (data[0] === 0x49 && data[1] === 0x44 && data[2] === 0x33 && data.length > 10) {
    let size = 0;
    for (let i = 6; i < 10; i += 1) size = (size << 7) | (data[i] & 0x7f);
    start = 10 + size;
  }
  let pos = start;
  let total = 0;
  let frames = 0;
  while (pos < data.length - 4 && frames < 20000) {
    if (data[pos] !== 0xff || (data[pos + 1] & 0xe0) !== 0xe0) { pos += 1; continue; }
    const versionBits = (data[pos + 1] >> 3) & 0x03;
    const layerBits = (data[pos + 1] >> 1) & 0x03;
    const bitrateIndex = (data[pos + 2] >> 4) & 0x0f;
    const rateIndex = (data[pos + 2] >> 2) & 0x03;
    if (versionBits === 1 || layerBits !== 1 || bitrateIndex === 0 || rateIndex === 3) { pos += 1; continue; }
    const table = versionBits === 3 ? MP3_BITRATES_MPEG1 : MP3_BITRATES_MPEG2;
    const rates = MP3_RATES[versionBits];
    if (!table || !rates || rateIndex >= rates.length || bitrateIndex >= table.length) { pos += 1; continue; }
    const samplesPerFrame = versionBits === 3 ? 1152 : 576;
    let frameLength = Math.floor((samplesPerFrame / 8) * table[bitrateIndex] * 1000 / rates[rateIndex]);
    frameLength += (data[pos + 2] >> 1) & 1;
    if (frameLength <= 4) { pos += 1; continue; }
    total += samplesPerFrame / rates[rateIndex];
    frames += 1;
    pos += frameLength;
  }
  return frames >= 3 ? total : null;
}

function oggDuration(data) {
  const granule = data.subarray(Math.max(0, data.length - 64));
  return null; // Ogg 探测量在本项目中未使用，忽略即可
}

async function fetchWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function ok(data, provider, accent, url) {
  const mime = sniffAudio(data);
  if (!mime) return { ok: false, provider, accent, error: "not audio" };
  if (data.length < MIN_AUDIO_BYTES) return { ok: false, provider, accent, error: "payload too small" };
  if (data.length > MAX_AUDIO_BYTES) return { ok: false, provider, accent, error: "payload too large" };
  const duration = probeDuration(data, mime);
  if (duration !== null && (duration < MIN_CLIP_SECONDS || duration > MAX_CLIP_SECONDS)) {
    return { ok: false, provider, accent, error: `implausible_duration=${duration.toFixed(2)}s` };
  }
  return { ok: true, provider, accent, data, mime, url, duration };
}

const YOUDAO_NOTE = "有道词典发音接口（真实录音，type=1 英式 / type=2 美式）";
const FREEDICT_NOTE = "Free Dictionary API（dictionaryapi.dev，MIT，音频来自维基媒体）";

async function youdao(word, accent) {
  const url = `https://dict.youdao.com/dictvoice?audio=${encodeURIComponent(word)}&type=${accent === "uk" ? 1 : 2}`;
  try {
    const res = await fetchWithTimeout(url, {
      headers: { "User-Agent": UA, Referer: "https://dict.youdao.com/", Accept: "audio/mpeg,audio/*;q=0.9,*/*;q=0.8" },
    });
    if (!res.ok) return { ok: false, provider: "youdao", accent, error: `HTTP ${res.status}`, url };
    const data = new Uint8Array(await res.arrayBuffer());
    const result = ok(data, "youdao", accent, url);
    result.note = YOUDAO_NOTE;
    return result;
  } catch (err) {
    return { ok: false, provider: "youdao", accent, error: String(err && err.message || err), url };
  }
}

async function freedict(word, accent) {
  const direct = `https://api.dictionaryapi.dev/media/pronunciations/en/${encodeURIComponent(word)}-${accent}.mp3`;
  let candidates = [direct];
  try {
    const res = await fetchWithTimeout(`https://api.dictionaryapi.dev/api/v2/entries/en/${encodeURIComponent(word)}`, {
      headers: { "User-Agent": UA },
    });
    if (res.ok) {
      const entries = await res.json();
      for (const entry of Array.isArray(entries) ? entries : []) {
        for (const phonetic of entry.phonetics || []) {
          const audio = String(phonetic.audio || "").trim();
          const low = audio.toLowerCase();
          if (!audio || !low.includes(`-${accent}`)) continue;
          if (low.includes("-uk-us") || low.includes("-us-uk")) continue;
          if (!candidates.includes(audio)) candidates.push(audio);
        }
      }
    }
  } catch { /* 目录接口失败就直接试直链 */ }

  let lastError = "no candidate";
  for (const url of candidates) {
    try {
      const res = await fetchWithTimeout(url, { headers: { "User-Agent": UA } });
      if (!res.ok) { lastError = `HTTP ${res.status}`; continue; }
      const data = new Uint8Array(await res.arrayBuffer());
      const result = ok(data, "freedict", accent, url);
      if (result.ok) { result.note = FREEDICT_NOTE; return result; }
      lastError = result.error;
    } catch (err) {
      lastError = String(err && err.message || err);
    }
  }
  return { ok: false, provider: "freedict", accent, error: lastError };
}

/** 依次尝试各来源；返回 { ok, data, mime, provider, duration, retryable } */
export async function resolveAudio(word, accent, providers = [youdao, freedict]) {
  const errors = [];
  for (const provider of providers) {
    const result = await provider(word, accent);
    if (result.ok) return result;
    errors.push(`${result.provider}:${result.error}`);
  }
  const joined = errors.join("; ") || "no audio";
  return { ok: false, accent, error: joined, retryable: isTransientError(joined) };
}
