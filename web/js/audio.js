// Pronunciation playback.
//
// * One shared <audio> element: starting a new clip always stops the previous
//   one, so rapid clicking can never overlap two pronunciations.
// * The server exposes UK and US through the same endpoint but resolves them
//   from independent sources and flags a missing accent instead of substituting
//   the other one.
// * Speech synthesis is only ever used as an explicitly-labelled fallback, and
//   only when the device really has an en-GB / en-US voice.

import { api, audioUrlFor } from './api.js';
import { toast } from './ui.js';

const player = new Audio();
player.preload = 'auto';

let currentButton = null;
let loopPaused = false;

// 循环播放等待器：playAndWait() 用它把"本段播完(ended)"变成 Promise
let pendingClip = null;

// 站点是否部署了 /api/audio 代理：
//   'unknown' 未探测 / 'ok' 可用 / 'missing' 没有（静态托管，如 GitHub Pages）
// 记住结果后，同一页面里所有发音按钮都直接用能播的地址，避免每次都 404 一次。
let proxyState = 'unknown';

/** 词典直连地址（绕开本站代理：<audio> 播放媒体不受跨域限制） */
function directAudioUrl(word, accent) {
  const type = accent === 'uk' ? 1 : 2;
  const text = (word && (word.lemma || word.display)) || String(word || '');
  return `https://dict.youdao.com/dictvoice?audio=${encodeURIComponent(text)}&type=${type}`;
}

function clearButton(button) {
  if (!button) return;
  button.classList.remove('loading', 'playing');
  const icon = button.querySelector('.wave');
  if (icon) icon.textContent = '▶';
}

player.addEventListener('ended', () => {
  clearButton(currentButton);
  currentButton = null;
  settlePending('resolve', 'ended');
});
player.addEventListener('error', () => {
  settlePending('reject', new Error('audio-error'));
  if (!currentButton) return;
  const button = currentButton;
  clearButton(button);
  currentButton = null;
  toast('音频播放失败，可点击“重试”重新加载', 'err', {
    label: '重试',
    onClick: () => playUrl(button.dataset.url, button, true),
  });
});

export function stopAudio() {
  clipToken += 1;
  loopPaused = false;
  player.pause();
  clearButton(currentButton);
  currentButton = null;
  settlePending('reject', 'stopped');
}

function settlePending(kind, value) {
  if (!pendingClip) return;
  const waiter = pendingClip;
  pendingClip = null;
  if (kind === 'resolve') waiter.resolve(value);
  else waiter.reject(value);
}

/**
 * 播放一段音频并等待它真正播完（ended 事件）。
 * 循环播放用它来驱动"播完 → 间隔 → 下一段"，不靠估算音频长度。
 * 暂停时不会 resolve（音频没播完），恢复后会自然继续。
 */
export async function playAndWait(url, button) {
  if (!url) throw new Error('missing-url');
  settlePending('reject', 'superseded');
  loopPaused = false;
  const token = ++clipToken;
  let resolveWait;
  let rejectWait;
  const completion = new Promise((resolve, reject) => {
    resolveWait = resolve;
    rejectWait = reject;
  });
  // playUrl 返回 false 的路径可能不会再 await 这个 Promise；先挂一个空
  // handler，避免 error/stop 已经拒绝后产生未处理异常。
  completion.catch(() => {});
  const waiter = { resolve: resolveWait, reject: rejectWait };

  // 必须在 play() 之前登记等待器：音频很短或重复播放同一 URL 时，
  // ended 可能在 play() 返回前就发生，晚注册会漏掉事件并提前重启。
  pendingClip = waiter;
  try {
    const started = await playUrl(url, button, false, {
      keepPending: true,
      restartFromBeginning: true,
      token,
    });
    if (!started) throw new Error('play-failed');
    return await completion;
  } catch (err) {
    if (pendingClip === waiter) pendingClip = null;
    throw err;
  }
}

let clipToken = 0;

/** 等待一个媒体事件；超时返回 false，不估算音频长度。 */
function waitForMediaEvent(media, type, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false;
    let timer = null;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      media.removeEventListener(type, onEvent);
      media.removeEventListener('error', onError);
      resolve(ok);
    };
    const onEvent = () => finish(true);
    const onError = () => finish(false);
    media.addEventListener(type, onEvent, { once: true });
    media.addEventListener('error', onError, { once: true });
    timer = setTimeout(() => finish(false), timeoutMs);
  });
}

/**
 * 把媒体真正退回起点后再交给 play()。
 * 移动浏览器在 ended 后立即设置 currentTime=0 可能尚未完成 seek，
 * 此时直接 play() 会从旧位置继续，听起来像词首被截掉。
 */
