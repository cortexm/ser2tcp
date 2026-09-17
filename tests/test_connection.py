"""Tests for Connection class"""

import selectors
import unittest
from unittest.mock import Mock, patch
import time

from ser2tcp.connection import Connection


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


class TestConnection(unittest.TestCase):
    def _make_connection(self, send_timeout=None, buffer_limit=None):
        """Helper to create Connection with mock socket"""
        mock_socket = MockSocket()
        addr = ('127.0.0.1', 12345)
        log = Mock()
        return Connection(
            (mock_socket, addr),
            send_timeout=send_timeout,
            buffer_limit=buffer_limit,
            log=log)

    def test_init_stores_socket_and_addr(self):
        conn = self._make_connection()
        self.assertIsNotNone(conn.socket())
        self.assertEqual(conn.get_address(), ('127.0.0.1', 12345))

    def test_fileno_returns_socket_fileno(self):
        conn = self._make_connection()
        self.assertEqual(conn.fileno(), 5)

    def test_send_adds_data_to_buffer(self):
        conn = self._make_connection()
        result = conn.send(b'hello')
        self.assertEqual(result, 5)
        self.assertTrue(conn.has_pending_data())

    def test_send_returns_none_when_closed(self):
        conn = self._make_connection()
        conn.close()
        result = conn.send(b'hello')
        self.assertIsNone(result)

    def test_send_respects_buffer_limit(self):
        conn = self._make_connection(buffer_limit=10)
        result1 = conn.send(b'hello')  # 5 bytes
        self.assertEqual(result1, 5)
        result2 = conn.send(b'world')  # 5 more bytes = 10 total
        self.assertEqual(result2, 5)
        result3 = conn.send(b'!')  # exceeds limit
        self.assertIsNone(result3)

    def test_flush_sends_buffered_data(self):
        conn = self._make_connection()
        conn.send(b'hello')
        sent = conn.flush()
        self.assertEqual(sent, 5)
        self.assertFalse(conn.has_pending_data())

    def test_flush_returns_zero_when_empty(self):
        conn = self._make_connection()
        sent = conn.flush()
        self.assertEqual(sent, 0)

    def test_close_closes_socket(self):
        conn = self._make_connection()
        socket = conn.socket()
        conn.close()
        self.assertTrue(socket.closed)
        self.assertIsNone(conn.socket())

    def test_fileno_returns_none_when_closed(self):
        conn = self._make_connection()
        conn.close()
        self.assertIsNone(conn.fileno())

    def test_has_pending_data_false_initially(self):
        conn = self._make_connection()
        self.assertFalse(conn.has_pending_data())

    def test_is_stale_false_initially(self):
        conn = self._make_connection(send_timeout=1.0)
        self.assertFalse(conn.is_stale())

    def test_is_stale_false_without_pending_data(self):
        conn = self._make_connection(send_timeout=0.001)
        time.sleep(0.01)
        self.assertFalse(conn.is_stale())

    @patch('ser2tcp.connection._time')
    def test_is_stale_true_after_timeout(self, mock_time):
        conn = self._make_connection(send_timeout=5.0)
        # Initial time
        mock_time.time.return_value = 100.0
        conn.send(b'hello')
        # Time after timeout
        mock_time.time.return_value = 106.0
        self.assertTrue(conn.is_stale())

    @patch('ser2tcp.connection._time')
    def test_flush_resets_timeout(self, mock_time):
        conn = self._make_connection(send_timeout=5.0)
        mock_time.time.return_value = 100.0
        conn.send(b'hello')
        mock_time.time.return_value = 103.0
        conn.flush()  # Should reset timer
        mock_time.time.return_value = 106.0
        # Would be stale if timer wasn't reset
        self.assertFalse(conn.has_pending_data())  # Buffer is empty after flush

    def test_default_send_timeout(self):
        conn = self._make_connection()
        self.assertEqual(conn._send_timeout, Connection.DEFAULT_SEND_TIMEOUT)

    def test_default_buffer_limit(self):
        conn = self._make_connection()
        self.assertEqual(conn._buffer_limit, Connection.DEFAULT_BUFFER_LIMIT)


class TestConnectionFlushError(unittest.TestCase):
    def test_flush_returns_none_on_oserror(self):
        mock_socket = Mock()
        mock_socket.send.side_effect = OSError("Connection reset")
        addr = ('127.0.0.1', 12345)
        log = Mock()
        conn = Connection((mock_socket, addr), log=log)
        conn._out_buffer.extend(b'hello')
        result = conn.flush()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()


