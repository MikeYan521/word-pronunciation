// Chapter vocabulary page: IPA, dual pronunciation, progress, filtering.

import { api } from '../api.js?v=20261125-2';
import { el, mount, toast, loadingCard, modal } from '../ui.js';
import { navigate } from '../app.js?v=20261125-2';
import { soundButtons, probeWords, playWordAudio,
         playAndWait, pauseAudio, resumeAudio, stopAudio, resolveAudioUrl,
         immediateAudioUrl } from '../audio.js?v=20261125-2';
import { LoopController, REPEAT_OPTIONS, INTERVAL_OPTIONS, ACCENT_OPTIONS, ORDER_OPTIONS,
         LOOP_DEFAULTS } from '../core/loop.js?v=20261125-2';
import { onUnmount } from '../lifecycle.js?v=20261125-2';

const state = {
  words: [],
  filter: 'all',
  search: '',
  sort: 'freq',
  enrichDone: 0,
  probeDone: 0,
  renderLimit: 80,      // 手机上一次性渲染上千行会明显卡顿：分批渲染
};

const RENDER_STEP = 80;

export async function render(root, chapterId) {
  mount(root, loadingCard('正在读取章节词汇…'));
  let chapter;
  try {
    chapter = await api.getChapter(chapterId);
  } catch (err) {
    mount(root, el('div', { class: 'notice danger', text: err.message }));
    return;
  }

  state.words = chapter.words || [];
  state.enrichDone = state.words.filter((w) => w.uk_ipa || w.us_ipa || w.meaning_cn).length;
  state.probeDone = state.words.filter((w) => w.has_uk_audio || w.has_us_audio).length;
  state.filter = 'all';
  state.search = '';
  state.renderLimit = RENDER_STEP;
  // Word books keep the printed order; prose is sorted by frequency.
  state.sort = chapter.extraction_mode === 'entries' ? 'original' : 'freq';

  const book = chapter.textbook || {};
  const listHost = el('div', { class: 'word-list' });

  const searchInput = el('input', {
    type: 'search',
    class: 'chapter-search',
    placeholder: '搜索单词…',
  });
  searchInput.addEventListener('input', () => {
    state.search = searchInput.value.trim().toLowerCase();
    state.renderLimit = RENDER_STEP;
    draw();
  });

  function visibleWords() {
    let words = state.words.slice();
    if (state.search) {
      words = words.filter((w) => w.lemma.includes(state.search) || (w.meaning_cn || '').includes(state.search));
    }
    if (state.filter === 'todo') words = words.filter((w) => !w.mastered);
    if (state.filter === 'fav') words = words.filter((w) => w.favorite);
    if (state.filter === 'nofav') words = words.filter((w) => !w.favorite);
    if (state.sort === 'alpha') words.sort((a, b) => a.lemma.localeCompare(b.lemma));
    else if (state.sort === 'recent') words.sort((a, b) => (a.chapter_pages?.[0] || 0) - (b.chapter_pages?.[0] || 0));
    else if (state.sort === 'original') words.sort((a, b) => (a.chapter_ordinal || 0) - (b.chapter_ordinal || 0));
    else words.sort((a, b) => (b.chapter_count || 0) - (a.chapter_count || 0) || a.lemma.localeCompare(b.lemma));
    return words;
  }

  function makeRow(word) {
    const row = el('div', { class: 'word-row', dataset: { wordId: word.id } });

    const star = el('button', {
      class: `star ${word.favorite ? 'on' : ''}`, title: word.favorite ? '取消收藏' : '收藏',
      text: word.favorite ? '★' : '☆',
      onclick: async () => {
        const updated = await api.updateWord(word.id, { favorite: !word.favorite });
        Object.assign(word, updated);
        star.textContent = word.favorite ? '★' : '☆';
        star.classList.toggle('on', word.favorite);
        if (state.filter === 'fav' || state.filter === 'nofav') draw();
      },
    });

    const mastered = el('button', {
      class: `tag ${word.mastered ? 'ok' : ''}`,
      style: 'cursor:pointer;border:0',
      text: word.mastered ? '✓ 已掌握' : '未掌握',
      onclick: async () => {
        const updated = await api.updateWord(word.id, { mastered: !word.mastered });
        Object.assign(word, updated);
        mastered.textContent = word.mastered ? '✓ 已掌握' : '未掌握';
        mastered.className = `tag ${word.mastered ? 'ok' : ''}`;
        if (state.filter === 'todo') draw();
      },
    });

    const ipa = el('div', { class: 'ipa-pair' }, [
      el('div', { class: 'uk' }, [el('span', { class: 'k', text: '英' }), word.uk_ipa ? `/${word.uk_ipa}/` : '—']),
      el('div', { class: 'us' }, [el('span', { class: 'k', text: '美' }), word.us_ipa ? `/${word.us_ipa}/` : '—']),
    ]);

    // In entry mode the book's own POS / Chinese text is shown while the
    // online meaning is still loading (OCR text is a hint, not authoritative).
    const meaningText = word.meaning_cn
      || (word.ocr_meaning ? `原书：${word.ocr_meaning}` : '')
      || (word.in_dict ? '…' : '（词典未收录，可能是 OCR 误识）');
    const tags = [];
    if (chapter.extraction_mode === 'entries' && word.chapter_ordinal) {
      tags.push(el('span', { class: 'tag', style: 'margin-right:6px', text: `#${word.chapter_ordinal}` }));
    }
    if (word.pos) tags.push(el('span', { class: 'tag', style: 'margin-right:6px', text: word.pos }));
    if (!word.in_dict) tags.push(el('span', { class: 'tag warn', style: 'margin-right:6px', text: '未收录' }));
    const main = el('div', { class: 'word-main' }, [
      el('a', { href: `#/word/${word.id}`, style: 'color:inherit' }, [
        el('div', { class: 'spelling', text: word.display || word.lemma }),
        (tags.length ? el('div', { class: 'row tight', style: 'margin:2px 0' }, tags) : null),
      ]),
      el('div', { class: 'meaning', text: meaningText }),
    ]);

    const sounds = soundButtons(word);
    sounds.querySelectorAll('.sound-btn').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const accent = btn.dataset.accent;
        if (loop && loop.status === 'playing') loop.pause();   // 手动试听时先暂停循环，避免抢音频
        if (btn.dataset.status === 'missing') {
          // previously reported as missing - let the user force a retry
          delete btn.dataset.playUrl;
          playWordAudio(word, accent, btn).catch(() => {});
          return;
        }
        playWordAudio(word, accent, btn).catch(() => {});
      });
    });

    const actions = el('div', { class: 'word-actions' }, [
      sounds,
      star,
      mastered,
      el('button', {
        class: 'icon-btn', title: '详情', text: '›',
        onclick: () => navigate(`/word/${word.id}`),
      }),
    ]);

    row.append(main, ipa, actions);
    // 点击词条正文即可试听：循环模式开启时连续播放，关闭时只播放一次。
    // 发音、收藏、掌握和详情按钮仍分别执行自己的操作。
    row.addEventListener('click', (event) => {
      if (event.target.closest('button, input, select, textarea')) return;
      event.preventDefault();
      if (loopModeEnabled) startLoopForWord(word);
      else playWordOnce(word, row);
    });
    return row;
  }

  const rowById = new Map();
  let visibleNow = [];
  let footer = null;
  let observer = null;

  /** 渲染 [from, state.renderLimit) 区间的行，并登记到 rowById。 */
  function renderChunk(from) {
    const slice = visibleNow.slice(from, state.renderLimit);
    const nodes = slice.map(makeRow);
    slice.forEach((word, index) => rowById.set(word.id, nodes[index]));
    return nodes;
  }

  /** 底部"显示更多"：只追加新行，绝不重绘已渲染的部分（手机上这是卡顿的主因）。 */
  function appendMore(step = RENDER_STEP) {
    if (state.renderLimit >= visibleNow.length) return;
    const from = state.renderLimit;
    state.renderLimit = Math.min(visibleNow.length, state.renderLimit + step);
    const nodes = renderChunk(from);
    if (footer) footer.before(...nodes);
    else listHost.append(...nodes);
    updateFooter();
  }

  function updateFooter() {
    if (observer) { observer.disconnect(); observer = null; }
    if (footer) { footer.remove(); footer = null; }
    const remaining = visibleNow.length - state.renderLimit;
    if (remaining <= 0) return;
    const sentinel = el('div', { style: 'height:1px' });
    const more = el('button', {
      class: 'btn', style: 'margin:16px auto;display:block',
      text: `显示更多（还有 ${remaining} 个）`,
      onclick: () => appendMore(RENDER_STEP * 2),
    });
    footer = el('div', { style: 'padding-bottom:8px' }, [sentinel, more]);
    listHost.append(footer);
    if ('IntersectionObserver' in window) {
      observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) appendMore();
      }, { rootMargin: '300px' });
      observer.observe(sentinel);
    }
  }

  function draw() {
    visibleNow = visibleWords();
    if (!state.renderLimit || state.renderLimit < RENDER_STEP) state.renderLimit = RENDER_STEP;
    rowById.clear();
    if (observer) { observer.disconnect(); observer = null; }
    footer = null;
    const nodes = renderChunk(0);
    if (!visibleNow.length) {
      mount(listHost, el('div', { class: 'muted center', style: 'padding:24px', text: '没有符合条件的单词' }));
      return;
    }
    mount(listHost, ...nodes);
    updateFooter();
    if (loopReady) applyLoopHighlight(false);
  }

  /** Replace only the rows whose data changed (keeps scroll position). */
  function updateRows(ids) {
    for (const id of ids) {
      const word = state.words.find((w) => w.id === id);
      const old = rowById.get(id);
      if (!word || !old || !old.parentNode) continue;
      const fresh = makeRow(word);
      old.replaceWith(fresh);
      rowById.set(id, fresh);
    }
  }

  const statusLine = el('div', { class: 'small muted' });

  async function enrichAll(force = false) {
    const pending = state.words.filter((w) => force || !(w.uk_ipa || w.us_ipa || w.meaning_cn));
    if (!pending.length) return;
    statusLine.textContent = `正在获取释义与音标（0/${pending.length}）…`;
    const batchSize = 10;
    for (let i = 0; i < pending.length; i += batchSize) {
      const batch = pending.slice(i, i + batchSize);
      try {
        const { words } = await api.enrich(batch.map((w) => w.id), force);
        const byId = new Map(words.map((w) => [w.id, w]));
        const changed = [];
        state.words.forEach((word, index) => {
          const fresh = byId.get(word.id);
          if (fresh) {
            state.words[index] = { ...word, ...fresh };
            changed.push(word.id);
          }
        });
        state.enrichDone = state.words.filter((w) => w.uk_ipa || w.us_ipa || w.meaning_cn).length;
        updateRows(changed);
        statusLine.textContent = `正在获取释义与音标（${Math.min(i + batchSize, pending.length)}/${pending.length}）…`;
      } catch (err) {
        statusLine.textContent = `释义获取失败：${err.message}`;
        toast(`释义获取失败：${err.message}`, 'err');
        break;
      }
    }
    statusLine.textContent = '释义与音标已更新（数据来源：本地 ipa-dict + 在线词典，缓存到本机数据库）';
  }

  async function probeAll() {
    const targets = state.words.slice();
    if (!targets.length) return;
    statusLine.textContent = `正在校验英美发音（0/${targets.length}）…`;
    const batchSize = 6;
    let processed = 0;
    // A few batches in flight at once keeps this quick without hammering the
    // providers (the earlier strictly-sequential version took minutes).
    const queue = [];
    for (let i = 0; i < targets.length; i += batchSize) queue.push(targets.slice(i, i + batchSize));
    const CONCURRENCY = 3;

    async function runBatch(batch) {
      try {
        const results = await probeWords(batch.map((w) => w.id));
        const byId = new Map(results.map((r) => [r.word_id, r]));
        const changed = [];
        state.words.forEach((word, index) => {
          const fresh = byId.get(word.id);
          if (fresh) {
            const ukStatus = fresh.accents.uk?.available ? 'available'
              : (fresh.accents.uk?.retryable ? 'unknown' : 'missing');
            const usStatus = fresh.accents.us?.available ? 'available'
              : (fresh.accents.us?.retryable ? 'unknown' : 'missing');
            state.words[index] = {
              ...word,
              has_uk_audio: !!fresh.accents.uk?.available,
              has_us_audio: !!fresh.accents.us?.available,
              uk_audio_status: ukStatus,
              us_audio_status: usStatus,
              uk_audio_source: fresh.accents.uk?.provider || null,
              us_audio_source: fresh.accents.us?.provider || null,
            };
            changed.push(word.id);
          }
        });
        state.probeDone = state.words.filter((w) => w.has_uk_audio || w.has_us_audio).length;
        updateRows(changed);
      } catch (err) {
        // leave the batch unverified - the buttons stay usable
        console.warn('probe batch failed', err);
      } finally {
        processed += batch.length;
        statusLine.textContent = `正在校验英美发音（${Math.min(processed, targets.length)}/${targets.length}）…`;
      }
    }

    let cursor = 0;
    async function worker() {
      while (cursor < queue.length) {
        const batch = queue[cursor++];
        await runBatch(batch);
      }
    }
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, worker));

    const missing = state.words.filter((w) => !w.has_uk_audio && !w.has_us_audio).length;
    statusLine.textContent = `发音校验完成：${state.probeDone}/${state.words.length} 个单词至少有一种真实发音` +
      (missing ? `（${missing} 个单词暂未取得音频）` : '');
  }

  // ------------------------------------------------------------ 循环播放
  const LOOP_STORAGE_KEY = 'wordbook.loop.settings';

  function loadLoopSettings() {
    try {
      const raw = localStorage.getItem(LOOP_STORAGE_KEY);
      if (!raw) return { ...LOOP_DEFAULTS };
      const saved = JSON.parse(raw);
      const repeat = saved.repeat === 'Infinity' || saved.repeat === null
        ? Infinity : Number(saved.repeat);
      return {
        repeat: REPEAT_OPTIONS.includes(repeat) ? repeat : LOOP_DEFAULTS.repeat,
        interval: INTERVAL_OPTIONS.includes(Number(saved.interval))
          ? Number(saved.interval) : LOOP_DEFAULTS.interval,
        accents: ['uk', 'us', 'both'].includes(saved.accents) ? saved.accents : LOOP_DEFAULTS.accents,
        order: ['seq', 'random'].includes(saved.order) ? saved.order : LOOP_DEFAULTS.order,
        loopList: Boolean(saved.loopList),
        autoNext: Boolean(saved.autoNext),
        enabled: Boolean(saved.enabled),
      };
    } catch (_) {
      return { ...LOOP_DEFAULTS };
    }
  }

  function saveLoopSettings(settings) {
    try {
      localStorage.setItem(LOOP_STORAGE_KEY, JSON.stringify({
        ...settings,
        enabled: loopModeEnabled,
        repeat: settings.repeat === Infinity ? 'Infinity' : settings.repeat,
      }));
    } catch (_) { /* 隐私模式下可能写不了，忽略 */ }
  }

  let lastLoopWordId = null;
  let lastLoopErrorShown = '';
  let loopRedrawGuard = false;
  let loopReady = false;

  function loopCurrentWordId() {
    const snap = loop.snapshot();
    return snap.word ? snap.word.id : null;
  }

  /** 给正在播放的词加高亮；必要时把尚未渲染的那一行补渲染出来 */
  function applyLoopHighlight(scroll = false) {
    if (!loopReady) return;
    document.querySelectorAll('.word-row.loop-current').forEach((row) => row.classList.remove('loop-current'));
    const id = loopCurrentWordId();
    if (id === undefined || id === null) return;
    const row = rowById.get(id);
    if (!row) {
      if (loopRedrawGuard) return;
      const list = visibleWords();
      const position = list.findIndex((word) => word.id === id);
      if (position < 0 || position < state.renderLimit) return;
      loopRedrawGuard = true;
      state.renderLimit = position + 1;
      draw();
      loopRedrawGuard = false;
    }
    const target = rowById.get(id);
    if (!target) return;
    target.classList.add('loop-current');
    if (scroll) {
      try { target.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (_) { /* ignore */ }
    }
  }

  const loopStatus = el('div', { class: 'loop-status small', text: '循环播放：未开始' });
  const loopBar = el('div', { class: 'progress loop-progress' }, [el('span', { style: 'width:0%' })]);
  const loopModeButton = el('button', {
    type: 'button',
    class: 'loop-mode-btn',
    'aria-pressed': 'false',
    title: '开启后点击任意单词即可连续播放',
  }, [
    el('span', { class: 'loop-mode-btn-icon' }),
    el('span', { class: 'loop-mode-btn-label', text: '循环模式 · 关闭' }),
  ]);
  const loopPauseBtn = el('button', { class: 'btn small', text: '⏸ 暂停', disabled: true });
  const loopStopBtn = el('button', { class: 'btn small', text: '⏹ 停止', disabled: true });
  const loopPrevBtn = el('button', { class: 'icon-btn', title: '上一个单词', text: '⏮' });
  const loopNextBtn = el('button', { class: 'icon-btn', title: '下一个单词', text: '⏭' });
  const loopPlaybackControls = el('div', { class: 'loop-controls', hidden: true }, [
    loopPrevBtn, loopPauseBtn, loopStopBtn, loopNextBtn,
  ]);
  const loopSettingsBox = el('div', { class: 'loop-settings', hidden: true });
  const loopSettingsBtn = el('button', {
    class: 'btn small ghost', text: '⚙ 设置',
    onclick: () => { loopSettingsBox.hidden = !loopSettingsBox.hidden; },
  });

  const loopPanel = el('div', { class: 'card loop-panel' }, [
    el('div', { class: 'loop-panel-head' }, [
      el('div', { class: 'loop-panel-copy' }, [
        el('h3', { text: '循环播放' }),
        el('div', { class: 'tiny muted', text: '开启后点击单词连续播放；关闭时点击单词试听一次。' }),
      ]),
      el('div', { class: 'loop-panel-actions' }, [loopModeButton, loopSettingsBtn]),
    ]),
    loopPlaybackControls,
    loopStatus,
    loopBar,
    loopSettingsBox,
  ]);

  function selectOptions(options, current, onChange, format = (value) => String(value)) {
    const box = el('select', {}, options.map(({ value, label }) =>
      el('option', { value: String(value), text: label })));
    box.value = String(current);
    box.addEventListener('change', () => onChange(box.value));
    return box;
  }

  const initialLoop = loadLoopSettings();
  const repeatSelect = selectOptions(
    REPEAT_OPTIONS.map((value) => ({ value, label: value === Infinity ? '无限循环' : `${value} 次` })),
    initialLoop.repeat,
    (value) => updateLoopSettings({ repeat: value === 'Infinity' ? Infinity : Number(value) }),
  );
  const intervalSelect = selectOptions(
    INTERVAL_OPTIONS.map((value) => ({ value, label: `${value} 秒` })),
    initialLoop.interval,
    (value) => updateLoopSettings({ interval: Number(value) }),
  );
  const accentSelect = selectOptions(ACCENT_OPTIONS, initialLoop.accents,
    (value) => updateLoopSettings({ accents: value }));
  const orderSelect = selectOptions(ORDER_OPTIONS, initialLoop.order,
    (value) => updateLoopSettings({ order: value }));
  const loopListToggle = el('input', { type: 'checkbox' });
  loopListToggle.checked = initialLoop.loopList;
  loopListToggle.addEventListener('change', () =>
    updateLoopSettings({ loopList: loopListToggle.checked }));
  const autoNextToggle = el('input', { type: 'checkbox' });
  autoNextToggle.checked = initialLoop.autoNext;
  autoNextToggle.addEventListener('change', () =>
    updateLoopSettings({ autoNext: autoNextToggle.checked }));

  mount(loopSettingsBox,
    el('div', { class: 'loop-settings-grid' }, [
      el('label', { class: 'field' }, ['单词重复次数', repeatSelect]),
      el('label', { class: 'field' }, ['播放间隔', intervalSelect]),
      el('label', { class: 'field' }, ['发音选择', accentSelect]),
      el('label', { class: 'field' }, ['播放顺序', orderSelect]),
      el('div', { class: 'field', style: 'gap:6px' }, [
        el('label', { class: 'row tight', style: 'gap:8px' }, [autoNextToggle, '一个词播完自动下一个']),
        el('label', { class: 'row tight', style: 'gap:8px' }, [loopListToggle, '列表播完后从头循环']),
      ]),
    ]),
    el('p', { class: 'tiny muted', style: 'margin:8px 0 0' },
      ['开启循环模式后，点击任意单词即开始循环；点另一个词会立即切换。'
       + '关闭「自动下一个」时，一个词循环完就停。列表 = 当前筛选/排序下的单词。']),
  );

  function updateLoopSettings(patch) {
    loop.setSettings(patch);
    saveLoopSettings(loop.settings);
  }

  let loopModeEnabled = false;

  function syncLoopModeUI() {
    loopModeButton.setAttribute('aria-pressed', String(loopModeEnabled));
    loopModeButton.classList.toggle('on', loopModeEnabled);
    loopModeButton.querySelector('.loop-mode-btn-label').textContent =
      loopModeEnabled ? '循环模式 · 开启' : '循环模式 · 关闭';
    loopModeButton.title = loopModeEnabled
      ? '关闭后点击单词只播放一次'
      : '开启后点击单词连续播放';
    loopPlaybackControls.hidden = !loopModeEnabled;
    listHost.classList.toggle('loop-mode', loopModeEnabled);
  }

  /** 刷新面板状态与按钮可用性（循环模式开关 / 播放状态都会调用） */
  function updateLoopControls(snapshot, event) {
    const total = snapshot.repeatTotal === Infinity ? '∞' : snapshot.repeatTotal;
    const accentLabel = snapshot.accent === 'uk' ? '英式' : '美式';
    if (snapshot.status === 'idle') {
      if (!loopModeEnabled) {
        loopStatus.textContent = '循环模式已关闭 · 点击单词或发音按钮试听一次';
      } else {
        loopStatus.textContent = event === 'completed'
          ? `循环完成：共播放 ${snapshot.played} 段 · 点击任意单词继续`
          : '循环模式已开启 · 点击任意单词开始循环';
      }
      lastLoopWordId = null;
      applyLoopHighlight(false);      // 停止/播完后取消高亮
    } else {
      const word = snapshot.word ? (snapshot.word.display || snapshot.word.lemma) : '…';
      const phase = snapshot.status === 'paused' ? '已暂停'
        : snapshot.status === 'waiting' ? '间隔中' : '播放中';
      loopStatus.textContent = `${phase} · ${word} · 第 ${snapshot.repeatIndex}/${total} 次`
        + ` · ${accentLabel} · 间隔 ${snapshot.settings.interval}s`;
      if (snapshot.word && snapshot.word.id !== lastLoopWordId) {
        lastLoopWordId = snapshot.word.id;
        applyLoopHighlight(true);
      } else {
        applyLoopHighlight(false);
      }
    }
    if (snapshot.lastError) {
      loopStatus.title = snapshot.lastError;
      if (snapshot.lastError !== lastLoopErrorShown) {
        lastLoopErrorShown = snapshot.lastError;
        toast(snapshot.lastError, 'err');
      }
    } else {
      lastLoopErrorShown = '';
      loopStatus.removeAttribute('title');
    }

    const active = snapshot.status !== 'idle';
    loopBar.firstChild.style.width = active
      ? `${Math.min(100, Math.round((snapshot.repeatIndex / Math.max(1, snapshot.repeatTotal === Infinity ? 1 : snapshot.repeatTotal)) * 100))}%`
      : '0%';
    loopPauseBtn.textContent = snapshot.status === 'paused' ? '▶ 继续' : '⏸ 暂停';
    loopPauseBtn.disabled = !active;
    loopStopBtn.disabled = !active;
    loopPrevBtn.disabled = !active;
    loopNextBtn.disabled = !active;
  }

  /** 普通模式：点击单词播放一次；优先使用设置中的口音。 */
  function playWordOnce(word, row) {
    const preferred = loop.settings.accents === 'us' ? 'us' : 'uk';
    const accents = preferred === 'us' ? ['us', 'uk'] : ['uk', 'us'];
    let target = null;
    for (const accent of accents) {
      const candidate = row.querySelector(`.sound-btn[data-accent="${accent}"]`);
      if (candidate && !candidate.disabled) {
        target = candidate;
        break;
      }
    }
    if (!target) {
      // 之前校验为 missing 的按钮会处于 disabled 状态，但直连录音可能已恢复。
      // 行点击不依赖按钮启用状态，强制再尝试一次真实音频。
      playWordAudio(word, preferred, null).catch(() => {});
      return;
    }
    playWordAudio(word, target.dataset.accent, target).catch(() => {});
  }

  /** 点击单词：循环模式下开始/切换循环；当前词暂停时则继续。 */
  function startLoopForWord(word) {
    if (!loopModeEnabled || !word) return;
    if (loop.isLoopingWord(word.id)) {
      // 同一个词不重建任务；如果它已暂停，点击该词等同继续播放。
      if (loop.status === 'paused') loop.resume();
      return;
    }
    stopAudio();
    const started = loop.startSingle(word, visibleWords());
    if (!started) {
      toast('这个单词暂时无法播放', 'err');
      return;
    }
    const snapshot = loop.snapshot();
    if (snapshot.status === 'idle' && snapshot.lastError) toast(snapshot.lastError, 'err');
  }

  function setLoopMode(enabled) {
    loopModeEnabled = Boolean(enabled);
    syncLoopModeUI();
    if (!loopModeEnabled && loop.isActive()) loop.stop();  // 关闭即停止当前循环任务
    saveLoopSettings(loop.settings);
    updateLoopControls(loop.snapshot(), null);
  }

  const loop = new LoopController({
    playClip: async (word, accent) => {
      const immediate = immediateAudioUrl(word, accent);
      if (immediate) return playAndWait(immediate.url, null);
      const resolved = await resolveAudioUrl(word, accent);
      if (resolved.retry) throw new Error('网络异常');
      if (resolved.missing || !resolved.url) throw new Error('没有可用音频');
      return playAndWait(resolved.url, null);
    },
    pauseClip: pauseAudio,
    resumeClip: resumeAudio,
    stopClip: stopAudio,
    onUpdate: (snapshot, event) => updateLoopControls(snapshot, event),
  });
  loopReady = true;
  loop.setSettings(initialLoop);

  loopModeButton.addEventListener('click', () => setLoopMode(!loopModeEnabled));
  loopPauseBtn.addEventListener('click', () => {
    if (loop.status === 'paused') loop.resume();
    else if (loop.status !== 'idle') loop.pause();
  });
  loopStopBtn.addEventListener('click', () => loop.stop());
  loopPrevBtn.addEventListener('click', () => { if (loop.status !== 'idle') loop.prev(); });
  loopNextBtn.addEventListener('click', () => { if (loop.status !== 'idle') loop.next(); });

  // 恢复上次的开关状态（默认关闭 = 普通单次播放）
  loopModeEnabled = Boolean(initialLoop.enabled);
  syncLoopModeUI();
  updateLoopControls(loop.snapshot(), null);

  onUnmount(() => loop.stop());

  mount(root,
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/', text: '首页' }), ' / ',
      el('a', { href: `#/book/${chapter.textbook_id}`, text: book.title || '单词书' }), ' / ', chapter.title,
    ]),
    el('div', { class: 'page-head' }, [
      el('div', {}, [
        el('div', { class: 'eyebrow', text: 'CHAPTER' }),
        el('h1', { text: chapter.title }),
      ]),
    ]),
    el('div', { class: 'pixel-panel chapter-tools' }, [
      el('div', { class: 'chapter-toolbar' }, [
        searchInput,
        el('button', {
          class: 'btn primary small', text: '+ 添加单词',
          onclick: () => {
            const input = el('input', { type: 'text', placeholder: '输入单词，例如 environment' });
            modal({
              title: '添加单词到本章', body: input,
              actions: [{
                label: '添加', kind: 'primary',
                onClick: async () => {
                  const value = input.value.trim();
                  if (!value) return false;
                  try {
                    const word = await api.addWord(chapter.id, value);
                    word.chapter_count = 1;
                    word.chapter_pages = [];
                    state.words.push(word);
                    draw();
                    toast('已添加', 'ok');
                    enrichAll();
                  } catch (err) { toast(err.message, 'err'); }
                  return true;
                },
              }],
            });
          },
        }),
      ]),
    ]),
    loopPanel,
    el('div', { class: 'pixel-panel chapter-words' }, [listHost]),
  );

  draw();

  // Fill in IPA/meanings in the background. Audio is resolved lazily on the
  // first click (see playWordAudio), so no word is ever shown as "无音频"
  // merely because the checker has not reached it yet.
  enrichAll().catch(() => {});
}