async function rewindToStart(media) {
  media.pause();
  if (media.readyState === 0) {
    media.load();
    await waitForMediaEvent(media, 'loadedmetadata', 1200);
    return;
  }
  if (media.currentTime <= 0.001 && !media.ended) return;

  const seeked = waitForMediaEvent(media, 'seeked', 350);
  try {
    media.currentTime = 0;
  } catch (_) {
    media.load();
    await waitForMediaEvent(media, 'canplay', 1200);
    return;
  }
  if (await seeked) return;

  // 少数移动浏览器不派发 seeked：完整 load 一次，确保重新从文件头解码。
  media.load();
  await waitForMediaEvent(media, 'canplay', 1200);
}

/**
 * 循环重复专用：重新装载同一 URL，强制媒体从文件头重新解码。
 * 比单纯 currentTime=0 更慢一点，但能规避移动端 seek 未完成导致的截音。
 */
async function reloadForReplay(media) {
  // ended 触发时系统输出缓冲里可能还有最后几十毫秒；立刻 load() 会把
  // 这段尾音刷掉。留一个很小的技术缓冲，不参与“播放间隔”的时长计算。
  await new Promise((resolve) => setTimeout(resolve, 60));
  media.pause();
  media.load();
  await waitForMediaEvent(media, 'canplay', 1600);
  // 给浏览器一个宏任务，让 seek/解码状态在 play() 前稳定下来。
  await new Promise((resolve) => setTimeout(resolve, 0));
}

export function pauseAudio() {
  loopPaused = true;
  try { player.pause(); } catch (_) { /* ignore */ }
}

export async function resumeAudio() {
  loopPaused = false;
  try {
    await player.play();
    return true;
  } catch (_) {
    return false;
  }
}

export async function playUrl(
  url,
  button,
  forceReload = false,
  { keepPending = false, restartFromBeginning = false, token = null } = {},
) {
  if (!url) return false;
  if (button && button.disabled && !forceReload) return false;
  if (token !== null && token !== clipToken) return false;

  const isSame = player.dataset.url === url && player.src && !forceReload;
  if (!keepPending) {
    clipToken += 1;
    settlePending('reject', 'superseded');
  }
  if (currentButton && currentButton !== button) clearButton(currentButton);
  currentButton = button || null;

  if (isSame) {
    // 重复同一发音：先回到文件开头再播放。readyState 为 0 时直接 load()，
    // 避免在 metadata 尚未就绪时设置 currentTime 被浏览器忽略。
    if (restartFromBeginning) await reloadForReplay(player);
    else await rewindToStart(player);
    if (token !== null && token !== clipToken) return false;
    if (token !== null && loopPaused) return true;
    if (button) button.classList.add('playing');
    try {
      await player.play();
      return true;
    } catch (err) {
      // 再试一次：先 load() 再播（移动端 Safari/Chrome 在资源被释放后常见）
      try {
        player.load();
        await player.play();
        return true;
      } catch (err2) {
        clearButton(button);
        currentButton = null;
        toast(`播放失败：${(err2 && err2.message) || err2}`, 'err', {
          label: '重试',
          onClick: () => playUrl(url, button, true),
        });
        return false;
      }
    }
    return true;
  }

  if (button) {
    button.classList.add('loading');
    const icon = button.querySelector('.wave');
    if (icon) icon.textContent = '⋯';
  }
  player.dataset.url = url;
  player.src = url;
  if (token !== null && token !== clipToken) return false;
  if (token !== null && loopPaused) return true;
  try {
    await player.play();
    if (button) {
      button.classList.remove('loading');
      button.classList.add('playing');
      const icon = button.querySelector('.wave');
      if (icon) icon.textContent = '❚❚';
    }
    return true;
  } catch (err) {
    clearButton(button);
    currentButton = null;
    if (forceReload) {
      toast('仍然无法播放该发音音频', 'err');
    }
    return false;
  }
}

/**
 * 解析某个单词某个口音"真正能播"的地址：
 * 优先站点代理（有 /api/audio 时），否则退回词典直链。
 * 返回 {url} / {missing:true} / {retry:true}，并可在按钮上缓存结果。
 */
export async function resolveAudioUrl(word, accent, cacheTarget = null) {
  const cached = cacheTarget && cacheTarget.dataset.playUrl;
  if (cached) return { url: cached };
  const remember = (url) => {
    if (cacheTarget) {
      cacheTarget.dataset.playUrl = url;
      if (!cacheTarget.dataset.status || cacheTarget.dataset.status === 'unknown') {
        cacheTarget.dataset.status = 'available';
      }
    }
    return { url };
  };
  if (proxyState === 'missing') return remember(directAudioUrl(word, accent));

  const proxyUrl = audioUrlFor(word, accent);
  try {
    const res = await fetch(proxyUrl, { headers: { Range: 'bytes=0-1' } });
    if (res.ok || res.status === 206) {
      proxyState = 'ok';
      return remember(proxyUrl);
    }
    let payload = null;
    try { payload = await res.json(); } catch (_) { payload = null; }
    if (payload && payload.error !== undefined) {
      return payload.retryable ? { retry: true } : { missing: true };
    }
    proxyState = 'missing';
    return remember(directAudioUrl(word, accent));
  } catch (_) {
    proxyState = 'missing';
    return remember(directAudioUrl(word, accent));
  }
}

