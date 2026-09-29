// ===========================================================================
// DOM helpers
// ===========================================================================
const $ = id => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k.startsWith('on')) e[k] = v;
    else if (v === true) e.setAttribute(k, '');
    else if (v === false || v == null) continue;
    else e.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    e.appendChild(typeof c === 'string' || typeof c === 'number'
      ? document.createTextNode(String(c)) : c);
  }
  return e;
}

function show(viewId) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const v = $(viewId);
  if (v) v.classList.add('active');
}

function btn(text, cls, onclick) {
  return el('button', { type: 'button', class: 'btn ' + (cls || ''), onclick }, text);
}

// Copy to the clipboard, wherever the page happens to be served from.
// navigator.clipboard exists only in a secure context and this UI is
// routinely reached over plain HTTP on a LAN, so the execCommand path is
// not a legacy nicety here — it is the one that runs. Resolves to whether
// the text actually made it.
function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text)
      .then(() => true, () => _execCopy(text));
  }
  return Promise.resolve(_execCopy(text));
}

// execCommand copies the selection, so it needs one: an off-screen
// textarea holding the text, selected and thrown away again.
function _execCopy(text) {
  const ta = el('textarea', {
    style: 'position:fixed;top:-1000px;opacity:0', 'aria-hidden': 'true',
  });
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch (e) {
    ok = false;
  }
  ta.remove();
  return ok;
}

const _COPY_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" '
  + 'fill="none" stroke="currentColor" stroke-width="1.4" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<rect x="6" y="6" width="8.5" height="9" rx="1.5"/>'
  + '<path d="M11 3.8V3A1.5 1.5 0 0 0 9.5 1.5H3A1.5 1.5 0 0 0 1.5 3v6.5'
  + 'A1.5 1.5 0 0 0 3 11h.8"/></svg>';
const _COPIED_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" '
  + 'fill="none" stroke="currentColor" stroke-width="1.8" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<path d="M2.5 8.5 6 12l7.5-8"/></svg>';
// Rotate: draw this one again. An arrow that comes back to where it
// started says "replace what is there", which is what generating over
// an existing token does.
const _REGENERATE_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" '
  + 'fill="none" stroke="currentColor" stroke-width="1.4" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9"/>'
  + '<path d="M13.6 1.9v2.9h-2.9"/></svg>';

// A prompt in a box: the VT100 terminal page.
const _TERMINAL_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" '
  + 'fill="none" stroke="currentColor" stroke-width="1.4" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/>'
  + '<path d="M4.5 6.5 6.8 8.5 4.5 10.5"/><path d="M8.5 10.5H11.5"/></svg>';
// Lines of output in the same box: the raw view, which does not
// interpret what it is given, it just shows it.
const _RAW_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" '
  + 'fill="none" stroke="currentColor" stroke-width="1.4" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/>'
  + '<path d="M4 6.2h8"/><path d="M4 8.5h8"/><path d="M4 10.8h5"/></svg>';
// One node handing what it has to two others.
const _SHARE_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" '
  + 'fill="none" stroke="currentColor" stroke-width="1.4" '
  + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<circle cx="4" cy="8" r="2"/><circle cx="12" cy="3.6" r="2"/>'
  + '<circle cx="12" cy="12.4" r="2"/>'
  + '<path d="M5.8 7 10.2 4.6"/><path d="M5.8 9 10.2 11.4"/></svg>';

// Small icon-only button for a field or a value shown as text, where a
// labelled button would be more furniture than the action is worth.
function iconBtn(svg, title, onclick) {
  const b = el('button', {
    type: 'button', class: 'icon-btn', title, 'aria-label': title,
  });
  b.innerHTML = svg;
  b.onclick = onclick;
  return b;
}

// Copy — for a value that is displayed as text (a filesystem path, a
// URL) and would otherwise have to be selected by hand. `value` may be
// a string or a function returning one, for values that are not known
// yet when the button is built.
function copyIconBtn(value, title) {
  const b = iconBtn(_COPY_ICON, title || 'Copy to clipboard', null);
  b.onclick = () => {
    copyText(typeof value === 'function' ? value() : value).then(ok => {
      if (!ok) return;
      b.innerHTML = _COPIED_ICON;
      b.classList.add('copied');
      setTimeout(() => {
        b.innerHTML = _COPY_ICON;
        b.classList.remove('copied');
      }, 1200);
    });
  };
  return b;
}

// The same button as iconBtn, as a link: for an action that is really a
// place - a terminal page, a share modal - so it can be middle-clicked,
// opened in a new tab and copied like any other link.
function iconLink(svg, title, href, newTab) {
  const a = el('a', { class: 'icon-btn', href, title, 'aria-label': title });
  if (newTab) {
    a.target = '_blank';
    a.rel = 'noopener';
  }
  a.innerHTML = svg;
  return a;
}

// Text that copies itself on a click and says so for a moment. For a
// value that is taken elsewhere - a device path, an address - where a
// button beside it would be more furniture than the action is worth.
function copyableSpan(text, cls) {
  const span = el('span', {
    class: (cls ? cls + ' ' : '') + 'copyable',
    title: 'Click to copy',
  }, text);
  span.onclick = e => {
    e.stopPropagation();
    copyText(text).then(ok => {
      span.textContent = ok ? 'Copied!' : 'Copy failed';
      setTimeout(() => { span.textContent = text; }, 1000);
    });
  };
  return span;
}

function formGroup(label, input, opts = {}) {
  const wrap = el('div', { class: 'form-group' });
  if (label) wrap.appendChild(el('label', {}, label));
  wrap.appendChild(input);
  if (opts.hint) wrap.appendChild(el('div', {
    class: 'card-subtitle',
    style: 'margin-top:4px;font-size:12px',
  }, opts.hint));
  return wrap;
}

// Two fields on one line, for the pairs that are one answer - where to
// listen, how to frame a byte. Stacked, the short one sat alone on a
// row with the width of an address it never needs. The first label
// takes the row's own label column, the second an inline one, and both
// drop onto their own lines when the modal is too narrow. The second
// half comes back separately because it is not always wanted: a Unix
// socket has a path and no port.
function pairRow(label, first, secondLabel, second) {
  const secondPart = el('span', { class: 'pair-second' },
    el('label', { class: 'inline-label' }, secondLabel), second);
  const row = el('div', { class: 'form-row' },
    typeof label === 'string' ? el('label', {}, label) : label,
    el('div', { class: 'field-pair' }, first, secondPart));
  return { row, secondPart };
}

// Three short answers on one line, each with its own label - the first
// in the row's label column, the other two inline. The last one keeps
// the right edge, so it lines up with the second field of a pairRow
// above it; the first two pack to the left.
function trioRow(label, first, secondLabel, second, thirdLabel, third) {
  const part = (text, field) => el('span', { class: 'pair-second' },
    el('label', { class: 'inline-label' }, text), field);
  return el('div', { class: 'form-row' },
    el('label', {}, label),
    el('div', { class: 'field-trio' },
      first, part(secondLabel, second), part(thirdLabel, third)));
}

function formRow(label, input) {
  const wrap = el('div', { class: 'form-row' });
  wrap.appendChild(el('label', {}, label));
  if (Array.isArray(input)) input.forEach(i => wrap.appendChild(i));
  else wrap.appendChild(input);
  return wrap;
}

// Themed autocomplete dropdown — replaces <datalist> which Safari renders
// via AppKit (ignoring page color-scheme, so it ends up dark-on-dark in
// light theme when macOS is in dark mode).
//
// `getOptions()` returns [{value, hint?}]. The dropdown shows on focus,
// filters by case-insensitive substring of `value`/`hint`, and on click
// sets the input value + dispatches an `input` event so existing
// listeners still fire.
function attachAutocomplete(input, getOptions) {
  input.removeAttribute('list');
  // Wrap the input so the dropdown can absolute-position relative to it.
  // Insertion happens lazily — when this is called the input may not yet
  // be in the DOM, so we wrap eagerly here and let the caller append the
  // wrap to the form row instead of the input.
  const wrap = el('div', { class: 'autocomplete' });
  if (input.parentNode) {
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
  } else {
    // Caller must append `wrap` (accessible via input.parentNode after this)
    wrap.appendChild(input);
  }
  const pop = el('div', { class: 'autocomplete-pop hidden' });
  wrap.appendChild(pop);

  let activeIdx = -1;
  let items = [];

  function rebuild() {
    if (input.disabled) { pop.classList.add('hidden'); return; }
    const q = (input.value || '').trim().toLowerCase();
    const all = getOptions() || [];
    items = q
      ? all.filter(o =>
          (o.value || '').toLowerCase().includes(q)
          || (o.hint || '').toLowerCase().includes(q))
      : all;
    pop.innerHTML = '';
    if (!items.length) { pop.classList.add('hidden'); return; }
    items.forEach((o, i) => {
      const item = el('div', {
        class: 'autocomplete-item',
        // mousedown (not click) so it fires before the input's blur
        // handler hides the popup.
        onmousedown: e => {
          e.preventDefault();
          input.value = o.value;
          input.dispatchEvent(new Event('input', { bubbles: true }));
          input.dispatchEvent(new Event('change', { bubbles: true }));
          pop.classList.add('hidden');
        },
      },
      el('span', { class: 'autocomplete-item-label' }, o.value),
      o.hint ? el('span', { class: 'autocomplete-item-hint' }, o.hint) : null);
      pop.appendChild(item);
    });
    activeIdx = -1;
    pop.classList.remove('hidden');
  }

  function highlight(i) {
    const nodes = pop.querySelectorAll('.autocomplete-item');
    nodes.forEach((n, idx) => n.classList.toggle('active', idx === i));
    if (i >= 0 && nodes[i]) nodes[i].scrollIntoView({ block: 'nearest' });
    activeIdx = i;
  }

  input.addEventListener('focus', rebuild);
  input.addEventListener('input', rebuild);
  input.addEventListener('blur', () => {
    setTimeout(() => pop.classList.add('hidden'), 120);
  });
  input.addEventListener('keydown', e => {
    if (pop.classList.contains('hidden')) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      highlight(Math.min(activeIdx + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      highlight(Math.max(activeIdx - 1, 0));
    } else if (e.key === 'Enter' && activeIdx >= 0) {
      e.preventDefault();
      input.value = items[activeIdx].value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      pop.classList.add('hidden');
    } else if (e.key === 'Escape') {
      pop.classList.add('hidden');
    }
  });
  return wrap;
}

// Generic kebab (3-dot) menu. Each action is {label, cls} plus one of:
//   onclick — a button
//   href    — a real link, so it can be opened in a new tab, middle-
//             clicked or copied, which a button cannot be
//   reason  — inert, and says why, which is more use than an item that
//             is simply missing
function kebabMenu(actions) {
  const wrap = el('div', { class: 'kebab-menu' });
  const btnEl = el('button', {
    type: 'button', class: 'kebab-btn', 'aria-label': 'Actions',
  });
  btnEl.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">'
    + '<circle cx="12" cy="5" r="2"/>'
    + '<circle cx="12" cy="12" r="2"/>'
    + '<circle cx="12" cy="19" r="2"/></svg>';
  const pop = el('div', { class: 'kebab-pop', hidden: true });
  for (const a of actions) {
    const cls = 'kebab-item' + (a.cls ? ' ' + a.cls : '');
    let item;
    if (a.reason) {
      // Without its colour class: an item that cannot be used should
      // not keep the emphasis of one that can.
      item = el('span', { class: 'kebab-item kebab-disabled',
        title: a.reason }, a.label);
    } else if (a.href) {
      item = el('a', {
        class: cls, href: a.href, target: '_blank', rel: 'noopener',
        onclick: () => { pop.hidden = true; },
      }, a.label);
    } else {
      item = el('button', {
        type: 'button', class: cls,
        onclick: e => {
          e.stopPropagation();
          pop.hidden = true;
          a.onclick();
        },
      }, a.label);
    }
    pop.appendChild(item);
  }
  btnEl.onclick = e => {
    e.stopPropagation();
    document.querySelectorAll('.kebab-pop').forEach(p => {
      if (p !== pop) p.hidden = true;
    });
    pop.hidden = !pop.hidden;
  };
  wrap.appendChild(btnEl);
  wrap.appendChild(pop);
  return wrap;
}

// Single delegated handler closes any open kebab when click lands outside.
document.addEventListener('click', e => {
  document.querySelectorAll('.kebab-pop:not([hidden])').forEach(pop => {
    const wrap = pop.closest('.kebab-menu');
    if (!wrap || !wrap.contains(e.target)) pop.hidden = true;
  });
});

// ===========================================================================
// Theme — single button cycles light → dark → auto
// ===========================================================================
const THEME_KEY = 'ser2tcp_theme';
const THEME_ICONS = {
  light: '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>',
  dark: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  auto: '<circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 0 0 20V2z" fill="currentColor"/>',
};
function currentTheme() { return localStorage.getItem(THEME_KEY) || 'auto'; }
function applyTheme(theme) {
  const resolved = theme === 'auto'
    ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : theme;
  document.documentElement.setAttribute('data-theme', resolved);
  const btnEl = $('theme-toggle');
  if (btnEl) {
    btnEl.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24"'
      + ' fill="none" stroke="currentColor" stroke-width="2">'
      + THEME_ICONS[theme] + '</svg>';
  }
}
applyTheme(currentTheme());
document.addEventListener('click', e => {
  if (!e.target.closest('#theme-toggle')) return;
  const order = ['light', 'dark', 'auto'];
  const next = order[(order.indexOf(currentTheme()) + 1) % order.length];
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (currentTheme() === 'auto') applyTheme('auto');
});

// ===========================================================================
// Auth state
// ===========================================================================
let token = localStorage.getItem('ser2tcp_token');
let username = localStorage.getItem('ser2tcp_user');
let isAdmin = false;

function setCredentials(t, u) {
  token = t;
  username = u;
  if (t) {
    localStorage.setItem('ser2tcp_token', t);
    localStorage.setItem('ser2tcp_user', u);
  } else {
    localStorage.removeItem('ser2tcp_token');
    localStorage.removeItem('ser2tcp_user');
  }
}

let _authed = false;

function updateNav() {
  const header = $('app-header');
  const userBtn = $('user-btn');
  const userName = $('user-name');
  const navUsers = $('nav-users');
  const navCerts = $('nav-certificates');
  const navDetected = $('nav-detected');
  header.hidden = !_authed;
  if (token && username) {
    userName.textContent = username;
    userBtn.classList.toggle('is-admin', isAdmin);
    userBtn.hidden = false;
  } else {
    userBtn.hidden = true;
  }
  if (navUsers) navUsers.hidden = !isAdmin;
  if (navCerts) navCerts.hidden = !isAdmin;
  if (navDetected) navDetected.hidden = !isAdmin;
}

function updateActiveTab() {
  const hash = location.hash.replace(/^#/, '') || '/ports';
  const root = '/' + hash.replace(/^\//, '').split('/')[0];
  document.querySelectorAll('#main-nav .nav-link').forEach(a => {
    a.classList.toggle('active', a.dataset.hash === root);
  });
}

// ===========================================================================
// API helper
// ===========================================================================
function api(method, path, body) {
  const opts = { method, headers: {}, cache: 'no-store' };
  if (token) opts.headers['Authorization'] = 'Bearer ' + token;
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  return fetch(path, opts).then(r => {
    if (r.status === 401) {
      setCredentials(null, null);
      navigate('/login');
      return Promise.reject('unauthorized');
    }
    return r.json().then(d => {
      if (r.ok) return d;
      // A refusal the caller can act on carries more than a sentence,
      // so hand back the whole body. Everything else stays a string,
      // which is what every other .catch() in here is written for.
      return Promise.reject(d.confirm ? d : (d.error || 'Error'));
    });
  });
}

// ===========================================================================
// Modal stack
// ===========================================================================
const _modalStack = [];

function _isHashDescendant(parent, child) {
  return child !== parent && child.startsWith(parent + '/');
}

function openModal(opts) {
  // opts: { title, body, footer, onClose, wide, key? }
  const key = opts.key || location.hash || '#';
  const top = _modalStack[_modalStack.length - 1];
  if (top && top.key === key) {
    _replaceModalContent(top, opts);
    return;
  }
  const idx = _modalStack.findIndex(m => m.key === key);
  if (idx !== -1) {
    while (_modalStack.length > idx + 1) _popModal();
    _replaceModalContent(_modalStack[idx], opts);
    return;
  }
  while (_modalStack.length
      && !_isHashDescendant(_modalStack[_modalStack.length - 1].key, key)) {
    _popModal();
  }
  _pushModal(key, opts);
}

function _pushModal(key, opts) {
  const overlay = $('modal-overlay');
  const titleEl = el('h2');
  const closeBtn = el('button', {
    type: 'button', class: 'modal-close',
    'aria-label': 'Close', onclick: () => backToList(),
  });
  closeBtn.innerHTML = '&times;';
  const bodyEl = el('div', { class: 'modal-body' });
  const errorEl = el('div', { class: 'modal-error hidden' });
  const footerEl = el('div', { class: 'modal-footer' });
  const modalEl = el('div', { class: 'modal' },
    el('div', { class: 'modal-header' }, titleEl, closeBtn),
    bodyEl, errorEl, footerEl);
  overlay.appendChild(modalEl);
  overlay.hidden = false;
  const slot = { key, modalEl, titleEl, bodyEl, footerEl, errorEl, onClose: null };
  _modalStack.push(slot);
  _replaceModalContent(slot, opts);
}

function _replaceModalContent(slot, opts) {
  slot.titleEl.textContent = opts.title || '';
  slot.bodyEl.innerHTML = '';
  if (opts.body) slot.bodyEl.appendChild(opts.body);
  slot.footerEl.innerHTML = '';
  if (opts.footer) {
    (Array.isArray(opts.footer) ? opts.footer : [opts.footer])
      .forEach(f => slot.footerEl.appendChild(f));
  }
  slot.modalEl.classList.toggle('modal-wide', !!opts.wide);
  slot.errorEl.classList.add('hidden');
  slot.errorEl.textContent = '';
  slot.onClose = opts.onClose || null;
}

function _popModal() {
  const slot = _modalStack.pop();
  if (!slot) return;
  if (slot.onClose) {
    try { slot.onClose(); } catch (e) { console.warn('modal onClose:', e); }
  }
  slot.modalEl.remove();
  if (!_modalStack.length) $('modal-overlay').hidden = true;
}

function closeModal() {
  while (_modalStack.length) _popModal();
}

function modalError(msg) {
  const top = _modalStack[_modalStack.length - 1];
  if (!top) return;
  top.errorEl.textContent = msg;
  top.errorEl.classList.remove('hidden');
}

// Close on overlay click only when both mousedown+mouseup land on overlay.
{
  let _downOnOverlay = false;
  const overlay = $('modal-overlay');
  overlay.addEventListener('mousedown', e => {
    _downOnOverlay = (e.target === overlay);
  });
  overlay.addEventListener('mouseup', e => {
    if (_downOnOverlay && e.target === overlay) backToList();
    _downOnOverlay = false;
  });
}
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && _modalStack.length) backToList();
});

