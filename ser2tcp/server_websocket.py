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
        self._data_enabled = config.get('data', True)
        self._control = config.get('control')
        if not self._data_enabled and not self._control:
            raise _server.ConfigError(
                'WebSocket "data": false requires "control" config')
        self._max_connections = config.get('max_connections', 0)
        # Parse control config
        self._ctl_rts = False
        self._ctl_dtr = False
        self._ctl_signals = set()
        if self._control:
            self._ctl_rts = bool(self._control.get('rts'))
            self._ctl_dtr = bool(self._control.get('dtr'))
            signals = self._control.get('signals', [])
            self._ctl_signals = set(s.lower() for s in signals)
        self._ip_filter = _ip_filter.create_filter(config, log=log)
        self._connections = []
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
        """Return list of connections (uhttp HttpConnection objects)"""
        return self._connections

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
        """Return True if data forwarding is enabled"""
        return self._data_enabled

    @property
    def ip_filter(self):
        """Return IP filter or None"""
        return self._ip_filter

    @property
    def max_connections(self):
        """Return max connections limit (0 = unlimited)"""
        return self._max_connections

    def has_connections(self):
        """True if server has active connections"""
        return bool(self._connections)

    def add_connection(self, client):
        """Add accepted WebSocket connection"""
        if self._max_connections > 0 and len(self._connections) >= self._max_connections:
            addr = self._client_addr(client)
            self._log.info(
                "Client rejected (server limit): %s WEBSOCKET", addr)
            client.ws_close(1013, 'Server limit reached')
            return
        if not self._serial.can_add_connection():
            addr = self._client_addr(client)
            self._log.info(
                "Client rejected (port limit): %s WEBSOCKET", addr)
            client.ws_close(1013, 'Port limit reached')
            return
        if self._serial.connect():
            self._connections.append(client)
            addr = self._client_addr(client)
            self._log.info(
                "Client connected: %s WEBSOCKET /ws/%s",
                addr, self._endpoint)
            if self._control:
                self._send_signals_to(client)
        else:
            client.ws_close(1011, 'Serial port unavailable')

    def set_read_paused(self, paused):
        """Backpressure has no handle here.

        uhttp owns these sockets and their selector registration, so
        there is nothing to un-arm. WebSocket clients are held back by
        the serial write buffer's hard limit instead - see
        SerialProxy.WRITE_BUFFER_LIMIT.
        """

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
            self._connections.remove(client)
            self._log.info(
                "Client disconnected: %s WEBSOCKET", addr)
            self._serial.disconnect()

    def process_message(self, client):
        """Process incoming WebSocket message"""
        data = client.read_buffer()
        if not data:
            return
        if client.ws_is_text:
            if self._control:
                self._process_control_message(client, data.decode('utf-8'))
        elif self._data_enabled:
            self._serial.send(data)

    def _process_control_message(self, client, msg):
        """Process JSON control message from client"""
        try:
            data = _json.loads(msg)
        except (ValueError, TypeError):
            self._log.warning("Invalid JSON control message")
            return
        if not isinstance(data, dict):
            return
        if 'rts' in data and self._ctl_rts:
            self._serial.set_rts(bool(data['rts']))
        if 'dtr' in data and self._ctl_dtr:
            self._serial.set_dtr(bool(data['dtr']))

    # uhttp owns these sockets: it registers them in the shared
    # selector and drives their reads and writes. Nothing to do here
    # but reap the ones it has closed.

    def process_stale(self):
        """Remove closed connections"""
        for client in list(self._connections):
            if not client.is_websocket or client.socket is None:
                self.remove_connection(client)

    def send(self, data):
        """Send serial data to all connections as binary frames"""
        if not self._data_enabled:
            return
        for client in list(self._connections):
            try:
                client.ws_send(data)
            except OSError:
                self.remove_connection(client)

    def send_signal_report(self, bitmask):
        """Send signal report to all connections as JSON text frame"""
        if not self._control:
            return
        msg = self._bitmask_to_json(bitmask)
        text = _json.dumps(msg)
        for client in list(self._connections):
            try:
                client.ws_send(text)
            except OSError:
                self.remove_connection(client)

    def close_connections(self):
        """Close all WebSocket connections"""
        while self._connections:
            client = self._connections.pop()
            try:
                client.ws_close(1001, 'Server shutting down')
            except OSError:
                pass
        self._serial.disconnect()

    def close(self):
        """Close all connections"""
        self.close_connections()

    def _send_signals_to(self, client):
        """Send current signal state to a single client"""
        bitmask = self._serial.get_signals()
        msg = self._bitmask_to_json(bitmask)
        try:
            client.ws_send(_json.dumps(msg))
        except OSError:
            pass

    def _bitmask_to_json(self, bitmask):
        """Convert signal bitmask to JSON dict filtered by config"""
        signals = {}
        for name in self._ctl_signals:
            bit = _control.SIGNAL_BITS.get(name)
            if bit is not None:
                signals[name] = bool(bitmask & (1 << bit))
        return {'signals': signals}

    def _client_addr(self, client):
        """Return formatted client address string"""
        try:
            addr = client.addr
            if isinstance(addr, tuple) and len(addr) >= 2:
                return "%s:%d" % (addr[0], addr[1])
            return str(addr)
        except Exception:
            return 'unknown'
