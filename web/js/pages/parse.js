// 章节校验页面：在正式提取词汇之前，先让用户核对章节边界。
//
// 每章显示：序号、名称、页码范围、词条数、识别依据，以及置信度状态：
//   ✓ 已确认 —— 正文页首存在明确 Chapter 标识，或用户已手动指定
//   ! 待核对 —— 依据不足（目录推算、边界校正、范围可疑）
// 点击 ✎ 可以直接修改起止页，只会重新处理该范围（不会重新 OCR 整本书）。

import { api } from '../api.js';
import { el, mount, toast, loadingCard, modal } from '../ui.js';
import { navigate } from '../app.js';

const SOURCE_LABEL = {
  toc: '目录推算',
  outline: 'PDF 书签',
  heading: '标题识别',
  marker: '正文标识',
  manual: '手动指定',
  fallback: '自动划分',
};

export async function render(root, textbookId) {
  mount(root, loadingCard('正在读取解析结果…'));
  let preview;
  try {
    preview = await api.preview(textbookId);
  } catch (err) {
    // 扫描版仍可能在后台解析中
    try {
      const info = await api.textbookStatus(textbookId);
      if (info.status === 'parsing' || !info.ready) {
        return renderParsing(root, textbookId);
      }
    } catch (_) { /* 保持原始错误 */ }
    mount(root, el('div', { class: 'notice danger', text: err.message }));
    return;
  }

  const book = preview.textbook;
  if (book.status === "needs_ocr") return renderNeedsOcr(root, preview);
  const report = preview.report || {};
  const vocabulary = preview.vocabulary_report || {};

  let chapters = (preview.chapters || []).map((chapter) => ({
    title: chapter.title,
    start_page: chapter.start_page,
    end_page: chapter.end_page,
    source: chapter.source,
    number: chapter.number ?? null,
    confidence: chapter.confidence || 'unknown',
    evidence: chapter.evidence || [],
    alerts: chapter.alerts || [],
    subsections: chapter.subsections || [],
    words: chapter.words || [],
  }));
  let showOnlyTodo = false;

  const strategyLabel = {
    outline: 'PDF 目录书签',
    toc: '印刷目录页 + 页码',
    heading: '标题样式识别',
    fallback: '自动平均划分（建议手动调整）',
  }[report.strategy] || report.strategy;

  const modeLabel = vocabulary.mode === 'entries'
    ? '词条模式（只提取书中编排的词汇条目）'
    : vocabulary.mode === 'tokens' ? '全词模式（提取正文中的英文单词）' : '—';

  const statusOf = (chapter) => (
    (chapter.confidence === 'high' || chapter.confidence === 'manual') ? 'ok' : 'warn'
  );
  const todoCount = () => chapters.filter((chapter) => statusOf(chapter) === 'warn').length;
  const wordTotal = () => chapters.reduce((sum, chapter) => sum + chapter.words.length, 0);

  // ------------------------------------------------------------------ 提示区
  const notices = el('div', { class: 'grid' });
  if (vocabulary.mode) {
    const lines = [];
    if (vocabulary.mode === 'entries') {
      lines.push(el('div', {}, [
        `词条模式：在 ${vocabulary.pages_scanned ?? 0} 页中识别出 ` +
        `${vocabulary.entries_total ?? 0} 个词汇条目` +
        `（其中 ${vocabulary.entries_from_footer ?? 0} 个由页脚词汇汇总补全）。`,
      ]));
      if (vocabulary.footer_coverage !== null && vocabulary.footer_coverage !== undefined) {
        lines.push(el('div', { class: 'tiny muted' },
          [`与页脚词汇汇总的吻合度：${Math.round(vocabulary.footer_coverage * 100)}%` +
           '（偏低说明 OCR 质量一般，请重点核对列表）']));
      }
      lines.push(el('div', { class: 'tiny muted' },
        ['例句中的单词、正文内容与页脚汇总不会被计入词条。']));
    } else {
      lines.push(el('div', {},
        ['全词模式：未检测到"词条 + 音标"的排版规律，因此按正文词汇提取。']));
    }
    notices.append(el('div', { class: 'notice ok' }, lines));
  }
  if (preview.scanned) {
    notices.append(el('div', { class: 'notice warn' }, [
      el('strong', { text: '扫描版 PDF：' }),
      `解析前仅 ${Math.round((preview.pre_ocr_text_ratio ?? 0) * 100)}% 页面含文本层，` +
      `已 OCR 识别 ${preview.ocr_page_count || 0} 页` +
      `（识别后覆盖率 ${Math.round((preview.text_ratio || 0) * 100)}%）。`,
    ]));
  }
  (preview.warnings || []).forEach((text) => notices.append(el('div', { class: 'notice warn', text })));
  if (report.toc_pages && report.toc_pages.length) {
    notices.append(el('div', { class: 'notice small' },
      [`目录页（不计入词条）：第 ${report.toc_pages.join('、')} 页`]));
  }
  if (report.page_offset) {
    notices.append(el('div', { class: 'notice small' },
      [`印刷页码与物理页相差 ${report.page_offset} 页（目录页码已按此换算）`]));
  }
  if (report.unassigned_pages && report.unassigned_pages.length) {
    const pages = report.unassigned_pages;
    notices.append(el('div', { class: 'notice warn small' }, [
      `不属于任何章节的页面（前置内容或空隙）：第 ${pages.slice(0, 40).join('、')} 页` +
      (pages.length > 40 ? ` …共 ${pages.length} 页` : '') +
      '。这些页面不会计入词表；如需包含，请调整相邻章节的页码范围。',
    ]));
  }

  // ------------------------------------------------------------------ 章节列表
  const list = el('div');
  const summaryLine = el('div', { class: 'small muted' });

  function refreshSummary() {
    summaryLine.textContent =
      `${chapters.length} 个章节 · 待核对 ${todoCount()} 个 · ${modeLabel} · ` +
      `共 ${wordTotal()} 个词条`;
  }

  function drawList() {
    const rows = chapters
      .map((chapter, index) => ({ chapter, index }))
      .filter(({ chapter }) => !showOnlyTodo || statusOf(chapter) === 'warn');
    if (!rows.length) {
      mount(list, el('div', { class: 'muted center', style: 'padding:20px', text: '全部章节均已确认。' }));
    } else {
      mount(list, ...rows.map(({ chapter, index }) => chapterCard(chapter, index)));
    }
    refreshSummary();
  }

  function chapterCard(chapter, index) {
    const status = statusOf(chapter);
    const editor = el('div', { class: 'row', style: 'display:none;margin-top:10px' });

    const titleInput = el('input', { type: 'text', value: chapter.title, style: 'flex:1;min-width:150px' });
    const startInput = el('input', {
      type: 'number', value: chapter.start_page, min: 1, max: preview.page_count, style: 'width:76px',
    });
    const endInput = el('input', {
      type: 'number', value: chapter.end_page, min: 1, max: preview.page_count, style: 'width:76px',
    });
    const saveButton = el('button', {
      class: 'btn primary small', text: '保存并重新处理',
      onclick: async () => {
        const start = Math.max(1, Number(startInput.value) || chapter.start_page);
        const end = Math.min(preview.page_count, Number(endInput.value) || chapter.end_page);
        if (end < start) { toast('结束页不能早于起始页', 'err'); return; }
        saveButton.disabled = true;
        saveButton.innerHTML = '<span class="spin"></span> 处理中';
        try {
          await api.chapterRange(chapter.id, {
            start_page: start, end_page: end, title: titleInput.value.trim(),
          });
          toast(`已重新处理第 ${start}–${end} 页`, 'ok');
          render(root, textbookId);
        } catch (err) {
          toast(err.message, 'err');
          saveButton.disabled = false;
          saveButton.textContent = '保存并重新处理';
        }
      },
    });
    mount(editor, [
      el('label', { class: 'field' }, ['章节名称', titleInput]),
      el('label', { class: 'field' }, ['起始页', startInput]),
      el('label', { class: 'field' }, ['结束页', endInput]),
      el('span', { class: 'spacer' }),
      saveButton,
    ]);

    const evidence = (chapter.evidence || []).join('；');
    return el('div', { class: `card chap-card ${status === 'warn' ? 'needs-review' : ''}` }, [
      el('div', { class: 'row', style: 'align-items:flex-start' }, [
        el('div', { class: 'chap-no', text: String(index + 1).padStart(2, '0') }),
        el('div', { style: 'flex:1;min-width:170px' }, [
          el('div', { class: 'row tight' }, [
            el('strong', { style: 'font-size:16px', text: chapter.title }),
            chapter.number ? el('span', { class: 'tag', text: `Chapter ${chapter.number}` }) : null,
            el('span', { class: 'tag', text: SOURCE_LABEL[chapter.source] || chapter.source || '—' }),
          ]),
          el('div', { class: 'small muted', style: 'margin-top:2px' },
            [`教材第 ${chapter.start_page}–${chapter.end_page} 页 · 词条 ${chapter.words.length} 个`]),
          evidence ? el('div', { class: 'tiny muted' }, [`依据：${evidence}`]) : null,
          (chapter.alerts || []).length
            ? el('div', { class: 'tiny', style: 'color:var(--warn);margin-top:2px' },
              [`提醒：${chapter.alerts.join('；')}`])
            : null,
        ]),
        el('div', { class: 'row tight' }, [
          el('span', {
            class: `status-icon ${status}`,
            title: status === 'ok' ? '已确认' : '章节边界待核对',
            text: status === 'ok' ? '✓' : '!',
          }),
          el('button', {
            class: 'icon-btn', title: '修改页码范围', text: '✎',
            onclick: () => {
              editor.style.display = editor.style.display === 'none' ? 'flex' : 'none';
            },
          }),
          el('button', {
            class: 'icon-btn', title: '在此处拆分为两章', text: '✂',
            onclick: () => {
              const middle = Math.floor((chapter.start_page + chapter.end_page) / 2);
              if (middle <= chapter.start_page) { toast('页码范围太小，无法拆分', 'err'); return; }
              chapters.splice(index + 1, 0, {
                ...chapter,
                title: `${chapter.title}（续）`,
                start_page: middle + 1,
                words: [],
                confidence: 'manual',
                evidence: ['用户拆分'],
              });
              chapter.end_page = middle;
              chapter.confidence = 'manual';
              chapter.evidence = ['用户拆分'];
              drawList();
            },
          }),
          el('button', {
            class: 'icon-btn', title: '删除该章节', text: '🗑',
            onclick: () => { chapters.splice(index, 1); drawList(); },
          }),
        ]),
      ]),
      editor,
    ]);
  }

  function chapterPayload() {
    return chapters.map((chapter) => ({
      title: chapter.title || '未命名章节',
      start_page: Math.max(1, Number(chapter.start_page) || 1),
      end_page: Math.min(preview.page_count, Number(chapter.end_page) || 1),
      source: chapter.source || 'manual',
      number: chapter.number ?? null,
      confidence: chapter.confidence || 'unknown',
      evidence: chapter.evidence || [],
    }));
  }

  async function applyChapters() {
    try {
      await api.applyChapters(textbookId, chapterPayload());
      toast('词表已生成', 'ok');
      navigate(`/book/${textbookId}`);
    } catch (err) {
      toast(err.message, 'err');
    }
  }

  function confirmAndGenerate() {
    if (!chapters.length) { toast('至少保留一个章节', 'err'); return; }
    const pending = todoCount();
    if (pending) {
      modal({
        title: '仍有章节待核对',
        body: `<p>还有 <strong>${pending}</strong> 个章节标记为 ⚠（边界依据不足）。` +
              '可以继续生成词表，也可以先点击 ✎ 手动指定起止页。</p>' +
              '<p class="small muted">生成的词表之后仍可在教材页面调整。</p>',
        actions: [{ label: '仍然生成词表', kind: 'primary', onClick: () => { void applyChapters(); } }],
      });
      return;
    }
    void applyChapters();
  }

  async function redetect() {
    try {
      await api.redetectChapters(textbookId);
      toast('已按整本书结构重新识别章节', 'ok');
      render(root, textbookId);
    } catch (err) { toast(err.message, 'err'); }
  }

  function mergeAll() {
    if (chapters.length < 2) return;
    const first = chapters[0];
    first.end_page = chapters[chapters.length - 1].end_page;
    first.confidence = 'manual';
    first.evidence = ['用户合并为一个章节'];
    chapters = [first];
    drawList();
  }

  const todoFilter = el('button', {
    class: 'chip', text: '仅看待核对',
    onclick: (event) => {
      showOnlyTodo = !showOnlyTodo;
      event.target.classList.toggle('active', showOnlyTodo);
      drawList();
    },
  });

  mount(root,
    el('div', { class: 'crumbs' }, [el('a', { href: '#/', text: '首页' }), ' / 章节校验']),
    el('div', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: `章节校验：${book.title}` }),
        el('div', { class: 'sub', text: `共 ${preview.page_count} 页 · 识别方式：${strategyLabel}` }),
        summaryLine,
      ]),
      el('div', { class: 'row' }, [
        el('button', { class: 'btn small', text: '重新识别章节', onclick: redetect }),
        el('button', { class: 'btn primary', text: '确认并生成词表', onclick: confirmAndGenerate }),
      ]),
    ]),
    notices,
    el('div', { class: 'card' }, [
      el('div', { class: 'row' }, [
        el('h3', { style: 'margin:0', text: '章节识别预览' }),
        todoFilter,
        el('span', { class: 'spacer' }),
        el('button', { class: 'btn small', text: '全部合并为一章', onclick: mergeAll }),
        el('button', {
          class: 'btn small', text: '新增章节',
          onclick: () => {
            const last = chapters[chapters.length - 1];
            chapters.push({
              title: `第 ${chapters.length + 1} 章`, start_page: last ? last.end_page : 1,
              end_page: last ? last.end_page : 1, source: 'manual', number: null,
              confidence: 'manual', evidence: ['用户新增'], words: [],
            });
            drawList();
          },
        }),
        el('button', { class: 'btn small', text: '重新 OCR', onclick: reparseDialog }),
      ]),
      el('p', { class: 'small muted', style: 'margin:10px 0 0' }, [
        '✓ = 正文页首存在明确 Chapter 标识或已手动确认；',
        '! = 依据不足（目录推算 / 边界校正 / 范围可疑），请核对后再生成词表。',
        '修改起止页只会重新处理该范围。',
      ]),
    ]),
    el('div', { class: 'grid', style: 'margin-top:14px' }, [list]),
    el('div', { class: 'row', style: 'margin-top:18px' }, [
      el('button', { class: 'btn', text: '稍后再说', onclick: () => navigate(`/book/${textbookId}`) }),
      el('span', { class: 'spacer' }),
      el('button', { class: 'btn primary', text: '确认并生成词表', onclick: confirmAndGenerate }),
    ]),
  );
  drawList();

  function reparseDialog() {
    const ocr = el('input', { type: 'checkbox', id: 're-ocr', checked: true });
    const body = el('div', {}, [
      el('p', { class: 'small muted' },
        ['重新 OCR 会用当前设置重新读取整本 PDF（每页约 1 秒）。' +
         '章节的手动修改会被覆盖，单词的收藏/掌握状态会保留。']),
      el('label', { class: 'row tight small' }, [ocr, '使用 OCR（扫描版必须勾选）']),
      (preview.ocr && !preview.ocr.available)
        ? el('div', { class: 'notice warn small', text: preview.ocr.reason })
        : null,
    ]);
    modal({
      title: '重新解析整本 PDF',
      body,
      actions: [{
        label: '开始',
        kind: 'primary',
        onClick: async () => {
          try {
            await api.reparse(textbookId, ocr.checked);
            toast('已开始重新解析', 'ok');
            renderParsing(root, textbookId);
          } catch (err) { toast(err.message, 'err'); }
        },
      }],
    });
  }
}