function backToList() {
  if (_modalStack.length > 1) {
    const parent = _modalStack[_modalStack.length - 2];
    location.hash = parent.key.replace(/^#/, '');
    return;
  }
  const hash = location.hash.replace(/^#/, '');
  if (hash.startsWith('/ports')) navigate('/ports');
  else if (hash.startsWith('/users') || hash.startsWith('/tokens')) navigate('/users');
  else if (hash.startsWith('/settings')) navigate('/settings');
  else if (hash.startsWith('/certificates')) navigate('/certificates');
  else closeModal();
}

// ===========================================================================
// Router
// ===========================================================================
function _parseQuery(s) {
  const out = {};
  if (!s) return out;
  for (const part of s.split('&')) {
    const eq = part.indexOf('=');
    if (eq === -1) out[decodeURIComponent(part)] = '';
    else out[decodeURIComponent(part.slice(0, eq))]
      = decodeURIComponent(part.slice(eq + 1));
  }
  return out;
}

const routes = [
  [/^\/ports$/,                    () => showPorts()],
  [/^\/ports\/new$/,               m => showPortEditor(null, m._q)],
  [/^\/ports\/([^/]+)\/edit$/,      m => showPortEditor(
      decodeURIComponent(m[1]), m._q)],
  [/^\/ports\/([^/]+)\/share\/(.+)$/, m => showShareLink(
      decodeURIComponent(m[1]), decodeURIComponent(m[2]))],
  [/^\/detected$/,                 () => showDetected()],
  [/^\/users$/,                    () => showUsers()],
  [/^\/users\/new$/,               () => showUserEditor(null)],
  [/^\/users\/([^/]+)\/edit$/,     m => showUserEditor(decodeURIComponent(m[1]))],
  [/^\/tokens\/new$/,              () => showTokenEditor(null)],
  [/^\/tokens\/([^/]+)\/edit$/,    m => showTokenEditor(decodeURIComponent(m[1]))],
  [/^\/settings$/,                 () => showSettings()],
  [/^\/settings\/http\/new$/,      () => showHttpEditor(null)],
  [/^\/settings\/http\/([^/]+)\/edit$/, m => showHttpEditor(
      decodeURIComponent(m[1]))],
  [/^\/certificates$/,             () => showCertificates()],
  [/^\/certificates\/new$/,        () => showCertEditor(null)],
  [/^\/certificates\/generate$/,   () => showCertGenerator()],
  [/^\/certificates\/([^/]+)$/,    m => showCertEditor(decodeURIComponent(m[1]))],
  [/^\/certificates\/([^/]+)\/paste\/(cert\.pem|key\.pem|ca\.pem)$/,
    m => _showPasteCertModal(decodeURIComponent(m[1]), m[2])],
  [/^\/certificates\/([^/]+)\/view\/(cert\.pem|ca\.pem)$/,
    m => _showViewCertModal(decodeURIComponent(m[1]), m[2])],
  [/^\/certificates\/([^/]+)\/replace$/,
    m => _showReplacePairModal(decodeURIComponent(m[1]))],
  [/^\/login$/,                    () => showLogin()],
];

function route() {
  const fullHash = location.hash.replace(/^#/, '') || '/ports';
  const [path, queryStr] = fullHash.split('?');
  const q = _parseQuery(queryStr || '');
  // List-view routes — close any modal so it doesn't linger.
  if (path === '/ports' || path === '/users' || path === '/settings'
      || path === '/certificates' || path === '/detected') {
    closeModal();
  }
  for (const [re, handler] of routes) {
    const m = path.match(re);
    if (m) { m._q = q; handler(m); updateActiveTab(); return; }
  }
  navigate('/ports');
}

function navigate(path) {
  if (location.hash === '#' + path) route();
  else location.hash = path;
}

window.addEventListener('hashchange', route);

// ===========================================================================
// Login / Logout
// ===========================================================================
function showLogin() {
  _authed = false;
  show('login-view');
  $('app-header').hidden = true;
  closeModal();
  $('login-user').focus();
}

function doLogin() {
  const login = $('login-user').value;
  const password = $('login-pass').value;
  const errEl = $('login-error');
  errEl.classList.add('hidden');
  fetch('/api/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({login, password}),
  }).then(r => r.json()).then(data => {
    if (data.token) {
      setCredentials(data.token, login);
      $('login-pass').value = '';
      bootApp();
    } else {
      errEl.textContent = data.error || 'Login failed';
      errEl.classList.remove('hidden');
    }
  }).catch(() => {
    errEl.textContent = 'Network error';
    errEl.classList.remove('hidden');
  });
}

function doLogout() {
  api('POST', '/api/logout').catch(() => {});
  setCredentials(null, null);
  isAdmin = false;
  stopStatusStream();
  navigate('/login');
}

// ===========================================================================
// Constants
// ===========================================================================
const MATCH_ATTRS = ['vid', 'pid', 'serial_number', 'manufacturer', 'product', 'location'];
// Shown instead of the attribute name where it is too long for the
// label column - the two that would otherwise widen it for everybody.
// The full name stays as the field's title, since that is what goes in
// the config file.
const MATCH_LABELS = { serial_number: 'serial', manufacturer: 'manufact' };
const PROTOCOLS = ['TCP', 'TELNET', 'TLS', 'SOCKET', 'WEBSOCKET'];
const CONTROL_SIGNALS = ['rts', 'dtr', 'cts', 'dsr', 'ri', 'cd'];
// Which way each line runs. RTS and DTR are driven from this end, so
// they are the only ones a client could ever be allowed to set; the
// rest are the device talking back.
const OUTPUT_SIGNALS = ['rts', 'dtr'];
const INPUT_SIGNALS = CONTROL_SIGNALS.filter(
  sig => !OUTPUT_SIGNALS.includes(sig));
const BAUDRATES = [300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200,
  230400, 460800, 921600];
const BYTESIZES = {8: 'EIGHTBITS', 7: 'SEVENBITS', 6: 'SIXBITS', 5: 'FIVEBITS'};
const PARITIES = ['NONE', 'EVEN', 'ODD', 'MARK', 'SPACE'];
const STOPBITS = {'1': 'ONE', '1.5': 'ONE_POINT_FIVE', '2': 'TWO'};

// ===========================================================================
// Ports view
// ===========================================================================
let detectedPorts = [];
let portsStatus = null;
let usedPorts = [];
let usedEndpoints = [];

function showPorts() {
  show('ports-view');
  // Both portsStatus and detectedPorts come live from the NDJSON stream —
  // render from cached state, no extra fetches needed.
  renderPortsActions();
  renderPortsList();
}

function _refreshDetectedView() {
  if ($('detected-view').classList.contains('active')) {
    renderDetectedSection();
  }
}

function showDetected() {
  // Admin-only: the whole point of this screen is adding a port from a
  // device, and only an admin can do that.
  if (!isAdmin) { navigate('/ports'); return; }
  show('detected-view');
  renderDetectedSection();
}

// Every id in use, so the editor can offer one that is not and warn
// before the server has to.
function knownIds() {
  const ids = new Set();
  ((portsStatus && portsStatus.ports) || []).forEach(p => {
    if (p.id) ids.add(p.id);
  });
  let http = (currentSettings && currentSettings.http) || [];
  if (!Array.isArray(http)) http = [http];
  http.forEach(srv => { if (srv && srv.id) ids.add(srv.id); });
  return ids;
}

const ID_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;

// Every server setting the editor has a field for. These are rebuilt
// from the form on save; anything else is carried over untouched.
const FORM_OWNED_SERVER_KEYS = [
  'address', 'port', 'endpoint', 'token', 'tls', 'access', 'data',
  'control', 'allow', 'deny', 'max_connections',
];

// `data` is the older spelling of `access`, and the server refuses a
// config that carries both and means different things by them. The
// editor writes `access`, so it has to take `data` with it — leaving
// the old key behind is how saving a port would break it.
function serverAccess(srv) {
  if (srv.access) return srv.access;
  return srv.data === false ? 'none' : 'rw';
}

const ACCESS_LABELS = {
  rw: 'read/write',
  ro: 'read only — clients cannot write to the device',
  wo: 'write only — clients are not sent the device output',
  none: 'control only',
};

// The same idea for an HTTP server: these come from the form, the rest
// of the entry - an IP filter, anything added later - is carried over.
const FORM_OWNED_HTTP_KEYS = ['address', 'port', 'name', 'tls'];

// An id is derived from the name: lowercase, digits and hyphens, with
// everything else becoming a hyphen, runs of them collapsing to one and
// neither end keeping one. So `My Port ##2` and `my/port--2` both give
// `my-port-2` - what a person types to mean the same thing should mean
// the same thing.
//
// The same rule lives in config_ids.py, because the server has to fill
// one in for a caller that sends none and this has to *show* one before
// the save that would create it. tests/test_config_ids_slug.py holds
// the cases both are pinned to.
const SLUG_MAX = 48;
// The last thing left to say about an entry with no name and no device
// is its kind. Calling an HTTP server `port` would be worse than
// saying nothing.
const FALLBACK_SLUG = 'port';
const HTTP_FALLBACK_SLUG = 'http';

function slugId(text) {
  if (typeof text !== 'string') return '';
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, SLUG_MAX)
    .replace(/^-+|-+$/g, '');
}

// `base`, or the first `base-N` nobody is answering to. Two names can
// land on one slug, so the count is against the ids in use.
function uniqueId(base, taken) {
  base = base || FALLBACK_SLUG;
  if (!taken.has(base)) return base;
  let n = 1;
  while (taken.has(base + '-' + n)) n++;
  return base + '-' + n;
}

// What an entry's id should be derived from: its name, or failing that
// the device it is configured for.
function entrySlug(name, device, fallback) {
  return slugId(name)
    || slugId(String(device || '').split('/').pop())
    || fallback || FALLBACK_SLUG;
}

function suggestId(name, device, ignore, fallback) {
  const taken = knownIds();
  if (ignore) taken.delete(ignore);
  return uniqueId(entrySlug(name, device, fallback), taken);
}

// Recompute usedPorts/usedEndpoints — used by editor for conflict checks.
function _rebuildUsedSets() {
  usedPorts = [];
  usedEndpoints = [];
  if (!portsStatus) return;
  portsStatus.ports.forEach(p => {
    // Carry the id to tell the port being edited apart from the rest,
    // and the label to say which port the clash is with - the same one
    // its card is titled by.
    const owner = { id: p.id, label: portTitle(p) };
    (p.servers || []).forEach(s => {
      if (s.port) {
        usedPorts.push({
          address: s.address, port: s.port,
          id: owner.id, label: owner.label});
      }
      if (s.endpoint) {
        usedEndpoints.push({
          endpoint: s.endpoint, id: owner.id, label: owner.label});
      }
    });
  });
}

// Re-render whichever views currently depend on portsStatus.
function _refreshPortViews() {
  _rebuildUsedSets();
  if ($('ports-view').classList.contains('active')) {
    renderPortsList();
  }
}

// ===========================================================================
// Status NDJSON stream
// ===========================================================================
let _statusStream = null;        // AbortController for the active fetch
let _statusReconnectTimer = null;
const STATUS_RECONNECT_MS = 2000;
// Whether the status stream is delivering. The page is built entirely
// from what the stream says, so when it stops, everything on screen is
// a snapshot of a server we can no longer reach - and the title says so.
let _serverReachable = null;

function setServerReachable(reachable) {
  if (_serverReachable === reachable) return;
  _serverReachable = reachable;
  const logo = document.querySelector('.logo');
  if (!logo) return;
  logo.classList.toggle('server-online', reachable === true);
  logo.classList.toggle('server-offline', reachable === false);
  logo.title = reachable === false ? 'Server unreachable' : '';
  // And everything the page is showing is now a memory, not a state.
  document.body.classList.toggle('stream-down', reachable === false);
  const banner = $('stream-banner');
  if (banner) banner.hidden = reachable !== false;
}

function startStatusStream() {
  stopStatusStream();
  const ctrl = new AbortController();
  _statusStream = ctrl;
  const headers = {};
  if (token) headers['Authorization'] = 'Bearer ' + token;
  fetch('/api/status?stream=1', {
    method: 'GET',
    headers,
    cache: 'no-store',
    signal: ctrl.signal,
  }).then(async resp => {
    if (resp.status === 401) {
      // The server answered, so it is not the one that is missing -
      // this session is. Say so, or the login page arrives under a
      // banner blaming the server we just reached.
      setServerReachable(true);
      _statusStream = null;
      setCredentials(null, null);
      navigate('/login');
      return;
    }
    if (!resp.ok || !resp.body) {
      throw new Error('stream HTTP ' + resp.status);
    }
    setServerReachable(true);
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        try { _applyStatusLine(JSON.parse(line)); }
        catch (e) { console.warn('stream parse:', e, line); }
      }
    }
    throw new Error('stream closed');
  }).catch(err => {
    if (ctrl.signal.aborted) return;
    if (_statusStream === ctrl) {
      _statusStream = null;
      setServerReachable(false);
      _scheduleStatusReconnect();
    }
  });
}

function stopStatusStream() {
  if (_statusReconnectTimer) {
    clearTimeout(_statusReconnectTimer);
    _statusReconnectTimer = null;
  }
  if (_statusStream) {
    _statusStream.abort();
    _statusStream = null;
  }
}

function _scheduleStatusReconnect() {
  if (_statusReconnectTimer) return;
  _statusReconnectTimer = setTimeout(() => {
    _statusReconnectTimer = null;
    if (_authed) startStatusStream();
  }, STATUS_RECONNECT_MS);
}

function _applyStatusLine(line) {
  // Heartbeat
  if (Object.keys(line).length === 0) return;
  // Full snapshot — replaces ports / detected / admin
  if (line.ports !== undefined) {
    portsStatus = { ports: line.ports, admin: !!line.admin };
    detectedPorts = line.detected || [];
    const wasAuthed = _authed;
    _authed = true;
    isAdmin = !!line.admin;
    updateNav();
    renderPortsActions();
    _refreshPortViews();
    _refreshDetectedView();
    if (!wasAuthed) {
      // First snapshot after page load or login — pick up routing.
      const hash = location.hash.replace(/^#/, '');
      if (!hash || hash === '/login') navigate('/ports');
      else route();
    }
    return;
  }
  // Detected USB devices changed (plug / unplug). Port cards depend on
  // detectedPorts too — their border color (error vs offline) is decided
  // by whether the configured device exists on the system right now —
  // so re-render both sections.
  if (line.detected !== undefined) {
    detectedPorts = line.detected;
    if ($('ports-view').classList.contains('active')) renderPortsList();
    _refreshDetectedView();
    return;
  }
  // Per-port delta, named by id. The server sends one only while the
  // list holds the same ports in the same order; a port added, removed,
  // moved or renamed comes as a whole snapshot instead.
  if (line._delta && line.id !== undefined && portsStatus) {
    const port = portsStatus.ports.find(p => p.id === line.id);
    if (!port) return;
    for (const [k, v] of Object.entries(line)) {
      if (k === 'id' || k === '_delta') continue;
      if (v === null) delete port[k];
      else port[k] = v;
    }
    _refreshPortViews();
  }
}

function renderPortsActions() {
  const c = $('ports-actions');
  c.innerHTML = '';
  if (isAdmin) {
    c.appendChild(btn('+ Add Port', 'btn-primary btn-small',
      () => navigate('/ports/new')));
  }
}

function renderPortsList() {
  // The port in hand was deleted meanwhile, in another tab: there is
  // nothing left to move. Ending the drag draws the list again.
  const inHand = portSort && portSort.item;
  if (inHand && portsStatus
      && !portsStatus.ports.some(p => p.id === inHand.dataset.portId)) {
    portSort.cancel();
    return;
  }
  const root = $('ports-content');
  if (!portsStatus || !portsStatus.ports.length) {
    root.innerHTML = '';
    root.appendChild(el('p', { class: 'empty' },
      portsStatus ? 'No ports configured' : 'Loading…'));
    return;
  }
  _resolveEndpointTokens(portsStatus.ports);
  let grid = root.querySelector(':scope > .card-grid');
  if (!grid) {
    root.innerHTML = '';
    grid = el('div', { class: 'card-grid' });
    // Once per grid; it asks whether this is an admin at every press.
    // After every press the list is drawn from what the server said:
    // back to its order if the drag was given up, and the card that was
    // held drawn afresh if its port changed meanwhile.
    portSort = makeSortable(grid, {
      item: '.card', handle: '.card-title', hold: true,
      enabled: () => isAdmin,
      onDrop: _dropPort,
      onEnd: renderPortsList,
    });
    root.appendChild(grid);
  }
  grid.classList.toggle('movable', !!isAdmin);
  _reconcilePortCards(grid, portsStatus.ports);
}

// Only a card whose port changed is drawn again. It used to be the
// whole grid on every status push - and a port that reports its signals
// pushes several times a second - so every card was rebuilt, and a menu
// open on any of them closed under the pointer.
//
// Each card remembers what it was drawn from: its port, and the few
// things outside it that a card shows - whether this is an admin, the
// USB devices present (a card's colour), the endpoint tokens (its share
// links). One that would come out the same is left as it is.
//
// A card in hand is the exception. Everything else stays live while it
// is dragged - status, ports added or deleted, and an order changed
// from another tab - but that card keeps the element under the pointer
// and the slot it is being shown in, with the others arranged around
// it. Redrawn, it would leave the hand; moved, it would leave the slot
// the person is looking at. If the other tab moved this very port, the
// drop still comes later and wins: the move sent is relative to where
// it lands, and the server answers with the order that results.
function _reconcilePortCards(grid, ports) {
  const context = JSON.stringify(
    [!!isAdmin, detectedPorts, [..._endpointTokens]]);
  const existing = new Map(
    [...grid.children].map(c => [c.dataset.portId, c]));
  const inHand = portSort && portSort.item;
  const held = inHand && existing.get(inHand.dataset.portId) === inHand
    ? inHand : null;
  if (held) {
    const heldId = held.dataset.portId;
    const slot = [...grid.children].indexOf(held);
    const others = ports.filter(p => p.id !== heldId);
    const mine = ports.find(p => p.id === heldId);
    ports = others.slice(0, slot).concat([mine], others.slice(slot));
  }
  const wanted = ports.map(p => {
    const drawnFrom = JSON.stringify(p) + '\n' + context;
    const old = existing.get(p.id);
    existing.delete(p.id);
    // Stale until it is let go, and drawn afresh then: the drawnFrom
    // it keeps says what it was drawn from, not what it should be.
    if (old && old === held) return old;
    if (old && old._drawnFrom === drawnFrom) return old;
    const card = renderPortCard(p);
    card._drawnFrom = drawnFrom;
    // In its old place, so the order pass below has nothing to move
    // for a port that only changed.
    if (old) old.replaceWith(card);
    return card;
  });
  existing.forEach(c => c.remove());  // ports that are gone
  // Then the order, moving only what is out of place - and letting it
  // slide, so a port moved from another tab is seen going where it went.
  const inOrder = wanted.length === grid.children.length
    && wanted.every((c, i) => grid.children[i] === c);
  if (inOrder) return;
  _slideCards(grid, () => {
    let ref = grid.firstChild;
    wanted.forEach(c => {
      if (c === ref) ref = ref.nextSibling;
      else grid.insertBefore(c, ref);
    });
  });
}

// ===========================================================================
// Putting things in order by dragging them
// ===========================================================================
// One gesture for every list that has an order - the port cards, the
// HTTP server cards, the server boxes in the port editor - written once,
// so the three cannot drift apart:
//
// - taken by its `handle` and nowhere else: the title of a card, the
//   grip of a server box. Anywhere-that-is-not-a-control was tried on
//   the cards, and a card is mostly things to read and copy - so a
//   press on one was as likely to mean selecting text as moving it
// - a mouse or pen starts after a few pixels, so a press that does not
//   travel is still a click - an unnamed port's title copies its path.
//   A finger on a card's title holds still first (`hold`), or every
//   swipe that started on one would move it instead of scrolling; a
//   grip takes the touch as its own and needs no hold
// - the others slide aside (_slideCards), and one still sliding is not
//   a drop target: it is not where it will be, and aiming at it sent
//   the dragged one back and forth
// - near the top or bottom of what scrolls, it scrolls - on a timer,
//   since a pointer held still at the edge sends no events to scroll on
// - Escape puts everything back and goes no further: in a dialog it
//   would close the dialog too, and a drag given up is not a form given
//   up with everything typed in it
//
// What the new order means is the caller's: `onDrop(items, item)` gets
// the items as they stand after a drag, and `onEnd()` runs after every
// press, dragged or not.
const DRAG_THRESHOLD = 5;     // px the mouse or pen has to travel
const DRAG_HOLD_MS = 400;     // how long a finger has to stay put
const DRAG_SCROLL_EDGE = 48;  // px from the edge where it scrolls
const DRAG_SCROLL_STEP = 12;  // px per tick while it does
const SORT_ENTER = 0.05;      // how far into another item, of its size

let sortDrag = null;          // the press or drag in progress, or null

function makeSortable(list, opts) {
  list.addEventListener('pointerdown', e => {
    if (sortDrag || e.button !== 0) return;
    if (opts.enabled && !opts.enabled()) return;
    const item = e.target.closest(opts.item);
    if (!item || item.parentElement !== list) return;
    const handle = e.target.closest(opts.handle);
    if (!handle || !item.contains(handle) || list.children.length < 2) {
      return;
    }
    e.preventDefault();  // no text selection from what is dragged by
    sortDrag = {
      list, item, opts,
      pointerId: e.pointerId,
      x: e.clientX, y: e.clientY, lastX: e.clientX, lastY: e.clientY,
      hold: !!opts.hold && e.pointerType === 'touch',
      active: false,
      timer: null,
      scrollTimer: null,
      scroller: (opts.scroller && opts.scroller(item))
        || document.scrollingElement,
      // What Escape puts back.
      before: [...list.children],
    };
    if (sortDrag.hold) {
      sortDrag.timer = setTimeout(_startSortDrag, DRAG_HOLD_MS);
    }
  });
  return {
    // The item pressed or in hand, or null - what a list redrawn under
    // a drag has to leave where it is.
    get item() {
      return sortDrag && sortDrag.list === list ? sortDrag.item : null;
    },
    // Give the drag up from outside: the thing in hand was deleted.
    cancel() {
      if (sortDrag && sortDrag.list === list) _endSortDrag(false);
    },
  };
}

function _startSortDrag() {
  const drag = sortDrag;
  if (!drag) return;
  clearTimeout(drag.timer);
  drag.active = true;
  drag.item.classList.add('dragging');
  document.body.classList.add('sorting');
  // A mouse that travelled the threshold has begun selecting text.
  const sel = window.getSelection && window.getSelection();
  if (sel) sel.removeAllRanges();
  drag.scrollTimer = setInterval(_scrollWhileSorting, 16);
}

function _scrollWhileSorting() {
  const drag = sortDrag;
  if (!drag || !drag.active) return;
  const s = drag.scroller;
  const r = s === document.scrollingElement
    ? { top: 0, bottom: window.innerHeight }
    : s.getBoundingClientRect();
  const before = s.scrollTop;
  if (drag.lastY < r.top + DRAG_SCROLL_EDGE) s.scrollTop -= DRAG_SCROLL_STEP;
  else if (drag.lastY > r.bottom - DRAG_SCROLL_EDGE) {
    s.scrollTop += DRAG_SCROLL_STEP;
  }
  // What is under a still pointer changed with the scroll.
  if (s.scrollTop !== before) _placeSorted(drag.lastX, drag.lastY);
}

// Which item the pointer is over, and which half of it, decides where
// the dragged one goes. Across a row of a grid the items read left to
// right; in a single column - a grid of one, or no grid at all - top to
// bottom.
// The item in hand takes the slot of the one it is moved onto, as soon
// as the pointer is a little way into it. It used to wait for the
// middle, and a card that stayed put under a pointer plainly on top of
// the next one read as the drag not working.
//
// Except when the item in hand is the smaller of the two. After the
// swap it fills only the far end of the slot the other one had - the
// part it is entered by is the other one's again - so a pointer short of
// that end is over the other item once more, and they swapped back and
// forth for as long as it stayed there. So the way in is as deep as the
// difference in size: past it the pointer lands on the item it holds,
// and nothing is left to swap back.
function _placeSorted(x, y) {
  const { list, item, opts } = sortDrag;
  const over = document.elementFromPoint(x, y);
  const target = over && over.closest(opts.item);
  if (!target || target === item || target.parentElement !== list) return;
  if (target.classList.contains('sliding')) return;
  const r = target.getBoundingClientRect();
  const own = item.getBoundingClientRect();
  const items = [...list.children];
  const forward = items.indexOf(item) < items.indexOf(target);
  // Along a row it is entered from the side, between rows from above or
  // below - and only that edge counts: a grip sits at the right edge of
  // a server box, so the pointer is always near that one.
  const alongX = Math.abs(own.top - r.top) < r.height / 2;
  const into = alongX
    ? (forward ? x - r.left : r.right - x) / r.width
    : (forward ? y - r.top : r.bottom - y) / r.height;
  const smaller = alongX
    ? 1 - own.width / r.width
    : 1 - own.height / r.height;
  if (into < Math.max(SORT_ENTER, smaller)) return;
  _slideCards(list, () =>
    list.insertBefore(item, forward ? target.nextSibling : target));
}

function _endSortDrag(commit) {
  const drag = sortDrag;
  if (!drag) return;
  clearTimeout(drag.timer);
  clearInterval(drag.scrollTimer);
  // Before the callbacks: a list redrawn from them must not see this
  // item as still in hand.
  sortDrag = null;
  drag.item.classList.remove('dragging');
  document.body.classList.remove('sorting');
  if (drag.active) {
    _swallowNextClick();
    if (commit) {
      drag.opts.onDrop([...drag.list.children], drag.item);
    } else {
      // Back as it was - but only what is still there: the list may
      // have lost an item while this lasted, and gained one at the end.
      const now = new Set(drag.list.children);
      _slideCards(drag.list, () => drag.before
        .filter(b => now.has(b))
        .forEach(b => drag.list.appendChild(b)));
    }
  }
  if (drag.opts.onEnd) drag.opts.onEnd();
}

window.addEventListener('pointermove', e => {
  const drag = sortDrag;
  if (!drag || e.pointerId !== drag.pointerId) return;
  drag.lastX = e.clientX;
  drag.lastY = e.clientY;
  if (!drag.active) {
    const moved = Math.hypot(e.clientX - drag.x, e.clientY - drag.y);
    if (drag.hold) {
      // Moved before the hold was up: that is a scroll, not a drag.
      if (moved > DRAG_THRESHOLD * 2) _endSortDrag(false);
      return;
    }
    if (moved < DRAG_THRESHOLD) return;
    _startSortDrag();
  }
  _placeSorted(e.clientX, e.clientY);
});
window.addEventListener('pointerup', e => {
  if (sortDrag && e.pointerId === sortDrag.pointerId) _endSortDrag(true);
});
window.addEventListener('pointercancel', e => {
  if (sortDrag && e.pointerId === sortDrag.pointerId) _endSortDrag(false);
});
window.addEventListener('keydown', e => {
  if (e.key === 'Escape' && sortDrag && sortDrag.active) {
    e.stopPropagation();
    _endSortDrag(false);
  }
}, true);
// Once a finger is dragging, it must not scroll the page too.
window.addEventListener('touchmove', e => {
  if (sortDrag && sortDrag.active) e.preventDefault();
}, { passive: false });

// Send a drop as a move: the dragged item's new neighbour is its anchor,
// since the API takes {before: id} or {after: id} and never a position.
// `show(order)` puts an order on screen: the dropped one at once, then
// the server's - somebody may have moved or added something in between
// - or `previous` again if the move was refused.
function _sendMove(url, order, id, show, previous) {
  const at = order.indexOf(id);
  const anchor = at + 1 < order.length
    ? { before: order[at + 1] }
    : { after: order[at - 1] };
  show(order);
  api('POST', url, anchor)
    .then(res => { if (res && res.order) show(res.order); })
    .catch(e => {
      if (e === 'unauthorized') return;
      alert(e);
      show(previous);
    });
}

// Move cards so that the ones pushed aside can be seen going where they
// went, rather than being somewhere else from one frame to the next.
// Where each card is, is measured before the change and after; each is
// then drawn back where it was and let slide to where it is now.
const CARD_SLIDE_MS = 150;

function _slideCards(grid, change) {
  const cards = [...grid.children];
  const reduced = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduced) {
    change();
    return;
  }
  // Measured as drawn, mid-slide included, so a card already on its way
  // carries on from where it is instead of jumping back first.
  const was = new Map(cards.map(c => [c, c.getBoundingClientRect()]));
  change();
  const sliding = [];
  cards.forEach(c => {
    const a = was.get(c);
    const b = c.getBoundingClientRect();
    const dx = a.left - b.left;
    const dy = a.top - b.top;
    if (!dx && !dy) return;
    c.style.transition = 'none';
    c.style.transform = 'translate(' + dx + 'px, ' + dy + 'px)';
    sliding.push(c);
  });
  if (!sliding.length) return;
  grid.getBoundingClientRect();  // commit the start before the slide
  sliding.forEach(c => {
    // A slide still running from the last move gives way to this one;
    // its timer would otherwise end this one early and snap the card.
    if (c._cancelSlide) c._cancelSlide();
    c.classList.add('sliding');
    c.style.transition = 'transform ' + CARD_SLIDE_MS + 'ms ease-out';
    c.style.transform = '';
    // transitionend does not come for a slide that was cut short or a
    // card taken out of the page, so a timer finishes the job as well.
    const cancel = () => {
      clearTimeout(timer);
      c.removeEventListener('transitionend', onEnd);
      c._cancelSlide = null;
    };
    const done = () => {
      cancel();
      c.classList.remove('sliding');
      c.style.transition = '';
    };
    const onEnd = e => {
      if (e.target === c && e.propertyName === 'transform') done();
    };
    const timer = setTimeout(done, CARD_SLIDE_MS + 50);
    c._cancelSlide = cancel;
    c.addEventListener('transitionend', onEnd);
  });
}

