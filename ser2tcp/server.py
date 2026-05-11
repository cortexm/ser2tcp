"""Server"""

# pylint: disable=C0209

import logging as _logging
import os as _os
import socket as _socket
import ssl as _ssl

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

    def __init__(self, config, ser, log=None):
        self._log = log if log else _logging.Logger(self.__class__.__name__)
        self._config = config
        self._serial = ser
        self._connections = []
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

    def __del__(self):
        self.close()

    def _create_ssl_context(self):
        """Create SSL context from config"""
        ssl_config = self._config.get('ssl', {})
        certfile = ssl_config.get('certfile')
        keyfile = ssl_config.get('keyfile')
        ca_certs = ssl_config.get('ca_certs')
        if not certfile or not keyfile:
            raise ConfigError('SSL protocol requires certfile and keyfile')
        context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        if ca_certs:
            context.load_verify_locations(ca_certs)
            context.verify_mode = _ssl.CERT_REQUIRED
        return context

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
        else:
            connection.close()

    def close_connections(self):
        """close all clients"""
        while self._connections:
            self._connections.pop().close()

    def close(self):
        """Close socket and all connections"""
        if self._socket is not None:
            self.close_connections()
            self._socket.close()
            self._socket = None
            if self._protocol == 'SOCKET':
                sock_path = self._config['address']
                if _os.path.exists(sock_path):
                    _os.unlink(sock_path)

    def has_connections(self):
        """True if server has some connections"""
        return bool(self._connections)

    def read_sockets(self):
        """Return sockets for reading (server + all clients)"""
        sockets = [self._socket]
        for con in self._connections:
            sockets.append(con.socket())
        return sockets

    def write_sockets(self):
        """Return sockets for writing (clients with pending data)"""
        sockets = []
        for con in self._connections:
            if con.has_pending_data():
                sockets.append(con.socket())
        return sockets

    def _remove_connection(self, con):
        """Remove connection and disconnect serial if no connections left"""
        con.close()
        self._connections.remove(con)
        if not self._connections:
            self._serial.disconnect()

    def process_read(self, read_sockets):
        """Process sockets with read event"""
        if self._socket in read_sockets:
            self._client_connect()
        for con in list(self._connections):
            if con.socket() in read_sockets:
                data = b''
                try:
                    data = con.socket().recv(4096)
                    self._log.debug("(%s): %s", con.address_str(), data)
                except (ConnectionResetError, _ssl.SSLError) as err:
                    self._log.info("(%s): %s", con.address_str(), err)
                if not data:
                    self._remove_connection(con)
                    continue
                con.on_received(data)

    def process_write(self, write_sockets):
        """Process sockets with write event, flush buffers"""
        for con in list(self._connections):
            if con.socket() in write_sockets:
                result = con.flush()
                if result is None:
                    self._log.info(
                        "(%s): write error", con.address_str())
                    self._remove_connection(con)

    def process_stale(self):
        """Remove stale connections (send timeout expired)"""
        for con in list(self._connections):
            if con.is_stale():
                self._log.info(
                    "(%s): send timeout", con.address_str())
                self._remove_connection(con)

    def send(self, data):
        """Send data to all connections"""
        if not self._data_enabled:
            return
        for con in self._connections:
            con.send(data)

    def send_signal_report(self, bitmask):
        """Send signal report to all control-enabled connections"""
        if not self._control:
            return
        for con in self._connections:
            con.send_signal_report(bitmask)
