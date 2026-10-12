import { api } from '../api.js';
import { el, mount, toast, progressBar, loadingCard, confirmDialog } from '../ui.js';
import { navigate } from '../app.js';

function bookCard(book) {
  const learned = book.learned_count || 0;
  const total = book.word_count || 0;
  // Notion's signature pastel feature card wraps the primary action.
  return el('div', { class: 'card' }, [
    el('div', { class: 'row' }, [
      el('h3', { style: 'margin:0;flex:1', text: book.title }),
      el('span', { class: `tag ${book.status === 'parsed' ? 'ok' : 'warn'}`, text: book.status === 'parsed' ? '已生成词表' : book.status === 'needs_review' ? '待确认' : book.status }),
    ]),
    el('p', { class: 'small muted', style: 'margin:6px 0 10px' },
      [`${book.chapter_count || 0} 个章节 · ${total} 个单词 · ${book.page_count || 0} 页`]),
    progressBar(book.mastered_count || 0, total, 'green'),
    el('div', { class: 'small muted', style: 'margin-top:6px' },
      [`已学习 ${learned} · 已掌握 ${book.mastered_count || 0} · 收藏 ${book.favorite_count || 0}`]),
    el('div', { class: 'row', style: 'margin-top:12px' }, [
      el('button', { class: 'btn primary small', text: '打开单词书', onclick: () => navigate(`/book/${book.id}`) }),
      el('button', { class: 'btn small', text: '解析结果', onclick: () => navigate(`/parse/${book.id}`) }),
      el('span', { class: 'spacer' }),
      el('button', {
        class: 'btn danger small', text: '删除',
        onclick: () => confirmDialog('删除单词书', `确定删除《${book.title}》及其词表与学习记录吗？`, async () => {
          await api.deleteTextbook(book.id);
          toast('已删除', 'ok');
          render(document.getElementById('view'));
        }),
      }),
    ]),
  ]);
}

function uploadPanel(onDone) {
  const input = el('input', { type: 'file', accept: 'application/pdf,.pdf', style: 'display:none' });
  const jsonInput = el('input', { type: 'file', accept: '.json,application/json', style: 'display:none' });
  const titleInput = el('input', { type: 'text', placeholder: '单词书名称（可留空，默认用文件名）', style: 'width:100%' });
  const ocrToggle = el('input', { type: 'checkbox', id: 'use-ocr' });
  const ocrPagesInput = el('input', { type: 'number', value: 20, min: 0, max: 9999, style: 'width:90px' });
  const status = el('div', { class: 'small muted' });
  const bar = el('div', { class: 'progress', style: 'display:none' }, [el('span', { style: 'width:0%' })]);
  const zone = el('div', { class: 'dropzone' }, [
    el('strong', { text: '上传英语单词书 PDF' }),
    el('div', { class: 'small muted', text: '拖拽文件到此处，或点击选择文件（仅保存在本机 data/ 目录）' }),
    el('div', { class: 'small muted', style: 'margin-top:8px', text: '支持带文本层的 PDF；扫描版会提示并可尝试 OCR（Windows 内置 OCR，无需额外安装）' }),
  ]);

  const pick = () => input.click();
  zone.addEventListener('click', pick);
  zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add('over'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('over'));
  zone.addEventListener('drop', (event) => {
    event.preventDefault();
    zone.classList.remove('over');
    if (event.dataTransfer.files.length) start(event.dataTransfer.files[0]);
  });
  input.addEventListener('change', () => { if (input.files.length) start(input.files[0]); });
  jsonInput.addEventListener('change', async () => {
    const file = jsonInput.files && jsonInput.files[0];
    if (!file) return;
    status.textContent = `正在导入 ${file.name}…`;
    try {
      const payload = JSON.parse(await file.text());
      const result = await api.importTextbook(payload);
      const bookId = result.textbook ? result.textbook.id : result.textbook_id;
      toast('词表已导入', 'ok');
      if (bookId) navigate(`/book/${bookId}`);
    } catch (err) {
      toast(`导入失败：${err.message}`, 'err');
      status.textContent = `导入失败：${err.message}`;
    } finally {
      jsonInput.value = '';
    }
  });

  async function start(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      toast('请选择 PDF 文件', 'err');
      return;
    }
    zone.style.pointerEvents = 'none';
    status.innerHTML = '';
    bar.style.display = 'block';
    bar.firstChild.style.width = '0%';
    status.append(el('span', { class: 'spin dark' }), ` 正在上传《${file.name}》…`);
    try {
      const started = await api.upload(file, {
        title: titleInput.value.trim(),
        useOcr: ocrToggle.checked,
        ocrMaxPages: Number(ocrPagesInput.value || 0),
      });
      const id = started.textbook_id;
      let last = null;
      for (;;) {
        const info = await api.textbookStatus(id);
        last = info;
        const progress = info.progress || {};
        bar.firstChild.style.width = `${progress.percent || 0}%`;
        const head = ocrToggle.checked ? '正在 OCR 并解析' : '正在解析';
        status.innerHTML = '';
        status.append(el('span', { class: 'spin dark' }),
          ` ${head}：${progress.message || '处理中…'}（${progress.percent || 0}%）`);
        if (info.status === 'failed') {
          throw new Error(progress.message || '解析失败');
        }
        if (info.ready) break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      bar.style.display = 'none';
      status.textContent = '解析完成，正在打开章节预览…';
      toast('解析完成，请确认章节划分', 'ok');
      onDone({ textbook: { id }, ...last });
    } catch (err) {
      toast(err.message, 'err');
      status.textContent = `解析失败：${err.message}`;
      bar.style.display = 'none';
    } finally {
      zone.style.pointerEvents = '';
    }
  }

  // Notion's signature pastel feature card wraps the primary action.
  return el('div', { class: 'card feature-card' }, [
    zone,
    input,
    el('div', { style: 'margin-top:14px;display:grid;gap:10px' }, [
      el('label', { class: 'field' }, ['单词书名称', titleInput]),
      el('label', { class: 'row tight small', style: 'gap:8px' }, [
        ocrToggle,
        '尝试 OCR（扫描版 PDF；优先使用 Windows 内置 OCR，其次 Tesseract）',
      ]),
      el('label', { class: 'row tight small', style: 'gap:8px' }, [
        'OCR 页数上限（0 = 整本；手机上建议先跑 20 页）',
        ocrPagesInput,
      ]),
      // 扫描版的最快路径：电脑上解析一次，手机上导入 JSON 学习
      el('div', { class: 'row' }, [
        el('button', {
          class: 'btn', text: '导入词表 JSON',
          title: '导入在电脑上导出的 .wordbook.json，手机端免解析直接学习',
          onclick: () => jsonInput.click(),
        }),
        el('button', {
          class: 'btn ghost', text: '载入内置词表',
          title: '直接读取随站点发布的示例词表（内置 Demo 词表；若站点带了自己的词表也会优先使用）',
          onclick: async (event) => {
            const button = event.target;
            button.disabled = true;
            status.textContent = '正在载入内置词表…';
            try {
              // 优先用部署时自带的词表（可能是你自己的书），否则用仓库里的 Demo
              const candidates = ['词汇真经.wordbook.json', 'sample-textbook.wordbook.json'];
              let payload = null;
              let lastStatus = 0;
              for (const name of candidates) {
                const res = await fetch(new URL(`../../sample/${encodeURIComponent(name)}`, import.meta.url));
                if (res.ok) { payload = await res.json(); break; }
                lastStatus = res.status;
              }
              if (!payload) throw new Error(`站点未包含内置词表（HTTP ${lastStatus}）`);
              const result = await api.importTextbook(payload);
              const bookId = result.textbook ? result.textbook.id : result.textbook_id;
              toast(`已载入「${payload.book ? payload.book.title : '内置词表'}」`, 'ok');
              if (bookId) navigate(`/book/${bookId}`);
            } catch (err) {
              toast(`载入失败：${err.message}`, 'err');
              status.textContent = `载入失败：${err.message}（可用「导入词表 JSON」选择本地文件）`;
            } finally {
              button.disabled = false;
            }
          },
        }),
        jsonInput,
        el('span', { class: 'small muted', text: '扫描版建议：电脑解析 → 导出 JSON → 手机导入' }),
      ]),
      bar,
      status,
    ]),
  ]);
}

