"""Connection Socket (Unix domain socket)"""

import ser2tcp.connection as _connection


class ConnectionSocket(_connection.Connection):
    """Unix domain socket connection"""

    def __init__(
            self, connection, ser, send_timeout=None, buffer_limit=None,
            log=None, can_write=True):
        super().__init__(
            connection, send_timeout, buffer_limit, log, can_write)
        self._serial = ser
        self._log.info("Client connected: %s SOCKET", self._addr[0])

    def address_str(self):
        """Return formatted address string"""
        return self._addr[0]

    def on_received(self, data):
        """Received data from client"""
        self.to_serial(data)
