// Small DOM helpers shared by every page.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

export function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

export function toast(message, kind = '', action) {
  const host = document.getElementById('toast-host');
  const node = el('div', { class: `toast ${kind}` }, [message]);
  if (action) {
    const btn = el('button', {
      class: 'btn small',
      style: 'margin-left:10px;background:rgba(255,255,255,.15);border-color:rgba(255,255,255,.35);color:#fff',
      text: action.label,
      onclick: () => { action.onClick(); node.remove(); },
    });
    node.append(btn);
  }
  host.append(node);
  const life = action ? 9000 : 4200;
  setTimeout(() => node.remove(), life);
}

export function modal({ title, body, actions = [] }) {
  const host = document.getElementById('modal-host');
  document.getElementById('modal-title').textContent = title || '提示';
  const bodyHost = document.getElementById('modal-body');
  clear(bodyHost);
  if (typeof body === 'string') bodyHost.innerHTML = body;
  else if (body) bodyHost.append(body);
  const foot = document.getElementById('modal-foot');
  clear(foot);
  const close = () => host.classList.add('hidden');
  for (const action of actions) {
    foot.append(el('button', {
      class: `btn ${action.kind || ''}`,
      text: action.label,
      onclick: () => { if (!action.onClick || action.onClick() !== false) close(); },
    }));
  }
  foot.append(el('button', { class: 'btn', text: '关闭', onclick: close }));
  host.classList.remove('hidden');
  host.querySelectorAll('[data-close]').forEach((node) => node.addEventListener('click', close));
  return { close };
}

export function confirmDialog(title, message, onConfirm) {
  modal({
    title,
    body: `<p>${escapeHtml(message)}</p>`,
    actions: [{ label: '确定', kind: 'danger', onClick: onConfirm }],
  });
}

export function progressBar(value, total, kind = '') {
  const percent = total > 0 ? Math.min(100, Math.round((value / total) * 100)) : 0;
  return el('div', { class: `progress ${kind}`, title: `${value}/${total}` }, [
    el('span', { style: `width:${percent}%` }),
  ]);
}

export function fmtPercent(value) {
  if (value === null || value === undefined) return '—';
  return `${Math.round(value * 100)}%`;
}

export function relativeTime(text) {
  if (!text) return '';
  return text;
}

export function mount(container, ...nodes) {
  clear(container);
  for (const node of nodes.flat()) {
    if (node === null || node === undefined || node === false) continue;
    container.append(node instanceof Node ? node : document.createTextNode(String(node)));
  }
}

export function loadingCard(text = '加载中…') {
  return el('div', { class: 'card center muted' }, [
    el('span', { class: 'spin dark' }), ' ', text,
  ]);
}
