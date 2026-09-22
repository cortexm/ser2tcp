"""Tests for ConnectionTelnet class"""

import unittest
import unittest.mock
from unittest.mock import Mock

from ser2tcp.connection_telnet import ConnectionTelnet


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


class TestConnectionTelnet(unittest.TestCase):
    def _make_connection(self):
        """Helper to create ConnectionTelnet with mock socket"""
        mock_socket = MockSocket()
        addr = ('127.0.0.1', 12345)
        mock_serial = Mock()
        log = Mock()
        conn = ConnectionTelnet(
            (mock_socket, addr),
            mock_serial,
            log=log)
        # Flush initial negotiation
        conn.flush()
        mock_socket.sent_data.clear()
        return conn, mock_serial

    def test_send_escapes_iac(self):
        """IAC bytes (0xff) should be escaped to 0xff 0xff"""
        conn, _ = self._make_connection()
        conn.send(b'\xff')
        conn.flush()
        self.assertEqual(conn.socket().sent_data, b'\xff\xff')

    def test_send_escapes_multiple_iac(self):
        conn, _ = self._make_connection()
        conn.send(b'a\xff b\xff c')
        conn.flush()
        self.assertEqual(conn.socket().sent_data, b'a\xff\xff b\xff\xff c')

    def test_on_received_plain_data(self):
        """Plain data should be forwarded to serial"""
        conn, serial = self._make_connection()
        conn.on_received(b'hello')
        # Named as the source, so a monitor can say who wrote it.
        serial.send.assert_called_once_with(bytearray(b'hello'), conn)

    def test_on_received_escaped_iac(self):
        """Escaped IAC (0xff 0xff) should become single 0xff"""
        conn, serial = self._make_connection()
        conn.on_received(b'\xff\xff')
        serial.send.assert_called_once_with(bytes((0xff,)), conn)

    def test_on_received_telnet_will_command(self):
        """TELNET WILL command should not be forwarded"""
        conn, serial = self._make_connection()
        # IAC WILL 0x01 (echo)
        conn.on_received(bytes((0xff, 0xfb, 0x01)))
        serial.send.assert_not_called()

    def test_on_received_telnet_wont_command(self):
        """TELNET WONT command should not be forwarded"""
        conn, serial = self._make_connection()
        # IAC WONT 0x01
        conn.on_received(bytes((0xff, 0xfc, 0x01)))
        serial.send.assert_not_called()

    def test_on_received_telnet_do_command(self):
        """TELNET DO command should not be forwarded"""
        conn, serial = self._make_connection()
        # IAC DO 0x01
        conn.on_received(bytes((0xff, 0xfd, 0x01)))
        serial.send.assert_not_called()

    def test_on_received_telnet_dont_command(self):
        """TELNET DONT command should not be forwarded"""
        conn, serial = self._make_connection()
        # IAC DONT 0x01
        conn.on_received(bytes((0xff, 0xfe, 0x01)))
        serial.send.assert_not_called()

    def test_on_received_mixed_data_and_command(self):
        """Data mixed with TELNET commands"""
        conn, serial = self._make_connection()
        # "hello" + IAC WILL 0x01 + "world"
        conn.on_received(b'hello\xff\xfb\x01world')
        # Should receive "hello" then "world"
        calls = serial.send.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][0], bytearray(b'hello'))
        self.assertEqual(calls[1][0][0], bytearray(b'world'))

    def test_on_received_subnegotiation(self):
        """TELNET subnegotiation should be handled"""
        conn, serial = self._make_connection()
        # IAC SB 0x22 (some data) IAC SE
        conn.on_received(bytes((0xff, 0xfa, 0x22, 0x01, 0x02, 0xff, 0xf0)))
        serial.send.assert_not_called()

    def test_data_after_subnegotiation_reaches_the_device(self):
        """The SB state has to end at IAC SE.

        Left set, the next data byte lands in a subnegotiation buffer
        that was already dropped - which used to raise AttributeError
        and, unhandled, take the whole process down. A client that
        answers the LINEMODE request this server opens with (IAC DO
        0x22) gets there on its first reply.
        """
        conn, serial = self._make_connection()
        conn.on_received(bytes((0xff, 0xfa, 0x22, 0x01, 0xff, 0xf0)))
        conn.on_received(b'hello')
        # Named as the source, so a monitor can say who wrote it.
        serial.send.assert_called_once_with(bytearray(b'hello'), conn)

    def test_data_after_subnegotiation_in_the_same_packet(self):
        """The end of a subnegotiation and data can share one recv()"""
        conn, serial = self._make_connection()
        conn.on_received(
            bytes((0xff, 0xfa, 0x22, 0x01, 0xff, 0xf0)) + b'hello')
        # Named as the source, so a monitor can say who wrote it.
        serial.send.assert_called_once_with(bytearray(b'hello'), conn)

    def test_subnegotiation_payload_is_collected(self):
        """Everything between SB and SE is handed over, and only that"""
        conn, serial = self._make_connection()
        with unittest.mock.patch.object(
                conn, '_telnet_subnegotiation') as handler:
            conn.on_received(
                bytes((0xff, 0xfa, 0x22, 0x01, 0x02, 0x03, 0xff, 0xf0)))
        handler.assert_called_once_with(bytearray((0x22, 0x01, 0x02, 0x03)))
        serial.send.assert_not_called()

    def test_subnegotiation_split_across_packets(self):
        """A subnegotiation may arrive in as many pieces as TCP likes"""
        conn, serial = self._make_connection()
        with unittest.mock.patch.object(
                conn, '_telnet_subnegotiation') as handler:
            conn.on_received(bytes((0xff, 0xfa, 0x22)))
            conn.on_received(bytes((0x01, 0x02)))
            conn.on_received(bytes((0xff, 0xf0)))
        handler.assert_called_once_with(bytearray((0x22, 0x01, 0x02)))
        serial.send.assert_not_called()
        conn.on_received(b'after')
        serial.send.assert_called_once_with(bytearray(b'after'), conn)

    def test_escaped_iac_inside_subnegotiation_is_a_literal_byte(self):
        """IAC IAC inside SB is data, not the start of a command"""
        conn, serial = self._make_connection()
        with unittest.mock.patch.object(
                conn, '_telnet_subnegotiation') as handler:
            conn.on_received(
                bytes((0xff, 0xfa, 0x22, 0xff, 0xff, 0x01, 0xff, 0xf0)))
        handler.assert_called_once_with(bytearray((0x22, 0xff, 0x01)))
        serial.send.assert_not_called()

    def test_subnegotiation_end_without_a_start_is_survivable(self):
        """A stray IAC SE is a protocol error, not a reason to die"""
        conn, serial = self._make_connection()
        conn.on_received(bytes((0xff, 0xf0)))
        conn.on_received(b'hello')
        # Named as the source, so a monitor can say who wrote it.
        serial.send.assert_called_once_with(bytearray(b'hello'), conn)

    def test_two_subnegotiations_in_a_row(self):
        """State from the first must not leak into the second"""
        conn, serial = self._make_connection()
        with unittest.mock.patch.object(
                conn, '_telnet_subnegotiation') as handler:
            conn.on_received(bytes((0xff, 0xfa, 0x22, 0x01, 0xff, 0xf0)))
            conn.on_received(bytes((0xff, 0xfa, 0x18, 0x02, 0xff, 0xf0)))
        self.assertEqual(
            [call.args[0] for call in handler.call_args_list],
            [bytearray((0x22, 0x01)), bytearray((0x18, 0x02))])
        conn.on_received(b'ok')
        serial.send.assert_called_once_with(bytearray(b'ok'), conn)

    def test_initial_negotiation_sent(self):
        """Initial TELNET negotiation should be sent on connect"""
        mock_socket = MockSocket()
        addr = ('127.0.0.1', 12345)
        mock_serial = Mock()
        log = Mock()
        conn = ConnectionTelnet((mock_socket, addr), mock_serial, log=log)
        conn.flush()
        # Should contain IAC DO 0x22 and IAC WILL 0x01
        self.assertIn(bytes((0xff, 0xfd, 0x22)), mock_socket.sent_data)
        self.assertIn(bytes((0xff, 0xfb, 0x01)), mock_socket.sent_data)


class TestTelnetConstants(unittest.TestCase):
    def test_iac_value(self):
        self.assertEqual(ConnectionTelnet.TELNET_IAC, 0xff)

    def test_command_values(self):
        self.assertEqual(ConnectionTelnet.TELNET_WILL, 0xfb)
        self.assertEqual(ConnectionTelnet.TELNET_WONT, 0xfc)
        self.assertEqual(ConnectionTelnet.TELNET_DO, 0xfd)
        self.assertEqual(ConnectionTelnet.TELNET_DONT, 0xfe)

    def test_subnegotiation_values(self):
        self.assertEqual(ConnectionTelnet.TELNET_SB, 0xfa)
        self.assertEqual(ConnectionTelnet.TELNET_SE, 0xf0)


if __name__ == "__main__":
    unittest.main()
