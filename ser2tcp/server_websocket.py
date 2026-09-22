"""WebSocket virtual server - manages WS connections through HTTP server"""

import json as _json
import logging as _logging

import ser2tcp.connection_control as _control
import ser2tcp.ip_filter as _ip_filter
import ser2tcp.server as _server


class ServerWebSocket():
    """WebSocket virtual server for one endpoint.

    Not a real listener - connections come from HttpServerWrapper
    when a WebSocket upgrade request matches this endpoint.
    """

    def __init__(self, config, ser, log=None):
        self._log = log if log else _logging.Logger(self.__class__.__name__)
        self._config = config
        self._serial = ser
        self._protocol = 'WEBSOCKET'
        self._endpoint = config.get('endpoint')
        if not self._endpoint:
            raise _server.ConfigError(
                'WebSocket server requires endpoint')
        self._token = config.get('token')
        self._can_read, self._can_write = _server.parse_access(config)
        self._data_enabled = self._can_read or self._can_write
        self._control = config.get('control')
        if not self._data_enabled and not self._control:
            raise _server.ConfigError(
                'a WebSocket that neither reads nor writes requires '
                '"control" config')
        self._max_connections = config.get('max_connections', 0)
        # Parse control config
        self._ctl_rts = False
        self._ctl_dtr = False
        self._ctl_signals = ()
        if self._control:
            self._ctl_rts = bool(self._control.get('rts'))
            self._ctl_dtr = bool(self._control.get('dtr'))
            signals = self._control.get('signals', [])
            # Ordered, so a report reads the way the config was written.
            self._ctl_signals = tuple(
                dict.fromkeys(str(s).lower() for s in signals))
        self._ip_filter = _ip_filter.create_filter(config, log=log)
        self._connections = []
        # Who is actually using the device. A connection is a control
        # channel; being attached is what carries data and what holds
        # the port open. Kept as a separate list rather than a flag on
        # the client because uhttp owns those objects.
        self._attached = []
        # Clients already told their data is going nowhere. One answer
        # per reason is enough - see _refuse_write().
        self._write_refused = set()
        # The signal states every connection has been given, so a
        # report can carry the difference instead of the whole set.
        self._reported = None
        # Set while the serial port is behind and clients must wait.
        self._read_paused = False
        self._log.info(
            "  Server: /ws/%s WEBSOCKET", self._endpoint)

    @property
    def protocol(self):
        """Return protocol name"""
        return self._protocol

    @property
    def config(self):
        """Return server configuration"""
        return self._config

    @property
    def control(self):
        """Return control configuration or None"""
        return self._control

    @property
    def connections(self):
        """Every client on this endpoint, attached or not.

        This is what the status payload lists and what the disconnect
        endpoint looks through: a detached client is still a client and
        must not vanish from either.
        """
        return self._connections

    @property
    def attached(self):
        """The clients carrying data, and so holding the port open"""
        return self._attached

    @property
    def endpoint(self):
        """Return endpoint name"""
        return self._endpoint

    @property
    def token(self):
        """Return per-server token or None"""
        return self._token

    @property
    def data_enabled(self):
        """Return True if data moves in either direction"""
        return self._data_enabled

    @property
    def can_read(self):
        """Return True if the device's output reaches clients"""
        return self._can_read

    @property
    def can_write(self):
        """Return True if a client may send to the device"""
        return self._can_write

    @property
    def ip_filter(self):
        """Return IP filter or None"""
        return self._ip_filter

    @property
    def max_connections(self):
        """Return max connections limit (0 = unlimited)"""
        return self._max_connections

    def has_connections(self):
        """True while somebody is holding the port open.

        Asked by SerialProxy.disconnect() to decide whether the device
        may be closed, so it has to mean *attached* - a control channel
        with nobody listening to the device is not a reason to keep it
        open.
        """
        return bool(self._attached)

    def attach(self, client):
        """Start carrying data for this client, opening the port.

        Attaching means wanting the device, not having it: a device
        that will not open is not a refusal, it is a `serial` frame and
        a reopen to wait for. Only the port limit says no, and even
        then the client stays connected - its control channel is still
        good and it has to be able to be told why.
        """
        if client not in self._connections:
            return False
        if client in self._attached:
            return True
        if not self._serial.can_add_connection():
            self._log.info(
                "Attach refused (port limit): %s /ws/%s",
                self._client_addr(client), self._endpoint)
            return False
        self._serial.connect()
        self._attached.append(client)
        # Whatever it was told about its writes no longer holds.
        self._write_refused.discard(client)
        if self._read_paused:
            # Joined while the port is behind: wait like the others.
            self._apply_read_paused(client)
        return True

    def detach(self, client):
        """Stop carrying data for this client and let go of the port.

        The connection stays open. Releasing is only safe once the
        client is out of the list - SerialProxy.disconnect() closes the
        device when nobody holds it, and asking while this one still
        counted would keep it open forever.
        """
        if client not in self._attached:
            return
        self._attached.remove(client)
        self._serial.disconnect()

    def add_connection(self, client):
        """Add accepted WebSocket connection"""
        if self._max_connections > 0 and len(self._connections) >= self._max_connections:
            addr = self._client_addr(client)
            self._log.info(
                "Client rejected (server limit): %s WEBSOCKET", addr)
            client.ws_close(1013, 'Server limit reached')
            return
        # Attached on arrival, so a client that only wants data still
        # has to do nothing - the change is what it may do afterwards.
        # A device that will not open no longer refuses it: the
        # greeting says so, and the port is retried while it waits.
        self._connections.append(client)
        if not self.attach(client):
            self._connections.remove(client)
            self._log.info(
                "Client rejected (port limit): %s WEBSOCKET",
                self._client_addr(client))
            client.ws_close(1013, 'Port limit reached')
            return
        addr = self._client_addr(client)
        self._log.info(
            "Client connected: %s WEBSOCKET /ws/%s", addr, self._endpoint)
        self._send_hello(client)

    def set_read_paused(self, paused):
        """Stop or resume reading every client on this endpoint.

        uhttp owns these sockets, so it does the work: pause_reading()
        drops the socket's read interest, its kernel buffer fills and
        TCP stalls the peer. Sending is untouched, so what the device
        says still reaches them.

        Before uhttp grew this there was no handle here at all, and a
        WebSocket client feeding a slow device was held back only by
        the serial write buffer's hard limit - which means having its
        data dropped once that filled.
        """
        if self._read_paused == bool(paused):
            return
        self._read_paused = bool(paused)
        # Only the attached: backpressure is about clients feeding the
        # device, and a detached one is not.
        for client in list(self._attached):
            self._apply_read_paused(client)

    def _apply_read_paused(self, client):
        """Put one client into the endpoint's current read state"""
        try:
            if self._read_paused:
                client.pause_reading()
            else:
                client.resume_reading()
        except AttributeError:
            # uhttp older than 3.1 has no handle for this.
            self._log.debug(
                "uhttp has no pause_reading(); cannot hold back %s",
                self._client_addr(client))
        except OSError:
            # Socket already gone; process_stale() will reap it.
            pass

    def disconnect_client(self, client):
        """Drop one client on request, return its address for logging.

        Counterpart of Server.disconnect_client() - same job, but these
        are uhttp connections, so they are closed with a WebSocket close
        frame rather than by closing a socket we own.
        """
        addr = self._client_addr(client)
        try:
            client.ws_close(1000, 'Disconnected by administrator')
        except OSError:
            # Socket already gone; reaping it below is still right.
            pass
        self.remove_connection(client)
        return addr

    def remove_connection(self, client):
        """Remove WebSocket connection"""
        if client in self._connections:
            addr = self._client_addr(client)
            # detach() releases the port, and only if this client was
            # holding it - a detached client leaving must not release a
            # claim it had already given up.
            self.detach(client)
            self._connections.remove(client)
            self._write_refused.discard(client)
            if not self._connections:
                # Nobody left to hold a difference against.
                self._reported = None
            self._log.info(
                "Client disconnected: %s WEBSOCKET", addr)

    def process_message(self, client):
        """Process incoming WebSocket message"""
        data = client.read_buffer()
        if not data:
            return
        if client.ws_is_text:
            self._process_text_message(
                client, data.decode('utf-8', 'replace'))
        elif not self._can_write:
            self._refuse_write(client, 'this endpoint does not write')
        elif client not in self._attached:
            self._refuse_write(client, 'not attached')
        else:
            # Named as the source so a monitor can say who wrote it.
            self._serial.send(data, client)

    def _process_text_message(self, client, text):
        """Act on a JSON frame from a client.

        Every key is looked at, because one frame may carry several
        topics - and anything refused is answered, because a request
        dropped in silence cannot be told from one that worked.
        """
        try:
            msg = _json.loads(text)
        except (ValueError, TypeError):
            self._send_error(client, None, 'not valid JSON')
            return
        if not isinstance(msg, dict):
            self._send_error(client, None, 'expected a JSON object')
            return
        if 'attach' in msg:
            self._request_attach(client, msg['attach'])
        signals = msg.get('signals')
        if isinstance(signals, dict):
            self._request_signals(client, signals)
        elif signals is not None:
            self._send_error(client, 'signals', 'expected an object')
        # Older clients name a line at the top level instead.
        legacy = dict((key, msg[key]) for key in ('rts', 'dtr') if key in msg)
        if legacy:
            self._request_signals(client, legacy)

    def _request_attach(self, client, wanted):
        """Take or give up the claim on the port, and say which it is.

        The answer reports the state that resulted rather than the one
        asked for, so a refusal needs no separate frame to correct it.
        """
        reply = {}
        if wanted:
            if not self.attach(client):
                reply['error'] = {
                    'request': 'attach',
                    'reason': 'the serial port cannot take another client',
                }
        else:
            self.detach(client)
        reply['attach'] = client in self._attached
        self._send_json(client, reply)

    def _request_signals(self, client, wanted):
        """Set the lines this client may set, refusing the rest by name"""
        answered = {}
        refused = []
        for name, value in wanted.items():
            name = str(name).lower()
            if name == 'rts' and self._ctl_rts:
                self._serial.set_rts(bool(value))
            elif name == 'dtr' and self._ctl_dtr:
                self._serial.set_dtr(bool(value))
            else:
                refused.append(name)
                continue
            if name not in self._ctl_signals:
                # Settable but not reported: set_rts()/set_dtr() only
                # broadcast what the config says to report, so without
                # this the client that asked would hear nothing back.
                answered[name] = bool(value)
        reply = {}
        if answered:
            reply['signals'] = answered
        if refused:
            reply['error'] = {
                'request': 'signals',
                'reason': '%s cannot be set here' % ', '.join(refused),
            }
        if reply:
            self._send_json(client, reply)

    def _refuse_write(self, client, reason):
        """Say why data was dropped, once per client and reason.

        A client that ignores what `can` told it may stream for as long
        as it likes; answering every frame would turn its mistake into
        a flood of ours. The note is dropped when the client attaches,
        so the next refusal is news again.
        """
        if client in self._write_refused:
            return
        self._write_refused.add(client)
        self._send_error(client, 'data', reason)

    # uhttp owns these sockets: it registers them in the shared
    # selector and drives their reads and writes. Nothing to do here
    # but reap the ones it has closed.

    def process_stale(self):
        """Remove closed connections"""
        for client in list(self._connections):
            if not client.is_websocket or client.socket is None:
                self.remove_connection(client)

    def send(self, data):
        """Send serial data to the attached clients as binary frames"""
        if not self._can_read:
            return
        for client in list(self._attached):
            try:
                client.ws_send(data)
            except OSError:
                self.remove_connection(client)

    def on_serial_lost(self, reason):
        """The device went away; say so and keep everybody.

        The clients stay attached: attached means wanting the device,
        and none of them changed its mind - that is what SerialProxy
        waits for before trying the port again.
        """
        # Whatever the lines read says nothing about the device that
        # comes back, so the next report is a full set again.
        self._reported = None
        self._broadcast_json(
            {'serial': {'connected': False, 'reason': reason}})

    def on_serial_found(self):
        """The device is back"""
        self._broadcast_json({'serial': {'connected': True}})

    def send_signal_report(self, bitmask):
        """Report the lines that moved, to every connection.

        Only the difference: a client is handed the whole set when it
        arrives, so repeating the lines that did not move costs a frame
        and says nothing. A detached client is told as well - it gave
        up the data, not the channel.
        """
        if not self._control:
            return
        signals = self._signals_dict(bitmask)
        changed = signals
        if self._reported is not None:
            changed = dict(
                (name, value) for name, value in signals.items()
                if self._reported.get(name) != value)
        self._reported = signals
        if changed:
            self._broadcast_json({'signals': changed})

    def close_connections(self):
        """Close all WebSocket connections"""
        while self._connections:
            client = self._connections.pop()
            try:
                client.ws_close(1001, 'Server shutting down')
            except OSError:
                pass
        # Nobody is holding the port any more; leaving the list behind
        # would have has_connections() vouch for clients that are gone.
        self._attached.clear()
        self._write_refused.clear()
        # Nobody has been told anything, so the next report is a full
        # set rather than a difference from what the last lot knew.
        self._reported = None
        self._serial.disconnect()

    def close(self):
        """Close all connections"""
        self.close_connections()

    def _send_hello(self, client):
        """Everything a new client needs, in one frame.

        One frame rather than four, so nothing renders a half-built
        state: the opening message and a later change have the same
        shape, and a client that dispatches on the keys it recognises
        needs no rule for which is which.
        """
        msg = {
            'port': self._serial.info,
            'can': self._capabilities(),
            'serial': {'connected': bool(self._serial.is_connected)},
            'attach': client in self._attached,
        }
        signals = self._signals_dict(self._serial.get_signals())
        if signals:
            # No key at all means nothing is reported, so a client
            # knows to show no indicators rather than six dead ones.
            msg['signals'] = signals
            if self._reported is None:
                # First client on an empty endpoint: what it was just
                # handed is what the audience knows, so the next report
                # can be a difference from it. A later joiner must not
                # move this - the state may have changed since the last
                # broadcast, and the others have not heard about it.
                self._reported = signals
        self._send_json(client, msg)

    def _capabilities(self):
        """What this client may do, not what the protocol has.

        The UI used to offer a clickable RTS badge wherever control
        existed, including where the server drops the request.
        """
        settable = []
        if self._ctl_rts:
            settable.append('rts')
        if self._ctl_dtr:
            settable.append('dtr')
        return {
            'read': self._can_read,
            'write': self._can_write,
            'signals': settable,
            'attach': True,
        }

    def _signals_dict(self, bitmask):
        """The reported lines, in the order the config named them"""
        return _control.signals_dict(bitmask, self._ctl_signals)

    def _send_json(self, client, msg):
        """Send one text frame, reaping a client that has gone"""
        try:
            client.ws_send(_json.dumps(msg))
        except OSError:
            self.remove_connection(client)

    def _broadcast_json(self, msg):
        """Send one text frame to every connection"""
        text = _json.dumps(msg)
        for client in list(self._connections):
            try:
                client.ws_send(text)
            except OSError:
                self.remove_connection(client)

    def _send_error(self, client, request, reason):
        """Answer a request that could not be carried out"""
        error = {'reason': reason}
        if request:
            error['request'] = request
        self._send_json(client, {'error': error})

    def _client_addr(self, client):
        """Return formatted client address string"""
        try:
            addr = client.addr
            if isinstance(addr, tuple) and len(addr) >= 2:
                return "%s:%d" % (addr[0], addr[1])
            return str(addr)
        except Exception:
            return 'unknown'
