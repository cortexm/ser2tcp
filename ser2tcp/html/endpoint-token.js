// The credential a terminal page runs on, and where it is allowed to
// come from. Shared by /xterm/ and /raw/ rather than written twice:
// which storage holds what is the whole security argument here, and two
// copies of it would be two chances to get it wrong.
//
// A shared link ("here is our device, try it") carries the endpoint's
// own token in the URL *fragment*. The fragment is the point:
//
//   - it is never sent to the server, so the token stays out of our
//     request log, out of whatever a reverse proxy in front of us
//     writes down, and out of the Referer header that the page's
//     subresources carry;
//   - it grants that one endpoint, as much of it as the server's
//     `access` says and nothing more. A session token would be an
//     account, which is not a thing to paste into a chat.
//
// The WebSocket URL itself still takes `?token=` — a device or a wscat
// has no fragment to read — and a query is something servers log. That
// is the trade, and it is why the link handed to a person is not the
// same string as the URL handed to a machine.
//
// sessionStorage, never localStorage: localStorage holds the SPA's
// session and is shared by every page of this origin, indefinitely. A
// shared endpoint token belongs to the tab somebody was handed — it
// survives a reload, and it is gone when the tab is.
function endpointToken(name) {
  const key = 'ser2tcp_ep_token:' + name;
  const fromUrl = _tokenFromHash();
  if (fromUrl) {
    _sessionSet(key, fromUrl);
    // Out of the address bar once it is ours: there it would only sit
    // and be copied by accident, into a screenshot or a second chat.
    // Losing it here is why it went to sessionStorage first.
    try {
      history.replaceState(null, '', location.pathname + location.search);
    } catch (e) {
      // Not fatal - the token is simply still on screen.
    }
    return fromUrl;
  }
  // An explicit link beats an ambient session, so this is asked second:
  // it says which credential was meant.
  return _sessionGet(key) || _localGet('ser2tcp_token');
}

// A share link pasted into a tab that is already on this page is a
// same-document navigation: nothing reloads, and the token would sit in
// the address bar doing nothing while the page keeps failing to
// connect. Start over instead - the fresh load reads it the usual way.
window.addEventListener('hashchange', () => {
  if (_tokenFromHash()) location.reload();
});

function _tokenFromHash() {
  if (location.hash.length < 2) return null;
  try {
    return new URLSearchParams(location.hash.slice(1)).get('token');
  } catch (e) {
    return null;
  }
}

// Every storage read and write is guarded: a browser in private mode
// throws on the accessor itself, and a terminal that cannot open
// because of that is worse than one that forgets a token on reload.
function _sessionGet(key) {
  try {
    return sessionStorage.getItem(key);
  } catch (e) {
    return null;
  }
}

function _sessionSet(key, value) {
  try {
    sessionStorage.setItem(key, value);
  } catch (e) {
    // Nothing to do - the page keeps the value in memory anyway.
  }
}

function _localGet(key) {
  try {
    return localStorage.getItem(key);
  } catch (e) {
    return null;
  }
}