export async function render(root) {
  mount(root, loadingCard('正在读取单词书列表…'));
  let books = [];
  try {
    books = (await api.listTextbooks()).textbooks || [];
  } catch (err) {
    mount(root, el('div', { class: 'notice danger', text: `无法连接后端：${err.message}` }));
    return;
  }

  const list = el('div', { class: 'grid cols-2' });
  if (!books.length) {
    list.append(el('div', { class: 'card muted center' }, [
      el('div', { text: '还没有单词书。' }),
      el('div', { class: 'small', style: 'margin-top:6px' },
        ['词表保存在当前浏览器里：换设备或换浏览器后需要重新导入或上传。'
         + '想先看效果，可以点上方「载入内置词表」。']),
    ]));
  } else {
    books.forEach((book) => list.append(bookCard(book)));
  }

  mount(root,
    el('div', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: '英语单词书 · 词汇与发音' }),
        el('div', { class: 'sub', text: '上传单词书 PDF → 按章节提取书中的词汇条目 → 英美音标 + 真实发音' }),
      ]),
      el('div', { class: 'row' }, [
        el('button', { class: 'btn', text: '学习统计', onclick: () => navigate('/stats') }),
      ]),
    ]),
    uploadPanel((preview) => navigate(`/parse/${preview.textbook.id}`)),
    el('h2', { style: 'margin-top:22px', text: '我的单词书' }),
    list,
  );
}

export async function renderBooks(root) {
  mount(root, loadingCard('正在读取单词书列表…'));
  const { textbooks } = await api.listTextbooks();
  const list = el('div', { class: 'grid cols-2' });
  if (!textbooks.length) {
    list.append(el('div', { class: 'card muted center', text: '还没有单词书。' }));
  } else {
    textbooks.forEach((book) => list.append(bookCard(book)));
  }
  mount(root,
    el('div', { class: 'page-head' }, [el('div', {}, [el('h1', { text: '单词书列表' })])]),
    list,
    el('div', { style: 'margin-top:16px' }, [
      el('button', { class: 'btn primary', text: '上传新单词书', onclick: () => navigate('/') }),
    ]),
  );
}