// ===========================================================================
// Port order: drag a card to move the port
// ===========================================================================
// Taken by its title. Everything else stays live while a card is dragged (see
// _reconcilePortCards), which is why the grid's sortable is kept: a
// redraw asks it which card is in hand.
let portSort = null;

// Compared with the order the server last sent, not the one at the
// press: the list may have changed in another tab while it lasted.
function _dropPort(cards, card) {
  const order = cards.map(c => c.dataset.portId);
  const serverOrder = portsStatus ? portsStatus.ports.map(p => p.id) : [];
  if (order.length < 2 || order.join('\n') === serverOrder.join('\n')) {
    return;
  }
  const id = card.dataset.portId;
  _sendMove('/api/ports/' + encodeURIComponent(id) + '/move', order, id,
    o => { _applyPortOrder(o); renderPortsList(); }, serverOrder);
}

// Reorder the ports held for the stream to a list of ids. A port not in
// it - added since - keeps its place at the end rather than vanishing.
function _applyPortOrder(order) {
  if (!portsStatus) return;
  const byId = new Map(portsStatus.ports.map(p => [p.id, p]));
  const next = order.filter(id => byId.has(id)).map(id => byId.get(id));
  portsStatus.ports.forEach(p => { if (!next.includes(p)) next.push(p); });
  portsStatus.ports = next;
}

// The release after a drag can arrive as a click on whatever is under
// it by then.
function _swallowNextClick() {
  const stop = e => { e.stopPropagation(); e.preventDefault(); };
  window.addEventListener('click', stop, { capture: true, once: true });
  setTimeout(
    () => window.removeEventListener('click', stop, { capture: true }), 0);
}

// The token a terminal link needs where this browser has no session to
// fall back on, so that an endpoint with a token opens from here like
// any other.
//
// That case is an installation with no users configured, and there
// every request is already admin: /api/ports/<id> hands the token to
// anybody who can reach the server at all, so carrying it in a link on
// this page costs nothing that was not free already. Where users *are*
// configured you had to sign in to be reading this, the plain link
// works on that session, and nothing here is fetched.
//
// /api/status reports `token_required` and never the token, on purpose
// - it is also fed to the status stream for hours - so the stored
// configuration is where this has to come from.
const _endpointTokens = new Map();
const _tokensAsked = new Set();

function _resolveEndpointTokens(ports) {
  if (token) return;
  const wanted = ports.filter(p => p.id && !_tokensAsked.has(p.id)
    && (p.servers || []).some(s => s.token_required));
  if (!wanted.length) return;
  // Marked before the answer comes, and left marked if it never does:
  // this runs on every status push, and a port that cannot be read
  // would otherwise be asked about for ever. The share dialog fetches
  // for itself and says what went wrong.
  wanted.forEach(p => _tokensAsked.add(p.id));
  Promise.all(wanted.map(p => api(
    'GET', '/api/ports/' + encodeURIComponent(p.id),
  ).then(cfg => (cfg.servers || []).forEach(s => {
    if (s.endpoint && s.token) _endpointTokens.set(s.endpoint, s.token);
  }), () => {}))).then(() => renderPortsList());
}

// A saved port may carry a new token, and a deleted one takes its
// endpoints with it. Cheaper to ask again than to work out what moved.
function forgetEndpointTokens() {
  _endpointTokens.clear();
  _tokensAsked.clear();
}

// Server now ships `state` in each port payload — fall back to a local
// recompute only if it's missing (e.g. older server during upgrade).
function _portState(port) {
  if (port.state) return port.state;
  const ser = port.serial || {};
  if (ser.connected) return 'online';
  let exists = false;
  if (ser.match) {
    exists = detectedPorts.some(p => _matchesPort(p, ser.match));
  } else if (ser.port) {
    exists = detectedPorts.some(p => p.device === ser.port);
  } else {
    exists = true;
  }
  return exists ? 'offline' : 'error';
}

function _matchesPort(detected, match) {
  return Object.entries(match).every(([k, v]) => {
    const pv = (detected[k] || '').toUpperCase();
    const mv = String(v).toUpperCase().replace(/\*/g, '.*');
    try { return new RegExp('^' + mv + '$').test(pv); }
    catch { return pv === mv; }
  });
}

// What to call a port where it is named for a person: its name, else
// its device path, else its id. It used to end in "Port 2", a position,
// which calls a different port by that name as soon as the list moves -
// and the conflict message skipped the device path the card used, so
// the two could call one port by different names.
function portTitle(port) {
  return port.name || (port.serial && port.serial.port) || port.id;
}

function renderPortCard(port) {
  const ser = port.serial || {};
  const state = _portState(port);
  const card = el('div', { class: 'card card-' + state });
  // Ports are addressed by id, not by where they sit in the list: an
  // entry added or removed elsewhere would otherwise renumber the rest
  // and send an edit to the wrong port.
  const id = port.id;
  card.dataset.portId = id;

  // Header row: title + kebab
  const titleText = portTitle(port);
  // Where the title *is* the device path - an unnamed port - it is the
  // one place the path is written, so it copies from here. A named
  // port carries it in the subtitle instead, and it copies from there.
  const titleSpan = (!port.name && ser.port)
    ? copyableSpan(titleText, 'card-title')
    : el('span', { class: 'card-title' }, titleText);
  const headerRow = el('div', { class: 'card-header-row' }, titleSpan);
  // Monitor is there whatever the port is doing: it watches the line,
  // and waiting for a device to turn up is a thing you open it to do.
  // It needs a name because that is what the route is keyed by.
  const actions = [port.name
    ? { label: 'Monitor', cls: 'btn-success',
        href: '/monitor/' + encodeURIComponent(port.name) }
    : { label: 'Monitor', reason: 'the port needs a name to be monitored' }];
  if (isAdmin) {
    actions.push(
      { label: 'Edit', cls: 'btn-accent',
        onclick: () => navigate('/ports/' + encodeURIComponent(id)
          + '/edit') },
      { label: 'Delete', cls: 'btn-danger',
        onclick: () => confirmDeletePort(id, titleText) });
  }
  headerRow.appendChild(kebabMenu(actions));
  card.appendChild(headerRow);

  // Subtitle: device path or match. The path is the thing that gets
  // typed into somebody else's command line, so it copies on a click -
  // a match description does not, there being no file to open.
  let device = '';
  let described = '';
  if (ser.port) {
    if (port.name || ser.match) device = ser.port;
  } else if (ser.match) {
    const matching = detectedPorts.filter(p => _matchesPort(p, ser.match));
    if (matching.length) device = matching.map(p => p.device).join(', ');
    else described = 'match: ' + Object.entries(ser.match).map(([k,v]) => k+'='+v).join(', ');
  }
  const subParts = [];
  if (device) subParts.push(copyableSpan(device));
  if (described) subParts.push(described);
  if (ser.baudrate) subParts.push(ser.baudrate + ' bps');
  // No "connected"/"disconnected" here: the card's colour is the answer
  // (green connected, blue idle but ready, red device missing) and the
  // word only repeated it.
  if (subParts.length) {
    const sub = el('div', { class: 'card-subtitle' });
    subParts.forEach((part, i) => {
      if (i) sub.appendChild(document.createTextNode(' — '));
      sub.appendChild(typeof part === 'string'
        ? document.createTextNode(part) : part);
    });
    card.appendChild(sub);
  }

  // A port that never started. Its servers do not exist and there is
  // nothing to link to, so the reason is all the card can offer - and
  // it is the one thing worth reading.
  if (port.error) {
    card.appendChild(el('div', { class: 'card-failure' },
      'Did not start: ' + port.error));
  }

  // Match attributes (if used)
  if (ser.match) {
    const dl = el('dl', { class: 'detect-attrs' });
    for (const [k, v] of Object.entries(ser.match)) {
      dl.appendChild(el('dt', {}, k));
      dl.appendChild(el('dd', {}, v));
    }
    card.appendChild(dl);
  }

  // Note: signal indicators (RTS/DTR/CTS/DSR/RI/CD) used to live on the
  // card here — moved to the terminal/raw/monitor page toolbars where
  // they're more actionable in context. Those pages report and toggle
  // them over their own WebSocket, not through the API.

  // Server list. The links inside do not care what state the port is
  // in: a device that is not plugged in yet is something to wait for,
  // and waiting for it from an open terminal is the point.
  const serverUl = el('ul', { class: 'server-list' });
  (port.servers || []).forEach((s, si) => {
    serverUl.appendChild(renderServerRow(s, id, si));
  });
  card.appendChild(serverUl);

  return card;
}

function renderServerRow(srv, portId, srvIdx) {
  const proto = (srv.protocol || 'tcp').toUpperCase();
  const li = el('li', { class: 'server-row' });

  // Head: protocol tag + address + what can be done with it
  const head = el('div', { class: 'server-row-head' });
  head.appendChild(el('span', { class: 'server-row-tag' }, proto));
  let addrText;
  if (proto === 'WEBSOCKET') addrText = '/ws/' + srv.endpoint;
  else if (proto === 'SOCKET') addrText = srv.address;
  else addrText = srv.address + ':' + srv.port;
  // The address is what you take elsewhere - into a terminal program,
  // into socat, into a colleague's message - so it copies on a click.
  // A WebSocket endpoint does not: the string worth having there is
  // the full URL with its token, and that is what the share link is.
  if (proto === 'WEBSOCKET') {
    head.appendChild(el('span', { class: 'server-row-addr' }, addrText));
  } else {
    head.appendChild(copyableSpan(addrText, 'server-row-addr'));
  }
  li.appendChild(head);

  // Which way data may flow, on every protocol — a read-only TCP server
  // is as worth saying as a read-only WebSocket. Only when it is not
  // the default, so an ordinary row stays uncluttered.
  const access = serverAccess(srv);
  if (access !== 'rw') {
    li.appendChild(el('div', { class: 'server-row-detail' },
      ACCESS_LABELS[access] || access));
  }

  // WebSocket: what can be done with the endpoint, as icons beside it.
  // The ws:// URL used to be printed underneath and copied on a click.
  // It is gone: the string worth handing anybody is the one with the
  // token in it, and that is what the share link is for.
  if (proto === 'WEBSOCKET') {
    const actions = el('span', { class: 'row-actions' });
    // Where the endpoint has a token and this browser has no session,
    // the link carries the token itself - in the fragment, the same way
    // a shared one does.
    const epToken = srv.token_required
      ? _endpointTokens.get(srv.endpoint) : null;
    const frag = epToken ? '#token=' + encodeURIComponent(epToken) : '';
    // A terminal needs to hear the device; write-only and control-only
    // endpoints would open and then sit silent forever.
    //
    // A device that is not plugged in is *not* a reason to withhold
    // them, which it used to be. The page is not refused by a port
    // that will not open — it is told, and the port is retried for as
    // long as it stays — so opening the terminal first and plugging
    // the board in second is a way of watching it from its first byte.
    const hasTerminal = access === 'rw' || access === 'ro';
    // A bare link authenticates as whoever is signed in here — the
    // pages read the session token and never look at the per-server
    // one — so an endpoint with a token needs one of the two: a session
    // to be, or its own token in the link. Until the token has been
    // fetched there is no link to offer, which is a moment, not a
    // state worth drawing.
    if (hasTerminal && (!srv.token_required || token || epToken)) {
      actions.appendChild(iconLink(_TERMINAL_ICON, 'Terminal (VT100)',
        '/xterm/' + srv.endpoint + frag, true));
      actions.appendChild(iconLink(_RAW_ICON, 'Raw view',
        '/raw/' + srv.endpoint + frag, true));
    }
    // Only an endpoint with a token can be handed to somebody who has
    // no account here — that token is the whole credential, and it
    // grants this endpoint and nothing else. Offered whatever the
    // access is: a write-only endpoint has no terminal to share, but
    // its WebSocket URL is still worth handing out.
    if (srv.token_required) {
      actions.appendChild(iconLink(_SHARE_ICON, 'Share link…',
        '#/ports/' + encodeURIComponent(portId) + '/share/'
          + encodeURIComponent(srv.endpoint)));
    }
    if (actions.childNodes.length) head.appendChild(actions);
  }

  // Control protocol summary
  if (srv.control) {
    const setParts = [];
    if (srv.control.rts) setParts.push('RTS');
    if (srv.control.dtr) setParts.push('DTR');
    const ctlText = 'ctrl: ' + (setParts.length ? setParts.join(', ') : 'escape only');
    li.appendChild(el('div', { class: 'server-row-detail' }, ctlText));
    if (srv.control.signals && srv.control.signals.length) {
      li.appendChild(el('div', { class: 'server-row-detail' },
        'report: ' + srv.control.signals.map(s => s.toUpperCase()).join(', ')));
    }
  }

  // Connected clients
  const clients = srv.connections || [];
  if (clients.length) {
    const cul = el('ul', { class: 'client-list' });
    clients.forEach((c, ci) => {
      const cli = el('li', {}, c.address);
      const dcBtn = el('button', {
        type: 'button',
        class: 'client-disconnect',
        title: 'Disconnect ' + c.address,
        onclick: () => disconnectClient(portId, c.id),
      });
      dcBtn.innerHTML = '<svg viewBox="0 0 24 24" width="12" height="12">'
        + '<path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"'
        + ' fill="currentColor"/></svg>';
      cli.appendChild(dcBtn);
      cul.appendChild(cli);
    });
    li.appendChild(cul);
  }

  return li;
}

// ===========================================================================
// Share link
// ===========================================================================

// A link for somebody who has no account here: "this is our device, try
// it". It carries the endpoint's own token, which is the one credential
// narrow enough to hand out — it opens that endpoint, as much of it as
// its access allows, and nothing else. A session token would be an
// account.
//
// The token is not in /api/status, deliberately (that payload is also
// broadcast to every status listener for hours), so the stored
// configuration is fetched when the link is actually asked for.
function showShareLink(portId, endpoint) {
  // Same wait as the port editor: on a reload the status stream has
  // not arrived yet, and opening the modal over an empty page would
  // leave nothing to go back to. The first snapshot calls route()
  // again, so this runs a second time on its own.
  if (!portsStatus) return;
  api('GET', '/api/ports/' + encodeURIComponent(portId))
    .then(cfg => _renderShareLink(endpoint, cfg))
    .catch(e => {
      if (e !== 'unauthorized') alert(String(e));
      navigate('/ports');
    });
}

function _renderShareLink(endpoint, cfg) {
  const srv = (cfg.servers || []).find(
    s => (s.protocol || '').toUpperCase() === 'WEBSOCKET'
      && s.endpoint === endpoint);
  if (!srv || !srv.token) {
    alert('This endpoint has no token — add one in the port editor first, '
      + 'or a link would let in anyone who can reach the server.');
    navigate('/ports');
    return;
  }
  // The fragment never leaves the browser: it is not sent with the
  // request, so the token stays out of the server log, out of anything
  // a proxy in front of us writes down, and out of the Referer header.
  // The page reads it, keeps it for the tab and wipes it from the
  // address bar.
  const frag = '#token=' + encodeURIComponent(srv.token);
  const wsScheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const access = serverAccess(srv);
  const body = el('div', {});
  if (access === 'rw' || access === 'ro') {
    body.appendChild(_shareRow('Terminal',
      location.origin + '/xterm/' + endpoint + frag,
      'VT100 terminal — for a device that echoes.'));
    body.appendChild(_shareRow('Raw',
      location.origin + '/raw/' + endpoint + frag,
      'Coloured raw view, TX and RX apart, hex escapes.'));
  }
  // A query, not a fragment: a device or a wscat has no fragment to
  // read. This is the URL for a machine, and a query is something
  // servers log — which is exactly why the two links differ.
  body.appendChild(_shareRow('WebSocket URL',
    wsScheme + '//' + location.host + '/ws/' + endpoint
      + '?token=' + encodeURIComponent(srv.token),
    'For a program rather than a browser — the token rides in the '
      + 'query, where a plain WebSocket client can put it.'));
  // The short spelling: ACCESS_LABELS explains each one in a sentence,
  // which does not fit inside a sentence of its own.
  const what = { rw: 'read and write', ro: 'read only',
    wo: 'write only', none: 'control only' }[access] || access;
  body.appendChild(el('p', { class: 'card-subtitle',
    style: 'font-size:12px;margin-top:14px' },
    'Anyone holding the link can use this endpoint (' + what + ')'
    + (srv.max_connections ? ', up to ' + srv.max_connections + ' at a time' : '')
    + '. To take it back, generate a new token in the port editor — '
    + 'that revokes every link handed out so far.'));
  openModal({
    title: 'Share ' + endpoint,
    body,
    footer: btn('Close', 'btn-primary', () => backToList()),
  });
}

function _shareRow(label, url, note) {
  // A readonly input rather than text: it selects on click, and it
  // scrolls instead of wrapping a long URL across four lines.
  const urlEl = el('input', {
    type: 'text', readonly: true, value: url,
    style: 'flex:1;font-family:monospace;font-size:12px',
    onclick: e => e.target.select(),
  });
  return el('div', { style: 'margin-bottom:14px' },
    el('label', { style: 'display:block;margin-bottom:4px' }, label),
    el('div', { style: 'display:flex;gap:8px;align-items:center' },
      urlEl, copyIconBtn(url, 'Copy ' + label)),
    el('div', { class: 'card-subtitle', style: 'font-size:12px;margin-top:4px' },
      note));
}

