"""Connection TCP"""

import ser2tcp.connection as _connection


class ConnectionTcp(_connection.Connection):
    """TCP connection"""

    def __init__(
            self, connection, ser, send_timeout=None, buffer_limit=None,
            log=None, can_write=True):
        super().__init__(
            connection, send_timeout, buffer_limit, log, can_write)
        self._serial = ser
        self._log_connected()

    def _log_connected(self):
        self._log.info("Client connected: %s TCP", self.address_str())

    def on_received(self, data):
        """Received data from client"""
        self.to_serial(data)
