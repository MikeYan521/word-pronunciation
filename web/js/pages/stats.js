// 学习统计：词条数量、已获取释义/音标、已掌握与收藏进度。

import { api } from '../api.js';
import { el, mount, loadingCard, progressBar } from '../ui.js';
import { navigate } from '../app.js';

export async function render(root, params = {}) {
  mount(root, loadingCard('正在统计…'));
  const bookId = params.textbook_id ? Number(params.textbook_id) : undefined;
  const data = await api.stats(bookId);
  const words = data.words || {};

  const tile = (value, label, hint) => el('div', { class: 'stat-tile' }, [
    el('div', { class: 'value', text: value }),
    el('div', { class: 'label', text: label }),
    hint ? el('div', { class: 'tiny muted', text: hint }) : null,
  ]);

  const bookRows = (data.book_list || []).map((book) => el('tr', {}, [
    el('td', {}, [el('a', { href: `#/book/${book.id}`, text: book.title })]),
    el('td', { class: 'small', text: `${book.chapter_count} 章` }),
    el('td', { class: 'small', text: `${book.word_count} 词条` }),
    el('td', { style: 'min-width:150px' }, [
      progressBar(book.mastered_count || 0, book.word_count || 1, 'green'),
      el('div', { class: 'tiny muted', style: 'margin-top:4px' },
        [`已掌握 ${book.mastered_count} / ${book.word_count}`]),
    ]),
    el('td', { class: 'small', text: `收藏 ${book.favorite_count}` }),
    el('td', {}, [el('button', {
      class: 'btn small', text: '查看', onclick: () => navigate(`/book/${book.id}`),
    })]),
  ]));

  const pendingRows = (data.unmastered || []).slice(0, 30).map((word) => el('tr', {}, [
    el('td', {}, [el('a', { href: `#/word/${word.id}`, text: word.lemma })]),
    el('td', { class: 'small', text: word.meaning_cn || '—' }),
    el('td', { class: 'mono small', text: word.uk_ipa ? `UK /${word.uk_ipa}/` : '—' }),
    el('td', { class: 'mono small', text: word.us_ipa ? `US /${word.us_ipa}/` : '—' }),
  ]));

  mount(root,
    el('div', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: '学习统计' }),
        el('div', { class: 'sub', text: '词汇掌握进度与释义/发音完成情况' }),
      ]),
      el('button', { class: 'btn primary', text: '打开单词书', onclick: () => navigate('/books') }),
    ]),
    el('div', { class: 'grid cols-3' }, [
      tile(words.total || 0, '词条总数', `${data.books} 本单词书`),
      tile(words.learned || 0, '已获取释义/音标'),
      tile(words.mastered || 0, '已掌握', `收藏 ${words.favorite || 0}`),
      tile(words.uk_audio || 0, '已缓存英式发音'),
      tile(words.us_audio || 0, '已缓存美式发音'),
    ]),
    el('div', { class: 'card', style: 'margin-top:16px' }, [
      el('h3', { text: '整体掌握进度' }),
      progressBar(words.mastered || 0, words.total || 1, 'green'),
      el('div', { class: 'small muted', style: 'margin-top:6px' },
        [`已掌握 ${words.mastered || 0} / ${words.total || 0} 个词条`]),
    ]),
    el('div', { class: 'card', style: 'margin-top:14px' }, [
      el('h3', { text: '各单词书进度' }),
      bookRows.length
        ? el('table', {}, [
          el('thead', {}, [el('tr', {}, [
            el('th', { text: '单词书' }), el('th', { text: '章节' }), el('th', { text: '词条' }),
            el('th', { text: '掌握进度' }), el('th', { text: '收藏' }), el('th', { text: '' }),
          ])]),
          el('tbody', {}, bookRows),
        ])
        : el('div', { class: 'muted small', text: '还没有单词书。' }),
    ]),
    el('div', { class: 'card', style: 'margin-top:14px' }, [
      el('h3', { text: '待掌握词条（前 30 个）' }),
      pendingRows.length
        ? el('table', {}, [
          el('thead', {}, [el('tr', {}, [
            el('th', { text: '单词' }), el('th', { text: '释义' }),
            el('th', { text: '英式' }), el('th', { text: '美式' }),
          ])]),
          el('tbody', {}, pendingRows),
        ])
        : el('div', { class: 'muted small', text: '全部词条都已标记为掌握。' }),
    ]),
  );
}