// Is this device already spoken for by a configured port? Either it is
// named outright, or some port's match filter selects it.
function _detectedIsConfigured(p) {
  const ports = (portsStatus && portsStatus.ports) || [];
  return ports.some(port => {
    const ser = port.serial || {};
    if (ser.port && ser.port === p.device) return true;
    return !!(ser.match && _matchesPort(p, ser.match));
  });
}

function renderDetectedSection() {
  const root = $('detected-ports');
  root.innerHTML = '';
  if (!detectedPorts.length) {
    root.appendChild(el('div', { class: 'empty' },
      'No serial devices detected.'));
    return;
  }
  const marked = detectedPorts.map(
    p => ({ port: p, configured: _detectedIsConfigured(p) }));
  // A vid is what makes a device identifiable as a particular piece of
  // hardware, so those come first and get their own section. The rest
  // are plain ports: built-in UARTs, Bluetooth pairings, virtual ones.
  _appendDetectedGroup(root, 'USB devices', marked.filter(m => m.port.vid));
  _appendDetectedGroup(root, 'Serial ports', marked.filter(m => !m.port.vid));
}

function _appendDetectedGroup(root, title, entries) {
  if (!entries.length) return;
  const sec = el('div', { class: 'detected-section' });
  sec.appendChild(el('h3', { class: 'detected-title' }, title));
  const grid = el('div', { class: 'detected-grid' });
  const free = entries.filter(m => !m.configured);
  const taken = entries.filter(m => m.configured);
  free.forEach(m => grid.appendChild(renderDetectedCard(m.port, false)));
  // The configured ones start a fresh row. They are reference rather
  // than something to act on, and the break says so without spending
  // another heading on it.
  if (free.length && taken.length) {
    grid.appendChild(el('div', { class: 'grid-break' }));
  }
  taken.forEach(m => grid.appendChild(renderDetectedCard(m.port, true)));
  sec.appendChild(grid);
  root.appendChild(sec);
}

function renderDetectedCard(p, configured) {
  const card = el('div', {
    class: 'card card-detected'
      + (configured ? ' card-detected-configured' : '') });
  const title = el('span', { class: 'card-title' }, p.device);
  const headerRow = el('div', { class: 'card-header-row' }, title);
  if (configured) {
    headerRow.appendChild(
      el('span', { class: 'tag tag-success' }, 'configured'));
  } else {
    headerRow.appendChild(btn('+ Add', 'btn-primary btn-small',
      () => navigate('/ports/new?device=' + encodeURIComponent(p.device))));
  }
  card.appendChild(headerRow);
  if (p.description) {
    card.appendChild(el('div', { class: 'card-subtitle' }, p.description));
  }
  const attrs = MATCH_ATTRS.filter(a => p[a]);
  if (attrs.length) {
    const dl = el('dl', { class: 'detect-attrs' });
    attrs.forEach(a => {
      dl.appendChild(el('dt', {}, a));
      const dd = el('dd');
      if (isAdmin) {
        const link = el('a', {
          href: '#',
          title: 'Add new port with match ' + a + '=' + p[a],
          onclick: e => {
            e.preventDefault();
            navigate('/ports/new?match_attr=' + encodeURIComponent(a)
              + '&match_val=' + encodeURIComponent(p[a])
              + '&device=' + encodeURIComponent(p.device));
          },
        }, p[a]);
        dd.appendChild(link);
      } else {
        dd.textContent = p[a];
      }
      dl.appendChild(dd);
    });
    card.appendChild(dl);
  }
  return card;
}

function confirmDeletePort(id, name) {
  if (!confirm('Delete port "' + name + '"?')) return;
  api('DELETE', '/api/ports/' + encodeURIComponent(id))
    .then(() => { forgetEndpointTokens(); navigate('/ports'); })
    .catch(e => { if (e !== 'unauthorized') alert(e); });
}

function disconnectClient(portId, connId) {
  // The connection is named, not counted: a client hanging up shifts
  // every position after it, so a click would land on someone else.
  // Stream auto-refreshes the connections list — no explicit reload here.
  api('DELETE', '/api/ports/' + encodeURIComponent(portId)
      + '/connections/' + encodeURIComponent(connId))
    .catch(e => { if (e !== 'unauthorized') alert(e); });
}

// ===========================================================================
// Port editor (modal)
// ===========================================================================
function _nextFreePort(start) {
  const used = new Set(usedPorts.map(u => u.port));
  let p = start || 10001;
  while (used.has(p)) p++;
  return p;
}

function showPortEditor(id, query) {
  // The form needs the detected device list and the port/endpoint
  // conflict sets, and those come from the status stream - which
  // pushes. There is nothing to wait for with a clock: the first
  // snapshot calls route() itself, so this runs again on its own, and
  // until then the stream-down banner says why the page is empty.
  if (!portsStatus) return;
  // The configuration, not the status. The status says what is
  // happening; the editor writes back what was configured, and the two
  // are not the same document - it reports no IP filters, no WebSocket
  // tokens, no timeouts, and serial settings already converted to what
  // pyserial was handed. Editing that and saving it rewrote the port
  // with different settings than it had.
  const wanted = [api('GET', '/api/certs')];
  if (id !== null) wanted.push(api('GET', '/api/ports/' + encodeURIComponent(id)));
  Promise.all(wanted).then(([certs, cfg]) => {
    _showPortEditorWithBundles(id, query, certs.bundles || [], cfg);
  }).catch(e => {
    if (e === 'unauthorized') return;
    // A port somebody else removed while this was being opened.
    navigate('/ports');
  });
}

function _showPortEditorWithBundles(id, query, bundles, stored) {
  let cfg;
  if (id !== null) {
    if (!stored) return navigate('/ports');
    cfg = JSON.parse(JSON.stringify(stored));
    cfg.serial = cfg.serial || {};
    cfg.servers = cfg.servers || [];
    if (!cfg.servers.length) {
      cfg.servers = [{
        protocol: 'tcp', address: '0.0.0.0', port: _nextFreePort()}];
    }
  } else {
    cfg = {
      serial: {},
      servers: [{protocol: 'tcp', address: '0.0.0.0', port: _nextFreePort()}],
    };
    if (query.device) cfg.serial.port = query.device;
    if (query.match_attr && query.match_val) {
      cfg.serial.match = { [query.match_attr]: query.match_val };
    }
  }

  const form = _buildPortForm(cfg, id, bundles);
  const saveBtn = btn('Save', 'btn-primary',
    () => _savePortFromForm(form, id));
  const addSrvBtn = btn('+ Add Server', 'btn-accent btn-small',
    () => form.addServerBox());
  const cancelBtn = btn('Cancel', '', () => backToList());
  const footer = [];
  footer.push(addSrvBtn);
  footer.push(el('span', { class: 'footer-spacer' }));
  if (id !== null) {
    footer.push(btn('Delete', 'btn-danger',
      () => confirmDeletePort(id, cfg.name || 'this port')));
  }
  footer.push(cancelBtn);
  footer.push(saveBtn);

  openModal({
    title: id !== null ? 'Edit Port' : 'New Port',
    body: form.root,
    footer,
    wide: true,
  });
}

function _buildPortForm(cfg, editId, bundles) {
  // The class is what lines the fields up: this form has rows at two
  // levels - its own, and those inside a server box - and the box's
  // padding used to leave the inner ones ending short of the outer
  // ones. See .port-form in style.css.
  const root = el('div', { class: 'port-form' });

  // The name is what a person calls the port; the id is what the API
  // and every link address it by, and it is derived from the name
  // rather than being four random bytes that point at the port without
  // saying anything about it. Editable all the same: the derived one
  // is a good default, not a rule.
  const nameInput = el('input', { type: 'text', value: cfg.name || '' });
  const idInput = el('input', {
    type: 'text',
    value: cfg.id || '',
    placeholder: 'letters, digits, . - _',
  });
  // Grey while it is only a suggestion, ordinary once somebody has
  // typed in it - and only until it is saved, because the file does
  // not record which of the two it was.
  let idTouched = false;
  function followName() {
    if (idTouched) return;
    idInput.value = suggestId(
      nameInput.value, portInput.value, editId !== null ? cfg.id : null);
    idInput.classList.add('is-derived');
  }
  idInput.oninput = () => {
    idTouched = true;
    idInput.classList.remove('is-derived');
  };
  const idHint = el('div', { class: 'field-hint' },
    editId !== null
      ? 'Follows the name until you change it. '
        + 'Changing it breaks existing links to this port.'
      : 'Used in the API and in links. Follows the name until you '
        + 'change it.');

  // Port input with autocomplete from detected USB devices.
  const portInput = el('input', {
    type: 'text', value: cfg.serial.port || '',
  });
  const portWrap = attachAutocomplete(portInput,
    () => detectedPorts.map(p => ({
      value: p.device,
      hint: p.description || '',
    })));

  nameInput.addEventListener('input', followName);
  // A new port picking up a device takes its name from it - /dev/ttyUSB0
  // becomes ttyUSB0, and the id follows to ttyusb0. Only while the name
  // is still empty: it is a starting point, not a correction.
  portInput.addEventListener('input', () => {
    if (editId === null && !nameInput.value.trim()) {
      nameInput.value = portInput.value.trim().split('/').pop();
    }
    followName();
  });
  if (editId === null) followName();

  // Match section — one row per USB attribute (vid, pid, ...). Each input
  // has an autocomplete listing every distinct value for that attribute
  // currently seen on a connected device, with the device path as hint.
  const matchDiv = el('div');
  const matchInputs = {};
  const matchCheckboxes = {};
  const matchCell = attr => {
    const cb = el('input', {
      type: 'checkbox',
      checked: !!(cfg.serial.match && cfg.serial.match[attr]),
    });
    const matchVal = cfg.serial.match ? cfg.serial.match[attr] : '';
    const detectedVal = _getDetectedAttr(cfg.serial.port, attr);
    const inp = el('input', {
      type: 'text',
      value: matchVal || detectedVal || '',
    });
    inp.disabled = !cb.checked;
    const inpWrap = attachAutocomplete(inp, () => {
      const seen = new Set();
      const opts = [];
      detectedPorts.forEach(p => {
        const v = p[attr];
        if (v && !seen.has(v)) {
          seen.add(v);
          opts.push({ value: v, hint: p.device });
        }
      });
      return opts;
    });
    cb.onchange = () => {
      inp.disabled = !cb.checked;
      updateMatchMode();
    };
    inp.oninput = updateMatchedDevicePreview;
    matchCheckboxes[attr] = cb;
    matchInputs[attr] = inp;
    return el('div', { class: 'match-cell' },
      cb, el('label', { title: attr }, MATCH_LABELS[attr] || attr), inpWrap);
  };
  // Two to a row, in the order they are listed: vid with pid, serial
  // with manufacturer, product with location. Six rows of one short
  // field each was a lot of modal for very little.
  for (let i = 0; i < MATCH_ATTRS.length; i += 2) {
    matchDiv.appendChild(el('div', { class: 'match-row' },
      matchCell(MATCH_ATTRS[i]), matchCell(MATCH_ATTRS[i + 1])));
  }

  // While match mode is on, portInput is disabled and shows the device(s)
  // currently matching the filter (informational only — not collected into
  // the saved config). We keep the user's last manually-typed value so it
  // can be restored when they uncheck all match attributes.
  let _savedDeviceValue = portInput.value;

  function updateMatchMode() {
    const anyChecked = MATCH_ATTRS.some(a => matchCheckboxes[a].checked);
    if (anyChecked && !portInput.disabled) {
      _savedDeviceValue = portInput.value;
    } else if (!anyChecked && portInput.disabled) {
      portInput.value = _savedDeviceValue;
      portInput.placeholder = '';
    }
    portInput.disabled = anyChecked;
    updateMatchedDevicePreview();
  }
  function updateMatchedDevicePreview() {
    if (!portInput.disabled) return;
    const match = {};
    MATCH_ATTRS.forEach(a => {
      if (matchCheckboxes[a].checked && matchInputs[a].value) {
        match[a] = matchInputs[a].value;
      }
    });
    if (!Object.keys(match).length) {
      portInput.value = '';
      portInput.placeholder = '(no match attributes set)';
      return;
    }
    const matching = detectedPorts.filter(p => _matchesPort(p, match));
    if (!matching.length) {
      portInput.value = '';
      portInput.placeholder = '(no matching device detected)';
    } else {
      portInput.value = matching.map(p => p.device).join(', ');
      portInput.placeholder = '';
    }
  }
  portInput.oninput = () => {
    if (portInput.disabled) return;
    _savedDeviceValue = portInput.value;
    const found = detectedPorts.find(p => p.device === portInput.value);
    MATCH_ATTRS.forEach(a => {
      if (matchCheckboxes[a].checked) return;
      matchInputs[a].value = found ? (found[a] || '') : '';
    });
  };

  // Serial parameters
  const baudSel = el('select');
  baudSel.appendChild(el('option', { value: '' }, '(default)'));
  BAUDRATES.forEach(b => {
    const o = el('option', { value: String(b) }, String(b));
    if (cfg.serial.baudrate === b) o.selected = true;
    baudSel.appendChild(o);
  });

  const byteSel = el('select');
  Object.entries(BYTESIZES).forEach(([bits, name]) => {
    const o = el('option', { value: name }, bits);
    if (cfg.serial.bytesize === name
        || (!cfg.serial.bytesize && bits === '8')) o.selected = true;
    byteSel.appendChild(o);
  });

  const paritySel = el('select');
  PARITIES.forEach(p => {
    const o = el('option', { value: p }, p);
    if (cfg.serial.parity === p
        || (!cfg.serial.parity && p === 'NONE')) o.selected = true;
    paritySel.appendChild(o);
  });

  const stopSel = el('select');
  Object.entries(STOPBITS).forEach(([bits, name]) => {
    const o = el('option', { value: name }, bits);
    if (cfg.serial.stopbits === name
        || (!cfg.serial.stopbits && bits === '1')) o.selected = true;
    stopSel.appendChild(o);
  });

  const portMaxInput = el('input', {
    type: 'number', min: '0', step: '1', inputMode: 'numeric',
    // Short, because the field is: "0 (unlimited)" was cut off mid-word
    // and said less than the bare zero does. The title carries the rest.
    placeholder: '0',
    value: cfg.max_connections !== undefined ? String(cfg.max_connections) : '',
    title: 'Total clients across all servers on this port (0 = unlimited)',
  });

  // ----- Compose form -----
  root.appendChild(el('div', { class: 'section-title' }, 'Identity'));
  // Name first: it is the one that is typed, and the id follows it.
  root.appendChild(pairRow('Name', nameInput, 'ID', idInput).row);
  root.appendChild(idHint);

  root.appendChild(el('div', { class: 'section-title' }, 'Serial port'));
  root.appendChild(formRow('Device', portWrap));
  root.appendChild(el('div', { class: 'card-subtitle',
    style: 'margin:6px 0 4px;font-size:12px' },
    'or match by USB attributes:'));
  root.appendChild(matchDiv);

  root.appendChild(el('div', { class: 'section-title' }, 'Parameters'));
  // Baudrate is the one that needs room; the rest are short answers, so
  // they go three to a line. Max clients rides along rather than taking
  // a row to itself for a field the width of a two-digit number.
  root.appendChild(pairRow('Baudrate', baudSel, 'Data bits', byteSel).row);
  root.appendChild(trioRow(
    'Max clients', portMaxInput, 'Parity', paritySel,
    'Stop bits', stopSel));

  root.appendChild(el('div', { class: 'section-title' }, 'Servers'));
  const serversDiv = el('div');
  root.appendChild(serversDiv);

  const serverBoxes = [];
  // Moved by the grip beside each box's delete icon, not by anywhere: a
  // box is mostly fields, and a click into the gaps between them is part
  // of filling a form in. Nothing is sent on the drop - the list the form
  // collects from follows the order on screen, and Save writes it; a save
  // that only reorders servers rebuilds none of them.
  makeSortable(serversDiv, {
    item: '.server-box', handle: '.box-grip',
    scroller: box => box.closest('.modal-overlay'),
    onDrop: boxes => serverBoxes.sort(
      (a, b) => boxes.indexOf(a.box) - boxes.indexOf(b.box)),
  });
  function addServerBox(initSrv) {
    const editorPorts = new Set();
    serverBoxes.forEach(b => {
      if (b.boxData.proto !== 'WEBSOCKET' && b.boxData.proto !== 'SOCKET') {
        const v = parseInt(b.boxData.portInput.value);
        if (v) editorPorts.add(v);
      }
    });
    let p = 10001;
    const globalUsed = new Set(usedPorts.map(u => u.port));
    while (globalUsed.has(p) || editorPorts.has(p)) p++;
    const seed = initSrv || { protocol: 'tcp', address: '0.0.0.0', port: p };
    // A server that is not in the file yet - added here, or all of a
    // new port's - is offered the port's id as its endpoint.
    const isNew = !initSrv || editId === null;
    const sb = _buildServerBox(seed, () => {
      const idx = serverBoxes.indexOf(sb);
      if (idx >= 0) serverBoxes.splice(idx, 1);
      sb.box.remove();
      _refreshRemoveButtons();
    }, editId, () => serverBoxes, bundles,
    isNew ? suggestEndpoint : null);
    serverBoxes.push(sb);
    serversDiv.appendChild(sb.box);
    _refreshRemoveButtons();
  }
  // The port's id as it stands, made unique the way ids are: against
  // every other port's endpoints, and against the other boxes here,
  // whose endpoints may not be saved yet. Nothing while there is no id
  // yet - including the stand-in `port` a new port shows until it has a
  // name or a device, which is not what it will be called.
  function suggestEndpoint(box) {
    const base = idInput.value.trim();
    if (!base) return '';
    const standIn = idInput.classList.contains('is-derived')
      && !slugId(nameInput.value)
      && !slugId(String(portInput.value || '').split('/').pop());
    if (standIn) return '';
    const taken = new Set(usedEndpoints
      .filter(u => u.id !== editId).map(u => u.endpoint));
    serverBoxes.forEach(b => {
      if (b.box === box || b.boxData.proto !== 'WEBSOCKET') return;
      const ep = b.boxData.epInput.value.trim();
      if (ep) taken.add(ep);
    });
    return uniqueId(base, taken);
  }
  function _refreshRemoveButtons() {
    serverBoxes.forEach(sb => {
      sb.removeBtn.disabled = serverBoxes.length <= 1;
      // One box has no order to change - greyed like the bin beside it
      // rather than hidden, so the row does not shift when a second
      // server is added.
      sb.grip.classList.toggle('disabled', serverBoxes.length <= 1);
    });
    serverBoxes.forEach(sb => sb.recheckConflicts && sb.recheckConflicts());
  }
  cfg.servers.forEach(s => addServerBox(s));

  updateMatchMode();

  return {
    root,
    addServerBox,
    // What the entry looked like when it was read. Sent back with the
    // save so a change somebody else made in the meantime is reported
    // instead of being quietly overwritten.
    rev: cfg.rev,
    idInput,
    nameInput,
    portInput,
    matchCheckboxes,
    matchInputs,
    baudSel,
    byteSel,
    paritySel,
    stopSel,
    portMaxInput,
    serverBoxes,
  };
}

function _getDetectedAttr(device, attr) {
  if (!device) return '';
  const found = detectedPorts.find(p => p.device === device);
  return found ? (found[attr] || '') : '';
}

