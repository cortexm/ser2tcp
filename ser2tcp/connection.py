"""Connection"""

import logging as _logging
import selectors as _selectors
import time as _time


class Connection():
    """Connection"""

    DEFAULT_SEND_TIMEOUT = 5.0
    DEFAULT_BUFFER_LIMIT = None

    def __init__(
            self, connection, send_timeout=None, buffer_limit=None,
            log=None):
        self._log = log if log else _logging.Logger(self.__class__.__name__)
        self._socket, self._addr = connection
        self._selector = None
        self._owner = None
        self._interest = None
        self._out_buffer = bytearray()
        self._last_write_time = _time.time()
        if send_timeout is not None:
            self._send_timeout = send_timeout
        else:
            self._send_timeout = self.DEFAULT_SEND_TIMEOUT
        if buffer_limit is not None:
            self._buffer_limit = buffer_limit
        else:
            self._buffer_limit = self.DEFAULT_BUFFER_LIMIT

    def __del__(self):
        self.close()

    def socket(self):
        """Return reference to socket"""
        return self._socket

    def attach(self, selector, owner):
        """Register this connection's socket in the event loop.

        `owner` is what the loop calls handle_event() on — the Server,
        which is the only thing that can drop the connection when the
        read or the flush fails.
        """
        if selector is None or self._socket is None:
            return
        self._selector = selector
        self._owner = owner
        self._interest = _selectors.EVENT_READ
        selector.register(self._socket, self._interest, owner)

    def update_interest(self):
        """Arm EVENT_WRITE only while there is something to flush.

        Left armed on an empty buffer the loop spins on a socket that is
        always writable; never armed, buffered data never leaves.
        """
        if self._selector is None or self._socket is None:
            return
        want = _selectors.EVENT_READ
        if self._out_buffer:
            want |= _selectors.EVENT_WRITE
        if want == self._interest:
            return
        try:
            self._selector.modify(self._socket, want, self._owner)
        except (KeyError, ValueError, OSError):
            # Cannot be re-armed, so it would hang instead of failing.
            self.close()
            return
        self._interest = want

    def _unregister(self):
        """Drop the socket from the selector before it is closed.

        A closed fd left registered raises on the next select() for
        everyone, not just this connection.
        """
        if self._selector is None or self._interest is None:
            return
        try:
            self._selector.unregister(self._socket)
        except (KeyError, ValueError, OSError):
            pass
        self._interest = None

    def is_closed(self):
        """Return True once the socket is gone"""
        return self._socket is None

    def close(self):
        """Close connection"""
        if self._socket:
            self._unregister()
            self._socket.close()
            self._socket = None
            self._log.info("Client disconnected: %s", self.address_str())

    def fileno(self):
        """emulate fileno method of socket"""
        return self._socket.fileno() if self._socket else None

    def get_address(self):
        """Return address"""
        return self._addr

    def address_str(self):
        """Return formatted address string"""
        return "%s:%d" % self._addr

    def send(self, data):
        """Add data to output buffer, return number of bytes added"""
        if not self._socket:
            return None
        new_size = len(self._out_buffer) + len(data)
        if self._buffer_limit and new_size > self._buffer_limit:
            return None
        if not self._out_buffer:
            # Reset timeout when buffer becomes non-empty
            self._last_write_time = _time.time()
        self._out_buffer.extend(data)
        return len(data)

    def flush(self):
        """Flush output buffer, return number of bytes sent or None on error"""
        if not self._socket or not self._out_buffer:
            return 0
        try:
            sent = self._socket.send(self._out_buffer)
            if sent > 0:
                del self._out_buffer[:sent]
                self._last_write_time = _time.time()
            return sent
        except OSError:
            return None

    def has_pending_data(self):
        """Return True if there is data in output buffer"""
        return bool(self._out_buffer)

    def is_stale(self):
        """Return True if send timeout expired"""
        if not self._out_buffer:
            return False
        return _time.time() - self._last_write_time > self._send_timeout
