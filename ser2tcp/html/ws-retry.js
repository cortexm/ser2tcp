// Picking a dropped WebSocket back up, without turning a refusal into a
// drumbeat. Shared by the terminal, raw and monitor pages, which all
// want the same thing and would otherwise want it three slightly
// different ways.
//
// A link that once opened and then went - the server restarted, the
// laptop slept, the network blinked - is worth trying again on a clock:
// `visibilitychange` only fires when somebody looks, and the whole
// point of leaving a terminal open on a device that is not plugged in
// yet is that nobody is looking.
//
// A link that never opened at all is a different answer: a token that
// is wrong, an endpoint that is not there. Retrying that every two
// seconds for ever is exactly what the old NDJSON status stream did -
// it said "connecting" for as long as the tab stayed open and never
// said why. So the clock starts only once a connection has succeeded,
// and it backs off after that: a server that is down stays down for a
// while, and asking every second is louder, not faster.
//
// The device itself needs nothing here. The server holds the WebSocket
// open across a device going away and reopens the port for as long as
// somebody is attached - this is only about the link to the server.
function wsRetry(reconnect, minDelay, maxDelay) {
  const min = minDelay || 1000;
  const max = maxDelay || 15000;
  let timer = null;
  let delay = min;
  let everOpened = false;

  return {
    // From onopen: this is what earns the page a retry at all, and it
    // starts the backoff over - the next outage is a fresh one.
    succeeded() {
      everOpened = true;
      delay = min;
    },
    // From onclose. `wanted` is false where the page has a Disconnect
    // button and the user pressed it: a link somebody hung up stays
    // hung up. `ev` is the close event: 4404 is the server saying the
    // port was deleted, and knocking on it with backoff for as long as
    // the tab stays open would be the old drumbeat again.
    schedule(wanted, ev) {
      if (timer || wanted === false || !everOpened) return;
      if (ev && ev.code === 4404) return;
      const wait = delay;
      delay = Math.min(delay * 2, max);
      timer = setTimeout(() => {
        timer = null;
        reconnect();
      }, wait);
    },
    // From Disconnect, and from anything that reconnects on its own
    // (coming back into view, the network returning) - there the wait
    // is over, whatever the clock says.
    cancel() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      delay = min;
    },
  };
}

// What to tell the person when the server closed the link, or null
// where it said nothing - a dropped network, or the page hanging up
// itself. The server names why where it knows: 'Reconfigured' when the
// port or its server was just changed and is already back, 'Port
// removed', 'Server shutting down'. 1012 is its way of saying "come
// back", and the page does, so it says that too.
function wsCloseText(ev) {
  if (!ev || !ev.reason) return null;
  if (ev.code === 1012) return ev.reason + ' - reconnecting';
  return 'Disconnected: ' + ev.reason;
}
