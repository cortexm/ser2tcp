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
  [/^\/ports\/(\d+)\/edit$/,       m => showPortEditor(parseInt(m[1]), m._q)],
  [/^\/detected$/,                 () => showDetected()],
  [/^\/users$/,                    () => showUsers()],
  [/^\/users\/new$/,               () => showUserEditor(null)],
  [/^\/users\/([^/]+)\/edit$/,     m => showUserEditor(decodeURIComponent(m[1]))],
  [/^\/tokens\/new$/,              () => showTokenEditor(null)],
  [/^\/tokens\/([^/]+)\/edit$/,    m => showTokenEditor(decodeURIComponent(m[1]))],
  [/^\/settings$/,                 () => showSettings()],
  [/^\/settings\/session$/,        () => showSessionEditor()],
  [/^\/settings\/http\/new$/,      () => showHttpEditor(null)],
  [/^\/settings\/http\/(\d+)\/edit$/, m => showHttpEditor(parseInt(m[1]))],
  [/^\/certificates$/,             () => showCertificates()],
  [/^\/certificates\/new$/,        () => showCertEditor(null)],
  [/^\/certificates\/generate$/,   () => showCertGenerator()],
  [/^\/certificates\/([^/]+)$/,    m => showCertEditor(decodeURIComponent(m[1]))],
  [/^\/certificates\/([^/]+)\/paste\/(cert\.pem|key\.pem|ca\.pem)$/,
    m => _showPasteCertModal(decodeURIComponent(m[1]), m[2])],
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
  // No "connected"/"disconnected" here: the card's colour is the answer
  // (green connected, blue idle but ready, red device missing) and the
  // word only repeated it.
  if (subParts.length) {
    card.appendChild(
      el('div', { class: 'card-subtitle' }, subParts.join(' — ')));
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
  // Load cert bundles fresh — needed for SSL server config.
  api('GET', '/api/certs').then(data => {
    _showPortEditorWithBundles(index, query, data.bundles || []);
  }).catch(e => {
    if (e !== 'unauthorized') alert(String(e));
  });
}

function _showPortEditorWithBundles(index, query, bundles) {
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

  const form = _buildPortForm(cfg, index, bundles);
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

function _buildPortForm(cfg, editIndex, bundles) {
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
    }, editIndex, () => serverBoxes, bundles);
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

function _buildServerBox(srv, onRemove, editIndex, getAllBoxes, bundles) {
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

  // SSL fields: bundle dropdown + mTLS toggle. Built via shared helper
  // so port-SSL and HTTPS editors stay consistent. We always want the
  // ssl block when protocol=SSL, so we hide the SSL-enable checkbox the
  // helper exposes and treat the bundle as required.
  const ssl = _buildSslFields(srv.ssl, bundles || []);
  ssl.sslCb.checked = true;
  ssl.sslCb.style.display = 'none';
  ssl.wrap.classList.remove('hidden');
  const sslDiv = ssl.wrap;

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
      tokenInput: wsTokenInput, ssl,
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
      const sslVal = d.ssl.getValue();
      if (!sslVal) throw new Error('SSL server requires a bundle');
      srv.ssl = sslVal;
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
  let cfg;
  try { cfg = _collectPortConfig(form); }
  catch (e) { return modalError(e.message); }
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
      el('span', { class: 'card-meta-label' }, 'Bundle'),
      el('span', {}, srv.ssl.bundle || '-')));
    if (srv.ssl.require_client_cert) {
      meta.appendChild(el('div', { class: 'card-meta-row' },
        el('span', { class: 'card-meta-label' }, 'mTLS'),
        el('span', {}, 'required')));
    }
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

// Build the bundle dropdown + mTLS toggle used by both HTTP and port SSL
// editors. Returns {wrap, sslCb, getValue} — `wrap` is the div to insert,
// `sslCb` is the enable checkbox (caller decides where to put it),
// `getValue()` returns the ssl block or null.
function _buildSslFields(currentSsl, bundles) {
  const sslCb = el('input', { type: 'checkbox', checked: !!currentSsl });
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
  if (currentSsl && currentSsl.bundle) {
    bundleSelect.value = currentSsl.bundle;
  }
  const mtlsCb = el('input', { type: 'checkbox',
    checked: !!(currentSsl && currentSsl.require_client_cert) });
  const updateMtlsState = () => {
    const sel = bundleSelect.value;
    const b = bundles.find(x => x.name === sel);
    const hasCa = b && _certFilePresent(b, 'ca.pem');
    mtlsCb.disabled = !hasCa;
    if (!hasCa) mtlsCb.checked = false;
  };
  bundleSelect.onchange = updateMtlsState;
  updateMtlsState();
  const manageLink = el('a', {
    href: '#/certificates', onclick: () => closeModal(),
    style: 'font-size:12px;margin-left:8px',
  }, 'Manage certificates…');
  const sslDiv = el('div', { class: 'subgroup' },
    formRow('Bundle', [bundleSelect, manageLink]),
    el('div', { style: 'margin:6px 0 0 120px' },
      el('label', { class: 'checkbox-label' },
        mtlsCb, el('span', {}, ' Require client certificate (mTLS)'))));
  if (!sslCb.checked) sslDiv.classList.add('hidden');
  sslCb.onchange = () => sslDiv.classList.toggle('hidden', !sslCb.checked);

  function getValue() {
    if (!sslCb.checked) return null;
    const bundle = bundleSelect.value;
    if (!bundle) throw new Error('Select a certificate bundle');
    const out = { bundle };
    if (mtlsCb.checked) out.require_client_cert = true;
    return out;
  }
  return { wrap: sslDiv, sslCb, getValue };
}

