// 本地 Python 后端实现（python run.py 启动时使用）。
// api.js 探测到 /api/health 可用时会自动选用本实现。

async function request(method, url, body, options = {}) {
  const init = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  if (options.raw) {
    init.headers = { ...init.headers, ...(options.headers || {}) };
  }
  const response = await fetch(url, init);
  const text = await response.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch (_) { data = { raw: text }; }
  }
  if (!response.ok) {
    const message = (data && (data.error || data.detail)) || `请求失败 (${response.status})`;
    const err = new Error(message);
    err.status = response.status;
    err.data = data;
    throw err;
  }
  return data;
}

export const serverApi = {
  health: () => request('GET', '/api/health'),

  listTextbooks: () => request('GET', '/api/textbooks'),
  getTextbook: (id) => request('GET', `/api/textbooks/${id}`),
  preview: (id) => request('GET', `/api/textbooks/${id}/preview`),
  textbookStatus: (id) => request('GET', `/api/textbooks/${id}/status`),
  renameTextbook: (id, title) => request('PATCH', `/api/textbooks/${id}`, { title }),
  deleteTextbook: (id) => request('DELETE', `/api/textbooks/${id}`),

  exportTextbook: (id) => request('GET', `/api/textbooks/${id}/export`),

  importTextbook: (payload) => request('POST', '/api/textbooks/import', payload),
  applyChapters: (id, chapters) => request('POST', `/api/textbooks/${id}/chapters`, { chapters }),
  redetectChapters: (id) => request('POST', `/api/textbooks/${id}/redetect-chapters`),
  chapterRange: (chapterId, payload) => request('POST', `/api/chapters/${chapterId}/range`, payload),
  reparse: (id, useOcr) => request('POST', `/api/textbooks/${id}/reparse`, { use_ocr: !!useOcr }),

  upload: (file, { title, useOcr } = {}) => {
    const form = new FormData();
    form.append('file', file, file.name);
    if (title) form.append('title', title);
    if (useOcr) form.append('use_ocr', '1');
    return request('POST', '/api/uploads', form);
  },

  getChapter: (id) => request('GET', `/api/chapters/${id}`),
  addWord: (chapterId, word) => request('POST', `/api/chapters/${chapterId}/words`, { word }),
  removeWord: (chapterId, wordId) => request('DELETE', `/api/chapters/${chapterId}/words/${wordId}`),
  listWords: (bookId, params = {}) => {
    const query = new URLSearchParams(params).toString();
    return request('GET', `/api/textbooks/${bookId}/words${query ? '?' + query : ''}`);
  },
  getWord: (id) => request('GET', `/api/words/${id}`),
  updateWord: (id, patch) => request('PATCH', `/api/words/${id}`, patch),
  enrich: (wordIds, force = false) => request('POST', '/api/enrich', { word_ids: wordIds, force }),

  audioUrl: (wordId, accent, refresh = false) =>
    `/api/audio?word_id=${wordId}&accent=${accent}${refresh ? '&refresh=1' : ''}`,
  audioByWord: (word, accent, refresh = false) =>
    `/api/audio?word=${encodeURIComponent(word)}&accent=${accent}${refresh ? '&refresh=1' : ''}`,
  probeAudio: (wordIds, accents = ['uk', 'us']) =>
    request('POST', '/api/audio/probe', { word_ids: wordIds, accents }),
  audioSources: () => request('GET', '/api/audio/sources'),

  stats: (bookId) => request('GET', `/api/stats${bookId ? '?textbook_id=' + bookId : ''}`),
  lookup: (word) => request('GET', `/api/dictionary/${encodeURIComponent(word)}`),
};
