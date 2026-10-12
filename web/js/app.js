// Bootstrap: health chip + hash router.

import { api } from './api.js';
import * as home from './pages/home.js';
import * as book from './pages/book.js';
import * as parsePage from './pages/parse.js';
import * as chapter from './pages/chapter.js';
import * as word from './pages/word.js';
import * as stats from './pages/stats.js';

const view = document.getElementById('view');

const routes = [
  [/^\/?$/, () => home.render(view)],
  [/^\/books\/?$/, () => home.renderBooks(view)],
  [/^\/parse\/(\d+)$/, (id) => parsePage.render(view, Number(id))],
  [/^\/book\/(\d+)$/, (id) => book.render(view, Number(id))],
  [/^\/chapter\/(\d+)$/, (id) => chapter.render(view, Number(id))],
  [/^\/word\/(\d+)$/, (id) => word.render(view, Number(id))],
  [/^\/stats\/?$/, (q) => stats.render(view, q)],
];

export function navigate(path) {
  if (location.hash.slice(1) === path) handleRoute();
  else location.hash = path;
}

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, query] = raw.split('?');
  const params = Object.fromEntries(new URLSearchParams(query || ''));
  return { path, params };
}

function setActiveNav(path) {
  const key = path.startsWith('/stats') ? 'stats'
      : path.startsWith('/book') || path.startsWith('/chapter') || path.startsWith('/parse') || path.startsWith('/books') ? 'books'
        : 'home';
  document.querySelectorAll('#main-nav a').forEach((a) => {
    a.classList.toggle('active', a.dataset.nav === key);
  });
}

async function handleRoute() {
  const { path, params } = parseHash();
  setActiveNav(path);
  window.scrollTo({ top: 0 });
  for (const [pattern, handler] of routes) {
    const match = path.match(pattern);
    if (!match) continue;
    view.innerHTML = '';
    try {
      await handler(match[1], params);
    } catch (err) {
      console.error(err);
      view.innerHTML = `<div class="notice danger">页面加载失败：${err.message}</div>`;
    }
    return;
  }
  view.innerHTML = '<div class="notice warn">未找到该页面。<a href="#/">返回首页</a></div>';
}

async function health() {
  const chip = document.getElementById('health-chip');
  try {
    const data = await api.health();
    const providers = (data.audio_providers || []).map((p) => p.name).join(' + ');
    chip.className = 'health ok';
    chip.textContent = `词库 ${data.ipa.us_entries} 词 · OCR ${data.ocr.available ? '可用' : '不可用'}`;
    chip.title = `发音来源：${providers}\nOCR：${data.ocr.available ? '可用' : data.ocr.reason}`;
  } catch (err) {
    chip.className = 'health bad';
    chip.textContent = '后端未连接';
    chip.title = err.message;
  }
}

window.addEventListener('hashchange', handleRoute);
window.addEventListener('DOMContentLoaded', () => {
  health();
  handleRoute();
});
handleRoute();