function _buildServerBox(
    srv, onRemove, editId, getAllBoxes, bundles, suggestEndpoint) {
  const box = el('div', { class: 'server-box' });
  // Names the server it is about to take away, because one click used
  // to be the whole thing: a box carrying an address, a control set and
  // an ACL went, with nothing to say which one it had been. Cancel
  // still restores it - the row is only gone from the form until the
  // save - but that is not obvious with the dialog missing.
  function describeServer() {
    const proto = protoSel.value;
    if (proto === 'WEBSOCKET') {
      return 'the WEBSOCKET endpoint "'
        + (wsEndpointInput.value.trim() || '(unnamed)') + '"';
    }
    if (proto === 'SOCKET') {
      return 'the Unix socket '
        + (addrInput.value.trim() || '(no path)');
    }
    return 'the ' + proto + ' server on '
      + (addrInput.value.trim() || '0.0.0.0')
      + ':' + (portInput.value.trim() || '?');
  }
  const removeBtn = el('button', {
    type: 'button', class: 'server-remove',
    title: 'Remove server',
    onclick: () => {
      if (!confirm('Remove ' + describeServer() + ' from this port?')) {
        return;
      }
      onRemove();
    },
  });
  // An open bin: a lid line, a body that is open at the top, and two
  // ribs. The one before it closed the body across the top as well, so
  // the lid ran through a sealed box and it read as a crate.
  removeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none"'
    + ' stroke="currentColor" stroke-width="2"'
    + ' stroke-linecap="round" stroke-linejoin="round">'
    + '<path d="M3 6h18"/>'
    + '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6'
    + 'm3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
    + '<path d="M10 11v6M14 11v6"/></svg>';
  // Goes in the protocol row further down rather than floating in the
  // corner, where it sat on top of the select's own arrow.

  // Where the box is taken to move it: six dots, the usual sign for
  // "hold here". Not a button - a press on it is the start of a drag,
  // not a click - and greyed while the box is the only one. Last in the
  // row, after the bin: the edge of the box is where a hand goes.
  const grip = el('span', {
    class: 'box-grip', title: 'Drag to change the order',
  });
  grip.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"'
    + ' aria-hidden="true">'
    + '<circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/>'
    + '<circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/>'
    + '<circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/>'
    + '</svg>';

  const protoSel = el('select');
  PROTOCOLS.forEach(p => {
    const o = el('option', { value: p }, p);
    if (srv.protocol && srv.protocol.toUpperCase() === p) o.selected = true;
    protoSel.appendChild(o);
  });

  // WS fields
  const wsEndpointInput = el('input', {
    type: 'text', placeholder: 'my-device', value: srv.endpoint || '',
  });
  const wsTokenInput = el('input', {
    type: 'text', placeholder: '(use global auth)', value: srv.token || '',
  });
  const wsCopyBtn = copyIconBtn(() => wsTokenInput.value, 'Copy token');
  // Nothing to copy while the field is empty - that is not a token, it
  // is the endpoint falling back to the global auth, and a button that
  // would hand over an empty string says otherwise.
  const syncTokenCopy = () => { wsCopyBtn.disabled = !wsTokenInput.value; };
  const wsGenBtn = iconBtn(_REGENERATE_ICON, 'Generate a new token', () => {
    wsTokenInput.value = crypto.randomUUID();
    syncTokenCopy();
  });
  wsTokenInput.oninput = syncTokenCopy;
  syncTokenCopy();
  const wsTokenRow = formRow('Token', [wsTokenInput, wsGenBtn, wsCopyBtn]);
  const wsRows = el('div',
    {},
    formRow('Endpoint', wsEndpointInput),
    wsTokenRow);

  // Address + Port (or Path)
  const addrLabel = el('label', {}, 'Address');
  const addrInput = el('input', {
    type: 'text', value: srv.address || '0.0.0.0',
  });
  const portInput = el('input', {
    type: 'number', value: srv.port !== undefined ? String(srv.port) : '',
  });
  const { row: addrRow, secondPart: portRow } = pairRow(
    addrLabel, addrInput, 'Port', portInput);

  // TLS fields: bundle dropdown + mTLS toggle. Built via shared helper
  // so port-TLS and HTTPS editors stay consistent. We always want the
  // tls block when protocol=TLS, so we hide the TLS-enable checkbox the
  // helper exposes and treat the bundle as required.
  const tls = _buildTlsFields(srv.tls, bundles || [], { clientCn: true });
  tls.tlsCb.checked = true;
  tls.tlsCb.style.display = 'none';
  tls.wrap.classList.remove('hidden');
  const tlsDiv = tls.wrap;

  // What this server offers a client, as three independent things
  // rather than a list of the combinations. Read and write were a
  // four-way dropdown whose options were the pairs spelled out, and
  // control was a checkbox somewhere else - so the one rule tying
  // them together, that a server has to do at least one of the three,
  // lived in neither of them.
  const access = serverAccess(srv);
  const enableCb = (label, on) => {
    const cb = el('input', { type: 'checkbox', checked: on });
    return [cb, el('label', { class: 'checkbox-label' },
      cb, el('span', {}, label))];
  };
  const [readCb, readLabel] = enableCb('Read', access === 'rw' || access === 'ro');
  const [writeCb, writeLabel] = enableCb('Write', access === 'rw' || access === 'wo');
  const [ctlCb, ctlLabel] = enableCb('Control', !!srv.control);
  // ACL covers the client limit as well as the address rules: capping
  // how many may connect is access control too, and it is the same
  // question - who gets in. A limit of 0 is the absence of one, so a
  // configured 0 reads the same as no key at all.
  const [aclCb, aclLabel] = enableCb('ACL', !!(
    (srv.allow || []).length || (srv.deny || []).length
    || srv.max_connections
    // Named clients live in the tls block but are shown here, and a
    // section that opened closed would drop them on the next save.
    || ((srv.tls || {}).allow_client_cn || []).length));
  const dataRow = formRow('Enable',
    [readLabel, writeLabel, ctlLabel, aclLabel]);

  // Direction is shown by colour rather than by a word in front of
  // each group: six boxes and two labels do not fit on one line, and
  // wrapping them scatters the row. The tooltip carries the meaning.
  const isOut = sig => OUTPUT_SIGNALS.includes(sig);
  const signalTitle = sig => sig.toUpperCase() + (isOut(sig)
    ? ' — output, driven from here'
    : ' — input, driven by the device');
  const signalBox = (cb, sig) => el(
    'label', {
      class: 'checkbox-label signal-box ' + (isOut(sig) ? 'out' : 'in'),
      title: signalTitle(sig),
    },
    cb, el('span', {}, sig.toUpperCase()));
  // One container, so the row's own gap falls between the label and
  // the set rather than between every box in it.
  const signalSet = boxes => el('div', { class: 'signal-set' }, ...boxes);

  // Report comes first: it is what decides the rest. A line nobody
  // reports cannot usefully be set either - the terminal draws a badge
  // only for the lines it is told about, so an allowed-but-unreported
  // line is one you can neither see nor click.
  const reportCbs = {};
  const reportBox = sig => {
    const cb = el('input', {
      type: 'checkbox',
      checked: !!(srv.control && (srv.control.signals || []).includes(sig)),
    });
    reportCbs[sig] = cb;
    cb.onchange = () => syncSettable();
    return signalBox(cb, sig);
  };
  const reportRow = formRow('Report', signalSet(
    [...OUTPUT_SIGNALS, ...INPUT_SIGNALS].map(reportBox)));

  const writeRowCbs = {};
  const writeLabels = OUTPUT_SIGNALS.map(sig => {
    const cb = el('input', {
      type: 'checkbox',
      checked: !!(srv.control && srv.control[sig]),
    });
    writeRowCbs[sig] = cb;
    return signalBox(cb, sig);
  });
  const writeRow = formRow('Allow set', signalSet(writeLabels));

  // A line can only be set if it is also reported.
  function syncSettable() {
    OUTPUT_SIGNALS.forEach(sig => {
      const reported = reportCbs[sig].checked;
      const cb = writeRowCbs[sig];
      cb.disabled = !reported;
      if (!reported) cb.checked = false;
      cb.parentNode.title = reported ? signalTitle(sig)
        : 'Report ' + sig.toUpperCase() + ' first — a line that is not '
          + 'reported cannot be shown, so it cannot be clicked either';
    });
  }
  syncSettable();

  const pollSel = el('select', { style: 'flex:0 0 auto;width:8em' });
  const pollOptions = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
  const curPoll = srv.control
    ? Math.round((srv.control.poll_interval || 0.1) * 1000) : 100;
  // A configured interval the list does not offer - 250 ms, written by
  // hand - joins it. Missing, nothing was selected, the select showed
  // its first entry, and a Save wrote 1 ms without anybody choosing it.
  if (!pollOptions.includes(curPoll)) {
    pollOptions.push(curPoll);
    pollOptions.sort((a, b) => a - b);
  }
  pollOptions.forEach(ms => {
    const o = el('option', { value: String(ms) },
      ms < 1000 ? ms + ' ms' : (ms / 1000) + ' s');
    if (ms === curPoll) o.selected = true;
    pollSel.appendChild(o);
  });
  const pollRow = formRow('Poll interval', pollSel);

  const ctlDescEl = el('div', { class: 'card-subtitle',
    style: 'font-size:12px;margin-bottom:6px' });

  const ctlMoreDetails = el('details', { style: 'margin:4px 0 6px' },
    el('summary', { style: 'cursor:pointer;color:var(--accent);font-size:12px' },
      'Protocol reference'),
    _buildCtlProtocolTable());

  const ctlDetails = el('div', { class: 'subgroup' },
    ctlDescEl, ctlMoreDetails, reportRow, writeRow, pollRow);
  const ctlDiv = el('div', {}, ctlDetails);

  // IP filter
  const allowInput = el('input', {
    type: 'text', placeholder: '192.168.1.0/24, 10.0.0.5',
    value: (srv.allow || []).join(', '),
  });
  const denyInput = el('input', {
    type: 'text', placeholder: '192.168.1.100',
    value: (srv.deny || []).join(', '),
  });
  // The address rules go away on a Unix socket, which has no address
  // to filter - but a cap on how many may connect still applies, so
  // only these two rows hide, not the section.
  const ipRows = el('div', {},
    formRow('Allow IPs', allowInput),
    formRow('Deny IPs', denyInput));

  // Max connections
  const maxConnInput = el('input', {
    type: 'number', min: '0', step: '1', inputMode: 'numeric',
    placeholder: '0',
    value: srv.max_connections !== undefined ? String(srv.max_connections) : '',
    title: '0 = unlimited',
  });
  const maxConnRow = formRow('Max clients', maxConnInput);
  // Who may connect, by name rather than by address - the same
  // question the rows above ask, so it is asked in the same place.
  // Built with the TLS fields because the value is written into the
  // tls block, and hidden by them when the server is not TLS or mTLS
  // is off.
  const ipDiv = el('div', { class: 'subgroup' },
    ipRows, maxConnRow, tls.cnRow);

  box.appendChild(formRow('Protocol', [protoSel, removeBtn, grip]));
  box.appendChild(wsRows);
  box.appendChild(addrRow);      // carries the port half with it
  box.appendChild(tlsDiv);
  // Which way data flows is not a control-protocol setting: a plain TCP
  // server can be read-only. It used to live inside the control section
  // because `data: false` was only legal with control configured.
  box.appendChild(dataRow);
  box.appendChild(ctlDiv);
  box.appendChild(ipDiv);

  // One place decides whether a section is on screen. It used to be
  // two - the checkbox toggled the inner div and the protocol switch
  // toggled its wrapper - so ticking Control on a server that did not
  // have it revealed nothing: the wrapper was still hidden from the
  // last time the protocol was looked at. It only appeared after a
  // save, when the box was rebuilt with control already on.
  function updateSections() {
    ctlDiv.classList.toggle('hidden', ctlCb.disabled || !ctlCb.checked);
    ipDiv.classList.toggle('hidden', !aclCb.checked);
    // The CN row lives in the ACL section but is owned by the TLS
    // fields, so it is re-checked whenever either side could have
    // moved: the protocol switch runs through here too.
    tls.syncCn();
  }
  [ctlCb, aclCb].forEach(
    cb => cb.addEventListener('change', updateSections));

  function updateProtoFields() {
    const proto = protoSel.value;
    const isSocket = proto === 'SOCKET';
    const isTls = proto === 'TLS';
    const isTelnet = proto === 'TELNET';
    const isWs = proto === 'WEBSOCKET';
    wsRows.classList.toggle('hidden', !isWs);
    addrRow.classList.toggle('hidden', isWs);
    portRow.classList.toggle('hidden', isWs || isSocket);
    addrLabel.textContent = isSocket ? 'Path' : 'Address';
    tlsDiv.classList.toggle('hidden', !isTls);
    // TELNET has no room for it: the escape protocol's 0xFF is IAC.
    ctlCb.disabled = isTelnet;
    if (isTelnet) ctlCb.checked = false;
    ctlLabel.title = isTelnet
      ? 'Not available on TELNET — the escape byte 0xFF is IAC there'
      : '';
    // A Unix socket has no address to filter on, but it can still be
    // capped - so the rules go, not the section.
    ipRows.classList.toggle('hidden', isSocket);
    updateSections();
    ctlDescEl.innerHTML = '';
    if (isWs) {
      ctlDescEl.appendChild(document.createTextNode(
        'JSON text frames for signal control.'));
      ctlMoreDetails.classList.add('hidden');
    } else if (!isTelnet) {
      ctlDescEl.appendChild(document.createTextNode(
        'Binary escape protocol using 0xFF prefix.'));
      ctlMoreDetails.classList.remove('hidden');
    } else {
      ctlMoreDetails.classList.add('hidden');
    }
    if (isSocket && addrInput.value === '0.0.0.0') addrInput.value = '';
  }

  function recheckConflicts() {
    const proto = protoSel.value;
    const ep = wsEndpointInput.value.trim();
    if (proto === 'WEBSOCKET' && ep) {
      const epConflict = usedEndpoints.find(u =>
        u.endpoint === ep && u.id !== editId);
      let editorDup = false;
      const all = getAllBoxes();
      all.forEach(b => {
        if (b.box === box) return;
        if (b.boxData.proto === 'WEBSOCKET'
            && b.boxData.epInput.value.trim() === ep) editorDup = true;
      });
      const epErr = epConflict || editorDup;
      wsEndpointInput.classList.toggle('field-error', !!epErr);
      wsEndpointInput.title = epConflict
        ? 'Endpoint already used by ' + epConflict.label
        : (editorDup ? 'Duplicate endpoint' : '');
    } else {
      wsEndpointInput.classList.remove('field-error');
      wsEndpointInput.title = '';
    }
    if (proto === 'SOCKET' || proto === 'WEBSOCKET') {
      portInput.classList.remove('field-error');
      portInput.title = '';
      return;
    }
    const addr = addrInput.value.trim();
    const p = parseInt(portInput.value);
    if (!p) { portInput.classList.remove('field-error'); return; }
    const conflict = usedPorts.find(u =>
      u.port === p && u.address === addr && u.id !== editId);
    portInput.classList.toggle('field-error', !!conflict);
    portInput.title = conflict
      ? 'Port already used by ' + conflict.label : '';
  }

  // A new server is offered an endpoint once, when it first becomes a
  // WebSocket - a new box starts as TCP, and a new port has no id until
  // its name is typed, so the moment it is created is too early. Taken
  // from the id as it is then, and not kept in step with it afterwards:
  // an endpoint is in links and devices, and renaming the port is no
  // reason to move it.
  let endpointOffered = !suggestEndpoint;
  function offerEndpoint() {
    if (endpointOffered || protoSel.value !== 'WEBSOCKET'
        || wsEndpointInput.value.trim()) {
      return;
    }
    const ep = suggestEndpoint(box);
    if (!ep) return;
    wsEndpointInput.value = ep;
    endpointOffered = true;
  }

  protoSel.onchange = () => {
    offerEndpoint();
    updateProtoFields();
    recheckConflicts();
  };
  portInput.oninput = recheckConflicts;
  addrInput.oninput = recheckConflicts;
  wsEndpointInput.oninput = recheckConflicts;
  updateProtoFields();
  recheckConflicts();

  return {
    box, removeBtn, grip, recheckConflicts,
    boxData: {
      get proto() { return protoSel.value; },
      protoSel, addrInput, portInput, epInput: wsEndpointInput,
      tokenInput: wsTokenInput, tls,
      readCb, writeCb, ctlCb, aclCb, writeRowCbs, reportCbs, pollSel,
      allowInput, denyInput, maxConnInput,
      // What was configured before this box was opened. The form does
      // not have a field for everything a server can carry, and
      // rebuilding the entry from the fields alone silently dropped
      // the rest - send_timeout and buffer_limit among them.
      original: srv || {},
    },
  };
}

function _collectPortConfig(form) {
  const cfg = { serial: {}, servers: [] };
  if (form.rev) cfg.rev = form.rev;
  const id = form.idInput.value.trim();
  if (id) {
    if (!ID_PATTERN.test(id)) {
      throw new Error('ID may only contain letters, digits, dot, dash '
        + 'and underscore');
    }
    cfg.id = id;
  }
  const name = form.nameInput.value.trim();
  // Required now that the id is derived from it - and it is what the
  // card, the monitor route and every log line call this port.
  if (!name) throw new Error('A port needs a name');
  cfg.name = name;
  const portMax = form.portMaxInput.value.trim();
  if (portMax !== '') cfg.max_connections = parseInt(portMax);

  const anyMatch = MATCH_ATTRS.some(a => form.matchCheckboxes[a].checked);
  if (anyMatch) {
    cfg.serial.match = {};
    MATCH_ATTRS.forEach(a => {
      if (form.matchCheckboxes[a].checked && form.matchInputs[a].value) {
        cfg.serial.match[a] = form.matchInputs[a].value;
      }
    });
  } else {
    const port = form.portInput.value.trim();
    if (port) cfg.serial.port = port;
  }
  if (form.baudSel.value) cfg.serial.baudrate = parseInt(form.baudSel.value);
  if (form.byteSel.value !== 'EIGHTBITS') cfg.serial.bytesize = form.byteSel.value;
  if (form.paritySel.value !== 'NONE') cfg.serial.parity = form.paritySel.value;
  if (form.stopSel.value !== 'ONE') cfg.serial.stopbits = form.stopSel.value;

  form.serverBoxes.forEach(sb => {
    const d = sb.boxData;
    const proto = d.proto.toLowerCase();
    // Start from what was there and let the form overwrite what it
    // owns, so settings with no field here survive the round trip.
    const srv = { ...d.original, protocol: proto };
    FORM_OWNED_SERVER_KEYS.forEach(key => delete srv[key]);
    if (proto === 'websocket') {
      const ep = d.epInput.value.trim();
      if (ep) srv.endpoint = ep;
      const tk = d.tokenInput.value.trim();
      if (tk) srv.token = tk;
    } else {
      srv.address = d.addrInput.value.trim();
      if (proto !== 'socket') {
        const p = d.portInput.value;
        if (p) srv.port = parseInt(p);
      }
    }
    if (proto === 'tls') {
      // The allowed-CN list is part of the ACL section, so it is saved
      // only while that section is on.
      const tlsVal = d.tls.getValue(d.aclCb.checked);
      if (!tlsVal) throw new Error('TLS server requires a bundle');
      srv.tls = tlsVal;
    }
    const read = d.readCb.checked;
    const write = d.writeCb.checked;
    const control = proto !== 'telnet' && d.ctlCb.checked;
    if (!read && !write && !control) {
      // It would answer connections and then do nothing with them, and
      // the server refuses to start such a thing. Say so here rather
      // than letting the save come back as an error.
      throw new Error(proto === 'telnet'
        ? 'A server has to read or write, or it would do nothing'
        : 'A server has to read, write or do control, '
          + 'or it would do nothing');
    }
    const reported = CONTROL_SIGNALS.filter(sig => d.reportCbs[sig].checked);
    if (control && !reported.length) {
      // Control over nothing. Allow set already requires the line to
      // be reported, so with no report there is nothing to set either
      // - all that is left is escaping 0xFF for no one's benefit.
      throw new Error(
        'Control needs at least one signal to report, or it does nothing');
    }
    // The default carries no key, so an ordinary server stays as plain
    // in the file as it was before this existed.
    const access = read && write ? 'rw' : read ? 'ro' : write ? 'wo' : 'none';
    if (access !== 'rw') srv.access = access;
    if (control) {
      const ctl = {};
      OUTPUT_SIGNALS.forEach(sig => {
        if (d.writeRowCbs[sig].checked) ctl[sig] = true;
      });
      if (reported.length) ctl.signals = reported;
      const pollMs = parseInt(d.pollSel.value);
      if (pollMs) ctl.poll_interval = pollMs / 1000;
      srv.control = ctl;
    }
    // Unticking ACL drops what it held rather than keeping it out of
    // sight: a rule nobody can see is one nobody will remember when
    // wondering why a client is being refused.
    if (d.aclCb.checked) {
      // A Unix socket has no address to filter, so only the cap counts.
      const rules = proto === 'socket' ? '' : d.allowInput.value.trim();
      const blocked = proto === 'socket' ? '' : d.denyInput.value.trim();
      // A limit of none is not a limit; it goes in as no key, which
      // is what the server already reads an absent one as.
      const mc = parseInt(d.maxConnInput.value.trim());
      // Naming the clients that may connect is a rule like the others,
      // so a section holding only that is not an empty one.
      const named = d.tls.clientCns().length;
      if (!rules && !blocked && !(mc > 0) && !named) {
        throw new Error(proto === 'socket'
          ? 'ACL needs a client limit, or it does nothing'
          : 'ACL needs an address rule, a client limit or an allowed '
            + 'CN, or it does nothing');
      }
      if (rules) {
        srv.allow = rules.split(',').map(s => s.trim()).filter(Boolean);
      }
      if (blocked) {
        srv.deny = blocked.split(',').map(s => s.trim()).filter(Boolean);
      }
      if (mc > 0) srv.max_connections = mc;
    }
    // A server that still means what it meant goes back exactly as it
    // was written. The form spells things its own way - a default made
    // explicit, the reported lines in its order, a false or a zero left
    // out - and the server now rebuilds only a server whose JSON
    // changed, so a re-spelling alone dropped its clients on a Save that
    // changed nothing.
    const original = d.original;
    const sameMeaning = Object.keys(original).length
      && _serverMeaning(srv) === _serverMeaning(original);
    cfg.servers.push(sameMeaning ? JSON.parse(JSON.stringify(original)) : srv);
  });
  return cfg;
}

// A server's configuration reduced to what it means, as a string two
// spellings of the same thing agree on: defaults dropped, a false or an
// empty value treated as absent, the reported lines as a set, and the
// old data flag read as the access it stands for.
const DEFAULT_POLL_INTERVAL = 0.1;  // as connection_control.py has it

function _serverMeaning(server) {
  const s = JSON.parse(JSON.stringify(server));
  s.protocol = String(s.protocol || '').toLowerCase();
  if ('data' in s) {
    if (s.data === false && !('access' in s)) s.access = 'none';
    delete s.data;
  }
  if (s.access === 'rw') delete s.access;
  if (s.control) {
    const c = s.control;
    ['rts', 'dtr'].forEach(k => { if (!c[k]) delete c[k]; });
    if (Array.isArray(c.signals)) {
      c.signals = [...new Set(c.signals)].sort();
      if (!c.signals.length) delete c.signals;
    }
    if (c.poll_interval === DEFAULT_POLL_INTERVAL) delete c.poll_interval;
  }
  if (s.tls) {
    if (!s.tls.require_client_cert) delete s.tls.require_client_cert;
    if (Array.isArray(s.tls.allow_client_cn) && !s.tls.allow_client_cn.length) {
      delete s.tls.allow_client_cn;
    }
  }
  ['max_connections'].forEach(k => { if (!s[k]) delete s[k]; });
  ['allow', 'deny'].forEach(k => {
    if (Array.isArray(s[k]) && !s[k].length) delete s[k];
  });
  const sorted = v => Array.isArray(v) ? v.map(sorted)
    : (v && typeof v === 'object')
      ? Object.fromEntries(Object.keys(v).sort().map(k => [k, sorted(v[k])]))
      : v;
  return JSON.stringify(sorted(s));
}

