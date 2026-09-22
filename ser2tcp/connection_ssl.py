"""Connection SSL"""

import selectors as _selectors
import ssl as _ssl
import time as _time

import ser2tcp.connection_tcp as _connection_tcp


class SslHandshakeError(Exception):
    """SSL handshake failed"""


class ConnectionSsl(_connection_tcp.ConnectionTcp):
    """SSL/TLS connection.

    The handshake runs in the event loop rather than inline. Done
    inline on a blocking socket it stopped everything: one loop serves
    every serial port and the whole HTTP API, so a bare TCP connect
    from anyone who could reach the port - no certificate, no
    authentication, nothing sent - froze the lot until that socket went
    away.
    """

    def __init__(
            self, connection, ser, send_timeout=None, buffer_limit=None,
            log=None, ssl_context=None, can_write=True):
        sock, addr = connection
        self._socket = None
        try:
            ssl_sock = ssl_context.wrap_socket(
                sock, server_side=True, do_handshake_on_connect=False)
        except (_ssl.SSLError, OSError) as err:
            sock.close()
            raise SslHandshakeError(f"SSL handshake failed: {err}") from err
        super().__init__(
            (ssl_sock, addr), ser, send_timeout, buffer_limit, log,
            can_write)
        self._handshake_done = False
        # What the TLS layer last asked to wait for, or None once the
        # handshake is over and ordinary interest rules apply again.
        self._handshake_want = _selectors.EVENT_READ
        self._handshake_started = _time.time()

    def _log_connected(self):
        """Nothing yet: a socket is not a client until TLS says so"""

    def needs_handshake(self):
        """True until the TLS handshake has finished"""
        return not self._handshake_done

    def handshake(self):
        """Advance the handshake; True once it is complete.

        False means it needs more I/O and the interest has been updated
        to whatever OpenSSL asked for. A failure raises, and the caller
        drops the connection.
        """
        if self._handshake_done:
            return True
        if not self._socket:
            raise SslHandshakeError('connection closed during handshake')
        try:
            self._socket.do_handshake()
        except _ssl.SSLWantReadError:
            self._handshake_want = _selectors.EVENT_READ
            self.update_interest()
            return False
        except _ssl.SSLWantWriteError:
            self._handshake_want = _selectors.EVENT_WRITE
            self.update_interest()
            return False
        except (OSError, ValueError) as err:
            raise SslHandshakeError(f"SSL handshake failed: {err}") from err
        self._handshake_done = True
        self._handshake_want = None
        self.update_interest()
        self._log.info("Client connected: %s SSL", self.address_str())
        return True

    def wanted_events(self):
        """During the handshake, watch for what OpenSSL asked for"""
        if self._handshake_want is not None:
            return self._handshake_want
        return super().wanted_events()

    def is_stale(self):
        """A handshake that never finishes must not hold its slot.

        Nothing is buffered while handshaking, so the ordinary send
        timeout never fires and a silent connection would sit there for
        good - one client's worth of the port's connection limit, for
        free.
        """
        if self.needs_handshake():
            return _time.time() - self._handshake_started > self._send_timeout
        return super().is_stale()

    def recv(self, size=4096):
        """Read, treating "not right now" as no data rather than an error"""
        try:
            return super().recv(size)
        except (_ssl.SSLWantReadError, _ssl.SSLWantWriteError):
            return None

    def _send_bytes(self, data):
        """Write, treating "not right now" as nothing written"""
        try:
            return super()._send_bytes(data)
        except (_ssl.SSLWantWriteError, _ssl.SSLWantReadError):
            return None

    def pending(self):
        """Decrypted bytes still held by the SSL object.

        One TLS record can carry far more than a single recv() returns,
        and what is left sits here rather than in the kernel buffer -
        so select() will not mention it again and the caller has to
        keep reading while this is non-zero.
        """
        if not self._socket:
            return 0
        try:
            return self._socket.pending()
        except (OSError, ValueError):
            return 0
