"""Tests for connection control protocol"""

import unittest
from unittest.mock import Mock

from ser2tcp.connection_tcp import ConnectionTcp
from ser2tcp.connection_control import (
    wrap_control, ESCAPE, CMD_RTS_LOW, CMD_RTS_HIGH,
    CMD_DTR_LOW, CMD_DTR_HIGH, CMD_GET_SIGNALS, REPORT_BASE,
    SIGNAL_BITS,
)


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


class TestControlProtocol(unittest.TestCase):
    def _make_connection(self, signals=None, rts=True, dtr=True):
        """Create a control-wrapped TCP connection"""
        if signals is None:
            signals = ['rts', 'dtr', 'cts', 'dsr', 'ri', 'cd']
        control_config = {'signals': signals, 'rts': rts, 'dtr': dtr}
        ControlTcp = wrap_control(ConnectionTcp, control_config)
        mock_socket = MockSocket()
        addr = ('127.0.0.1', 12345)
        mock_serial = Mock()
        log = Mock()
        conn = ControlTcp(
            (mock_socket, addr), mock_serial, log=log)
        mock_socket.sent_data.clear()
        return conn, mock_serial

    # --- Send escaping ---

    def test_send_escapes_ff(self):
        """0xFF in outgoing data should be escaped to FF FF"""
        conn, _ = self._make_connection()
        conn.send(b'\xff')
        conn.flush()
        self.assertEqual(conn.socket().sent_data, b'\xff\xff')

    def test_send_escapes_multiple_ff(self):
        conn, _ = self._make_connection()
        conn.send(b'a\xffb\xffc')
        conn.flush()
        self.assertEqual(conn.socket().sent_data, b'a\xff\xffb\xff\xffc')

    def test_send_plain_data(self):
        conn, _ = self._make_connection()
        conn.send(b'hello')
        conn.flush()
        self.assertEqual(conn.socket().sent_data, b'hello')

    # --- Receive parsing ---

    def test_receive_plain_data(self):
        """Plain data should be forwarded to serial"""
        conn, serial = self._make_connection()
        conn.on_received(b'hello')
        # Named as the source, so a monitor can say who wrote it.
        serial.send.assert_called_once_with(b'hello', conn)

    def test_receive_ff_ff_literal(self):
        """FF FF should become single 0xFF"""
        conn, serial = self._make_connection()
        conn.on_received(b'\xff\xff')
        serial.send.assert_called_once_with(bytes([0xff]), conn)

    def test_receive_rts_low(self):
        conn, serial = self._make_connection(rts=True)
        conn.on_received(bytes([ESCAPE, CMD_RTS_LOW]))
        serial.set_rts.assert_called_once_with(False)
        serial.send.assert_not_called()

    def test_receive_rts_high(self):
        conn, serial = self._make_connection(rts=True)
        conn.on_received(bytes([ESCAPE, CMD_RTS_HIGH]))
        serial.set_rts.assert_called_once_with(True)
        serial.send.assert_not_called()

    def test_receive_rts_ignored_when_disabled(self):
        conn, serial = self._make_connection(rts=False)
        conn.on_received(bytes([ESCAPE, CMD_RTS_HIGH]))
        serial.set_rts.assert_not_called()

    def test_receive_dtr_low(self):
        conn, serial = self._make_connection(dtr=True)
        conn.on_received(bytes([ESCAPE, CMD_DTR_LOW]))
        serial.set_dtr.assert_called_once_with(False)
        serial.send.assert_not_called()

    def test_receive_dtr_high(self):
        conn, serial = self._make_connection(dtr=True)
        conn.on_received(bytes([ESCAPE, CMD_DTR_HIGH]))
        serial.set_dtr.assert_called_once_with(True)
        serial.send.assert_not_called()

    def test_receive_dtr_ignored_when_disabled(self):
        conn, serial = self._make_connection(dtr=False)
        conn.on_received(bytes([ESCAPE, CMD_DTR_HIGH]))
        serial.set_dtr.assert_not_called()

    def test_receive_get_signals(self):
        """GET signals should trigger a signal report"""
        conn, serial = self._make_connection()
        serial.get_signals.return_value = 0b001100  # CTS + DSR
        conn.on_received(bytes([ESCAPE, CMD_GET_SIGNALS]))
        serial.get_signals.assert_called_once()
        conn.flush()
        # Should send FF 8C (0x80 | 0x0C)
        self.assertEqual(
            conn.socket().sent_data,
            bytes([ESCAPE, REPORT_BASE | 0x0C]))

    def test_receive_mixed_data_and_commands(self):
        """Data mixed with control commands"""
        conn, serial = self._make_connection(rts=True)
        # "AB" + RTS HIGH + "CD"
        data = b'AB' + bytes([ESCAPE, CMD_RTS_HIGH]) + b'CD'
        conn.on_received(data)
        serial.set_rts.assert_called_once_with(True)
        # serial.send should be called with combined clean data
        serial.send.assert_called_once_with(b'ABCD', conn)

    def test_receive_split_escape(self):
        """Escape byte at end of chunk, command in next chunk"""
        conn, serial = self._make_connection(rts=True)
        conn.on_received(b'AB\xff')
        serial.send.assert_called_once_with(b'AB', conn)
        serial.send.reset_mock()
        conn.on_received(bytes([CMD_RTS_HIGH]))
        serial.set_rts.assert_called_once_with(True)

    def test_receive_unknown_command(self):
        """Unknown command should log warning"""
        conn, serial = self._make_connection()
        conn.on_received(bytes([ESCAPE, 0x99]))
        serial.send.assert_not_called()

    # --- Signal report ---

    def test_send_signal_report(self):
        conn, _ = self._make_connection()
        bitmask = (1 << SIGNAL_BITS['cts']) | (1 << SIGNAL_BITS['rts'])
        conn.send_signal_report(bitmask)
        conn.flush()
        expected = bytes([ESCAPE, REPORT_BASE | bitmask])
        self.assertEqual(conn.socket().sent_data, expected)

    def test_send_signal_report_filters_unconfigured(self):
        """Only configured signals should be in report"""
        conn, _ = self._make_connection(signals=['cts', 'dsr'])
        # Full bitmask with all signals
        bitmask = 0x3F
        conn.send_signal_report(bitmask)
        conn.flush()
        # Only CTS (bit 2) and DSR (bit 3) should remain
        expected_mask = (1 << SIGNAL_BITS['cts']) | (1 << SIGNAL_BITS['dsr'])
        expected = bytes([ESCAPE, REPORT_BASE | expected_mask])
        self.assertEqual(conn.socket().sent_data, expected)

    def test_empty_control_still_escapes(self):
        """Control with no signals/rts/dtr still escapes 0xFF"""
        conn, serial = self._make_connection(
            signals=[], rts=False, dtr=False)
        conn.send(b'\xff')
        conn.flush()
        self.assertEqual(conn.socket().sent_data, b'\xff\xff')
        # Incoming FF FF still produces literal 0xFF
        conn.on_received(b'\xff\xff')
        serial.send.assert_called_once_with(bytes([0xff]), conn)

    def test_class_name(self):
        """Wrapped class should have descriptive name"""
        ControlTcp = wrap_control(ConnectionTcp, {'signals': []})
        self.assertEqual(ControlTcp.__name__, 'ControlConnectionTcp')


if __name__ == "__main__":
    unittest.main()