/** 扫描版且未 OCR：给出明确原因和两条可行路径，而不是"0 个词条"。 */
function renderNeedsOcr(root, preview) {
  const book = preview.textbook;
  mount(root,
    el('div', { class: 'crumbs' }, [el('a', { href: '#/', text: '首页' }), ' / 扫描版提示']),
    el('div', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: '这是扫描版 PDF' }),
        el('div', { class: 'sub', text: `${book.title} · 共 ${book.page_count || preview.page_count} 页` }),
      ]),
    ]),
    el('div', { class: 'notice warn' }, [
      el('strong', { text: '没有识别出文字，因为该 PDF 没有文字层（整页都是扫描图片）。' }),
      el('div', { style: 'margin-top:6px' },
        ['浏览器只能读取图片上的文字——必须开启 OCR。' +
         `${book.page_count || preview.page_count} 页扫描本在手机上 OCR 会很慢、也可能因内存不足中断。`]),
    ]),
    el('div', { class: 'grid cols-2', style: 'margin-top:14px' }, [
      el('div', { class: 'card' }, [
        el('h3', { text: '推荐：电脑解析，手机导入' }),
        el('p', { class: 'small muted' },
          ['在电脑上打开本项目（python run.py 或 Netlify 版）上传同一本 PDF，勾选「尝试 OCR」，' +
           '解析完成后在单词书页面点「导出词表」下载 JSON；把 JSON 传到手机，' +
           '在首页点「导入词表」即可看到全部词条、音标与发音。']),
        el('div', { class: 'row' }, [
          el('button', { class: 'btn primary', text: '回首页导入词表',
                         onclick: () => navigate('/') }),
        ]),
      ]),
      el('div', { class: 'card' }, [
        el('h3', { text: '或者：直接重试 OCR' }),
        el('p', { class: 'small muted' },
          ['重新上传该 PDF 并勾选「尝试 OCR」。默认只识别前 20 页（可在上传卡片里改），' +
           '先确认效果；整本识别请用电脑，速度快得多。']),
        el('div', { class: 'row' }, [
          el('button', { class: 'btn', text: '回首页重新上传', onclick: () => navigate('/') }),
          el('button', {
            class: 'btn ghost', text: '删除这本空记录',
            onclick: async () => {
              await api.deleteTextbook(book.id);
              toast('已删除', 'ok');
              navigate('/');
            },
          }),
        ]),
      ]),
    ]),
    (preview.warnings && preview.warnings.length)
      ? el('div', { class: 'notice small', style: 'margin-top:14px' }, preview.warnings.map((text) => el('div', { text })))
      : null,
  );
}

