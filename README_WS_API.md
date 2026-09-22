# ser2tcp WebSocket API

Two WebSocket routes, one message format:

| Route | What it is | Holds the port open |
|-------|------------|---------------------|
| `/ws/<endpoint>` | A way **into** the serial port. Configured; a port may have several, with different tokens and permissions | Yes, while attached |
| `/ws/monitor/<port-name>` | A way to **watch** the port. Automatic for every named port, read-only | No |

Both carry everything a client needs, so nothing has to be polled
alongside them.

Authentication is the per-server `token` or a user session — see
**HTTP authentication** in the main [README](README.md).

## Frames

| Frame | Meaning |
|-------|---------|
| **Binary** | Serial data. On an endpoint, the bytes as they are. On the monitor, one byte first saying **who produced them** — see below |
| **Text** | One JSON object |

Serial data stays binary: a WebSocket already has two frame types, and
wrapping bytes in JSON would cost size and CPU for nothing.

**A text frame carries as many topics as it likes.** Each top-level key
is one topic; a frame can update several at once, and often does. There
is no separate "hello" message — the first frame simply carries every
key, and later ones carry only what changed. A client that handles a
frame by looking at the keys it cares about needs no other rule:

```javascript
if (msg.serial)  setDeviceState(msg.serial.connected);
if (msg.signals) updateSignals(msg.signals);
```

Unknown keys are ignored, which is what makes the format extensible.

### The monitor's first byte

Every binary frame on `/ws/monitor/<port-name>` starts with one byte
naming its source:

| Byte | Source |
|------|--------|
| `0` | The **device** — everything it sent |
| `1`–`254` | A **client**, by its `slot` (see `peers`) |
| `255` | A client with no slot left, when more than 254 are connected |

So a monitor can colour each client's traffic separately and say who
wrote what, which a plain "to the device / from the device" byte cannot.
A client that does not care may simply treat anything non-zero as "some
client wrote this".

Slots are **reused**: when a client leaves, its number becomes available
to the next one. A slot therefore only means anything together with the
most recent `peers` frame. Both travel over the same WebSocket and are
therefore ordered, so everything before a `peer_disconnected` belongs to
the old holder and everything after a `peer_connected` to the new one —
a client that re-reads `peers` on every change never has to guess.

## Server → client

The first frame, on an endpoint:

```json
{
  "port": {"name": "esp32", "device": "/dev/ttyUSB0", "baudrate": 115200},
  "can": {"read": true, "write": true, "signals": ["rts"], "attach": true},
  "serial": {"connected": true},
  "signals": {"rts": true, "cts": false, "dsr": true},
  "attach": true
}
```

### `port`

What is on the other end. Sent once; changes if the port is
reconfigured.

### `can`

What **this** client may do, after the server's `access` and `control`
configuration and the client's credentials have been applied. Offer only
what it allows — asking for anything else is answered with `error`.

`can.signals` is the set this client may **set**. It is not the same as
the `signals` object, which is the set being **reported**: a line can be
reported without being settable.

On the monitor, `write` and `attach` are both false and `signals` is
empty — it watches, and holds nothing open.

### `serial`

The device appeared or went away. **The WebSocket is not closed** — it
stays open and tells you, which is the whole point. Anything written to
a device that is gone is discarded.

```json
{"serial": {"connected": false, "reason": "device disappeared"}}
```

```json
{"serial": {"connected": true}}
```

Clients on TCP, TELNET, SSL and Unix sockets are disconnected instead,
because those protocols have no channel to be told on. The monitor sees
that as a peer disconnecting.

Connecting to an endpoint whose device is not there is **not** refused,
for the same reason: the first frame says `{"connected": false}` and the
server keeps trying to open the port for as long as somebody is
attached. A client that wants the device the moment it appears has only
to stay.

### `signals`

Only the lines that changed:

```json
{"signals": {"rts": false}}
```

Which lines are reported is set by the server's `control.signals`
configuration. **If the first frame has no `signals` key, nothing is
being reported** — a client should then show no signal indicators at
all. Sent in monitor mode too.

### `peers`

Who else is on this serial port, across every protocol. **Monitor only.**

```json
{
  "peers": [
    {"slot": 1, "id": "a1b2c3d4", "protocol": "tcp",
     "address": "192.168.1.50", "port": 51234},
    {"slot": 2, "id": "9f3c1a20", "protocol": "websocket",
     "address": "192.168.1.9", "port": 51500,
     "socket": "10.0.0.1:443", "forwarded": ["192.168.1.9", "10.0.0.1"]}
  ]
}
```

