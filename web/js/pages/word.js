import { api } from '../api.js';
import { el, mount, toast, loadingCard, modal } from '../ui.js';
import { soundButtons, soundButton, playUrl, playWordAudio, ttsSupport, speak } from '../audio.js';
import { audioUrlFor } from '../api.js';

export async function render(root, wordId) {
  mount(root, loadingCard('正在读取词条…'));
  let word;
  try {
    word = await api.getWord(wordId);
  } catch (err) {
    mount(root, el('div', { class: 'notice danger', text: err.message }));
    return;
  }

  async function enrich(force = false) {
    try {
      const { words } = await api.enrich([word.id], force);
      if (words[0]) { word = { ...word, ...words[0] }; render(root, wordId); }
    } catch (err) { toast(err.message, 'err'); }
  }

  const sounds = soundButtons(word);
  sounds.querySelectorAll('.sound-btn').forEach((btn) => {
    btn.addEventListener('click', () => playWordAudio(word, btn.dataset.accent, btn));
  });

  const favoriteBtn = el('button', {
    class: `btn small ${word.favorite ? '' : 'ghost'}`,
    text: word.favorite ? '★ 已收藏' : '☆ 收藏',
    onclick: async () => {
      const updated = await api.updateWord(word.id, { favorite: !word.favorite });
      word = { ...word, ...updated };
      render(root, wordId);
    },
  });
  const masteredBtn = el('button', {
    class: `btn small ${word.mastered ? 'primary' : 'ghost'}`,
    text: word.mastered ? '✓ 已掌握' : '标记已掌握',
    onclick: async () => {
      const updated = await api.updateWord(word.id, { mastered: !word.mastered });
      word = { ...word, ...updated };
      render(root, wordId);
    },
  });

  const sourceLine = el('div', { class: 'tiny muted', style: 'margin-top:6px' }, [
    `音标来源：${word.ipa_source || '—'} · 释义来源：${word.meaning_source || '—'} · ` +
    `发音来源：英式 ${word.uk_audio_source || '无'} / 美式 ${word.us_audio_source || '无'}`,
  ]);

  const ipaTable = el('table', {}, [
    el('thead', {}, [el('tr', {}, [
      el('th', { text: '口音' }), el('th', { text: 'IPA' }), el('th', { text: '发音' }), el('th', { text: '来源' }),
    ])]),
    el('tbody', {}, [
      el('tr', {}, [
        el('td', {}, [el('span', { class: 'tag uk', text: '英 UK' })]),
        el('td', { class: 'mono', text: word.uk_ipa ? `/${word.uk_ipa}/` : '未获取到' }),
        el('td', {}, [soundButton(word, 'uk')]),
        el('td', { class: 'small muted', text: word.uk_audio_source || '—' }),
      ]),
      el('tr', {}, [
        el('td', {}, [el('span', { class: 'tag us', text: '美 US' })]),
        el('td', { class: 'mono', text: word.us_ipa ? `/${word.us_ipa}/` : '未获取到' }),
        el('td', {}, [soundButton(word, 'us')]),
        el('td', { class: 'small muted', text: word.us_audio_source || '—' }),
      ]),
    ]),
  ]);
  ipaTable.querySelectorAll('.sound-btn').forEach((btn) => {
    btn.addEventListener('click', () => playWordAudio(word, btn.dataset.accent, btn));
  });

  const chapters = el('div', { class: 'grid' }, (word.chapters || []).map((c) => el('div', { class: 'row' }, [
    el('a', { href: `#/chapter/${c.id}`, text: c.title }),
    el('span', { class: 'tag', text: `${c.count || 0} 次` }),
    el('span', { class: 'small muted', text: (c.pages || []).length ? `页码 ${(JSON.parse(c.pages || '[]') || []).join('、')}` : '' }),
  ])));

  // --- 跟读（Phase 4 groundwork: record + replay, evaluation hook) ------------
  const recordState = { recorder: null, chunks: [], url: null };
  const recordStatus = el('div', { class: 'small muted', text: '尚未录音' });
  const playback = el('audio', { controls: true, style: 'display:none;width:100%;margin-top:8px' });
  const startBtn = el('button', { class: 'btn small', text: '🎙 开始录音' });
  const stopBtn = el('button', { class: 'btn small', text: '■ 停止', disabled: true });
  const clearBtn = el('button', { class: 'btn small ghost', text: '清除', disabled: true });

  startBtn.addEventListener('click', async () => {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      toast('当前浏览器不支持录音', 'err');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordState.recorder = new MediaRecorder(stream);
      recordState.chunks = [];
      recordState.recorder.ondataavailable = (event) => recordState.chunks.push(event.data);
      recordState.recorder.onstop = () => {
        const blob = new Blob(recordState.chunks, { type: 'audio/webm' });
        if (recordState.url) URL.revokeObjectURL(recordState.url);
        recordState.url = URL.createObjectURL(blob);
        playback.src = recordState.url;
        playback.style.display = 'block';
        recordStatus.textContent = '录音完成，可回放对比';
        clearBtn.disabled = false;
        stream.getTracks().forEach((track) => track.stop());
      };
      recordState.recorder.start();
      startBtn.disabled = true;
      stopBtn.disabled = false;
      recordStatus.textContent = '录音中…请朗读该单词';
    } catch (err) {
      toast(`无法访问麦克风：${err.message}`, 'err');
    }
  });
  stopBtn.addEventListener('click', () => {
    recordState.recorder?.stop();
    startBtn.disabled = false;
    stopBtn.disabled = true;
  });
  clearBtn.addEventListener('click', () => {
    if (recordState.url) URL.revokeObjectURL(recordState.url);
    recordState.url = null;
    playback.style.display = 'none';
    playback.src = '';
    recordStatus.textContent = '尚未录音';
    clearBtn.disabled = true;
  });

  const tts = ttsSupport();

  mount(root,
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/', text: '首页' }), ' / ',
      el('a', { href: `#/word/${word.id}`, text: word.lemma }), ' 详情',
    ]),
    el('div', { class: 'card' }, [
      el('div', { class: 'row' }, [
        el('div', { style: 'flex:1;min-width:200px' }, [
          el('h1', { style: 'margin:0', text: word.lemma }),
          (word.pos ? el('div', { class: 'small muted', text: word.pos }) : null),
        ]),
        sounds,
      ]),
      el('div', { style: 'display:flex;gap:22px;flex-wrap:wrap;margin:12px 0' }, [
        el('div', {}, [el('div', { class: 'tiny muted', text: '英式 IPA' }),
          el('div', { class: 'mono', style: 'color:var(--uk);font-size:16px', text: word.uk_ipa ? `/${word.uk_ipa}/` : '—' })]),
        el('div', {}, [el('div', { class: 'tiny muted', text: '美式 IPA' }),
          el('div', { class: 'mono', style: 'color:var(--us);font-size:16px', text: word.us_ipa ? `/${word.us_ipa}/` : '—' })]),
      ]),
      el('div', { class: 'notice', text: word.meaning_cn || '（暂无中文释义，可点击“重新获取”）' }),
      (!word.meaning_cn && word.ocr_meaning) || (word.ocr_meaning && word.meaning_cn !== word.ocr_meaning)
        ? el('div', { class: 'small muted', style: 'margin-top:-4px' },
          [`原书释义（OCR 识别，可能有误）：${word.ocr_meaning}`])
        : null,
      sourceLine,
      el('div', { class: 'row', style: 'margin-top:12px' }, [
        favoriteBtn, masteredBtn,
        el('button', { class: 'btn small ghost', text: '重新获取音标/释义', onclick: () => enrich(true) }),
      ]),
    ]),
    el('div', { class: 'card' }, [el('h3', { text: '英美发音对照' }), ipaTable]),
    el('div', { class: 'card' }, [
      el('h3', { text: '出现章节' }),
      (word.chapters || []).length ? chapters : el('div', { class: 'muted small', text: '没有章节记录' }),
    ]),
    el('div', { class: 'card' }, [
      el('h3', { text: '跟读练习（预留功能）' }),
      el('p', { class: 'small muted' },
        ['先播放标准发音，再录下自己的读音回放对比。发音评估服务尚未配置，因此这里不会给出评分。']),
      el('div', { class: 'row' }, [startBtn, stopBtn, clearBtn]),
      recordStatus,
      playback,
      el('div', { class: 'row', style: 'margin-top:10px' }, [
        el('button', {
          class: 'btn small', text: '对比播放标准音',
          onclick: () => playUrl(audioUrlFor(word, word.has_uk_audio ? 'uk' : 'us'), null),
        }),
        el('button', {
          class: 'btn small ghost',
          text: '合成语音（备用）',
          title: tts.supported ? `可用语音：${(tts.voices || []).map((v) => v.lang).slice(0, 4).join(', ') || '无'}` : tts.reason,
          onclick: () => {
            if (!tts.uk && !tts.us) { toast('系统没有 en-GB / en-US 合成语音', 'err'); return; }
            speak(word.lemma, tts.uk ? 'uk' : 'us');
          },
        }),
        el('button', {
          class: 'btn small ghost', text: '发音评估（未配置）',
          onclick: () => modal({
            title: '发音评估服务未配置',
            body: `<p class="small">本版本不提供自动发音评分：普通语音识别并不等于发音评估，随意打分会产生误导。</p>
                   <p class="small">后续接入方式：在 <code>app/pronunciation.py</code> 中实现
                   <code>evaluate(audio_bytes, word, accent)</code>，对接 Azure Speech
                   Pronunciation Assessment、ELSA、讯飞等服务的评分接口；密钥放在服务端环境变量中，
                   不要写入前端。</p>`,
          }),
        }),
      ]),
      (!tts.uk || !tts.us) ? el('div', { class: 'tiny muted', style: 'margin-top:6px' },
        [`系统语音检测：${tts.uk ? '有 en-GB' : '无 en-GB'} / ${tts.us ? '有 en-US' : '无 en-US'}。` +
         '合成语音只作为备用，界面上始终标注为“合成语音”。']) : null,
    ]),
  );
}
