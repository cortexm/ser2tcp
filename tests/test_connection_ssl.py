"""Tests for ConnectionSsl class"""

import unittest
import unittest.mock
from unittest.mock import Mock

from ser2tcp.connection_ssl import ConnectionSsl


class MockSocket:
    """Mock socket for testing"""
    def __init__(self):
        self.sent_data = bytearray()
        self.closed = False
        self._fileno = 5

    def send(self, data):
        self.sent_data.extend(data)
        return len(data)

    def close(self):
        self.closed = True

    def fileno(self):
        return self._fileno


class TestConnectionSsl(unittest.TestCase):
    def _make_connection(self):
        """Helper to create ConnectionSsl with mock socket"""
        mock_socket = MockSocket()
        mock_ssl_socket = MockSocket()
        addr = ('127.0.0.1', 12345)
        mock_serial = Mock()
        log = Mock()
        mock_context = Mock()
        mock_context.wrap_socket.return_value = mock_ssl_socket
        conn = ConnectionSsl(
            (mock_socket, addr),
            mock_serial,
            log=log,
            ssl_context=mock_context)
        return conn, mock_serial, mock_context, mock_socket

    def test_wraps_socket_without_handshaking(self):
        """The handshake belongs to the event loop, not to __init__.

        Run here it would block every serial port and the HTTP API for
        as long as the client felt like saying nothing.
        """
        conn, _, context, raw_socket = self._make_connection()
        context.wrap_socket.assert_called_once_with(
            raw_socket, server_side=True, do_handshake_on_connect=False)
        self.assertTrue(conn.needs_handshake())

    def test_nothing_is_logged_before_the_handshake(self):
        """A socket is not a client until TLS says so"""
        conn, _, _, _ = self._make_connection()
        self.assertEqual(conn._log.info.call_args_list, [])

    def test_the_handshake_is_logged_when_it_lands(self):
        conn, _, _, _ = self._make_connection()
        conn.socket().do_handshake = Mock()
        self.assertTrue(conn.handshake())
        self.assertFalse(conn.needs_handshake())
        self.assertEqual(
            conn._log.info.call_args_list[0],
            unittest.mock.call(
                "Client connected: %s SSL", '127.0.0.1:12345'))

    def test_a_handshake_that_wants_more_input_is_not_done(self):
        import selectors
        import ssl
        conn, _, _, _ = self._make_connection()
        conn.socket().do_handshake = Mock(side_effect=ssl.SSLWantReadError())
        self.assertFalse(conn.handshake())
        self.assertTrue(conn.needs_handshake())
        self.assertEqual(conn.wanted_events(), selectors.EVENT_READ)

    def test_a_handshake_that_wants_to_write_asks_for_write(self):
        import selectors
        import ssl
        conn, _, _, _ = self._make_connection()
        conn.socket().do_handshake = Mock(side_effect=ssl.SSLWantWriteError())
        self.assertFalse(conn.handshake())
        self.assertEqual(conn.wanted_events(), selectors.EVENT_WRITE)

    def test_a_failed_handshake_raises(self):
        import ssl
        from ser2tcp.connection_ssl import SslHandshakeError
        conn, _, _, _ = self._make_connection()
        conn.socket().do_handshake = Mock(
            side_effect=ssl.SSLError('no shared cipher'))
        with self.assertRaises(SslHandshakeError):
            conn.handshake()

    def test_a_silent_client_goes_stale(self):
        """Nothing is buffered, so the ordinary timeout never fires"""
        import time
        conn, _, _, _ = self._make_connection()
        self.assertFalse(conn.is_stale())
        conn._handshake_started = time.time() - 3600
        self.assertTrue(conn.is_stale())

    def test_a_finished_connection_uses_the_ordinary_timeout(self):
        import time
        conn, _, _, _ = self._make_connection()
        conn.socket().do_handshake = Mock()
        conn.handshake()
        conn._handshake_started = time.time() - 3600
        self.assertFalse(conn.is_stale())

    def test_pending_reports_what_the_ssl_object_still_holds(self):
        conn, _, _, _ = self._make_connection()
        conn.socket().pending = Mock(return_value=4096)
        self.assertEqual(conn.pending(), 4096)

    def test_recv_treats_want_read_as_no_data(self):
        import ssl
        conn, _, _, _ = self._make_connection()
        conn.socket().recv = Mock(side_effect=ssl.SSLWantReadError())
        self.assertIsNone(conn.recv())

    def test_send_treats_want_write_as_nothing_written(self):
        import ssl
        conn, _, _, _ = self._make_connection()
        conn.send(b'data')
        conn.socket().send = Mock(side_effect=ssl.SSLWantWriteError())
        # Not an error: the buffer stays and the loop comes back to it.
        self.assertEqual(conn.flush(), 0)
        self.assertTrue(conn.has_pending_data())

    def test_on_received_forwards_to_serial(self):
        """Data should be forwarded to serial"""
        conn, serial, _, _ = self._make_connection()
        conn.on_received(b'hello')
        # Named as the source, so a monitor can say who wrote it.
        serial.send.assert_called_once_with(b'hello', conn)

    def test_send_adds_to_buffer(self):
        """Send should add data to buffer"""
        conn, _, _, _ = self._make_connection()
        result = conn.send(b'test')
        self.assertEqual(result, 4)
        self.assertTrue(conn.has_pending_data())


if __name__ == "__main__":
    unittest.main()
