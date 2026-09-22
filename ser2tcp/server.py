"""Server"""

# pylint: disable=C0209

import errno as _errno
import logging as _logging
import os as _os
import selectors as _selectors
import socket as _socket
import time as _time

import ser2tcp.cert_manager as _cert_manager
import ser2tcp.connection_control as _connection_control
import ser2tcp.connection_socket as _connection_socket
import ser2tcp.connection_ssl as _connection_ssl
import ser2tcp.connection_tcp as _connection_tcp
import ser2tcp.connection_telnet as _connection_telnet
import ser2tcp.ip_filter as _ip_filter


class ConfigError(Exception):
    """Configuration error exception"""


# Which way data may flow on a server, as (can_read, can_write) — read
# being the device talking to the client.
ACCESS_MODES = {
    'rw': (True, True),
    'ro': (True, False),
    'wo': (False, True),
    'none': (False, False),
}

# What `data` used to say. It could only express the two ends of that
# range, which is why `access` replaced it.
_DATA_ALIAS = {True: 'rw', False: 'none'}


def access_name(can_read, can_write):
    """The mode name for a pair of direction flags"""
    for name, flags in ACCESS_MODES.items():
        if flags == (can_read, can_write):
            return name
    return 'rw'


def parse_access(config):
    """Return (can_read, can_write) for a server config.

    Raises ValueError on a mode that cannot be read, or on a config that
    says two different things: picking one of them silently is how a
    configuration comes to mean less than it says, which is the same
    mistake the IP filter used to make with an unreadable rule.
    """
    mode = config.get('access')
    if mode is not None:
        if not isinstance(mode, str) or mode.lower() not in ACCESS_MODES:
            raise ValueError(
                f"access must be one of {', '.join(ACCESS_MODES)}, "
                f"got {mode!r}")
        mode = mode.lower()
    if 'data' in config:
        alias = _DATA_ALIAS[bool(config['data'])]
        if mode is not None and mode != alias:
            raise ValueError(
                f"data: {bool(config['data'])} and access: '{mode}' "
                f"disagree - data is the older spelling of "
                f"access: '{alias}'")
        mode = alias
    return ACCESS_MODES[mode or 'rw']