function showHttpEditor(index) {
  if (!isAdmin) { navigate('/settings'); return; }
  if (!currentSettings) {
    loadSettings();
    setTimeout(() => showHttpEditor(index), 50);
    return;
  }
  // Fetch bundle list each time so newly created bundles show up.
  api('GET', '/api/certs').then(data => {
    _showHttpEditorWithBundles(index, data.bundles || []);
  }).catch(e => {
    if (e !== 'unauthorized') alert(String(e));
  });
}

function _showHttpEditorWithBundles(index, bundles) {
  const isNew = index === null;
  const srv = isNew ? { address: '0.0.0.0', port: 8080 }
                    : (currentSettings.http || [])[index] || {};

  const nameInput = el('input', { type: 'text', value: srv.name || '',
    placeholder: 'optional' });
  const addrInput = el('input', { type: 'text',
    value: srv.address || '0.0.0.0', placeholder: '0.0.0.0' });
  const portInput = el('input', { type: 'number',
    value: String(srv.port || 8080), min: '1', max: '65535' });
  const ssl = _buildSslFields(srv.ssl, bundles);

  const body = el('div',
    {},
    formRow('Name', nameInput),
    formRow('Address', addrInput),
    formRow('Port', portInput),
    el('div', { style: 'margin:8px 0' },
      el('label', { class: 'checkbox-label' },
        ssl.sslCb, el('span', {}, ' SSL'))),
    ssl.wrap);

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
          { nameInput, addrInput, portInput, ssl })),
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
  let sslVal;
  try { sslVal = fields.ssl.getValue(); }
  catch (e) { return modalError(e.message); }
  if (sslVal) data.ssl = sslVal;
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
// Certificates
// ===========================================================================
let currentCerts = null;  // {certs_dir, bundles: [...]}

const CERT_FILES = ['cert.pem', 'key.pem', 'ca.pem'];

function showCertificates() {
  if (!isAdmin) { navigate('/ports'); return; }
  show('certificates-view');
  loadCerts();
}

function loadCerts() {
  api('GET', '/api/certs').then(data => {
    currentCerts = data;
    renderCertsActions();
    renderCertsList();
  }).catch(e => { if (e !== 'unauthorized') console.error(e); });
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
  $('certificates-path').textContent =
    'Certificates stored in: ' + (currentCerts.certs_dir || '');
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
  return card;
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
    loadCerts();
    setTimeout(() => showCertEditor(name), 50);
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
  body.appendChild(el('div', {
    class: 'card-subtitle', style: 'margin-bottom:12px;word-break:break-all',
  }, 'Path: ' + info.path));

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
      actions.appendChild(btn('View / download', 'btn-small',
        () => _downloadCertFile(bundleName, fname)));
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

function _downloadCertFile(bundleName, fname) {
  api('GET', '/api/certs/' + encodeURIComponent(bundleName)
        + '/files/' + encodeURIComponent(fname))
    .then(data => {
      const blob = new Blob([data.content], { type: 'application/x-pem-file' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = bundleName + '-' + fname;
      a.click();
      URL.revokeObjectURL(url);
    })
    .catch(e => alert(String(e)));
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

function _showClientCertDownload(data) {
  // Present cert+key+ca as separate downloads. Combined ZIP would
  // require a JS library; three buttons is good enough and lets the
  // user pick what to install on the client.
  const downloadBtn = (label, filename, content) => btn(
    label, 'btn-accent btn-small',
    () => {
      const blob = new Blob([content], { type: 'application/x-pem-file' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    });
  const namePrefix = (data.cn || 'client').replace(/[^a-zA-Z0-9._-]/g, '_');
  const body = el('div', {},
    el('p', {},
      'Client certificate generated. Download the files and install '
      + 'them on the mTLS client. ',
      el('strong', {}, 'The private key is not stored on the server '),
      '— if you lose it, regenerate.'),
    el('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin:12px 0' },
      downloadBtn('cert.pem', namePrefix + '-cert.pem', data.cert_pem),
      downloadBtn('key.pem', namePrefix + '-key.pem', data.key_pem),
      downloadBtn('ca.pem', namePrefix + '-ca.pem', data.ca_pem)),
    el('p', { class: 'card-subtitle', style: 'font-size:12px' },
      'On the client, configure your TLS stack to present '
      + namePrefix + '-cert.pem + ' + namePrefix + '-key.pem '
      + 'and trust ' + namePrefix + '-ca.pem.'));
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
  const where = u.type === 'http'
    ? 'HTTPS ' + (u.name || '')
    : 'port ' + (u.port_name || '#' + u.port_index);
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