class TestReadBackpressure(unittest.TestCase):
    """A connection can be told to stop being read.

    The device drains at its baud rate; when its queue backs up the
    honest answer is to stop reading clients, so the TCP window closes
    and the sender waits, rather than buffering without end or dropping
    what has already been accepted.
    """

    def _attached(self):
        conn = Connection((MockSocket(), ('127.0.0.1', 5555)), log=Mock())
        selector = Mock()
        conn.attach(selector, owner='server')
        selector.register.reset_mock()
        return conn, selector

    def test_a_connection_starts_readable(self):
        conn, _ = self._attached()
        self.assertEqual(conn.wanted_events(), selectors.EVENT_READ)

    def test_pausing_drops_read_interest(self):
        conn, _ = self._attached()
        conn.set_read_paused(True)
        self.assertFalse(conn.wanted_events() & selectors.EVENT_READ)

    def test_pausing_with_nothing_to_write_stops_watching_entirely(self):
        """A zero mask is not selectable, so the socket is unregistered"""
        conn, selector = self._attached()
        conn.set_read_paused(True)
        selector.unregister.assert_called_once_with(conn.socket())

    def test_resuming_watches_it_again(self):
        conn, selector = self._attached()
        conn.set_read_paused(True)
        selector.register.reset_mock()
        conn.set_read_paused(False)
        selector.register.assert_called_once()
        self.assertEqual(
            selector.register.call_args.args[1], selectors.EVENT_READ)

    def test_a_paused_connection_still_flushes_what_it_owes(self):
        conn, selector = self._attached()
        conn.send(b'pending')
        conn.set_read_paused(True)
        want = conn.wanted_events()
        self.assertTrue(want & selectors.EVENT_WRITE)
        self.assertFalse(want & selectors.EVENT_READ)
        selector.unregister.assert_not_called()

    def test_pausing_twice_changes_nothing(self):
        conn, selector = self._attached()
        conn.set_read_paused(True)
        selector.unregister.reset_mock()
        conn.set_read_paused(True)
        selector.unregister.assert_not_called()

    def test_the_send_timeout_keeps_running_while_paused(self):
        """Pausing is about reading; what we owe the client is unaffected"""
        conn, _ = self._attached()
        conn.send(b'pending')
        conn.set_read_paused(True)
        conn._last_write_time = time.time() - 3600
        self.assertTrue(conn.is_stale())


class TestBufferLimit(unittest.TestCase):
    """A client that cannot keep up with the device.

    Nothing can slow a serial port down, so there is no backpressure to
    apply in this direction - only a choice between handing the client a
    stream with silent holes in it and telling it to go away. For a
    serial protocol the holes are worse, and the silence is worse still:
    the connection looked healthy while its data was being discarded.
    """

    def _conn(self, limit=100):
        conn = Connection(
            (MockSocket(), ('127.0.0.1', 5555)), buffer_limit=limit,
            log=Mock())
        selector = Mock()
        conn.attach(selector, owner='server')
        return conn

    def test_what_fits_is_accepted(self):
        conn = self._conn()
        self.assertEqual(conn.send(b'x' * 50), 50)
        self.assertFalse(conn.is_closed())

    def test_overflowing_drops_the_client(self):
        conn = self._conn()
        conn.send(b'x' * 80)
        conn.send(b'x' * 80)
        self.assertTrue(conn.is_closed())

    def test_overflowing_says_so(self):
        conn = self._conn()
        conn.send(b'x' * 80)
        conn.send(b'x' * 80)
        self.assertTrue(conn._log.warning.called)
        message = str(conn._log.warning.call_args)
        self.assertIn('buffer', message.lower())

    def test_nothing_half_written_is_left_behind(self):
        """The buffer keeps what it had; the rest was never accepted"""
        conn = self._conn()
        conn.send(b'a' * 80)
        before = bytes(conn._out_buffer)
        conn.send(b'b' * 80)
        self.assertEqual(bytes(conn._out_buffer), before)

    def test_a_reaped_connection_is_visible_to_the_server(self):
        conn = self._conn()
        conn.send(b'x' * 80)
        conn.send(b'x' * 80)
        # process_stale() looks for exactly this.
        self.assertTrue(conn.is_closed())

    def test_without_a_limit_nothing_is_dropped(self):
        conn = self._conn(limit=None)
        for _ in range(10):
            conn.send(b'x' * 1000)
        self.assertFalse(conn.is_closed())
        self.assertEqual(len(conn._out_buffer), 10000)