function _savePortFromForm(form, id) {
  let cfg;
  try { cfg = _collectPortConfig(form); }
  catch (e) { return modalError(e.message); }
  const method = id !== null ? 'PUT' : 'POST';
  const path = id !== null ? '/api/ports/' + encodeURIComponent(id)
    : '/api/ports';
  api(method, path, cfg)
    .then(() => { forgetEndpointTokens(); navigate('/ports'); })
    .catch(e => {
      if (e === 'unauthorized') return;
      // Includes the answer to a save aimed at a version of the port
      // that has since been edited. The box stays open with the typing
      // in it - discarding the change to report that it clashed would
      // be the very thing this is here to prevent.
      modalError(String(e));
    });
}

// ===========================================================================
// Control protocol reference table (used inline in port editor)
// ===========================================================================
function _buildCtlProtocolTable() {
  const wrap = el('div', { style: 'margin-top:6px;font-size:12px' });
  wrap.innerHTML = `
    <table style="margin-bottom:8px">
      <thead><tr><th>Sequence</th><th>Direction</th><th>Description</th></tr></thead>
      <tbody>
        <tr><td><code>FF FF</code></td><td>&harr;</td><td>Literal 0xFF byte</td></tr>
        <tr><td><code>FF 00</code></td><td>&rarr; serial</td><td>RTS low</td></tr>
        <tr><td><code>FF 01</code></td><td>&rarr; serial</td><td>RTS high</td></tr>
        <tr><td><code>FF 10</code></td><td>&rarr; serial</td><td>DTR low</td></tr>
        <tr><td><code>FF 11</code></td><td>&rarr; serial</td><td>DTR high</td></tr>
        <tr><td><code>FF C0</code></td><td>&rarr; serial</td><td>Request signal report</td></tr>
        <tr><td><code>FF 8<em>x</em></code></td><td>&larr; client</td><td>Signal report (<em>x</em> = 6-bit bitmask)</td></tr>
      </tbody>
    </table>
    <div style="color:var(--text-muted)">
      Report bitmask: bit0=RTS, 1=DTR, 2=CTS, 3=DSR, 4=RI, 5=CD.
      Range <code>0x80</code>&ndash;<code>0xBF</code>.
    </div>
  `;
  return wrap;
}

// ===========================================================================
// Users & Tokens
// ===========================================================================
let currentUsers = [];
let currentTokens = [];
// Whether the lists above have been fetched, which is not the same as
// whether they hold anything: no users and no tokens is a valid
// configuration, so their length says nothing about it.
let usersLoaded = false;

function showUsers() {
  if (!isAdmin) { navigate('/ports'); return; }
  show('users-view');
  loadUsers().catch(e => console.error(e));
}

// Open an editor once the data it needs has arrived.
//
// Not a timer. These editors used to call themselves again every 50 ms
// until the data showed up, which asks "do I have any?" where it means
// "has the load finished?" - so a configuration with no tokens span
// the loop for ever at two requests a turn, and went on spinning after
// the user had navigated away, because a timeout has no idea which
// page scheduled it. Waiting on the load answers the right question,
// happens once, and has somewhere to put a failure.
function whenLoaded(load, open) {
  const from = location.hash;
  load().then(() => {
    // Gone somewhere else in the meantime: nothing to open.
    if (location.hash === from) open();
  }).catch(e => modalError(String(e)));
}

function loadUsers() {
  return Promise.all([
    api('GET', '/api/users').catch(() => []),
    api('GET', '/api/tokens').catch(() => []),
  ]).then(([users, tokens]) => {
    currentUsers = users;
    currentTokens = tokens;
    // Set even when both came back empty. "None configured" is an
    // answer, and reading it as "not yet" is what span the retry.
    usersLoaded = true;
    renderUsersActions();
    renderUsersList();
  });
}

function renderUsersActions() {
  const c = $('users-actions');
  c.innerHTML = '';
  c.appendChild(btn('+ Add User', 'btn-primary btn-small',
    () => navigate('/users/new')));
  c.appendChild(btn('+ Add Token', 'btn-accent btn-small',
    () => navigate('/tokens/new')));
}

function renderUsersList() {
  const root = $('users-content');
  root.innerHTML = '';
  if (!currentUsers.length && !currentTokens.length) {
    root.appendChild(el('p', { class: 'empty' },
      'No users or tokens configured'));
    return;
  }
  if (currentUsers.length) {
    root.appendChild(el('div', { class: 'section-title' }, 'Users'));
    const grid = el('div', { class: 'card-grid' });
    currentUsers.forEach(u => grid.appendChild(renderUserCard(u)));
    root.appendChild(grid);
  }
  if (currentTokens.length) {
    root.appendChild(el('div', { class: 'section-title' }, 'API Tokens'));
    const grid = el('div', { class: 'card-grid' });
    currentTokens.forEach(t => grid.appendChild(renderTokenCard(t)));
    root.appendChild(grid);
  }
}

function renderUserCard(user) {
  const card = el('div', { class: 'card card-online' });
  const title = el('span', { class: 'card-title' }, user.login);
  if (user.admin) title.appendChild(el('span', { class: 'tag tag-admin' }, 'admin'));
  const headerRow = el('div', { class: 'card-header-row' }, title);
  headerRow.appendChild(kebabMenu([
    { label: 'Edit', cls: 'btn-accent',
      onclick: () => navigate('/users/' + encodeURIComponent(user.login) + '/edit') },
    { label: 'Delete', cls: 'btn-danger',
      onclick: () => confirmDeleteUser(user.login) },
  ]));
  card.appendChild(headerRow);
  return card;
}

function renderTokenCard(tok) {
  const card = el('div', { class: 'card card-online' });
  const title = el('span', { class: 'card-title' }, tok.name);
  if (tok.admin) title.appendChild(el('span', { class: 'tag tag-admin' }, 'admin'));
  const headerRow = el('div', { class: 'card-header-row' }, title);
  headerRow.appendChild(kebabMenu([
    { label: 'Edit', cls: 'btn-accent',
      onclick: () => navigate('/tokens/' + encodeURIComponent(tok.token) + '/edit') },
    { label: 'Delete', cls: 'btn-danger',
      onclick: () => confirmDeleteToken(tok.token) },
  ]));
  card.appendChild(headerRow);
  const masked = tok.token.slice(0, 8) + '…' + tok.token.slice(-4);
  const tv = el('div', {
    class: 'token-value',
    title: 'Click to copy',
    onclick: () => {
      copyText(tok.token).then(ok => {
        if (!ok) return;
        tv.classList.add('copied');
        setTimeout(() => tv.classList.remove('copied'), 1000);
      });
    },
  }, masked);
  card.appendChild(tv);
  return card;
}

// Deleting the last account of all turns authentication off, and the
// server refuses that once and says what it would mean. That refusal is
// what prompts here, rather than a check against the list this page
// happens to hold: the list can be out of date, the server cannot.
function deleteAccount(path, onDeleted) {
  function run(confirmed) {
    return api('DELETE', path + (confirmed ? '?disable_auth=1' : ''))
      .then(() => {
        // Authentication is off now, so whatever token this page held
        // is dead - and the API hands out admin without one. Dropping
        // it keeps a stale token from turning up if auth comes back.
        if (confirmed) setCredentials(null, null);
        else if (onDeleted) onDeleted();
        navigate('/users');
      })
      .catch(e => {
        if (e === 'unauthorized') return;
        if (e && e.confirm === 'disable_auth') {
          if (confirm(e.error)) run(true);
          return;
        }
        alert(e && e.error ? e.error : e);
      });
  }
  run(false);
}

function confirmDeleteUser(login) {
  if (!confirm('Delete user "' + login + '"?')) return;
  deleteAccount('/api/users/' + encodeURIComponent(login), () => {
    if (login === username) setCredentials(null, null);
  });
}

function confirmDeleteToken(tokenId) {
  if (!confirm('Delete this token?')) return;
  deleteAccount('/api/tokens/' + encodeURIComponent(tokenId), null);
}

// ----- User editor -----
function showUserEditor(login) {
  if (!isAdmin) { navigate('/users'); return; }
  // We may not have currentUsers loaded if entered via direct hash
  if (login !== null && !usersLoaded) {
    whenLoaded(loadUsers, () => showUserEditor(login));
    return;
  }
  const isNew = login === null;
  const user = isNew ? {} : currentUsers.find(u => u.login === login);
  // An id that is not in the list used to fall back to an empty record,
  // so a stale link offered a blank form with a Delete button on it.
  // Nobody ever saw it: getting here meant the retry loop had given up
  // waiting, and it never did.
  if (!isNew && !user) return navigate('/users');
  const firstUser = currentUsers.length === 0;

  const loginInput = el('input', { type: 'text', value: user.login || '',
    autocomplete: 'off' });
  if (!isNew) loginInput.disabled = true;
  const passInput = el('input', {
    type: 'password',
    placeholder: isNew ? '' : 'leave empty to keep',
    autocomplete: 'new-password',
  });
  const adminCb = el('input', {
    type: 'checkbox',
    checked: !!(user.admin || firstUser),
  });
  if (firstUser) adminCb.disabled = true;

  const body = el('div',
    {},
    formGroup('Login', loginInput),
    formGroup(isNew ? 'Password' : 'New password', passInput),
    formGroup(null, el('label', { class: 'checkbox-label' },
      adminCb, el('span', {}, ' Admin'))));

  openModal({
    title: isNew ? 'New user' : 'Edit user',
    body,
    footer: [
      !isNew ? btn('Delete', 'btn-danger',
        () => confirmDeleteUser(login)) : null,
      el('span', { class: 'footer-spacer' }),
      btn('Cancel', '', () => backToList()),
      btn('Save', 'btn-primary',
        () => _saveUser(isNew ? null : login, loginInput, passInput, adminCb)),
    ].filter(Boolean),
  });
  loginInput.focus();
}

async function _saveUser(login, loginInput, passInput, adminCb) {
  const isNew = login === null;
  const newLogin = loginInput.value.trim();
  const password = passInput.value;
  const admin = adminCb.checked;
  if (!newLogin) return modalError('Login is required');
  if (isNew && !password) return modalError('Password is required');
  const data = { admin };
  if (isNew) data.login = newLogin;
  if (password) data.password = await hashPassword(password);
  const method = isNew ? 'POST' : 'PUT';
  const path = isNew ? '/api/users' : '/api/users/' + encodeURIComponent(login);
  api(method, path, data).then(response => {
    if (response.token) setCredentials(response.token, newLogin);
    navigate('/users');
  }).catch(e => modalError(String(e)));
}

// ----- Token editor -----
function showTokenEditor(tokenId) {
  if (!isAdmin) { navigate('/users'); return; }
  if (tokenId !== null && !usersLoaded) {
    whenLoaded(loadUsers, () => showTokenEditor(tokenId));
    return;
  }
  const isNew = tokenId === null;
  const tok = isNew ? {} : currentTokens.find(t => t.token === tokenId);
  if (!isNew && !tok) return navigate('/users');
  const tokenValue = tok.token || crypto.randomUUID();

  const nameInput = el('input', { type: 'text', value: tok.name || '',
    autocomplete: 'off' });
  const tokenInput = el('input', { type: 'text', value: tokenValue,
    autocomplete: 'off' });
  const genBtn = btn('Generate', 'btn-accent btn-small',
    () => { tokenInput.value = crypto.randomUUID(); });
  const copyBtn = btn('Copy', 'btn-small', () => {
    copyText(tokenInput.value).then(ok => {
      copyBtn.textContent = ok ? 'Copied' : 'Copy failed';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1000);
    });
  });
  const adminCb = el('input', { type: 'checkbox', checked: !!tok.admin });

  const tokenWrap = el('div', { style: 'display:flex;gap:8px;align-items:stretch' });
  tokenInput.style.flex = '1';
  tokenWrap.appendChild(tokenInput);
  tokenWrap.appendChild(genBtn);
  tokenWrap.appendChild(copyBtn);

  const body = el('div',
    {},
    formGroup('Name', nameInput),
    formGroup('Token', tokenWrap),
    formGroup(null, el('label', { class: 'checkbox-label' },
      adminCb, el('span', {}, ' Admin'))));

  openModal({
    title: isNew ? 'New API token' : 'Edit API token',
    body,
    footer: [
      !isNew ? btn('Delete', 'btn-danger',
        () => confirmDeleteToken(tokenId)) : null,
      el('span', { class: 'footer-spacer' }),
      btn('Cancel', '', () => backToList()),
      btn('Save', 'btn-primary',
        () => _saveToken(isNew ? null : tokenId, nameInput, tokenInput, adminCb)),
    ].filter(Boolean),
  });
  nameInput.focus();
}

function _saveToken(tokenId, nameInput, tokenInput, adminCb) {
  const isNew = tokenId === null;
  const name = nameInput.value.trim();
  const tokenValue = tokenInput.value.trim();
  const admin = adminCb.checked;
  if (!name) return modalError('Name is required');
  if (!tokenValue) return modalError('Token is required');
  const data = { name, admin, token: tokenValue };
  const method = isNew ? 'POST' : 'PUT';
  const path = isNew ? '/api/tokens' : '/api/tokens/' + encodeURIComponent(tokenId);
  api(method, path, data)
    .then(() => navigate('/users'))
    .catch(e => modalError(String(e)));
}

// ===========================================================================
// Settings
// ===========================================================================
let currentSettings = null;

function showSettings() {
  show('settings-view');
  loadSettings().catch(logFailure);
}

function loadSettings() {
  return api('GET', '/api/settings').then(data => {
    currentSettings = data;
    renderSettingsActions();
    renderSettingsList();
  });
}

// What the list views want from a failed load: a line in the console.
// An editor wants to be told, so it says so itself.
function logFailure(err) {
  if (err !== 'unauthorized') console.error(err);
}

function renderSettingsActions() {
  const c = $('settings-actions');
  c.innerHTML = '';
  if (isAdmin) {
    c.appendChild(btn('+ Add HTTP Server', 'btn-primary btn-small',
      () => navigate('/settings/http/new')));
  }
}

// What applies to the whole process, as fields at the top of the tab
// rather than a card of one line with an editor behind a menu. Grouped
// by what they are about, so what comes later has somewhere to go.
// Save is live only while something differs from what was loaded, and
// sends the rev it was loaded with: another admin's change in between
// is reported, not overwritten.
let _generalSettingsError = null;  // a refused save, shown after re-reading

// Where the session-timeout slider stops: about double each time, since
// the difference that matters between 5 and 10 minutes does not between
// 7 and 8 hours - and nobody needs 3601 s.
const SESSION_TIMEOUT_STEPS = [
  300, 600, 900, 1800, 3600, 7200, 14400, 28800, 43200,
  86400, 172800, 604800, 2592000];

// 5400 -> "1 h 30 min": the two largest units it has, which is all a
// timeout needs and all that fits beside a slider.
function formatDuration(seconds) {
  const units = [['d', 86400], ['h', 3600], ['min', 60], ['s', 1]];
  const parts = [];
  let left = seconds;
  for (const [name, size] of units) {
    if (left >= size && parts.length < 2) {
      parts.push(Math.floor(left / size) + ' ' + name);
      left %= size;
    }
  }
  return parts.join(' ') || '0 s';
}

function renderGeneralSettings() {
  const form = el('form', { class: 'settings-form' });
  const loaded = currentSettings.session_timeout;
  const fallback = (currentSettings.defaults || {}).session_timeout;
  const current = loaded != null ? loaded : fallback;
  // A value set by hand, or a default that moved, gets a stop of its
  // own where it belongs: opening the form must not change it, and
  // snapping it to a neighbour would.
  const steps = [...new Set([...SESSION_TIMEOUT_STEPS, current, fallback]
    .filter(s => s != null))].sort((a, b) => a - b);
  const timeout = el('input', {
    type: 'range', min: '0', max: String(steps.length - 1), step: '1',
    value: String(Math.max(0, steps.indexOf(current))),
  });
  const shown = el('span', { class: 'field-value' });
  const error = el('div', { class: 'settings-error hidden' });
  const save = el('button', { type: 'submit', class: 'btn btn-primary' },
    'Save');

  // What the slider says, as the API takes it: the default is null, so
  // the file keeps saying nothing and follows the default if it moves.
  function value() {
    const s = steps[Number(timeout.value)];
    return s === fallback ? null : s;
  }
  function show() {
    const s = steps[Number(timeout.value)];
    shown.textContent = formatDuration(s)
      + (s === fallback ? ' (default)' : '');
  }
  // Compared with where the slider started rather than with what was
  // loaded: a default written out by hand (3600) sits on the default's
  // stop, which reads as null, and is not worth a save of its own.
  let start;
  function sync() {
    show();
    save.disabled = value() === start;
    error.classList.add('hidden');
  }

  form.appendChild(el('div', { class: 'section-title' }, 'Authentication'));
  form.appendChild(el('div', { class: 'form-row' },
    el('label', {}, 'Session timeout'),
    timeout,
    shown,
    isAdmin ? save : null));
  form.appendChild(el('div', { class: 'field-hint' },
    'How long a sign-in lasts without being used. '
    + 'API tokens do not expire.'));
  form.appendChild(error);
  show();

  if (!isAdmin) {
    timeout.disabled = true;
    return form;
  }
  start = value();
  timeout.oninput = sync;
  save.disabled = true;
  form.onsubmit = e => {
    e.preventDefault();
    const v = value();
    save.disabled = true;
    api('PUT', '/api/settings',
      { session_timeout: v, rev: currentSettings.rev })
      .then(res => {
        currentSettings.session_timeout = res.session_timeout;
        currentSettings.rev = res.rev;
        renderSettingsList();
      })
      .catch(e => {
        if (e === 'unauthorized') return;
        // Read again whatever the refusal: after a conflict the form
        // would otherwise carry a rev that can never be saved against,
        // and it should show what the other admin set. The reason
        // outlives the redraw that shows it.
        _generalSettingsError = String(e);
        loadSettings().catch(logFailure);
      });
  };
  if (_generalSettingsError) {
    error.textContent = _generalSettingsError;
    error.classList.remove('hidden');
    _generalSettingsError = null;
  }
  return form;
}

function renderSettingsList() {
  const root = $('settings-content');
  root.innerHTML = '';
  root.appendChild(renderGeneralSettings());

  const servers = currentSettings.http || [];
  root.appendChild(el('div', { class: 'section-title' }, 'HTTP servers'));
  if (!servers.length) {
    root.appendChild(el('p', { class: 'empty' }, 'No HTTP servers configured'));
  } else {
    const grid = el('div', { class: 'card-grid' });
    servers.forEach(s => grid.appendChild(renderHttpCard(s)));
    // Moved like a port's card. Nothing redraws this list while a card
    // is in hand, so there is no card to pin and no end to redraw.
    const movable = () => isAdmin && servers.length > 1;
    grid.classList.toggle('movable', movable());
    makeSortable(grid, {
      item: '.card', handle: '.card-title', hold: true,
      enabled: movable,
      onDrop: _dropHttp,
    });
    root.appendChild(grid);
  }
}

// Compared with the order the settings were loaded in; unlike the ports,
// the list is not streamed, so that is also what the server last said.
function _dropHttp(cards, card) {
  const order = cards.map(c => c.dataset.httpId);
  const loaded = currentSettings.http.map(s => s.id);
  if (order.join('\n') === loaded.join('\n')) return;
  const id = card.dataset.httpId;
  _sendMove('/api/settings/http/' + encodeURIComponent(id) + '/move',
    order, id, _showHttpOrder, loaded);
}

// A server not in the order - added meanwhile - keeps its place at the
// end rather than vanishing.
function _showHttpOrder(order) {
  const byId = new Map(currentSettings.http.map(s => [s.id, s]));
  const next = order.filter(id => byId.has(id)).map(id => byId.get(id));
  currentSettings.http.forEach(s => { if (!next.includes(s)) next.push(s); });
  currentSettings.http = next;
  renderSettingsList();
}

function renderHttpCard(srv) {
  const id = srv.id;
  // A server that did not bind is still configured, so it still has a
  // card - red, with the reason. Leaving it out of the list made a
  // process that was not serving look exactly like one that was.
  const card = el('div',
    { class: 'card ' + (srv.error ? 'card-error' : 'card-online') });
  card.dataset.httpId = id;
  const tlsTag = srv.tls ? ' (TLS)' : '';
  const titleText = srv.name
    || `${srv.address || '0.0.0.0'}:${srv.port}${tlsTag}`;
  const title = el('span', { class: 'card-title' }, titleText);
  const headerRow = el('div', { class: 'card-header-row' }, title);
  if (isAdmin) {
    headerRow.appendChild(kebabMenu([
      { label: 'Edit', cls: 'btn-accent',
        onclick: () => navigate('/settings/http/'
          + encodeURIComponent(id) + '/edit') },
      { label: 'Delete', cls: 'btn-danger',
        onclick: () => confirmDeleteHttp(id, titleText) },
    ]));
  }
  card.appendChild(headerRow);
  if (srv.error) {
    card.appendChild(el('div', { class: 'card-failure' },
      'Did not start: ' + srv.error));
  }
  const meta = el('div', { class: 'card-meta' });
  meta.appendChild(el('div', { class: 'card-meta-row' },
    el('span', { class: 'card-meta-label' }, 'Listen'),
    el('span', {}, `${srv.address || '0.0.0.0'}:${srv.port}${tlsTag}`)));
  if (srv.tls) {
    meta.appendChild(el('div', { class: 'card-meta-row' },
      el('span', { class: 'card-meta-label' }, 'Bundle'),
      el('span', {}, srv.tls.bundle || '-')));
    if (srv.tls.require_client_cert) {
      meta.appendChild(el('div', { class: 'card-meta-row' },
        el('span', { class: 'card-meta-label' }, 'mTLS'),
        el('span', {}, 'required')));
    }
  }
  card.appendChild(meta);
  return card;
}

