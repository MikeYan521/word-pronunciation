import { api } from '../api.js?v=20261125-1';
import { el, mount, toast, progressBar, loadingCard, confirmDialog } from '../ui.js';
import { navigate } from '../app.js?v=20261125-1';

function bookCard(book, onChange) {
  const learned = book.learned_count || 0;
  const total = book.word_count || 0;
  return el('article', { class: 'book-card pixel-panel' }, [
    el('div', { class: 'book-card-head' }, [
      el('span', { class: 'pixel-cube gold', 'aria-hidden': 'true' }),
      el('h3', { text: book.title }),
      el('span', {
        class: `tag ${book.status === 'parsed' ? 'ok' : 'warn'}`,
        text: book.status === 'parsed' ? 'READY' : 'CHECK',
      }),
    ]),
    el('div', { class: 'book-meta' }, [
      el('span', { text: `${book.chapter_count || 0} 章` }),
      el('span', { text: `${total} 词` }),
      el('span', { text: `已掌握 ${book.mastered_count || 0}` }),
    ]),
    progressBar(book.mastered_count || 0, total, 'green'),
    el('div', { class: 'book-card-foot' }, [
      el('span', { class: 'tiny muted', text: `已学习 ${learned} / ${total}` }),
      el('span', { class: 'spacer' }),
      el('button', {
        class: 'btn primary small',
        text: '进入',
        onclick: () => navigate(`/book/${book.id}`),
      }),
      el('button', {
        class: 'btn danger small',
        text: '删除',
        onclick: () => confirmDialog(
          '删除单词书',
          `确定删除《${book.title}》及其词表与学习记录吗？`,
          async () => {
            await api.deleteTextbook(book.id);
            toast('已删除', 'ok');
            onChange();
          },
        ),
      }),
    ]),
  ]);
}

function importPanel() {
  const input = el('input', {
    type: 'file',
    accept: '.json,application/json',
    style: 'display:none',
  });
  const status = el('div', { class: 'tiny muted', text: '选择电脑导出的 .wordbook.json 词表文件。' });

  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    status.textContent = `正在导入 ${file.name}…`;
    try {
      const payload = JSON.parse(await file.text());
      const result = await api.importTextbook(payload);
      const bookId = result.textbook ? result.textbook.id : result.textbook_id;
      toast('词表已导入', 'ok');
      if (bookId) navigate(`/book/${bookId}`);
    } catch (err) {
      status.textContent = `导入失败：${err.message}`;
      toast(`导入失败：${err.message}`, 'err');
    } finally {
      input.value = '';
    }
  });

  return el('section', { class: 'pixel-panel import-panel' }, [
    el('div', { class: 'panel-title' }, [
      el('span', { class: 'pixel-cube blue', 'aria-hidden': 'true' }),
      el('div', {}, [
        el('h2', { text: '导入词表 JSON' }),
        el('div', { class: 'tiny muted', text: 'IMPORT VOCABULARY' }),
      ]),
    ]),
    el('div', { class: 'import-body' }, [
      el('div', { class: 'import-icon', text: '▣' }),
      el('div', { class: 'import-copy' }, [
        el('div', { class: 'small', text: '把已经整理好的单词书词表导入浏览器。' }),
        status,
      ]),
      el('button', {
        class: 'btn primary',
        text: '选择 JSON 文件',
        onclick: () => input.click(),
      }),
    ]),
    input,
  ]);
}

async function renderHome(root) {
  let books = [];

  async function load() {
    mount(root, loadingCard('正在读取单词书…'));
    try {
      books = (await api.listTextbooks()).textbooks || [];
    } catch (err) {
      mount(root, el('div', { class: 'notice danger', text: `无法连接后端：${err.message}` }));
      return;
    }

    const list = el('div', { class: 'book-grid' });
    if (!books.length) {
      list.append(el('div', { class: 'pixel-panel empty-panel' }, [
        el('div', { class: 'empty-mark', text: '◇' }),
        el('h3', { text: '还没有单词书' }),
        el('p', { class: 'small muted', text: '导入一个词表 JSON 后，它会出现在这里。' }),
      ]));
    } else {
      books.forEach((book) => list.append(bookCard(book, load)));
    }

    mount(root,
      importPanel(),
      el('section', { class: 'home-library' }, [
        el('div', { class: 'section-title' }, [
          el('span', { class: 'pixel-cube gold', 'aria-hidden': 'true' }),
          el('h2', { text: '我的单词书' }),
          el('span', { class: 'tag', text: `${books.length} BOOKS` }),
        ]),
        list,
      ]),
    );
  }

  await load();
}

export async function render(root) {
  await renderHome(root);
}

export async function renderBooks(root) {
  mount(root, loadingCard('正在读取单词书…'));
  const { textbooks } = await api.listTextbooks();
  const list = el('div', { class: 'book-grid' });
  if (!textbooks.length) {
    list.append(el('div', { class: 'pixel-panel empty-panel' }, [
      el('h3', { text: '还没有单词书' }),
      el('p', { class: 'small muted', text: '返回首页导入词表 JSON。' }),
    ]));
  } else {
    textbooks.forEach((book) => list.append(bookCard(book, () => renderBooks(root))));
  }
  mount(root,
    el('div', { class: 'page-head' }, [
      el('div', {}, [
        el('div', { class: 'eyebrow', text: 'BOOK ARCHIVE' }),
        el('h1', { text: '单词书' }),
      ]),
    ]),
    list,
  );
}
