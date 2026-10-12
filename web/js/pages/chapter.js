// Chapter vocabulary page: IPA, dual pronunciation, progress, filtering.

import { api } from '../api.js';
import { el, mount, toast, loadingCard, progressBar, modal } from '../ui.js';
import { navigate } from '../app.js';
import { playUrl, soundButtons, probeWords, playWordAudio, markAudioMissing, ttsSupport, speak } from '../audio.js';

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
  const listHost = el('div');
  const progressLabel = el('span', { class: 'small muted' });
  const progressHost = el('div', { class: 'row tight', style: 'min-width:180px' }, [
    el('span', { class: 'small muted', text: '释义/音标' }), progressBar(0, 1), progressLabel,
  ]);

  const searchInput = el('input', { type: 'search', placeholder: '搜索单词…', style: 'width:170px' });
  searchInput.addEventListener('input', () => {
    state.search = searchInput.value.trim().toLowerCase();
    state.renderLimit = RENDER_STEP;
    draw();
  });

  const filterChips = el('div', { class: 'chip-row' });
  [['all', '全部'], ['todo', '未掌握'], ['fav', '收藏'], ['nofav', '未收藏']].forEach(([key, label]) => {
    const chip = el('button', {
      class: `chip ${state.filter === key ? 'active' : ''}`, text: label,
      onclick: () => {
        state.filter = key;
        state.renderLimit = RENDER_STEP;
        filterChips.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
        draw();
      },
    });
    filterChips.append(chip);
  });

  const sortChips = el('div', { class: 'chip-row' });
  const sortOptions = chapter.extraction_mode === 'entries'
    ? [['original', '按原书顺序'], ['alpha', '按字母'], ['freq', '按页码顺序']]
    : [['freq', '按出现频率'], ['alpha', '按字母'], ['recent', '按页码']];
  sortOptions.forEach(([key, label]) => {
    const chip = el('button', {
      class: `chip ${state.sort === key ? 'active' : ''}`, text: label,
      onclick: () => {
        state.sort = key;
        state.renderLimit = RENDER_STEP;
        sortChips.querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
        draw();
      },
    });
    sortChips.append(chip);
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
        if (btn.dataset.status === 'missing') {
          // previously reported as missing - let the user force a retry
          playUrl(btn.dataset.url + '&refresh=1', btn, true).catch(() => {});
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

  function refreshProgress() {
    const total = state.words.length || 1;
    mount(progressHost, el('span', { class: 'small muted', text: '释义/音标' }),
      progressBar(state.enrichDone, total), progressLabel);
    progressLabel.textContent = `${state.enrichDone}/${state.words.length} · 已验证发音 ${state.probeDone}`;
  }

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
        refreshProgress();
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
        refreshProgress();
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

  const tts = ttsSupport();

  mount(root,
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/', text: '首页' }), ' / ',
      el('a', { href: `#/book/${chapter.textbook_id}`, text: book.title || '单词书' }), ' / ', chapter.title,
    ]),
    el('div', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: chapter.title }),
        el('div', { class: 'sub' },
          [`第 ${chapter.start_page}–${chapter.end_page} 页 · ${state.words.length} ` +
           (chapter.extraction_mode === 'entries' ? '个词条（按原书排版提取）' : '个单词')]),
      ]),
      el('div', { class: 'row' }, [
        progressHost,
        el('button', { class: 'btn small', text: '刷新释义', onclick: () => enrichAll(true) }),
        el('button', { class: 'btn small', text: '校验发音', onclick: () => probeAll() }),
      ]),
    ]),
    el('div', { class: 'card' }, [
      el('div', { class: 'row' }, [
        searchInput,
        filterChips,
        el('span', { class: 'spacer' }),
        sortChips,
        el('button', {
          class: 'btn small', text: '+ 添加单词',
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
      el('p', { class: 'small muted', style: 'margin:10px 0 0' },
        ['英式 / 美式发音分别来自真实录音，点击即可获取并缓存；' +
         '只有确认某个口音确实没有录音时才会显示“无音频”，不会用另一种口音代替。']),
      statusLine,
    ]),
    el('div', { class: 'card', style: 'margin-top:14px' }, [listHost]),
    tts.supported === false ? null : el('div', { class: 'small muted', style: 'margin-top:12px' },
      [`备用方案：系统语音合成（${tts.uk ? '有 en-GB' : '无 en-GB'} / ${tts.us ? '有 en-US' : '无 en-US'}）。` +
       '仅在真实音频缺失时可用于跟读对比，界面会明确标注为“合成语音”。']),
  );

  refreshProgress();
  draw();

  // Fill in IPA/meanings in the background. Audio is resolved lazily on the
  // first click (see playWordAudio), so no word is ever shown as "无音频"
  // merely because the checker has not reached it yet.
  enrichAll().catch(() => {});
}