// Build the bundle dropdown + mTLS toggle used by both HTTP and port TLS
// editors. Returns {wrap, tlsCb, getValue} — `wrap` is the div to insert,
// `tlsCb` is the enable checkbox (caller decides where to put it),
// `getValue()` returns the tls block or null.
// `clientCn` builds the allowed-CN field, which serial TLS servers take
// and HTTP servers do not - the API refuses the key there, so offering
// the field would be offering a value that cannot be saved. The row is
// returned rather than placed: naming who may connect is access
// control, so the port editor puts it in the ACL section, while the
// value still belongs to the tls block and is written from here.
function _buildTlsFields(currentTls, bundles, opts = {}) {
  const tlsCb = el('input', { type: 'checkbox', checked: !!currentTls });
  const bundleSelect = el('select');
  const placeholderOpt = el('option', { value: '' }, '-- select bundle --');
  bundleSelect.appendChild(placeholderOpt);
  bundles.forEach(b => {
    const hasCert = _certFilePresent(b, 'cert.pem');
    const hasKey = _certFilePresent(b, 'key.pem');
    const ok = hasCert && hasKey;
    const opt = el('option', {
      value: b.name,
      disabled: !ok,
    }, b.name + (ok ? '' : ' (incomplete)')
      + (_certFilePresent(b, 'ca.pem') ? ' [mTLS-capable]' : ''));
    bundleSelect.appendChild(opt);
  });
  if (currentTls && currentTls.bundle) {
    bundleSelect.value = currentTls.bundle;
  }
  const mtlsCb = el('input', { type: 'checkbox',
    checked: !!(currentTls && currentTls.require_client_cert) });
  const cnInput = el('input', {
    type: 'text', autocomplete: 'off',
    placeholder: 'operator, gateway-2 — empty: any client the CA signed',
    value: ((currentTls && currentTls.allow_client_cn) || []).join(', '),
  });
  const cnRow = opts.clientCn
    ? formRow('Allowed CNs', cnInput)
    : null;
  const updateMtlsState = () => {
    const sel = bundleSelect.value;
    const b = bundles.find(x => x.name === sel);
    const hasCa = b && _certFilePresent(b, 'ca.pem');
    mtlsCb.disabled = !hasCa;
    if (!hasCa) mtlsCb.checked = false;
    updateCnState();
  };
  // The list only means anything while a client certificate is
  // demanded; without mTLS there is no name to check. It also has
  // nothing to say on a server that is not TLS at all - and that is
  // read off the TLS section's own visibility, since the row now sits
  // somewhere else and cannot go with it.
  const updateCnState = () => {
    if (!cnRow) return;
    const on = mtlsCb.checked && !tlsDiv.classList.contains('hidden');
    cnInput.disabled = !on;
    cnRow.classList.toggle('hidden', !on);
  };
  bundleSelect.onchange = updateMtlsState;
  mtlsCb.onchange = updateCnState;
  // Beside the bundle it depends on, rather than on a row of its own:
  // whether mTLS can be switched on at all is a property of the bundle
  // selected right there, and the checkbox greys out with it.
  const mtlsLabel = el('label', {
    class: 'checkbox-label inline-check',
    title: 'Require client certificate',
  }, mtlsCb, el('span', {}, ' mTLS'));
  const tlsDiv = el('div', { class: 'subgroup' },
    formRow('Bundle', [bundleSelect, mtlsLabel]));
  if (!tlsCb.checked) tlsDiv.classList.add('hidden');
  tlsCb.onchange = () => {
    tlsDiv.classList.toggle('hidden', !tlsCb.checked);
    updateCnState();
  };
  // After tlsDiv exists: updateCnState reads its visibility.
  updateMtlsState();

  function clientCns() {
    if (!cnRow || !mtlsCb.checked || !tlsCb.checked) return [];
    return cnInput.value.split(',').map(s => s.trim()).filter(Boolean);
  }

  // `withClientCn` false leaves the names out - the port editor passes
  // the ACL checkbox, so switching ACL off drops them the same way it
  // drops the address rules, rather than saving a limit that is no
  // longer on screen.
  function getValue(withClientCn = true) {
    if (!tlsCb.checked) return null;
    const bundle = bundleSelect.value;
    if (!bundle) throw new Error('Select a certificate bundle');
    const out = { bundle };
    if (mtlsCb.checked) out.require_client_cert = true;
    const names = withClientCn ? clientCns() : [];
    // Empty means the key is left out entirely: the backend refuses an
    // empty list, because a list that lets nobody in is far more likely
    // a mistake than an intention.
    if (names.length) out.allow_client_cn = names;
    return out;
  }
  return { wrap: tlsDiv, tlsCb, cnRow, clientCns, syncCn: updateCnState,
    getValue };
}

function showHttpEditor(id) {
  if (!isAdmin) { navigate('/settings'); return; }
  if (!currentSettings) {
    whenLoaded(loadSettings, () => showHttpEditor(id));
    return;
  }
  // Fetch bundle list each time so newly created bundles show up, and
  // the settings with it: the editor saves back the entry it opened,
  // and one read when the Settings screen was drawn can be minutes old
  // by now.
  Promise.all([api('GET', '/api/certs'), api('GET', '/api/settings')])
    .then(([certs, settings]) => {
      currentSettings = settings;
      _showHttpEditorWithBundles(id, certs.bundles || []);
    }).catch(e => {
      if (e !== 'unauthorized') alert(String(e));
    });
}

function _showHttpEditorWithBundles(id, bundles) {
  const isNew = id === null;
  let servers = currentSettings.http || [];
  if (!Array.isArray(servers)) servers = [servers];
  const srv = isNew ? { address: '0.0.0.0', port: 8080 }
                    : servers.find(x => x.id === id);
  if (!isNew && !srv) return navigate('/settings');

  // Same as a port: the name is typed, the id follows it until
  // somebody says otherwise. An HTTP server's name is optional, so
  // when there is none the fallback carries the whole weight.
  const nameInput = el('input', { type: 'text', value: srv.name || '',
    placeholder: 'optional' });
  const idInput = el('input', {
    type: 'text',
    value: srv.id || '',
    placeholder: 'letters, digits, . - _',
  });
  let idTouched = false;
  const followName = () => {
    if (idTouched) return;
    idInput.value = suggestId(
      nameInput.value, null, isNew ? null : srv.id, HTTP_FALLBACK_SLUG);
    idInput.classList.add('is-derived');
  };
  idInput.oninput = () => {
    idTouched = true;
    idInput.classList.remove('is-derived');
  };
  nameInput.addEventListener('input', followName);
  if (isNew) followName();
  const addrInput = el('input', { type: 'text',
    value: srv.address || '0.0.0.0', placeholder: '0.0.0.0' });
  const portInput = el('input', { type: 'number',
    value: String(srv.port || 8080), min: '1', max: '65535' });
  const tls = _buildTlsFields(srv.tls, bundles);

  // Its own class, and its own label columns with it: short labels in
  // a narrow dialog, where the port editor's are long ones in a wide
  // one. See .http-form in style.css.
  const body = el('div',
    { class: 'http-form' },
    pairRow('Name', nameInput, 'ID', idInput).row,
    el('div', { class: 'field-hint' },
      isNew ? 'Used in the API and in links. Follows the name until '
        + 'you change it.'
        : 'Follows the name until you change it. Changing it breaks '
          + 'existing links to this server.'),
    pairRow('Address', addrInput, 'Port', portInput).row,
    el('div', { style: 'margin:8px 0' },
      el('label', { class: 'checkbox-label' },
        tls.tlsCb, el('span', {}, ' TLS'))),
    tls.wrap);

  openModal({
    title: isNew ? 'New HTTP server' : 'Edit HTTP server',
    body,
    footer: [
      !isNew ? btn('Delete', 'btn-danger',
        () => confirmDeleteHttp(id)) : null,
      el('span', { class: 'footer-spacer' }),
      btn('Cancel', '', () => backToList()),
      btn('Save', 'btn-primary',
        () => _saveHttpServer(isNew ? null : id,
          { idInput, nameInput, addrInput, portInput, tls },
          isNew ? {} : srv)),
    ].filter(Boolean),
  });
}

function _saveHttpServer(id, fields, original) {
  // Start from the stored entry so settings with no field here - an IP
  // filter, most of all - are not dropped by an edit that never
  // mentioned them.
  const data = { ...(original || {}) };
  FORM_OWNED_HTTP_KEYS.forEach(key => delete data[key]);
  // Reported by the API, not part of the configuration.
  delete data.error;
  data.address = fields.addrInput.value.trim() || '0.0.0.0';
  data.port = parseInt(fields.portInput.value) || 8080;
  const chosenId = fields.idInput.value.trim();
  if (chosenId) {
    if (!ID_PATTERN.test(chosenId)) {
      return modalError('ID may only contain letters, digits, dot, dash '
        + 'and underscore');
    }
    data.id = chosenId;
  }
  const name = fields.nameInput.value.trim();
  if (name) data.name = name;
  let tlsVal;
  try { tlsVal = fields.tls.getValue(); }
  catch (e) { return modalError(e.message); }
  if (tlsVal) data.tls = tlsVal;
  const method = id === null ? 'POST' : 'PUT';
  const path = id === null
    ? '/api/settings/http'
    : '/api/settings/http/' + encodeURIComponent(id);
  api(method, path, data)
    .then(() => navigate('/settings'))
    .catch(e => modalError(String(e)));
}

function confirmDeleteHttp(id) {
  if (!confirm('Delete this HTTP server?')) return;
  api('DELETE', '/api/settings/http/' + encodeURIComponent(id))
    .then(() => navigate('/settings'))
    .catch(e => { if (e !== 'unauthorized') alert(e); });
}

// ===========================================================================
// Certificates
// ===========================================================================
let currentCerts = null;  // {certs_dir, bundles: [...]}

const CERT_FILES = ['cert.pem', 'key.pem', 'ca.pem'];
// The ones served over the API, and so the ones a private key must
// never end up in.
const PUBLIC_CERT_FILES = ['cert.pem', 'ca.pem'];

function showCertificates() {
  if (!isAdmin) { navigate('/ports'); return; }
  show('certificates-view');
  loadCerts().catch(logFailure);
}

function loadCerts() {
  return api('GET', '/api/certs').then(data => {
    currentCerts = data;
    renderCertsActions();
    renderCertsList();
  });
}

function renderCertsActions() {
  const c = $('certificates-actions');
  c.innerHTML = '';
  c.appendChild(btn('+ Add Bundle', 'btn-primary btn-small',
    () => navigate('/certificates/new')));
  c.appendChild(btn('Generate…', 'btn-accent btn-small',
    () => navigate('/certificates/generate')));
}

function renderCertsList() {
  const certsDir = currentCerts.certs_dir || '';
  const pathEl = $('certificates-path');
  pathEl.innerHTML = '';
  pathEl.appendChild(el('span', { style: 'word-break:break-all;min-width:0' },
    'Certificates stored in: ' + certsDir));
  if (certsDir) {
    pathEl.appendChild(copyIconBtn(certsDir, 'Copy path'));
  }
  const root = $('certificates-content');
  root.innerHTML = '';
  const bundles = currentCerts.bundles || [];
  if (!bundles.length) {
    root.appendChild(el('p', { class: 'empty' },
      'No certificate bundles. Click "+ Add Bundle" to create one.'));
    return;
  }
  const grid = el('div', { class: 'card-grid' });
  bundles.forEach(b => grid.appendChild(renderCertCard(b)));
  root.appendChild(grid);
}

function _certFilePresent(bundle, fname) {
  const f = bundle.files[fname];
  // Tolerate both bool (legacy) and {present: bool} (current backend).
  return !!(f && (typeof f === 'boolean' ? f : f.present));
}

function renderCertCard(bundle) {
  const hasCert = _certFilePresent(bundle, 'cert.pem');
  const hasKey = _certFilePresent(bundle, 'key.pem');
  const hasCa = _certFilePresent(bundle, 'ca.pem');
  // Mark incomplete server bundles (have one of cert/key but not both)
  // as warning; pure CA bundles (only ca.pem) are ok.
  const isServerComplete = hasCert && hasKey;
  const isCaOnly = !hasCert && !hasKey && hasCa;
  let cls = 'card card-online';
  if (!isServerComplete && !isCaOnly) cls = 'card card-warning';
  // A complete-looking bundle whose key belongs to another cert fails
  // only at handshake time — flag it here instead.
  if (bundle.key_match === false) cls = 'card card-warning';
  if (_holdsPrivateKey(bundle)) cls = 'card card-error';
  const card = el('div', { class: cls });
  const headerRow = el('div', { class: 'card-header-row' },
    el('span', { class: 'card-title' }, bundle.name));
  headerRow.appendChild(kebabMenu([
    { label: 'Edit', cls: 'btn-accent',
      onclick: () => navigate('/certificates/' + encodeURIComponent(bundle.name)) },
    { label: 'Delete', cls: 'btn-danger',
      onclick: () => confirmDeleteCertBundle(bundle.name) },
  ]));
  card.appendChild(headerRow);
  const meta = el('div', { class: 'card-meta' });
  CERT_FILES.forEach(fname => {
    const f = bundle.files[fname];
    const present = !!(f && (typeof f === 'boolean' ? f : f.present));
    let label = present ? '✓' : '—';
    let color = null;
    // Pull cert_info from list endpoint if backend provides it
    const ci = f && typeof f === 'object' ? f.cert_info : null;
    if (ci && !ci.error) {
      const exp = _expiryStatus(ci.not_after);
      if (exp) { label = '✓ · ' + exp.label; color = exp.color; }
      if (ci.is_ca && fname === 'cert.pem') label += ' · CA';
    }
    const row = el('div', { class: 'card-meta-row' },
      el('span', { class: 'card-meta-label' }, fname),
      el('span', color ? { style: 'color:' + color } : {}, label));
    meta.appendChild(row);
  });
  if (bundle.key_match === false) {
    meta.appendChild(el('div', { class: 'card-meta-row' },
      el('span', { class: 'card-meta-label' }, 'pair'),
      el('span', { style: 'color:var(--error)' }, 'cert/key mismatch')));
  }
  card.appendChild(meta);
  if (_holdsPrivateKey(bundle)) {
    card.appendChild(el('div', { class: 'card-failure' },
      'A public file holds a private key — it will not be served'));
  }
  return card;
}

// A private key inside cert.pem or ca.pem. Those files are written
// world-readable and any authenticated user may download them, so the
// download refuses — and this is what says why, rather than leaving a
// download that stopped working for no visible reason.
function _holdsPrivateKey(bundle) {
  return PUBLIC_CERT_FILES.some(fname => {
    const f = bundle.files && bundle.files[fname];
    return f && typeof f === 'object' && f.private_key;
  });
}

function confirmDeleteCertBundle(name) {
  if (!confirm('Delete certificate bundle "' + name
      + '" and all its files?')) return;
  api('DELETE', '/api/certs/' + encodeURIComponent(name))
    .then(() => navigate('/certificates'))
    .catch(e => { if (e !== 'unauthorized') alert(e); });
}

function showCertEditor(name) {
  if (!isAdmin) { navigate('/certificates'); return; }
  if (name !== null && !currentCerts) {
    whenLoaded(loadCerts, () => showCertEditor(name));
    return;
  }
  const isNew = name === null;
  if (isNew) {
    _showNewBundleModal();
    return;
  }
  // Existing bundle: fetch detail (has mtime/size/symlink info)
  api('GET', '/api/certs/' + encodeURIComponent(name)).then(info => {
    _showBundleEditor(info);
  }).catch(e => {
    if (e !== 'unauthorized') alert(e);
    navigate('/certificates');
  });
}

function _showNewBundleModal() {
  const nameInput = el('input', {
    type: 'text', autocomplete: 'off',
    placeholder: 'e.g. main, le-mydomain, internal-ca',
  });
  openModal({
    title: 'New certificate bundle',
    body: el('div', {},
      formGroup('Bundle name', nameInput, {
        hint: 'Letters, digits, dot, dash, underscore. '
          + 'After creating, you can upload cert.pem, key.pem and (optionally) ca.pem.',
      })),
    footer: [
      btn('Cancel', '', () => backToList()),
      btn('Create', 'btn-primary', () => {
        const n = nameInput.value.trim();
        if (!n) return modalError('Name is required');
        api('POST', '/api/certs', { name: n })
          .then(() => navigate('/certificates/' + encodeURIComponent(n)))
          .catch(e => modalError(String(e)));
      }),
    ],
  });
  nameInput.focus();
}

function _showBundleEditor(info) {
  const body = el('div', {});
  // Flex row, not a plain line: the path wraps (it is long and breaks
  // anywhere) and the icon must stay beside it, not drop below.
  body.appendChild(el('div', {
    class: 'card-subtitle',
    style: 'margin-bottom:12px;display:flex;align-items:flex-start',
  }, el('span', { style: 'word-break:break-all;min-width:0' },
       'Path: ' + info.path),
     copyIconBtn(info.path, 'Copy path')));

  const usedBy = info.used_by || [];
  if (usedBy.length) {
    body.appendChild(el('div', {
      class: 'card-subtitle', style: 'margin-bottom:12px',
    }, 'In use by ' + usedBy.map(_usageLabel).join(', ')
       + '. After replacing the certificate, use Reload so running '
       + 'servers pick it up.'));
  }

  if (info.key_match === false) {
    body.appendChild(el('div', {
      class: 'card-subtitle',
      style: 'margin-bottom:12px;color:var(--error)',
    }, 'cert.pem and key.pem do not match — TLS handshakes will fail. '
       + 'Use "Replace cert + key" to upload a matching pair.'));
  }

  PUBLIC_CERT_FILES.forEach(fname => {
    const f = info.files && info.files[fname];
    if (!(f && typeof f === 'object' && f.private_key)) return;
    body.appendChild(el('div', {
      class: 'card-subtitle',
      style: 'margin-bottom:12px;color:var(--error)',
    }, fname + ' contains a private key. It is world-readable and this '
       + 'endpoint serves it to any signed-in user, so downloading it '
       + 'is refused. Split the file: the certificate here, the key in '
       + 'key.pem.'));
  });

  body.appendChild(el('div', { style: 'margin-bottom:16px' },
    btn('Replace cert + key', 'btn-small btn-accent',
      () => navigate('/certificates/' + encodeURIComponent(info.name)
        + '/replace'))));

  CERT_FILES.forEach(fname => {
    body.appendChild(
      _renderCertFileRow(info.name, fname, info.files[fname], usedBy));
  });

  const footer = [
    btn('Delete bundle', 'btn-danger',
      () => confirmDeleteCertBundle(info.name)),
    el('span', { class: 'footer-spacer' }),
  ];
  if (usedBy.length) {
    footer.push(btn('Reload', 'btn-accent',
      () => _reloadCertBundle(info.name)));
  }
  footer.push(btn('Close', '', () => backToList()));

  openModal({ title: 'Bundle: ' + info.name, body, wide: true, footer });
}

function _reloadCertBundle(name) {
  api('POST', '/api/certs/' + encodeURIComponent(name) + '/reload')
    .then(res => {
      const servers = res.reloaded || [];
      alert(servers.length
        ? 'New certificate is live on:\n' + servers.join('\n')
        : 'No running server uses this bundle — nothing to reload.');
    })
    .catch(e => { if (e !== 'unauthorized') alert(String(e)); });
}

// A PEM textarea with a "load from file" button next to it.
function _pemField(label, placeholder) {
  const ta = el('textarea', {
    rows: '8', style: 'width:100%;font-family:monospace;font-size:12px',
    placeholder,
  });
  const fileInput = el('input', {
    type: 'file', accept: '.pem,.crt,.key,.cer,application/x-pem-file',
  });
  fileInput.style.display = 'none';
  fileInput.onchange = () => {
    const f = fileInput.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => { ta.value = reader.result; };
    reader.readAsText(f);
  };
  const group = formGroup(label, ta);
  group.appendChild(fileInput);
  group.appendChild(btn('Load from file', 'btn-small',
    () => fileInput.click()));
  return { group, ta };
}

function _showReplacePairModal(bundleName) {
  if (!isAdmin) { navigate('/certificates'); return; }
  const certField = _pemField(
    'cert.pem', '-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----');
  const keyField = _pemField(
    'key.pem', '-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----');
  openModal({
    title: 'Replace cert + key (' + bundleName + ')',
    body: el('div', {},
      el('div', { class: 'card-subtitle', style: 'margin-bottom:12px' },
        'Sent and validated as a pair. Uploading them one at a time is '
        + 'rejected while the other half still belongs to a different '
        + 'certificate.'),
      certField.group, keyField.group),
    wide: true,
    footer: [
      btn('Cancel', '', () => backToList()),
      btn('Save both', 'btn-primary', () => {
        const cert = certField.ta.value.trim();
        const key = keyField.ta.value.trim();
        if (!cert || !key) {
          return modalError('Both cert.pem and key.pem are required');
        }
        api('POST', '/api/certs/' + encodeURIComponent(bundleName) + '/files',
            { files: [
              { filename: 'cert.pem', content: cert },
              { filename: 'key.pem', content: key },
            ] })
          .then(() => navigate(
            '/certificates/' + encodeURIComponent(bundleName)))
          .catch(e => modalError(String(e)));
      }),
    ],
  });
  certField.ta.focus();
}

// Map a not_after epoch (seconds) to an expiry status: returns
// {label, color} where color is a CSS color string used inline.
function _expiryStatus(notAfter) {
  if (!notAfter) return null;
  const now = Date.now() / 1000;
  const days = Math.floor((notAfter - now) / 86400);
  if (days < 0) return { label: 'expired ' + (-days) + 'd ago', color: 'var(--error)' };
  if (days < 7) return { label: 'expires in ' + days + 'd', color: 'var(--error)' };
  if (days < 30) return { label: 'expires in ' + days + 'd', color: 'var(--warning)' };
  return { label: 'expires in ' + days + 'd', color: 'var(--success, #4caf50)' };
}

