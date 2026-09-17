"""Server"""

# pylint: disable=C0209

import logging as _logging
import os as _os
import selectors as _selectors
import socket as _socket
import ssl as _ssl

import ser2tcp.cert_manager as _cert_manager
import ser2tcp.connection_control as _connection_control
import ser2tcp.connection_socket as _connection_socket
import ser2tcp.connection_ssl as _connection_ssl
import ser2tcp.connection_tcp as _connection_tcp
import ser2tcp.connection_telnet as _connection_telnet
import ser2tcp.ip_filter as _ip_filter


class ConfigError(Exception):
    """Configuration error exception"""


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
        self._data_enabled = self._config.get('data', True)
        self._max_connections = self._config.get('max_connections', 0)
        self._ip_filter = _ip_filter.create_filter(self._config, log=self._log)
        self._ssl_context = None
        self._socket = None
        if self._protocol not in self.CONNECTIONS:
            raise ConfigError('Unknown protocol %s' % self._protocol)
        if not self._data_enabled and not self._control:
            raise ConfigError(
                '"data": false requires "control" configuration')
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
        self._socket.listen(1)
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
        """Return True if data forwarding is enabled"""
        return self._data_enabled

    @property
    def max_connections(self):
        """Return max connections limit (0 = unlimited)"""
        return self._max_connections

    @property
    def connections(self):
        """Return list of connections"""
        return self._connections

    def _client_connect(self):
        """connect to client, will accept waiting connection"""
        sock, addr = self._socket.accept()
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
        if self._control:
            connection_class = _connection_control.wrap_control(
                connection_class, self._control, self._data_enabled)
        try:
            connection = connection_class(**kwargs)
        except _connection_ssl.SslHandshakeError as err:
            self._log.info(
                "Client rejected: %s:%d (%s)", addr[0], addr[1], err)
            if not self._connections:
                self._serial.disconnect()
            return
        if self._serial.connect():
            self._connections.append(connection)
            self._conn_by_socket[connection.socket()] = connection
            connection.attach(self._selector, self)
        else:
            connection.close()

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
        if mask & _selectors.EVENT_WRITE and not self._flush(con):
            return None
        if mask & _selectors.EVENT_READ:
            self._read(con)
        return None

    def _read(self, con):
        """Read from a client and forward it, or drop the connection"""
        data = b''
        try:
            data = con.socket().recv(4096)
            self._log.debug("(%s): %s", con.address_str(), data)
        except (ConnectionResetError, _ssl.SSLError) as err:
            self._log.info("(%s): %s", con.address_str(), err)
        if not data:
            self._remove_connection(con)
            return
        # on_received may answer (a control protocol signal report), so
        # the send buffer has to be re-checked afterwards.
        con.on_received(data)
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
        for con in list(self._connections):
            if con.is_closed():
                self._remove_connection(con)
            elif con.is_stale():
                self._log.info(
                    "(%s): send timeout", con.address_str())
                self._remove_connection(con)

    def send(self, data):
        """Send data to all connections"""
        if not self._data_enabled:
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
