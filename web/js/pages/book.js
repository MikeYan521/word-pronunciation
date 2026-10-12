import { api } from '../api.js';
import { el, mount, toast, progressBar, loadingCard, modal } from '../ui.js';
import { navigate } from '../app.js';

export async function render(root, textbookId) {
  mount(root, loadingCard('正在读取单词书…'));
  let book;
  try {
    book = await api.getTextbook(textbookId);
  } catch (err) {
    mount(root, el('div', { class: 'notice danger', text: err.message }));
    return;
  }

  const chapters = book.chapters || [];
  const totalWords = chapters.reduce((sum, c) => sum + (c.word_count || 0), 0);
  const mastered = chapters.reduce((sum, c) => sum + (c.mastered_count || 0), 0);
  const learned = chapters.reduce((sum, c) => sum + (c.learned_count || 0), 0);

  const rows = chapters.map((chapter) => el('tr', {}, [
    el('td', { style: 'width:34%' }, [
      el('div', { class: 'row tight' }, [
        el('a', { href: `#/chapter/${chapter.id}`, style: 'font-weight:700;color:inherit', text: chapter.title }),
        ['low', 'medium', 'unknown'].includes(chapter.confidence || 'unknown')
          ? el('a', {
            href: `#/parse/${book.id}`, class: 'tag warn',
            title: '章节边界依据不足，点击进入章节校验',
            text: '待核对',
          })
          : null,
      ]),
      el('div', { class: 'tiny muted' },
        [`第 ${chapter.start_page}–${chapter.end_page} 页` +
         (chapter.number ? ` · Chapter ${chapter.number}` : '')]),
    ]),
    el('td', { class: 'small', text: `${chapter.word_count} 词` }),
    el('td', { style: 'min-width:150px' }, [
      progressBar(chapter.mastered_count || 0, chapter.word_count || 1, 'green'),
      el('div', { class: 'tiny muted', style: 'margin-top:4px' },
        [`已掌握 ${chapter.mastered_count} / ${chapter.word_count}`]),
    ]),
    el('td', { class: 'right', style: 'text-align:right' }, [
      el('button', { class: 'btn small', text: '学习', onclick: () => navigate(`/chapter/${chapter.id}`) }),
    ]),
  ]));

  const renameBtn = el('button', {
    class: 'btn small', text: '重命名',
    onclick: () => {
      const input = el('input', { type: 'text', value: book.title, style: 'width:100%' });
      modal({
        title: '重命名单词书', body: input,
        actions: [{
          label: '保存', kind: 'primary',
          onClick: async () => {
            await api.renameTextbook(book.id, input.value.trim() || book.title);
            toast('已保存', 'ok');
            render(root, textbookId);
          },
        }],
      });
    },
  });

  mount(root,
    el('div', { class: 'crumbs' }, [
      el('a', { href: '#/', text: '首页' }), ' / ',
      el('a', { href: '#/books', text: '单词书' }), ' / ', book.title,
    ]),
    el('div', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: book.title }),
        el('div', { class: 'sub' },
          [`${chapters.length} 章 · ${totalWords} 个单词 · 共 ${book.page_count} 页 · 已学习 ${learned} · 已掌握 ${mastered}`]),
      ]),
      el('div', { class: 'row' }, [
        el('button', { class: 'btn small', text: '解析结果/编辑章节', onclick: () => navigate(`/parse/${book.id}`) }),
        el('button', {
          class: 'btn small', text: '导出词表',
          title: '导出 JSON，可在手机或其它设备上导入学习',
          onclick: async (event) => {
            event.target.disabled = true;
            try {
              const payload = await api.exportTextbook(book.id);
              const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
              const url = URL.createObjectURL(blob);
              const link = document.createElement('a');
              link.href = url;
              link.download = `${book.title}.wordbook.json`;
              document.body.append(link);
              link.click();
              link.remove();
              setTimeout(() => URL.revokeObjectURL(url), 4000);
              toast('已导出词表 JSON', 'ok');
            } catch (err) { toast(`导出失败：${err.message}`, 'err'); }
            finally { event.target.disabled = false; }
          },
        }),
        renameBtn,
        el('button', { class: 'btn small', text: '学习统计', onclick: () => navigate(`/stats?textbook_id=${book.id}`) }),
      ]),
    ]),
    el('div', { class: 'card', style: 'padding:0;overflow:hidden' }, [
      el('table', {}, [
        el('thead', {}, [el('tr', {}, [
          el('th', { text: '章节' }), el('th', { text: '词汇量' }),
          el('th', { text: '学习进度' }), el('th', { style: 'text-align:right', text: '操作' }),
        ])]),
        el('tbody', {}, rows),
      ]),
    ]),
  );
}