class Server():
    """Server connection manager"""

    CONNECTIONS = {
        'TCP': _connection_tcp.ConnectionTcp,
        'TELNET': _connection_telnet.ConnectionTelnet,
        'SSL': _connection_ssl.ConnectionSsl,
        'SOCKET': _connection_socket.ConnectionSocket,
    }

    def __init__(
            self, config, ser, log=None, certs_dir=None, selector=None):
        # First, before anything that can raise: a config this refuses
        # leaves a half-built object for __del__ to close, and close()
        # asking about a socket that was never assigned turns a clear
        # ConfigError into an AttributeError nobody can act on.
        self._socket = None
        self._ssl_context = None
        self._connections = []
        self._log = log if log else _logging.Logger(self.__class__.__name__)
        self._config = config
        self._serial = ser
        self._certs_dir = certs_dir
        self._selector = selector
        self._connections = []
        # Dispatch needs to get from a ready socket back to its
        # connection; the listening socket is handled separately.
        self._conn_by_socket = {}
        self._protocol = self._config['protocol'].upper()
        self._send_timeout = self._config.get('send_timeout')
        self._buffer_limit = self._config.get('buffer_limit')
        self._control = self._config.get('control')
        self._can_read, self._can_write = parse_access(self._config)
        # Kept for the places that only ask "is any data moving" — the
        # control wrapper and the status payload.
        self._data_enabled = self._can_read or self._can_write
        self._max_connections = self._config.get('max_connections', 0)
        self._ip_filter = _ip_filter.create_filter(self._config, log=self._log)
        self._ssl_context = None
        self._socket = None
        # Set while the listening socket is deliberately not watched
        # after running out of file descriptors (see _accept_failed).
        self._accept_paused_until = 0
        # Set while the serial port is behind and clients must wait.
        self._read_paused = False
        if self._protocol not in self.CONNECTIONS:
            raise ConfigError('Unknown protocol %s' % self._protocol)
        if not self._data_enabled and not self._control:
            raise ConfigError(
                'a server that neither reads nor writes requires '
                '"control" configuration')
        if self._control and self._protocol == 'TELNET':
            raise ConfigError(
                'Control protocol not supported with TELNET')
        if self._protocol == 'SOCKET':
            self._log.info(
                "  Server: %s %s",
                self._config['address'],
                self._protocol)
            self._socket = _socket.socket(
                _socket.AF_UNIX, _socket.SOCK_STREAM)
            sock_path = config['address']
            if _os.path.exists(sock_path):
                _os.unlink(sock_path)
            try:
                self._socket.bind(sock_path)
            except OSError as err:
                raise ConfigError(
                    f"{self._protocol} {sock_path}: failed to bind: "
                    f"{err.strerror or err}") from err
        else:
            self._log.info(
                "  Server: %s %d %s",
                self._config['address'],
                self._config['port'],
                self._protocol)
            if self._protocol == 'SSL':
                self._ssl_context = self._create_ssl_context()
            self._socket = _socket.socket(
                _socket.AF_INET, _socket.SOCK_STREAM, _socket.IPPROTO_TCP)
            self._socket.setsockopt(
                _socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            try:
                self._socket.bind((config['address'], config['port']))
            except OSError as err:
                raise ConfigError(
                    f"{self._protocol} {config['address']}:{config['port']}: "
                    f"failed to bind: {err.strerror or err}") from err
        # A backlog of one drops clients that arrive together, and
        # reconnect storms arrive together.
        self._socket.listen(_socket.SOMAXCONN)
        if self._selector is not None:
            self._selector.register(
                self._socket, _selectors.EVENT_READ, self)

    def __del__(self):
        self.close()

    def _create_ssl_context(self):
        """Create SSL context from config (bundle-based)."""
        if not self._certs_dir:
            raise ConfigError(
                'SSL protocol requires certs_dir (internal wiring error)')
        ssl_config = self._config.get('ssl', {})
        try:
            return _cert_manager.build_ssl_context(ssl_config, self._certs_dir)
        except _cert_manager.CertManagerError as err:
            raise ConfigError(str(err)) from err

    def reload_ssl_context(self):
        """Re-read the bundle into this server's existing SSLContext.

        Clients connecting from now on are served the new certificate;
        the ones already connected keep the session they negotiated.
        Returns False for a non-SSL server, which has nothing to reload.
        """
        if self._ssl_context is None:
            return False
        _cert_manager.reload_ssl_context(
            self._ssl_context, self._config.get('ssl', {}), self._certs_dir)
        return True

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
    def max_connections(self):
        """Return max connections limit (0 = unlimited)"""
        return self._max_connections

    @property
    def connections(self):
        """Return list of connections"""
        return self._connections

    # How long to stop watching the listening socket after running out
    # of file descriptors, so the loop is not pinned at 100% CPU.
    ACCEPT_COOLDOWN = 1.0
    # Errors that mean "no descriptors left", not "this client failed".
    _FD_EXHAUSTED = frozenset((_errno.EMFILE, _errno.ENFILE, _errno.ENOBUFS,
        _errno.ENOMEM))

    def _client_connect(self):
        """connect to client, will accept waiting connection"""
        try:
            sock, addr = self._socket.accept()
        except OSError as err:
            self._accept_failed(err)
            return
        # Everything from here on shares one event loop with every other
        # port and the HTTP API, so nothing may wait on this socket.
        sock.setblocking(False)
        if self._protocol == 'SOCKET':
            addr = (self._config['address'],)
        elif self._ip_filter and not self._ip_filter.is_allowed(addr[0]):
            self._log.info("Client rejected (IP filter): %s:%d", addr[0], addr[1])
            sock.close()
            return
        if self._max_connections > 0 and len(self._connections) >= self._max_connections:
            self._log.info(
                "Client rejected (server limit): %s:%d", addr[0], addr[1])
            sock.close()
            return
        if not self._serial.can_add_connection():
            self._log.info(
                "Client rejected (port limit): %s:%d", addr[0], addr[1])
            sock.close()
            return
        kwargs = {
            'connection': (sock, addr),
            'ser': self._serial,
            'send_timeout': self._send_timeout,
            'buffer_limit': self._buffer_limit,
            'log': self._log,
        }
        if self._ssl_context:
            kwargs['ssl_context'] = self._ssl_context
        connection_class = self.CONNECTIONS[self._protocol]
        kwargs['can_write'] = self._can_write
        if self._control:
            connection_class = _connection_control.wrap_control(
                connection_class, self._control,
                can_read=self._can_read, can_write=self._can_write)
        try:
            connection = connection_class(**kwargs)
        except _connection_ssl.SslHandshakeError as err:
            self._log.info(
                "Client rejected: %s:%d (%s)", addr[0], addr[1], err)
            if not self._connections:
                self._serial.disconnect()
            return
        if connection.needs_handshake():
            # Track it and watch it, but do not open the serial port for
            # it yet: an unfinished TLS connection is not a client.
            self._add_connection(connection)
            self._handshake(connection)
            return
        if self._serial.connect():
            self._add_connection(connection)
        else:
            connection.close()

    def set_read_paused(self, paused):
        """Stop or resume reading every client on this server.

        The serial port asks for this when its write queue backs up:
        not reading is what closes the TCP window and makes the sender
        wait, instead of queueing without end at the device's baud rate.
        """
        self._read_paused = bool(paused)
        for con in self._connections:
            con.set_read_paused(self._read_paused)

    def _add_connection(self, connection):
        """Track a connection and start watching its socket"""
        self._connections.append(connection)
        self._conn_by_socket[connection.socket()] = connection
        connection.attach(self._selector, self)
        if self._read_paused:
            # Joined while the port is behind: wait like the others.
            connection.set_read_paused(True)

    def _handshake(self, con):
        """Carry a TLS handshake one step further.

        Called from the event loop, so a client that stalls costs only
        its own connection - and its own slot, until process_stale()
        reaps it.
        """
        try:
            done = con.handshake()
        except _connection_ssl.SslHandshakeError as err:
            self._log.info(
                "Client rejected: %s (%s)", con.address_str(), err)
            self._remove_connection(con)
            return
        if not done:
            return
        if not self._serial.connect():
            self._remove_connection(con)

    def _accept_failed(self, err):
        """Deal with an accept() that raised.

        Most failures are about the one connection - the peer gave up
        between select() and accept(), or the readiness was spurious -
        and the next pass carries on as normal.

        Running out of file descriptors is different: the connection
        stays queued, so the listening socket is readable again
        immediately and accept() fails again, forever, at 100% CPU.
        Nothing can be accepted until a descriptor frees up, so stop
        watching the listener for a moment instead. Everything already
        connected keeps being served in the meantime, which is the part
        that actually matters while the process is out of descriptors.
        """
        if err.errno in self._FD_EXHAUSTED:
            self._log.error(
                "%s %s: cannot accept (%s); not accepting for %.0fs",
                self._protocol, self._where(), err.strerror or err,
                self.ACCEPT_COOLDOWN)
            self._pause_accepting()
            return
        self._log.info(
            "%s %s: accept failed: %s",
            self._protocol, self._where(), err.strerror or err)

    def _where(self):
        """Address of this server, for log lines"""
        if self._protocol == 'SOCKET':
            return str(self._config.get('address'))
        return "%s:%s" % (
            self._config.get('address'), self._config.get('port'))

    def _pause_accepting(self):
        """Stop watching the listening socket until the cooldown ends"""
        self._accept_paused_until = _time.time() + self.ACCEPT_COOLDOWN
        if self._selector is None or self._socket is None:
            return
        try:
            self._selector.unregister(self._socket)
        except (KeyError, ValueError, OSError):
            pass

    def _resume_accepting(self):
        """Watch the listening socket again once the cooldown is over"""
        if not self._accept_paused_until:
            return
        if _time.time() < self._accept_paused_until:
            return
        self._accept_paused_until = 0
        if self._selector is None or self._socket is None:
            return
        try:
            self._selector.register(
                self._socket, _selectors.EVENT_READ, self)
        except (KeyError, ValueError, OSError) as err:
            self._log.warning(
                "%s %s: cannot listen again: %s",
                self._protocol, self._where(), err)

    def on_serial_lost(self, reason):
        """The device is gone, so these clients are too.

        TCP, TELNET, SSL and Unix sockets carry serial data and nothing
        else: there is no way to tell a client the device went away, so
        holding it open would only feed it silence. Closing says it in
        the only language those protocols have.
        """
        del reason      # nothing to say it with
        self.close_connections()

    def on_serial_found(self):
        """Nothing to do: whoever wants the port will connect again"""

    def close_connections(self):
        """close all clients"""
        while self._connections:
            con = self._connections.pop()
            self._conn_by_socket.pop(con.socket(), None)
            con.close()

    def close(self):
        """Close socket and all connections"""
        if self._socket is not None:
            self.close_connections()
            if self._selector is not None:
                try:
                    self._selector.unregister(self._socket)
                except (KeyError, ValueError, OSError):
                    pass
            self._socket.close()
            self._socket = None
            if self._protocol == 'SOCKET':
                sock_path = self._config['address']
                if _os.path.exists(sock_path):
                    _os.unlink(sock_path)

    @property
    def attached(self):
        """The clients holding the port open.

        A socket protocol has no way to say "stay connected but let go
        of the device", so every connection is attached. The property
        exists so SerialProxy can ask both server kinds the same thing.
        """
        return self._connections

    def has_connections(self):
        """True if server has some connections"""
        return bool(self._connections)

    def disconnect_client(self, con):
        """Drop one client on request, return its address for logging.

        The API needs one way to say "drop this client" that works for
        every server kind; ServerWebSocket holds uhttp connections with
        a completely different interface and implements this too.
        """
        addr = con.address_str()
        self._remove_connection(con)
        return addr

    def _remove_connection(self, con):
        """Remove connection and disconnect serial if no connections left"""
        self._conn_by_socket.pop(con.socket(), None)
        con.close()
        if con in self._connections:
            self._connections.remove(con)
        if not self._connections:
            self._serial.disconnect()

    def handle_event(self, fileobj, mask):
        """Owner dispatch for one ready socket.

        Returns None: nothing here produces an object for the loop to
        pass on, unlike uhttp whose connections carry requests.
        """
        if fileobj is self._socket:
            self._client_connect()
            return None
        con = self._conn_by_socket.get(fileobj)
        if con is None:
            # Closed earlier in this same batch of events.
            return None
        if con.is_closed():
            # Closed without going through us - a connection drops
            # itself when its interest cannot be re-armed - so its
            # socket is already None. Reap it instead of reading it.
            self._remove_connection(con)
            return None
        if con.needs_handshake():
            self._handshake(con)
            return None
        if mask & _selectors.EVENT_WRITE and not self._flush(con):
            return None
        if mask & _selectors.EVENT_READ:
            self._read(con)
        return None

    def _read(self, con):
        """Read from a client and forward it, or drop the connection.

        Reads again while the connection says it is still holding
        decrypted bytes. Under TLS those sit in the SSL object rather
        than the kernel buffer, so select() will never mention them
        again - one large record used to arrive 4 KB at a time, the
        rest stuck until the client happened to send something else.
        """
        while True:
            try:
                data = con.recv(4096)
            except OSError as err:
                # OSError covers the lot: a reset or aborted peer, an
                # SSL error, a timeout, and a descriptor another handler
                # closed earlier in this same batch of events. Every one
                # of them means this connection is finished, and naming
                # only two of them left the rest to reach the event loop.
                self._log.info("(%s): %s", con.address_str(), err)
                self._remove_connection(con)
                return
            if data is None:
                # Nothing to read right now; not an error.
                break
            if not data:
                self._remove_connection(con)
                return
            self._log.debug("(%s): %s", con.address_str(), data)
            # on_received may answer (a control protocol signal report),
            # and may also take the whole port down with it.
            con.on_received(data)
            if con.is_closed():
                return
            if not con.pending():
                break
        con.update_interest()

    def _flush(self, con):
        """Flush a client's buffer, return False if it was dropped"""
        if con.flush() is None:
            self._log.info("(%s): write error", con.address_str())
            self._remove_connection(con)
            return False
        con.update_interest()
        return True

    def process_stale(self):
        """Remove stale connections (send timeout expired, or closed)"""
        self._resume_accepting()
        for con in list(self._connections):
            if con.is_closed():
                self._remove_connection(con)
            elif con.is_stale():
                self._log.info(
                    "(%s): send timeout", con.address_str())
                self._remove_connection(con)

    def send(self, data):
        """Send data to all connections"""
        if not self._can_read:
            return
        for con in self._connections:
            con.send(data)
            con.update_interest()

    def send_signal_report(self, bitmask):
        """Send signal report to all control-enabled connections"""
        if not self._control:
            return
        for con in self._connections:
            con.send_signal_report(bitmask)
            con.update_interest()