function _renderCertInfo(info) {
  // Best-effort: if backend couldn't parse, just show a hint
  if (!info || info.error) {
    return el('div', {
      class: 'card-subtitle',
      style: 'margin-top:6px;color:var(--warning)',
    }, info && info.error
      ? 'Cert parse error: ' + info.error
      : 'No cert metadata');
  }
  const rows = [];
  const row = (label, val) => el('div', { class: 'card-meta-row' },
    el('span', { class: 'card-meta-label' }, label),
    el('span', {}, val));
  rows.push(row('Subject', info.subject_cn || '(no CN)'));
  if (!info.self_signed) {
    rows.push(row('Issuer', info.issuer_cn || '(no CN)'));
  } else {
    rows.push(row('Issuer', 'self-signed'));
  }
  if (info.san_dns && info.san_dns.length) {
    rows.push(row('SAN DNS', info.san_dns.join(', ')));
  }
  if (info.san_ip && info.san_ip.length) {
    rows.push(row('SAN IP', info.san_ip.join(', ')));
  }
  const exp = _expiryStatus(info.not_after);
  if (exp) {
    rows.push(el('div', { class: 'card-meta-row' },
      el('span', { class: 'card-meta-label' }, 'Validity'),
      el('span', { style: 'color:' + exp.color },
        new Date(info.not_after * 1000).toLocaleDateString()
        + ' (' + exp.label + ')')));
  }
  if (info.key_type) {
    const keyStr = info.key_type
      + (info.key_size ? ' ' + info.key_size : '');
    rows.push(row('Key', keyStr));
  }
  if (info.fingerprint_sha256) {
    const fp = info.fingerprint_sha256;
    const short = fp.slice(0, 23) + '…' + fp.slice(-8);
    rows.push(el('div', { class: 'card-meta-row', title: fp },
      el('span', { class: 'card-meta-label' }, 'SHA-256'),
      el('span', { style: 'font-family:monospace;font-size:11px' }, short)));
  }
  return el('div', {
    class: 'card-meta',
    style: 'margin-top:6px;padding:6px 8px;background:var(--bg-alt);'
      + 'border-radius:4px;font-size:13px',
  }, ...rows);
}

function _renderCertFileRow(bundleName, fname, fileInfo, usedBy) {
  const wrap = el('div', {
    class: 'subgroup cert-file-row',
    style: 'margin-bottom:16px;transition:background-color .15s',
  });
  // Drag-and-drop: highlight on dragover, upload on drop. dragenter/leave
  // counter avoids flicker when crossing child elements.
  let dragDepth = 0;
  wrap.addEventListener('dragenter', e => {
    e.preventDefault();
    dragDepth++;
    wrap.style.backgroundColor = 'var(--accent-bg, rgba(0,120,255,0.1))';
  });
  wrap.addEventListener('dragleave', () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) wrap.style.backgroundColor = '';
  });
  wrap.addEventListener('dragover', e => { e.preventDefault(); });
  wrap.addEventListener('drop', e => {
    e.preventDefault();
    dragDepth = 0;
    wrap.style.backgroundColor = '';
    const f = e.dataTransfer.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => _uploadCertFile(bundleName, fname, reader.result);
    reader.readAsText(f);
  });
  const header = el('div', { class: 'card-header-row' },
    el('strong', {}, fname));
  if (fileInfo.present) {
    // CA badge on header for quick visual identification
    if (fileInfo.cert_info && fileInfo.cert_info.is_ca) {
      header.appendChild(el('span', {
        class: 'card-status-badge',
        style: 'background:var(--accent);color:#fff;padding:1px 6px;'
          + 'border-radius:8px;font-size:11px;margin-left:6px',
      }, 'CA'));
    }
    const status = el('span', { class: 'card-subtitle' },
      fileInfo.symlink
        ? '→ ' + fileInfo.symlink
        : new Date(fileInfo.mtime * 1000).toLocaleString()
          + ' · ' + fileInfo.size + ' B');
    header.appendChild(status);
  } else {
    header.appendChild(el('span', { class: 'card-subtitle' }, 'not uploaded'));
  }
  wrap.appendChild(header);

  // Parsed cert metadata (CN / issuer / SAN / expiry / fingerprint).
  // Only shown for cert.pem and ca.pem (key.pem has no cert_info).
  if (fileInfo.present && fileInfo.cert_info) {
    wrap.appendChild(_renderCertInfo(fileInfo.cert_info));
  }

  // Action buttons
  const fileInput = el('input', { type: 'file', accept: '.pem,.crt,.key,.cer,application/x-pem-file' });
  fileInput.style.display = 'none';
  fileInput.onchange = () => {
    const f = fileInput.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      _uploadCertFile(bundleName, fname, reader.result);
    };
    reader.readAsText(f);
  };

  const actions = el('div', { style: 'display:flex;gap:8px;margin-top:8px;flex-wrap:wrap' });
  actions.appendChild(btn('Upload file', 'btn-small btn-accent',
    () => fileInput.click()));
  actions.appendChild(btn('Paste content', 'btn-small',
    () => navigate('/certificates/' + encodeURIComponent(bundleName)
      + '/paste/' + fname)));
  if (fileInfo.present) {
    if (fname !== 'key.pem') {
      actions.appendChild(btn('View', 'btn-small',
        () => navigate('/certificates/' + encodeURIComponent(bundleName)
          + '/view/' + fname)));
    }
    actions.appendChild(btn('Delete', 'btn-small btn-danger',
      () => _confirmDeleteCertFile(bundleName, fname, usedBy)));
  }
  wrap.appendChild(fileInput);
  wrap.appendChild(actions);
  return wrap;
}

function _uploadCertFile(bundleName, fname, content) {
  api('POST', '/api/certs/' + encodeURIComponent(bundleName) + '/files',
      { filename: fname, content })
    .then(() => navigate('/certificates/' + encodeURIComponent(bundleName)))
    .catch(e => alert(String(e)));
}

function _showPasteCertModal(bundleName, fname) {
  if (!isAdmin) { navigate('/certificates'); return; }
  const ta = el('textarea', {
    rows: '14', style: 'width:100%;font-family:monospace;font-size:12px',
    placeholder: '-----BEGIN ...-----\n...\n-----END ...-----',
  });
  openModal({
    title: 'Paste ' + fname + ' (' + bundleName + ')',
    body: el('div', {},
      formGroup('PEM content', ta, {
        hint: 'Paste the full PEM block including BEGIN/END markers.',
      })),
    wide: true,
    footer: [
      btn('Cancel', '', () => backToList()),
      btn('Save', 'btn-primary', () => {
        const content = ta.value;
        if (!content.trim()) return modalError('Content is required');
        api('POST', '/api/certs/' + encodeURIComponent(bundleName) + '/files',
            { filename: fname, content })
          .then(() => navigate(
            '/certificates/' + encodeURIComponent(bundleName)))
          .catch(e => modalError(String(e)));
      }),
    ],
  });
  ta.focus();
}

// Save a PEM string as a file. The content is already in the browser,
// so there is nothing to fetch — no server round trip, no second request
// that could fail after the user already sees the text.
function _downloadPem(filename, content) {
  const blob = new Blob([content], { type: 'application/x-pem-file' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// Show the PEM in a read-only box; download and copy act on what is
// displayed. A plain download gave no way to check what is in the file
// without opening it from the download folder, and pasting a cert into
// another host's config is the common case anyway.
function _showViewCertModal(bundleName, fname) {
  api('GET', '/api/certs/' + encodeURIComponent(bundleName)
        + '/files/' + encodeURIComponent(fname))
    .then(data => _renderViewCertModal(bundleName, fname, data.content || ''))
    .catch(e => {
      if (e !== 'unauthorized') alert(String(e));
      navigate('/certificates/' + encodeURIComponent(bundleName));
    });
}

function _renderViewCertModal(bundleName, fname, content) {
  const ta = el('textarea', {
    rows: '16', readonly: true, spellcheck: 'false',
    style: 'width:100%;font-family:monospace;font-size:12px',
  });
  ta.value = content;
  const downloadName = bundleName + '-' + fname;
  // The visible textarea is selected as well, so that a browser which
  // refuses both copy paths still leaves the user one keystroke away.
  const copyBtn = btn('Copy', 'btn-small', () => {
    ta.focus();
    ta.select();
    copyText(content).then(ok => {
      copyBtn.textContent = ok ? 'Copied' : 'Press Ctrl+C';
      setTimeout(() => { copyBtn.textContent = 'Copy'; }, 1500);
    });
  });
  const body = el('div', {},
    el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px' },
      btn('Download', 'btn-small btn-accent',
        () => _downloadPem(downloadName, content)),
      copyBtn),
    ta,
    el('div', { class: 'card-subtitle', style: 'margin-top:4px;font-size:12px' },
      'Saved as ' + downloadName + '. Public file — it contains no '
      + 'private key.'));
  openModal({
    title: fname + ' (' + bundleName + ')',
    body,
    wide: true,
    footer: [btn('Close', 'btn-primary', () => backToList())],
  });
  ta.focus();
  ta.setSelectionRange(0, 0);
}

// ----- Certificate generator -----
function showCertGenerator() {
  if (!isAdmin) { navigate('/certificates'); return; }
  // Fetch bundles (for signer dropdown — only CA-flagged bundles allowed)
  api('GET', '/api/certs').then(data => {
    _showCertGeneratorWithBundles(data.bundles || []);
  }).catch(e => { if (e !== 'unauthorized') alert(String(e)); });
}

function _showCertGeneratorWithBundles(bundles) {
  // Identify CA bundles — those whose cert.pem parses to is_ca=true and
  // also have a key.pem (so we can actually sign with them).
  const caBundles = bundles.filter(b => {
    const cf = b.files['cert.pem'];
    return _certFilePresent(b, 'cert.pem')
      && _certFilePresent(b, 'key.pem')
      && cf.cert_info && cf.cert_info.is_ca;
  });

  // Mode selector (radio)
  const modes = [
    { value: 'self_signed', label: 'Self-signed server cert',
      hint: 'Standalone cert+key. Clients must pin or skip validation.' },
    { value: 'ca', label: 'CA (root)',
      hint: 'Use to sign server/client certs. Distribute the CA cert to clients.' },
    { value: 'signed_by', label: 'Server cert signed by CA',
      hint: 'Server cert chain-validated by clients that trust the CA.' },
    { value: 'client', label: 'Client cert (for mTLS)',
      hint: 'Generates cert+key, downloads as files. Not stored on server.' },
  ];
  const modeInputs = {};
  const modeWrap = el('div', { style: 'margin-bottom:12px' });
  modes.forEach((m, i) => {
    const r = el('input', { type: 'radio', name: 'cert-mode',
      value: m.value, checked: i === 0 });
    modeInputs[m.value] = r;
    const row = el('div', { style: 'margin:4px 0' },
      el('label', { class: 'checkbox-label',
        style: 'align-items:flex-start' },
        r,
        el('div', { style: 'margin-left:4px' },
          el('div', {}, m.label),
          el('div', { class: 'card-subtitle',
            style: 'font-size:11px' }, m.hint))));
    modeWrap.appendChild(row);
  });

  // Form fields
  const bundleNameInput = el('input', { type: 'text',
    placeholder: 'e.g. main, internal-ca' });
  const cnInput = el('input', { type: 'text',
    placeholder: 'e.g. myserver.local' });
  const sanDnsInput = el('input', { type: 'text',
    placeholder: 'localhost, myserver.local' });
  const sanIpInput = el('input', { type: 'text',
    placeholder: '127.0.0.1, 192.168.1.10' });
  const daysInput = el('input', { type: 'number',
    value: '365', min: '1', max: '36500' });
  const keyTypeSel = el('select');
  // Order: most-compatible first. Ed25519 is fastest/smallest but
  // requires a modern TLS stack on the client (TLS 1.3 typically).
  const KEY_TYPE_LABELS = {
    rsa2048: 'RSA 2048 (universal compatibility)',
    rsa4096: 'RSA 4096 (slower, for long-lived CA)',
    ec_p256: 'EC P-256 (modern, fast — recommended)',
    ed25519: 'Ed25519 (fastest, needs modern client)',
  };
  Object.entries(KEY_TYPE_LABELS).forEach(([kt, label]) => {
    keyTypeSel.appendChild(el('option', { value: kt }, label));
  });
  const signerSel = el('select');
  signerSel.appendChild(el('option', { value: '' }, '-- select CA bundle --'));
  caBundles.forEach(b => {
    signerSel.appendChild(el('option', { value: b.name }, b.name));
  });

  // Group rows so we can show/hide based on mode
  const bundleRow = formRow('Target bundle', bundleNameInput);
  const cnRow = formRow('Common Name (CN)', cnInput);
  const sanDnsRow = formRow('SAN DNS', sanDnsInput);
  const sanIpRow = formRow('SAN IP', sanIpInput);
  const daysRow = formRow('Validity (days)', daysInput);
  const keyTypeRow = formRow('Key type', keyTypeSel);
  const signerRow = formRow('Signing CA', signerSel);

  // Track whether the user has manually edited `days` so we don't
  // clobber their value when switching modes.
  let daysTouched = false;
  daysInput.addEventListener('input', () => { daysTouched = true; });

  function updateFieldVisibility() {
    const mode = _getCheckedValue(modeInputs);
    const isClient = mode === 'client';
    const isCa = mode === 'ca';
    const needsSigner = mode === 'signed_by' || mode === 'client';
    bundleRow.style.display = isClient ? 'none' : '';
    signerRow.style.display = needsSigner ? '' : 'none';
    // SAN is only meaningful for end-entity server certs. Client certs
    // usually don't need it (clientAuth doesn't match on hostname), and
    // CA certs ignore it entirely (chain validation skips SAN on CAs).
    sanDnsRow.style.display = (isClient || isCa) ? 'none' : '';
    sanIpRow.style.display = (isClient || isCa) ? 'none' : '';
    // CAs should outlive the certs they sign — default to ~10 years
    // for CA mode, 1 year for everything else. User-typed values take
    // precedence (daysTouched flag).
    if (!daysTouched) {
      daysInput.value = isCa ? '3650' : '365';
    }
  }
  Object.values(modeInputs).forEach(r => {
    r.onchange = updateFieldVisibility;
  });

  const disclaimer = el('div', {
    class: 'card-subtitle',
    style: 'margin-top:12px;padding:8px;background:var(--bg-alt);'
      + 'border-left:3px solid var(--warning);font-size:12px',
  }, 'Generated certificates are intended for testing and internal '
    + 'use. For production PKI use a dedicated tool (smallstep, '
    + 'Vault, ACM, etc.).');

  const body = el('div', {},
    modeWrap,
    bundleRow,
    cnRow,
    sanDnsRow,
    sanIpRow,
    daysRow,
    keyTypeRow,
    signerRow,
    disclaimer);
  updateFieldVisibility();

  if (!caBundles.length) {
    // Disable modes that need a CA
    modeInputs.signed_by.disabled = true;
    modeInputs.client.disabled = true;
    body.appendChild(el('div', { class: 'card-subtitle',
      style: 'margin-top:8px;color:var(--warning);font-size:12px' },
      'No CA bundles available. Generate a "CA (root)" bundle first '
      + 'to enable signing.'));
  }

  openModal({
    title: 'Generate certificate',
    body,
    wide: true,
    footer: [
      btn('Cancel', '', () => backToList()),
      btn('Generate', 'btn-primary',
        () => _submitCertGeneration({
          modeInputs, bundleNameInput, cnInput,
          sanDnsInput, sanIpInput, daysInput,
          keyTypeSel, signerSel,
        })),
    ],
  });
  cnInput.focus();
}

function _getCheckedValue(radioMap) {
  for (const [v, el] of Object.entries(radioMap)) {
    if (el.checked) return v;
  }
  return null;
}

function _splitCsv(s) {
  return s.split(',').map(x => x.trim()).filter(Boolean);
}

function _submitCertGeneration(fields) {
  const mode = _getCheckedValue(fields.modeInputs);
  const cn = fields.cnInput.value.trim();
  if (!cn) return modalError('CN is required');
  const days = parseInt(fields.daysInput.value) || 365;
  const params = {
    cn,
    days,
    key_type: fields.keyTypeSel.value,
    san_dns: _splitCsv(fields.sanDnsInput.value),
    san_ip: _splitCsv(fields.sanIpInput.value),
  };
  if (mode === 'signed_by' || mode === 'client') {
    const signer = fields.signerSel.value;
    if (!signer) return modalError('Select a signing CA');
    params.signer_bundle = signer;
  }
  if (mode === 'client') {
    api('POST', '/api/certs/generate-client', params)
      .then(data => _showClientCertDownload(data))
      .catch(e => modalError(String(e)));
    return;
  }
  // Bundle modes
  const bundle = fields.bundleNameInput.value.trim();
  if (!bundle) return modalError('Target bundle name is required');
  params.mode = mode;
  api('POST', '/api/certs/' + encodeURIComponent(bundle) + '/generate', params)
    .then(() => navigate('/certificates/' + encodeURIComponent(bundle)))
    .catch(e => modalError(String(e)));
}

// Join PEM blocks into one file. Order is cert, key, CA — what OpenSSL
// (and therefore curl --cert, Python's load_cert_chain, HAProxy, stunnel)
// expects when it is handed a single file. No comment lines between the
// blocks: OpenSSL would skip them, but a stricter parser need not, and
// the order already says which block is which.
function _joinPem(...parts) {
  return parts
    .filter(p => p && p.trim())
    .map(p => p.trim() + '\n')
    .join('');
}

function _showClientCertDownload(data) {
  // cert+key+ca separately, and the three of them as one file — most
  // clients take a single combined PEM and that is one thing to copy
  // over instead of three. A ZIP would need a JS library, and a .p12
  // would need the key to make a second trip through the server.
  const namePrefix = (data.cn || 'client').replace(/[^a-zA-Z0-9._-]/g, '_');
  const combined = _joinPem(data.cert_pem, data.key_pem, data.ca_pem);
  const fileRow = (label, filename, content, note) => el('div', {
    style: 'display:flex;align-items:center;gap:8px;flex-wrap:wrap',
  },
    btn('Download ' + label, 'btn-accent btn-small',
      () => _downloadPem(filename, content)),
    copyIconBtn(content, 'Copy ' + label + ' to clipboard'),
    el('span', { class: 'card-subtitle', style: 'font-size:12px' }, note));
  const body = el('div', {},
    el('p', {},
      'Client certificate generated. Download the files and install '
      + 'them on the mTLS client. ',
      el('strong', {}, 'The private key is not stored on the server '),
      '— if you lose it, regenerate.'),
    el('div', { style: 'display:flex;flex-direction:column;gap:8px;margin:12px 0' },
      fileRow('all-in-one', namePrefix + '-combined.pem', combined,
        'certificate + key + CA in one file'),
      fileRow('cert.pem', namePrefix + '-cert.pem', data.cert_pem,
        'the client certificate'),
      fileRow('key.pem', namePrefix + '-key.pem', data.key_pem,
        'the private key — keep it secret'),
      fileRow('ca.pem', namePrefix + '-ca.pem', data.ca_pem,
        'the CA to trust the server with')),
    el('p', { class: 'card-subtitle', style: 'font-size:12px' },
      'Either hand the client ' + namePrefix + '-combined.pem, or the '
      + 'separate files: present cert.pem + key.pem, trust ca.pem. '
      + 'The combined file and key.pem both hold the private key — '
      + 'store them mode 0600.'));
  openModal({
    title: 'Client certificate ready',
    body,
    footer: [
      btn('Done', 'btn-primary', () => navigate('/certificates')),
    ],
  });
}

// Short "where is this used" label for one entry of a bundle's used_by.
function _usageLabel(u) {
  // A name where there is one, the id where there is not - it used to
  // fall back to "#2", a position that names another port once the
  // list moves.
  const where = u.type === 'http'
    ? 'HTTPS ' + (u.name || u.http_id || '')
    : 'port ' + (u.port_name || u.port_id);
  return where.trim() + ' ' + (u.address || '') + ':' + u.server_port;
}

// Servers that would break if this file went away. ca.pem only matters
// to a server that verifies client certificates; cert.pem and key.pem
// matter to every server using the bundle.
function _certFileUsers(fname, usedBy) {
  if (!usedBy || !usedBy.length) return [];
  if (fname === 'ca.pem') return usedBy.filter(u => u.mtls);
  return usedBy;
}

function _confirmDeleteCertFile(bundleName, fname, usedBy) {
  // Deleting a file from a bundle in use is allowed on purpose — it is
  // how you regenerate into a bundle, since generate refuses to
  // overwrite cert.pem. Say what it costs rather than blocking it.
  const users = _certFileUsers(fname, usedBy);
  let msg = 'Delete ' + fname + ' from bundle "' + bundleName + '"?';
  if (users.length) {
    msg += '\n\nIn use by:\n'
      + users.map(u => '  · ' + _usageLabel(u)).join('\n')
      + '\n\nThose servers keep running on the certificate they already '
      + 'loaded, but will fail on the next reload or restart until '
      + fname + ' is back.';
  }
  if (!confirm(msg)) return;
  api('DELETE', '/api/certs/' + encodeURIComponent(bundleName)
        + '/files/' + encodeURIComponent(fname))
    .then(() => navigate('/certificates/' + encodeURIComponent(bundleName)))
    .catch(e => alert(String(e)));
}

// ===========================================================================
// Password hashing (SHA-256 + random salt — same format as server)
// ===========================================================================
async function hashPassword(password) {
  const salt = Array.from(crypto.getRandomValues(new Uint8Array(16)))
    .map(b => b.toString(16).padStart(2, '0')).join('');
  const data = new TextEncoder().encode(salt + password);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  const hash = Array.from(new Uint8Array(hashBuffer))
    .map(b => b.toString(16).padStart(2, '0')).join('');
  return `sha256:${salt}:${hash}`;
}

// ===========================================================================
// Init
// ===========================================================================
function bootApp() {
  // The NDJSON stream is the only source of /api/status data. Its first
  // line is the full snapshot; _applyStatusLine flips _authed=true and
  // routes from there. 401 inside startStatusStream navigates to /login.
  startStatusStream();
}

function init() {
  $('login-btn').addEventListener('click', doLogin);
  $('login-pass').addEventListener('keydown',
    e => { if (e.key === 'Enter') doLogin(); });
  $('login-user').addEventListener('keydown',
    e => { if (e.key === 'Enter') $('login-pass').focus(); });
  $('logout-btn').addEventListener('click', doLogout);
  $('menu-toggle').addEventListener('click', e => {
    e.stopPropagation();
    $('header-inner').classList.toggle('menu-open');
  });
  document.addEventListener('click', e => {
    const inner = $('header-inner');
    if (inner.classList.contains('menu-open')
        && !inner.contains(e.target)) inner.classList.remove('menu-open');
  });

  bootApp();
}

document.addEventListener('DOMContentLoaded', init);