/** 解析中的进度页面（扫描版可能持续数分钟）。 */
async function renderParsing(root, textbookId) {
  const bar = el('div', { class: 'progress' }, [el('span', { style: 'width:0%' })]);
  const label = el('div', { class: 'small muted', text: '正在解析…' });
  mount(root,
    el('div', { class: 'crumbs' }, [el('a', { href: '#/', text: '首页' }), ' / 解析中']),
    el('div', { class: 'card' }, [
      el('h2', { text: '正在解析 PDF' }),
      el('p', { class: 'small muted' },
        ['扫描版单词书需要 OCR，每页约 1 秒，请保持页面打开。']),
      bar, label,
    ]),
  );
  for (;;) {
    let info;
    try {
      info = await api.textbookStatus(textbookId);
    } catch (err) {
      label.textContent = `无法获取进度：${err.message}`;
      return;
    }
    const progress = info.progress || {};
    bar.firstChild.style.width = `${progress.percent || 0}%`;
    label.textContent = `${progress.message || '处理中…'}（${progress.percent || 0}%）`;
    if (info.status === 'failed') {
      mount(root, el('div', { class: 'notice danger' }, [
        `解析失败：${progress.message || '未知错误'}`,
        el('div', { style: 'margin-top:8px' }, [el('a', { href: '#/', text: '返回首页' })]),
      ]));
      return;
    }
    if (info.ready) {
      toast('解析完成', 'ok');
      render(root, textbookId);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}
