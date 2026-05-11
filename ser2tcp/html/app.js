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

// Generic kebab (3-dot) menu — `actions` is array of {label, cls, onclick}.
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
    const item = el('button', {
      type: 'button',
      class: 'kebab-item' + (a.cls ? ' ' + a.cls : ''),
      onclick: e => {
        e.stopPropagation();
        pop.hidden = true;
        a.onclick();
      },
    }, a.label);
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
  header.hidden = !_authed;
  if (token && username) {
    userName.textContent = username;
    userBtn.classList.toggle('is-admin', isAdmin);
    userBtn.hidden = false;
  } else {
    userBtn.hidden = true;
  }
  if (navUsers) navUsers.hidden = !isAdmin;
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
    return r.json().then(d => r.ok ? d : Promise.reject(d.error || 'Error'));
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
  [/^\/ports\/(\d+)\/edit$/,       m => showPortEditor(parseInt(m[1]), m._q)],
  [/^\/users$/,                    () => showUsers()],
  [/^\/users\/new$/,               () => showUserEditor(null)],
  [/^\/users\/([^/]+)\/edit$/,     m => showUserEditor(decodeURIComponent(m[1]))],
  [/^\/tokens\/new$/,              () => showTokenEditor(null)],
  [/^\/tokens\/([^/]+)\/edit$/,    m => showTokenEditor(decodeURIComponent(m[1]))],
  [/^\/settings$/,                 () => showSettings()],
  [/^\/settings\/session$/,        () => showSessionEditor()],
  [/^\/settings\/http\/new$/,      () => showHttpEditor(null)],
  [/^\/settings\/http\/(\d+)\/edit$/, m => showHttpEditor(parseInt(m[1]))],
  [/^\/login$/,                    () => showLogin()],
];