// --- speech synthesis fallback -------------------------------------------------

let voicesCache = [];
function loadVoices() {
  if (!('speechSynthesis' in window)) return [];
  voicesCache = window.speechSynthesis.getVoices() || [];
  return voicesCache;
}
if ('speechSynthesis' in window) {
  loadVoices();
  window.speechSynthesis.onvoiceschanged = loadVoices;
}

export function ttsSupport() {
  if (!('speechSynthesis' in window)) {
    return { supported: false, uk: null, us: null, reason: '当前浏览器不支持语音合成' };
  }
  const voices = voicesCache.length ? voicesCache : loadVoices();
  const find = (lang) => voices.find((v) => (v.lang || '').toLowerCase().startsWith(lang)) || null;
  const uk = find('en-gb');
  const us = find('en-us');
  return {
    supported: true,
    uk,
    us,
    voices: voices.filter((v) => (v.lang || '').toLowerCase().startsWith('en')),
    reason: (!uk && !us) ? '系统未安装 en-GB / en-US 语音' : '',
  };
}

export function speak(text, accent) {
  const support = ttsSupport();
  const voice = accent === 'uk' ? support.uk : support.us;
  if (!support.supported || !voice) {
    toast('系统没有可用的 ' + (accent === 'uk' ? '英式' : '美式') + ' 合成语音', 'err');
    return false;
  }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.voice = voice;
  utterance.lang = voice.lang;
  utterance.rate = 0.9;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
  return true;
}

// --- reusable markup ----------------------------------------------------------

/**
 * Pronunciation buttons for a word.
 *
 * Three states per accent (``<accent>_audio_status`` from the API):
 *   available -> enabled, verified real recording
 *   missing   -> disabled "无音频" (providers were asked and have nothing)
 *   unknown   -> enabled, resolved on the first click (never shown as 无音频,
 *                because "not checked yet" is not the same as "no audio")
 */
export function soundButton(word, accent, { size = '' } = {}) {
  const status = word[`${accent}_audio_status`]
    || ((accent === 'uk' ? word.has_uk_audio : word.has_us_audio) ? 'available' : 'unknown');
  const button = document.createElement('button');
  button.className = `sound-btn ${accent} ${size}`.trim();
  button.dataset.accent = accent;
  button.dataset.wordId = word.id;
  button.dataset.url = audioUrlFor(word, accent);
  button.dataset.status = status;
  const label = accent === 'uk' ? '英' : '美';
  if (status === 'missing') {
    button.disabled = true;
    button.innerHTML = `<span class="accent-label">${label} 无音频</span>`;
    button.title = '已查询：该口音没有可用的真实录音（不会用另一种口音代替）';
  } else {
    button.innerHTML = `<span class="wave">▶</span><span class="accent-label">${label} 发音</span>`;
    button.title = status === 'available'
      ? (accent === 'uk' ? '播放英式发音（真实录音）' : '播放美式发音（真实录音）')
      : '点击即可获取该口音的真实录音';
  }
  return button;
}

export function soundButtons(word, options = {}) {
  const wrap = document.createElement('span');
  wrap.className = 'sound-group';
  for (const accent of ['uk', 'us']) wrap.append(soundButton(word, accent, options));
  return wrap;
}

/** Flip one button to the "no recording" state (definitive answer only). */
export function markAudioMissing(button) {
  if (!button) return;
  const accent = button.dataset.accent;
  const label = accent === 'uk' ? '英' : '美';
  button.disabled = true;
  button.dataset.status = 'missing';
  button.classList.remove('loading', 'playing');
  button.innerHTML = `<span class="accent-label">${label} 无音频</span>`;
  button.title = '已查询：该口音没有可用的真实录音（不会用另一种口音代替）';
}

/**
 * Play one accent of a word, resolving it on demand.
 *
 * The first click performs a cheap range request; a real 404 (with
 * ``retryable: false``) means the accent genuinely has no recording, so the
 * button is switched to "无音频".  A transient network error keeps the button
 * usable and simply asks the user to retry.
 */
export async function playWordAudio(word, accent, button) {
  if (accent !== 'uk' && accent !== 'us') return;
  const target = button || null;
  const status = target ? target.dataset.status : 'unknown';
  const resolved = await resolveAudioUrl(word, accent, target);
  if (resolved.retry) {
    toast('网络异常，暂时无法获取发音，请重试', 'err');
    return;
  }
  if (resolved.missing || !resolved.url) {
    markAudioMissing(target);
    toast(`“${(word && word.lemma) || ''}” 没有${accent === 'uk' ? '英式' : '美式'}录音`, 'err');
    return;
  }
  await playUrl(resolved.url, target);
}

export async function probeWords(wordIds, accents = ['uk', 'us']) {
  if (!wordIds.length) return [];
  const result = await api.probeAudio(wordIds, accents);
  return result.results || [];
}
