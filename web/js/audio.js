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
    player.currentTime = 0;
    if (button) button.classList.add('playing');
    try { await player.play(); } catch (_) { /* autoplay guard */ }
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
  const url = audioUrlFor(word, accent);
  const target = button || null;
  const status = target ? target.dataset.status : 'unknown';

  // 站点未部署 /api/audio 函数时（只上传了静态文件），退回浏览器直连录音地址：
  // <audio> 播放媒体不受 CORS 限制，仍然播放的是词典真实录音。
  const directFallback = () => {
    const type = accent === 'uk' ? 1 : 2;
    const remote = `https://dict.youdao.com/dictvoice?audio=${encodeURIComponent(word.lemma || word)}&type=${type}`;
    return playUrl(remote, target).then(() => {
      if (target) target.dataset.status = 'available';
    });
  };

  if (status === 'available' || status === 'unknown') {
    if (status === 'unknown' && word.id !== undefined) {
      // resolve first so we can tell "no recording" from "network problem"
      try {
        const probe = await fetch(url, {
          headers: { Range: 'bytes=0-1' },
        });
        if (!probe.ok) {
          let payload = {};
          try { payload = await probe.json(); } catch (_) { /* not json */ }
          // 404 但响应不是我们的 JSON（说明没有部署函数）→ 直接试录音源
          if (!payload || payload.error === undefined) {
            await directFallback();
            return;
          }
          if (payload.retryable) {
            toast('网络异常，暂时无法获取发音，请重试', 'err');
            return;
          }
          markAudioMissing(target);
          toast(`“${word.lemma || ''}” 没有${accent === 'uk' ? '英式' : '美式'}录音`, 'err');
          return;
        }
        if (target) target.dataset.status = 'available';
      } catch (err) {
        await directFallback();
        return;
      }
    }
    await playUrl(url, target);
    return;
  }
  // status === 'missing': server said there is nothing, but allow a manual retry
  await playUrl(audioUrlFor(word, accent, true), target);
}

export async function probeWords(wordIds, accents = ['uk', 'us']) {
  if (!wordIds.length) return [];
  const result = await api.probeAudio(wordIds, accents);
  return result.results || [];
}