- `slot` — the number this client's bytes carry in binary frames. The
  lowest one free is handed out, and it is released when the client
  leaves, so it identifies a client only for as long as that client is
  in the list
- `address` / `port` — the client, as well as it can be known
- `socket` — what actually connected, when it differs: behind a reverse
  proxy this is the proxy. Only on WebSocket peers
- `forwarded` — the `X-Forwarded-For` chain, when the connection came
  through a trusted proxy. Only on WebSocket peers
- `id` — the same connection id `/api/status` reports, so the two can be
  matched up. Unlike `slot`, it is never reused

A change adds `peer_connected` or `peer_disconnected` alongside the
list, so an event can be logged and the list rendered from one frame:

```json
{
  "peer_connected": {"slot": 3, "id": "7d41e8b2", "protocol": "ssl",
                     "address": "10.0.0.7", "port": 40112},
  "peers": [
    {"slot": 1, "id": "a1b2c3d4", "protocol": "tcp",
     "address": "192.168.1.50", "port": 51234},
    {"slot": 3, "id": "7d41e8b2", "protocol": "ssl",
     "address": "10.0.0.7", "port": 40112}
  ]
}
```

This matters more than it looks: a serial port is shared. Everything the
device says goes to **every** reader, and anything any writer sends goes
to the device — so knowing somebody else is on the line changes how you
read what you see.

### `error`

A request could not be carried out. Nothing is ever refused silently.

```json
{"error": {"request": "signals", "reason": "rts is not settable here"}}
```

It can ride along with whatever else the frame carries.

## Client → server

Only on `/ws/<endpoint>`. The monitor ignores anything sent to it.

### `signals` — set a line

```json
{"signals": {"rts": false}}
```

The same key and shape the server uses to report them: the client says
what it wants, the server answers with what it got. A successful change
comes back as a `signals` frame carrying the line that actually moved.

### `attach` — hold the port, or let go

```json
{"attach": false}
```

A client is attached when it connects, so a client that only wants data
never has to send anything. Detaching keeps the WebSocket open and the
`serial`, `signals` and `error` frames coming — it only gives up the
data and the claim on the port. The server confirms with `{"attach":
false}`, and the port closes if nobody else holds it.

For compatibility with earlier versions, `{"rts": true}` and
`{"dtr": false}` are still accepted at the top level.

## Reading and writing

A server entry's `access` decides the direction, and `can` reports the
result:

| `access` | receives data | may write | `can` |
|----------|---------------|-----------|-------|
| `"rw"` *(default)* | yes | yes | `read: true, write: true` |
| `"ro"` | yes | no | `read: true, write: false` |
| `"wo"` | no | yes | `read: false, write: true` |
| `"none"` | no | no | `read: false, write: false` |

`"none"` with `control` configured is a signal-only endpoint. The older
`"data": false` means the same thing and is still accepted.

An `"ro"` endpoint is not the same as the monitor: it sees only what the
device sends, and it holds the port open. The monitor sees both
directions and holds nothing.

## Example

```javascript
const ws = new WebSocket('wss://host/ws/esp32?token=…');
ws.binaryType = 'arraybuffer';

ws.onmessage = (event) => {
  if (typeof event.data !== 'string') {
    return showSerialData(new Uint8Array(event.data));
  }
  const msg = JSON.parse(event.data);
  if (msg.port)    describePort(msg.port);
  if (msg.can)     enableControls(msg.can);
  if (msg.serial)  setDeviceState(msg.serial.connected);
  if (msg.signals) updateSignals(msg.signals);
  if (msg.peers)   showPeers(msg.peers);
  if (msg.error)   console.warn(msg.error.request, msg.error.reason);
};

ws.send(new Uint8Array([0x41, 0x42]));                 // to the device
ws.send(JSON.stringify({signals: {rts: false}}));      // pull RTS low
ws.send(JSON.stringify({attach: false}));              // watch, don't hold
```

The first frame sets everything up, so the same handler covers the
opening state and every change after it.

## Keeping the connection open

An idle WebSocket is closed by the server after its keep-alive timeout.
A client with nothing to send should send an empty text frame well
inside it.

Browsers throttle timers in background tabs, so a page that pings on an
interval will eventually be closed while it is not visible. Reconnect
when the page becomes visible again rather than relying on the timer.
