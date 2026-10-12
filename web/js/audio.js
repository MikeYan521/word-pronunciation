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
});
player.addEventListener('error', () => {
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
  player.pause();
  clearButton(currentButton);
  currentButton = null;
}

export async function playUrl(url, button, forceReload = false) {
  if (!url) return;
  if (button && button.disabled && !forceReload) return;

  const isSame = player.dataset.url === url && player.src && !forceReload;
  if (currentButton && currentButton !== button) clearButton(currentButton);
  currentButton = button || null;

  if (isSame) {
    // repeat the same clip without overlap
    player.pause();
    try { player.currentTime = 0; } catch (_) { /* 某些浏览器在 metadata 未就绪时会抛错 */ }
    if (player.readyState === 0) player.load();   // 资源被浏览器回收时重新加载
    if (button) button.classList.add('playing');
    try {
      await player.play();
    } catch (err) {
      // 再试一次：先 load() 再播（移动端 Safari/Chrome 在资源被释放后常见）
      try {
        player.load();
        await player.play();
      } catch (err2) {
        clearButton(button);
        currentButton = null;
        toast(`播放失败：${(err2 && err2.message) || err2}`, 'err', {
          label: '重试',
          onClick: () => playUrl(url, button, true),
        });
      }
    }
    return;
  }

  if (button) {
    button.classList.add('loading');
    const icon = button.querySelector('.wave');
    if (icon) icon.textContent = '⋯';
  }
  player.dataset.url = url;
  player.src = url;
  try {
    await player.play();
    if (button) {
      button.classList.remove('loading');
      button.classList.add('playing');
      const icon = button.querySelector('.wave');
      if (icon) icon.textContent = '❚❚';
    }
  } catch (err) {
    clearButton(button);
    currentButton = null;
    if (forceReload) {
      toast('仍然无法播放该发音音频', 'err');
    }
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

  // 已经知道哪个地址能播 → 一直用它（重播走的也是同一个地址，才能重复播放）
  let url = target && target.dataset.playUrl;

  if (!url) {
    if (proxyState === 'missing') {
      url = directAudioUrl(word, accent);
    } else {
      const proxyUrl = audioUrlFor(word, accent);
      try {
        const res = await fetch(proxyUrl, { headers: { Range: 'bytes=0-1' } });
        if (res.ok || res.status === 206) {
          proxyState = 'ok';
          url = proxyUrl;
        } else {
          let payload = null;
          try { payload = await res.json(); } catch (_) { payload = null; }
          if (payload && payload.error !== undefined) {
            // 是我们的代理返回的明确结果
            if (payload.retryable) {
              toast('网络异常，暂时无法获取发音，请重试', 'err');
              return;
            }
            markAudioMissing(target);
            toast(`“${(word && word.lemma) || ''}” 没有${accent === 'uk' ? '英式' : '美式'}录音`, 'err');
            return;
          }
          // 404 但不是我们的 JSON → 站点没部署 /api/audio（纯静态托管）
          proxyState = 'missing';
          url = directAudioUrl(word, accent);
        }
      } catch (_) {
        // 网络异常或跨域：退回直连地址
        proxyState = 'missing';
        url = directAudioUrl(word, accent);
      }
    }
    if (target && url) {
      target.dataset.playUrl = url;
      if (!target.dataset.status || target.dataset.status === 'unknown') target.dataset.status = 'available';
    }
  }

  if (!url) return;
  await playUrl(url, target);
}

export async function probeWords(wordIds, accents = ['uk', 'us']) {
  if (!wordIds.length) return [];
  const result = await api.probeAudio(wordIds, accents);
  return result.results || [];
}