function route() {
  const fullHash = location.hash.replace(/^#/, '') || '/ports';
  const [path, queryStr] = fullHash.split('?');
  const q = _parseQuery(queryStr || '');
  // List-view routes — close any modal so it doesn't linger.
  if (path === '/ports' || path === '/users' || path === '/settings') {
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
const PROTOCOLS = ['TCP', 'TELNET', 'SSL', 'SOCKET', 'WEBSOCKET'];
const CONTROL_SIGNALS = ['rts', 'dtr', 'cts', 'dsr', 'ri', 'cd'];
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
  renderDetectedSection();
}

// Recompute usedPorts/usedEndpoints — used by editor for conflict checks.
function _rebuildUsedSets() {
  usedPorts = [];
  usedEndpoints = [];
  if (!portsStatus) return;
  portsStatus.ports.forEach((p, i) => {
    (p.servers || []).forEach(s => {
      if (s.port) usedPorts.push({address: s.address, port: s.port, index: i});
      if (s.endpoint) usedEndpoints.push({endpoint: s.endpoint, index: i});
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
      _statusStream = null;
      setCredentials(null, null);
      navigate('/login');
      return;
    }
    if (!resp.ok || !resp.body) {
      throw new Error('stream HTTP ' + resp.status);
    }
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
    renderDetectedSection();
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
    if ($('ports-view').classList.contains('active')) {
      renderPortsList();
      renderDetectedSection();
    }
    return;
  }
  // Per-port delta
  if (line.port_index !== undefined && portsStatus) {
    const idx = line.port_index;
    const port = portsStatus.ports[idx];
    if (!port) return;
    for (const [k, v] of Object.entries(line)) {
      if (k === 'port_index' || k === '_delta') continue;
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
  const root = $('ports-content');
  root.innerHTML = '';
  if (!portsStatus) {
    root.appendChild(el('p', { class: 'empty' }, 'Loading…'));
    return;
  }
  if (!portsStatus.ports.length) {
    root.appendChild(el('p', { class: 'empty' }, 'No ports configured'));
    return;
  }
  const grid = el('div', { class: 'card-grid' });
  portsStatus.ports.forEach((p, i) => grid.appendChild(renderPortCard(p, i)));
  root.appendChild(grid);
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

function renderPortCard(port, index) {
  const ser = port.serial || {};
  const state = _portState(port);
  const card = el('div', { class: 'card card-' + state });
  card.dataset.portIndex = index;

  // Header row: title + kebab
  const titleText = port.name || ser.port || ('Port ' + index);
  const titleSpan = el('span', { class: 'card-title' }, titleText);
  const headerRow = el('div', { class: 'card-header-row' }, titleSpan);
  if (isAdmin) {
    headerRow.appendChild(kebabMenu([
      { label: 'Edit', cls: 'btn-accent',
        onclick: () => navigate('/ports/' + index + '/edit') },
      { label: 'Delete', cls: 'btn-danger',
        onclick: () => confirmDeletePort(index, titleText) },
    ]));
  }
  card.appendChild(headerRow);

  // Subtitle: device path or match
  let subtitle = '';
  if (ser.port) {
    if (port.name || ser.match) subtitle = ser.port;
  } else if (ser.match) {
    const matching = detectedPorts.filter(p => _matchesPort(p, ser.match));
    if (matching.length) subtitle = matching.map(p => p.device).join(', ');
    else subtitle = 'match: ' + Object.entries(ser.match).map(([k,v]) => k+'='+v).join(', ');
  }
  const subParts = [];
  if (subtitle) subParts.push(subtitle);
  if (ser.baudrate) subParts.push(ser.baudrate + ' bps');
  subParts.push(ser.connected ? 'connected' : 'disconnected');
  card.appendChild(el('div', { class: 'card-subtitle' }, subParts.join(' — ')));

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
  // they're more actionable in context. Toggle still works via the
  // PUT /api/ports/<i>/signals endpoint inside those pages.

  // Monitor link — only useful when the serial proxy is actually
  // connected and producing TX/RX traffic (state === 'online'). Hidden
  // when the device is missing (red) or just configured but idle (grey).
  if (port.name && state === 'online') {
    card.appendChild(el('div', { class: 'ws-links' },
      el('a', {
        href: '/monitor/' + encodeURIComponent(port.name),
        target: '_blank', rel: 'noopener',
      }, 'Monitor')));
  }

  // Server list — Terminal / Raw links inside need to know the port
  // state to gate themselves (hidden when the device isn't on the
  // system, since opening them would just immediately fail).
  const serverUl = el('ul', { class: 'server-list' });
  (port.servers || []).forEach((s, si) => {
    serverUl.appendChild(renderServerRow(s, index, si, state));
  });
  card.appendChild(serverUl);

  return card;
}

function renderServerRow(srv, portIdx, srvIdx, portState) {
  const proto = (srv.protocol || 'tcp').toUpperCase();
  const li = el('li', { class: 'server-row' });

  // Head: protocol tag + address
  const head = el('div', { class: 'server-row-head' });
  head.appendChild(el('span', { class: 'server-row-tag' }, proto));
  let addrText;
  if (proto === 'WEBSOCKET') addrText = '/ws/' + srv.endpoint;
  else if (proto === 'SOCKET') addrText = srv.address;
  else addrText = srv.address + ':' + srv.port;
  head.appendChild(document.createTextNode(addrText));
  li.appendChild(head);

  // WebSocket: clickable URL + terminal links
  if (proto === 'WEBSOCKET') {
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = scheme + '//' + location.host + '/ws/' + srv.endpoint;
    const urlEl = el('div', {
      class: 'server-row-detail copyable',
      title: 'Click to copy',
      onclick: e => {
        e.stopPropagation();
        navigator.clipboard.writeText(wsUrl);
        urlEl.textContent = 'Copied!';
        setTimeout(() => { urlEl.textContent = wsUrl; }, 1000);
      },
    }, wsUrl);
    li.appendChild(urlEl);
    if (srv.data !== false) {
      // Skip Terminal / Raw when the configured device isn't present —
      // clicking them would just fail to open a serial connection.
      if (portState !== 'error') {
        const tokenParam = srv.token
          ? '?token=' + encodeURIComponent(srv.token) : '';
        li.appendChild(el('div', { class: 'ws-links' },
          el('a', {
            href: '/xterm/' + srv.endpoint + tokenParam,
            target: '_blank', rel: 'noopener',
          }, 'Terminal'),
          el('a', {
            href: '/raw/' + srv.endpoint + tokenParam,
            target: '_blank', rel: 'noopener',
          }, 'Raw')));
      }
    } else {
      li.appendChild(el('div', { class: 'server-row-detail' }, 'control only'));
    }
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
        onclick: () => disconnectClient(portIdx, srvIdx, ci),
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

function renderDetectedSection() {
  const root = $('detected-ports');
  root.innerHTML = '';
  if (!detectedPorts.length) return;
  const sec = el('div', { class: 'detected-section' });
  sec.appendChild(el('h3', { class: 'detected-title' }, 'Detected serial ports'));
  const grid = el('div', { class: 'detected-grid' });
  detectedPorts.forEach(p => grid.appendChild(renderDetectedCard(p)));
  sec.appendChild(grid);
  root.appendChild(sec);
}

function renderDetectedCard(p) {
  const card = el('div', { class: 'card card-detected card-detected-online' });
  const title = el('span', { class: 'card-title' }, p.device);
  const headerRow = el('div', { class: 'card-header-row' }, title);
  if (isAdmin) {
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

function confirmDeletePort(index, name) {
  if (!confirm('Delete port "' + name + '"?')) return;
  api('DELETE', '/api/ports/' + index)
    .then(() => navigate('/ports'))
    .catch(e => { if (e !== 'unauthorized') alert(e); });
}

function disconnectClient(portIdx, srvIdx, conIdx) {
  // Stream auto-refreshes the connections list — no explicit reload here.
  api('DELETE', '/api/ports/' + portIdx + '/connections/' + srvIdx + '/' + conIdx)
    .catch(e => { if (e !== 'unauthorized') alert(e); });
}

// ===========================================================================
// Port editor (modal)
// ===========================================================================
function _buildConfigFromStatus(port) {
  const ser = port.serial || {};
  const cfg = { serial: {} };
  if (port.name) cfg.name = port.name;
  if (port.max_connections !== undefined) cfg.max_connections = port.max_connections;
  if (ser.match) cfg.serial.match = { ...ser.match };
  if (ser.port) cfg.serial.port = ser.port;
  if (ser.baudrate) cfg.serial.baudrate = ser.baudrate;
  if (ser.bytesize) cfg.serial.bytesize = ser.bytesize;
  if (ser.parity) cfg.serial.parity = ser.parity;
  if (ser.stopbits) cfg.serial.stopbits = ser.stopbits;
  cfg.servers = (port.servers || []).map(s => {
    const srv = { protocol: s.protocol.toLowerCase() };
    if (s.data === false) srv.data = false;
    if (s.protocol === 'WEBSOCKET') {
      if (s.endpoint) srv.endpoint = s.endpoint;
      if (s.token) srv.token = s.token;
    } else {
      srv.address = s.address;
      if (s.port !== undefined) srv.port = s.port;
      if (s.ssl) srv.ssl = s.ssl;
    }
    if (s.control) srv.control = s.control;
    if (s.allow) srv.allow = s.allow;
    if (s.deny) srv.deny = s.deny;
    if (s.max_connections !== undefined) srv.max_connections = s.max_connections;
    return srv;
  });
  if (!cfg.servers.length) {
    cfg.servers = [{protocol: 'tcp', address: '0.0.0.0', port: _nextFreePort()}];
  }
  return cfg;
}

function _nextFreePort(start) {
  const used = new Set(usedPorts.map(u => u.port));
  let p = start || 10001;
  while (used.has(p)) p++;
  return p;
}

function showPortEditor(index, query) {
  // Wait for portsStatus from the stream (e.g. when arriving via direct
  // hash like #/ports/3/edit before the snapshot lands).
  if (!portsStatus) {
    setTimeout(() => showPortEditor(index, query), 50);
    return;
  }

  let cfg;
  if (index !== null) {
    const port = portsStatus.ports[index];
    if (!port) return navigate('/ports');
    cfg = _buildConfigFromStatus(port);
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

  const form = _buildPortForm(cfg, index);
  const saveBtn = btn('Save', 'btn-primary',
    () => _savePortFromForm(form, index));
  const addSrvBtn = btn('+ Add Server', 'btn-accent btn-small',
    () => form.addServerBox());
  const cancelBtn = btn('Cancel', '', () => backToList());
  const footer = [];
  footer.push(addSrvBtn);
  footer.push(el('span', { class: 'footer-spacer' }));
  if (index !== null) {
    footer.push(btn('Delete', 'btn-danger',
      () => confirmDeletePort(index, cfg.name || ('Port ' + index))));
  }
  footer.push(cancelBtn);
  footer.push(saveBtn);

  openModal({
    title: index !== null ? 'Edit Port' : 'New Port',
    body: form.root,
    footer,
    wide: true,
  });
}

function _buildPortForm(cfg, editIndex) {
  const root = el('div');

  // Name
  const nameInput = el('input', { type: 'text', value: cfg.name || '' });

  // Port input with autocomplete from detected USB devices.
  const portInput = el('input', {
    type: 'text', value: cfg.serial.port || '',
  });
  const portWrap = attachAutocomplete(portInput,
    () => detectedPorts.map(p => ({
      value: p.device,
      hint: p.description || '',
    })));

  // Match section — one row per USB attribute (vid, pid, ...). Each input
  // has an autocomplete listing every distinct value for that attribute
  // currently seen on a connected device, with the device path as hint.
  const matchDiv = el('div');
  const matchInputs = {};
  const matchCheckboxes = {};
  MATCH_ATTRS.forEach(attr => {
    const row = el('div', { class: 'match-row' });
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
    row.appendChild(cb);
    row.appendChild(el('label', {}, attr));
    row.appendChild(inpWrap);
    matchDiv.appendChild(row);
  });

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
    placeholder: '0 (unlimited)',
    value: cfg.max_connections !== undefined ? String(cfg.max_connections) : '',
    title: 'Total clients across all servers on this port (0 = unlimited)',
  });

  // ----- Compose form -----
  root.appendChild(el('div', { class: 'section-title' }, 'Identity'));
  root.appendChild(formRow('Name', nameInput));

  root.appendChild(el('div', { class: 'section-title' }, 'Serial port'));
  root.appendChild(formRow('Device', portWrap));
  root.appendChild(el('div', { class: 'card-subtitle',
    style: 'margin:6px 0 4px;font-size:12px' },
    'or match by USB attributes:'));
  root.appendChild(matchDiv);

  root.appendChild(el('div', { class: 'section-title' }, 'Parameters'));
  root.appendChild(formRow('Baudrate', baudSel));
  root.appendChild(formRow('Data bits', byteSel));
  root.appendChild(formRow('Parity', paritySel));
  root.appendChild(formRow('Stop bits', stopSel));
  root.appendChild(formRow('Max clients', portMaxInput));

  root.appendChild(el('div', { class: 'section-title' }, 'Servers'));
  const serversDiv = el('div');
  root.appendChild(serversDiv);

  const serverBoxes = [];
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
    const sb = _buildServerBox(seed, () => {
      const idx = serverBoxes.indexOf(sb);
      if (idx >= 0) serverBoxes.splice(idx, 1);
      sb.box.remove();
      _refreshRemoveButtons();
    }, editIndex, () => serverBoxes);
    serverBoxes.push(sb);
    serversDiv.appendChild(sb.box);
    _refreshRemoveButtons();
  }
  function _refreshRemoveButtons() {
    serverBoxes.forEach(sb => {
      sb.removeBtn.disabled = serverBoxes.length <= 1;
    });
    serverBoxes.forEach(sb => sb.recheckConflicts && sb.recheckConflicts());
  }
  cfg.servers.forEach(s => addServerBox(s));

  updateMatchMode();

  return {
    root,
    addServerBox,
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

function _buildServerBox(srv, onRemove, editIndex, getAllBoxes) {
  const box = el('div', { class: 'server-box' });
  const removeBtn = el('button', {
    type: 'button', class: 'server-remove',
    title: 'Remove server',
    onclick: () => onRemove(),
  });
  removeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
    + '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14"/></svg>';
  box.appendChild(removeBtn);

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
  const wsGenBtn = el('button', { type: 'button', class: 'btn btn-small btn-accent',
    onclick: () => { wsTokenInput.value = crypto.randomUUID(); } }, 'Generate');
  const wsCopyBtn = el('button', { type: 'button', class: 'btn btn-small',
    onclick: () => {
      if (wsTokenInput.value) navigator.clipboard.writeText(wsTokenInput.value);
    } }, 'Copy');
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
  const addrRow = el('div', { class: 'form-row' }, addrLabel, addrInput);
  const portInput = el('input', {
    type: 'number', value: srv.port !== undefined ? String(srv.port) : '',
  });
  const portRow = formRow('Port', portInput);

  // SSL fields
  const ssl = srv.ssl || {};
  const certInput = el('input', { type: 'text', value: ssl.certfile || '' });
  const keyInput = el('input', { type: 'text', value: ssl.keyfile || '' });
  const caInput = el('input', { type: 'text', value: ssl.ca_certs || '' });
  const sslDiv = el('div', { class: 'subgroup' },
    formRow('Certfile', certInput),
    formRow('Keyfile', keyInput),
    formRow('CA certs', caInput));

  // Control fields
  const ctlEnableCb = el('input', { type: 'checkbox', checked: !!srv.control });
  const ctlEnableRow = el('div', { style: 'margin-bottom:6px' },
    el('label', { class: 'checkbox-label' }, ctlEnableCb,
      el('span', {}, ' Control protocol')));
  const dataCb = el('input', { type: 'checkbox', checked: srv.data !== false });
  const dataRow = el('div', { style: 'margin-bottom:6px' },
    el('label', { class: 'checkbox-label' }, dataCb,
      el('span', {}, ' Forward serial data')));

  const writeRowCbs = {};
  const writeLabels = ['rts', 'dtr'].map(sig => {
    const cb = el('input', {
      type: 'checkbox',
      checked: !!(srv.control && srv.control[sig]),
    });
    writeRowCbs[sig] = cb;
    return el('label', { class: 'checkbox-label', style: 'margin-right:10px' },
      cb, el('span', {}, ' ' + sig.toUpperCase()));
  });
  const writeRow = formRow('Allow set', writeLabels);

  const reportCbs = {};
  const reportLabels = CONTROL_SIGNALS.map(sig => {
    const cb = el('input', {
      type: 'checkbox',
      checked: !!(srv.control && (srv.control.signals || []).includes(sig)),
    });
    reportCbs[sig] = cb;
    return el('label', { class: 'checkbox-label', style: 'margin-right:10px' },
      cb, el('span', {}, ' ' + sig.toUpperCase()));
  });
  const reportRow = formRow('Report', reportLabels);

  const pollSel = el('select', { style: 'flex:0 0 auto;width:8em' });
  const pollOptions = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000];
  const curPoll = srv.control
    ? Math.round((srv.control.poll_interval || 0.1) * 1000) : 100;
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
    dataRow, ctlDescEl, ctlMoreDetails, writeRow, reportRow, pollRow);
  const ctlDiv = el('div', {}, ctlEnableRow, ctlDetails);
  ctlEnableCb.onchange = () => {
    ctlDetails.classList.toggle('hidden', !ctlEnableCb.checked);
  };
  if (!ctlEnableCb.checked) ctlDetails.classList.add('hidden');

  // IP filter
  const allowInput = el('input', {
    type: 'text', placeholder: '192.168.1.0/24, 10.0.0.5',
    value: (srv.allow || []).join(', '),
  });
  const denyInput = el('input', {
    type: 'text', placeholder: '192.168.1.100',
    value: (srv.deny || []).join(', '),
  });
  const ipDiv = el('div', { class: 'subgroup' },
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

  box.appendChild(formRow('Protocol', protoSel));
  box.appendChild(wsRows);
  box.appendChild(addrRow);
  box.appendChild(portRow);
  box.appendChild(sslDiv);
  box.appendChild(ctlDiv);
  box.appendChild(ipDiv);
  box.appendChild(maxConnRow);

  function updateProtoFields() {
    const proto = protoSel.value;
    const isSocket = proto === 'SOCKET';
    const isSsl = proto === 'SSL';
    const isTelnet = proto === 'TELNET';
    const isWs = proto === 'WEBSOCKET';
    wsRows.classList.toggle('hidden', !isWs);
    addrRow.classList.toggle('hidden', isWs);
    portRow.classList.toggle('hidden', isWs || isSocket);
    addrLabel.textContent = isSocket ? 'Path' : 'Address';
    sslDiv.classList.toggle('hidden', !isSsl);
    ctlDiv.classList.toggle('hidden', isTelnet);
    ipDiv.classList.toggle('hidden', isSocket);
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
        u.endpoint === ep && u.index !== editIndex);
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
        ? 'Endpoint used by Port ' + epConflict.index
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
      u.port === p && u.address === addr && u.index !== editIndex);
    portInput.classList.toggle('field-error', !!conflict);
    portInput.title = conflict
      ? 'Port already used by Port ' + conflict.index : '';
  }

  protoSel.onchange = () => { updateProtoFields(); recheckConflicts(); };
  portInput.oninput = recheckConflicts;
  addrInput.oninput = recheckConflicts;
  wsEndpointInput.oninput = recheckConflicts;
  updateProtoFields();
  recheckConflicts();

  return {
    box, removeBtn, recheckConflicts,
    boxData: {
      get proto() { return protoSel.value; },
      protoSel, addrInput, portInput, epInput: wsEndpointInput,
      tokenInput: wsTokenInput, certInput, keyInput, caInput,
      ctlEnableCb, dataCb, writeRowCbs, reportCbs, pollSel,
      allowInput, denyInput, maxConnInput,
    },
  };
}

function _collectPortConfig(form) {
  const cfg = { serial: {}, servers: [] };
  const name = form.nameInput.value.trim();
  if (name) cfg.name = name;
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
    const srv = { protocol: proto };
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
    if (proto === 'ssl') {
      const ssl = {};
      const cf = d.certInput.value.trim();
      const kf = d.keyInput.value.trim();
      const ca = d.caInput.value.trim();
      if (cf) ssl.certfile = cf;
      if (kf) ssl.keyfile = kf;
      if (ca) ssl.ca_certs = ca;
      if (Object.keys(ssl).length) srv.ssl = ssl;
    }
    if (proto !== 'telnet' && d.ctlEnableCb.checked) {
      if (!d.dataCb.checked) srv.data = false;
      const ctl = {};
      ['rts', 'dtr'].forEach(sig => {
        if (d.writeRowCbs[sig].checked) ctl[sig] = true;
      });
      const signals = [];
      CONTROL_SIGNALS.forEach(sig => {
        if (d.reportCbs[sig].checked) signals.push(sig);
      });
      if (signals.length) ctl.signals = signals;
      const pollMs = parseInt(d.pollSel.value);
      if (pollMs) ctl.poll_interval = pollMs / 1000;
      srv.control = ctl;
    }
    if (proto !== 'socket') {
      const allow = d.allowInput.value.trim();
      if (allow) srv.allow = allow.split(',').map(s => s.trim()).filter(Boolean);
      const deny = d.denyInput.value.trim();
      if (deny) srv.deny = deny.split(',').map(s => s.trim()).filter(Boolean);
    }
    const mc = d.maxConnInput.value.trim();
    if (mc !== '') srv.max_connections = parseInt(mc);
    cfg.servers.push(srv);
  });
  return cfg;
}

function _savePortFromForm(form, index) {
  const cfg = _collectPortConfig(form);
  const method = index !== null ? 'PUT' : 'POST';
  const path = index !== null ? '/api/ports/' + index : '/api/ports';
  api(method, path, cfg)
    .then(() => navigate('/ports'))
    .catch(e => { if (e !== 'unauthorized') modalError(String(e)); });
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

function showUsers() {
  if (!isAdmin) { navigate('/ports'); return; }
  show('users-view');
  loadUsers();
}

function loadUsers() {
  Promise.all([
    api('GET', '/api/users').catch(() => []),
    api('GET', '/api/tokens').catch(() => []),
  ]).then(([users, tokens]) => {
    currentUsers = users;
    currentTokens = tokens;
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
      navigator.clipboard.writeText(tok.token).then(() => {
        tv.classList.add('copied');
        setTimeout(() => tv.classList.remove('copied'), 1000);
      });
    },
  }, masked);
  card.appendChild(tv);
  return card;
}

function confirmDeleteUser(login) {
  if (!confirm('Delete user "' + login + '"?')) return;
  api('DELETE', '/api/users/' + encodeURIComponent(login)).then(() => {
    if (login === username) setCredentials(null, null);
    navigate('/users');
  }).catch(e => { if (e !== 'unauthorized') alert(e); });
}

function confirmDeleteToken(tokenId) {
  if (!confirm('Delete this token?')) return;
  api('DELETE', '/api/tokens/' + encodeURIComponent(tokenId))
    .then(() => navigate('/users'))
    .catch(e => { if (e !== 'unauthorized') alert(e); });
}

// ----- User editor -----
function showUserEditor(login) {
  if (!isAdmin) { navigate('/users'); return; }
  // We may not have currentUsers loaded if entered via direct hash
  if (login !== null && !currentUsers.length) {
    loadUsers();
    setTimeout(() => showUserEditor(login), 50);
    return;
  }
  const isNew = login === null;
  const user = isNew ? {} : currentUsers.find(u => u.login === login) || {};
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
  if (tokenId !== null && !currentTokens.length) {
    loadUsers();
    setTimeout(() => showTokenEditor(tokenId), 50);
    return;
  }
  const isNew = tokenId === null;
  const tok = isNew ? {} : currentTokens.find(t => t.token === tokenId) || {};
  const tokenValue = tok.token || crypto.randomUUID();

  const nameInput = el('input', { type: 'text', value: tok.name || '',
    autocomplete: 'off' });
  const tokenInput = el('input', { type: 'text', value: tokenValue,
    autocomplete: 'off' });
  const genBtn = btn('Generate', 'btn-accent btn-small',
    () => { tokenInput.value = crypto.randomUUID(); });
  const copyBtn = btn('Copy', 'btn-small', () => {
    navigator.clipboard.writeText(tokenInput.value).then(() => {
      copyBtn.textContent = 'Copied';
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
  loadSettings();
}

function loadSettings() {
  api('GET', '/api/settings').then(data => {
    currentSettings = data;
    renderSettingsActions();
    renderSettingsList();
  }).catch(e => { if (e !== 'unauthorized') console.error(e); });
}

function renderSettingsActions() {
  const c = $('settings-actions');
  c.innerHTML = '';
  if (isAdmin) {
    c.appendChild(btn('+ Add HTTP Server', 'btn-primary btn-small',
      () => navigate('/settings/http/new')));
  }
}

function renderSettingsList() {
  const root = $('settings-content');
  root.innerHTML = '';

  // Session card
  const sessionCard = el('div', { class: 'card card-online' });
  const sessionHeader = el('div', { class: 'card-header-row' },
    el('span', { class: 'card-title' }, 'Session'));
  if (isAdmin) {
    sessionHeader.appendChild(kebabMenu([
      { label: 'Edit', cls: 'btn-accent',
        onclick: () => navigate('/settings/session') },
    ]));
  }
  sessionCard.appendChild(sessionHeader);
  const t = currentSettings.session_timeout;
  sessionCard.appendChild(el('div', { class: 'card-meta' },
    el('div', { class: 'card-meta-row' },
      el('span', { class: 'card-meta-label' }, 'Timeout'),
      el('span', {}, t != null ? t + ' s' : 'default'))));

  root.appendChild(el('div', { class: 'section-title' }, 'Session'));
  const sessGrid = el('div', { class: 'card-grid' });
  sessGrid.appendChild(sessionCard);
  root.appendChild(sessGrid);

  const servers = currentSettings.http || [];
  root.appendChild(el('div', { class: 'section-title' }, 'HTTP servers'));
  if (!servers.length) {
    root.appendChild(el('p', { class: 'empty' }, 'No HTTP servers configured'));
  } else {
    const grid = el('div', { class: 'card-grid' });
    servers.forEach((s, i) => grid.appendChild(renderHttpCard(s, i)));
    root.appendChild(grid);
  }
}

function renderHttpCard(srv, index) {
  const card = el('div', { class: 'card card-online' });
  const ssl = srv.ssl ? ' (SSL)' : '';
  const titleText = srv.name || `${srv.address || '0.0.0.0'}:${srv.port}${ssl}`;
  const title = el('span', { class: 'card-title' }, titleText);
  const headerRow = el('div', { class: 'card-header-row' }, title);
  if (isAdmin) {
    headerRow.appendChild(kebabMenu([
      { label: 'Edit', cls: 'btn-accent',
        onclick: () => navigate('/settings/http/' + index + '/edit') },
      { label: 'Delete', cls: 'btn-danger',
        onclick: () => confirmDeleteHttp(index) },
    ]));
  }
  card.appendChild(headerRow);
  const meta = el('div', { class: 'card-meta' });
  meta.appendChild(el('div', { class: 'card-meta-row' },
    el('span', { class: 'card-meta-label' }, 'Listen'),
    el('span', {}, `${srv.address || '0.0.0.0'}:${srv.port}${ssl}`)));
  if (srv.ssl) {
    meta.appendChild(el('div', { class: 'card-meta-row' },
      el('span', { class: 'card-meta-label' }, 'Cert'),
      el('span', {}, srv.ssl.certfile || '-')));
  }
  card.appendChild(meta);
  return card;
}

function showSessionEditor() {
  if (!isAdmin) { navigate('/settings'); return; }
  if (!currentSettings) {
    loadSettings();
    setTimeout(() => showSessionEditor(), 50);
    return;
  }
  const t = currentSettings.session_timeout;
  const input = el('input', {
    type: 'number', min: '0', placeholder: '3600',
    value: t || '',
  });
  openModal({
    title: 'Session settings',
    body: el('div', {}, formGroup('Timeout (seconds)', input,
      { hint: 'Leave empty for default (3600 s).' })),
    footer: [
      btn('Cancel', '', () => backToList()),
      btn('Save', 'btn-primary', () => {
        const val = input.value.trim();
        const num = val === '' ? null : parseInt(val);
        if (val !== '' && (isNaN(num) || num < 0)) {
          return modalError('Invalid timeout value');
        }
        api('PUT', '/api/settings', { session_timeout: num })
          .then(() => navigate('/settings'))
          .catch(e => modalError(String(e)));
      }),
    ],
  });
}

function showHttpEditor(index) {
  if (!isAdmin) { navigate('/settings'); return; }
  if (!currentSettings) {
    loadSettings();
    setTimeout(() => showHttpEditor(index), 50);
    return;
  }
  const isNew = index === null;
  const srv = isNew ? { address: '0.0.0.0', port: 8080 }
                    : (currentSettings.http || [])[index] || {};

  const nameInput = el('input', { type: 'text', value: srv.name || '',
    placeholder: 'optional' });
  const addrInput = el('input', { type: 'text',
    value: srv.address || '0.0.0.0', placeholder: '0.0.0.0' });
  const portInput = el('input', { type: 'number',
    value: String(srv.port || 8080), min: '1', max: '65535' });
  const sslCb = el('input', { type: 'checkbox', checked: !!srv.ssl });
  const certInput = el('input', { type: 'text',
    value: (srv.ssl && srv.ssl.certfile) || '',
    placeholder: '/path/to/cert.pem' });
  const keyInput = el('input', { type: 'text',
    value: (srv.ssl && srv.ssl.keyfile) || '',
    placeholder: '/path/to/key.pem' });
  const sslDiv = el('div', { class: 'subgroup' },
    formRow('Cert', certInput),
    formRow('Key', keyInput));
  if (!sslCb.checked) sslDiv.classList.add('hidden');
  sslCb.onchange = () => sslDiv.classList.toggle('hidden', !sslCb.checked);

  const body = el('div',
    {},
    formRow('Name', nameInput),
    formRow('Address', addrInput),
    formRow('Port', portInput),
    el('div', { style: 'margin:8px 0' },
      el('label', { class: 'checkbox-label' },
        sslCb, el('span', {}, ' SSL'))),
    sslDiv);

  openModal({
    title: isNew ? 'New HTTP server' : 'Edit HTTP server',
    body,
    footer: [
      !isNew ? btn('Delete', 'btn-danger',
        () => confirmDeleteHttp(index)) : null,
      el('span', { class: 'footer-spacer' }),
      btn('Cancel', '', () => backToList()),
      btn('Save', 'btn-primary',
        () => _saveHttpServer(isNew ? null : index,
          { nameInput, addrInput, portInput, sslCb, certInput, keyInput })),
    ].filter(Boolean),
  });
}

function _saveHttpServer(index, fields) {
  const data = {
    address: fields.addrInput.value.trim() || '0.0.0.0',
    port: parseInt(fields.portInput.value) || 8080,
  };
  const name = fields.nameInput.value.trim();
  if (name) data.name = name;
  if (fields.sslCb.checked) {
    const cf = fields.certInput.value.trim();
    const kf = fields.keyInput.value.trim();
    if (!cf || !kf) return modalError('SSL requires certificate and key file paths');
    data.ssl = { certfile: cf, keyfile: kf };
  }
  const method = index === null ? 'POST' : 'PUT';
  const path = index === null
    ? '/api/settings/http' : '/api/settings/http/' + index;
  api(method, path, data)
    .then(() => navigate('/settings'))
    .catch(e => modalError(String(e)));
}

function confirmDeleteHttp(index) {
  if (!confirm('Delete this HTTP server?')) return;
  api('DELETE', '/api/settings/http/' + index)
    .then(() => navigate('/settings'))
    .catch(e => { if (e !== 'unauthorized') alert(e); });
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
