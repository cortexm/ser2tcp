"""Tests for a server's access mode — which way data may flow.

`data: true/false` could only say "everything" or "nothing". A tap that
reads a line without being able to disturb it, or an injector that may
write without seeing what anybody else is doing, had no way to be
expressed. `access` says the direction instead.
"""

import unittest
from unittest.mock import Mock

from ser2tcp.connection_control import wrap_control
from ser2tcp.connection_socket import ConnectionSocket
from ser2tcp.connection_tcp import ConnectionTcp
from ser2tcp.connection_telnet import ConnectionTelnet
from ser2tcp.server import ConfigError, Server, parse_access
from ser2tcp.server_websocket import ServerWebSocket


class TestParsingTheMode(unittest.TestCase):

    def _parse(self, **config):
        return parse_access(config)

    def test_the_default_is_both_ways(self):
        self.assertEqual(self._parse(), (True, True))

    def test_read_only(self):
        self.assertEqual(self._parse(access='ro'), (True, False))

    def test_write_only(self):
        self.assertEqual(self._parse(access='wo'), (False, True))

    def test_neither(self):
        self.assertEqual(self._parse(access='none'), (False, False))

    def test_both_spelled_out(self):
        self.assertEqual(self._parse(access='rw'), (True, True))


class TestTheOlderSpelling(unittest.TestCase):
    """`data` said the same thing for the two cases it could express."""

    def test_data_false_is_none(self):
        self.assertEqual(parse_access({'data': False}), (False, False))

    def test_data_true_is_rw(self):
        self.assertEqual(parse_access({'data': True}), (True, True))

    def test_saying_the_same_thing_twice_is_fine(self):
        self.assertEqual(
            parse_access({'data': False, 'access': 'none'}), (False, False))
        self.assertEqual(
            parse_access({'data': True, 'access': 'rw'}), (True, True))

    def test_saying_two_different_things_is_refused(self):
        """Picking one silently is how a config comes to mean less than
        it says — the same mistake the IP filter used to make."""
        with self.assertRaises(ValueError) as caught:
            parse_access({'data': False, 'access': 'rw'})
        self.assertIn('data', str(caught.exception))
        self.assertIn('access', str(caught.exception))

    def test_data_false_with_a_one_way_mode_is_also_a_conflict(self):
        with self.assertRaises(ValueError):
            parse_access({'data': False, 'access': 'ro'})


class TestAModeThatCannotBeRead(unittest.TestCase):

    def test_an_unknown_mode_names_itself(self):
        with self.assertRaises(ValueError) as caught:
            parse_access({'access': 'readonly'})
        self.assertIn('readonly', str(caught.exception))

    def test_the_message_lists_what_is_allowed(self):
        with self.assertRaises(ValueError) as caught:
            parse_access({'access': 'readonly'})
        for mode in ('rw', 'ro', 'wo', 'none'):
            self.assertIn(mode, str(caught.exception))

    def test_a_non_string_is_refused(self):
        with self.assertRaises(ValueError):
            parse_access({'access': True})

    def test_case_does_not_matter(self):
        self.assertEqual(parse_access({'access': 'RO'}), (True, False))


class TestTheServerCarriesIt(unittest.TestCase):
    """Both server kinds answer the same two questions."""

    def _tcp(self, **extra):
        config = {'protocol': 'tcp', 'address': '127.0.0.1', 'port': 0}
        config.update(extra)
        return Server(config, Mock(), log=Mock())

    def _ws(self, **extra):
        config = {'protocol': 'websocket', 'endpoint': 'x'}
        config.update(extra)
        return ServerWebSocket(config, Mock(), log=Mock())

    def test_tcp_defaults_to_both(self):
        server = self._tcp()
        self.assertTrue(server.can_read)
        self.assertTrue(server.can_write)

    def test_tcp_read_only(self):
        server = self._tcp(access='ro')
        self.assertTrue(server.can_read)
        self.assertFalse(server.can_write)

    def test_websocket_defaults_to_both(self):
        server = self._ws()
        self.assertTrue(server.can_read)
        self.assertTrue(server.can_write)

    def test_websocket_write_only(self):
        server = self._ws(access='wo')
        self.assertFalse(server.can_read)
        self.assertTrue(server.can_write)

    def test_a_bad_mode_stops_the_server_being_built(self):
        with self.assertRaises((ValueError, ConfigError)):
            self._tcp(access='nonsense')

    def test_and_the_same_for_a_websocket(self):
        with self.assertRaises((ValueError, ConfigError)):
            self._ws(access='nonsense')

    def test_neither_direction_still_needs_control(self):
        """An endpoint that neither reads nor writes and has no control
        does nothing at all; saying so is better than running it."""
        with self.assertRaises(ConfigError):
            self._tcp(access='none')

    def test_and_is_fine_with_control(self):
        server = self._tcp(access='none', control={'rts': True})
        self.assertFalse(server.can_read)
        self.assertFalse(server.can_write)


class TestWhatReachesTheDevice(unittest.TestCase):
    """The WebSocket server is the one that can be driven without a
    socket, so the two directions are checked there."""

    def _ws(self, **extra):
        config = {'protocol': 'websocket', 'endpoint': 'x'}
        config.update(extra)
        self.serial = Mock()
        # The frame a new client is greeted with describes the port.
        self.serial.is_connected = True
        self.serial.name = 'test'
        self.serial.serial_config = {'port': '/dev/null', 'baudrate': 9600}
        return ServerWebSocket(config, self.serial, log=Mock())

    def _client(self, text=False, server=None):
        """A client joined the ordinary way, so it is attached.

        Only attached clients carry data now, so reaching into the
        server's lists would test something the real path never does.
        """
        client = Mock()
        client.ws_is_text = text
        client.is_websocket = True
        client.read_buffer.return_value = b'hello'
        if server is not None:
            server.add_connection(client)
        return client

    def test_rw_passes_input_on(self):
        server = self._ws()
        server.process_message(self._client(server=server))
        self.serial.send.assert_called_once_with(b'hello')

    def test_ro_does_not(self):
        server = self._ws(access='ro')
        server.process_message(self._client(server=server))
        self.serial.send.assert_not_called()

    def test_wo_does(self):
        server = self._ws(access='wo', control={'rts': True})
        server.process_message(self._client(server=server))
        self.serial.send.assert_called_once_with(b'hello')

    def test_rw_sends_to_clients(self):
        server = self._ws()
        client = self._client(server=server)
        server.send(b'from device')
        client.ws_send.assert_called_with(b'from device')

    def test_wo_sends_nothing_to_clients(self):
        server = self._ws(access='wo', control={'rts': True})
        client = self._client(server=server)
        client.ws_send.reset_mock()
        server.send(b'from device')
        client.ws_send.assert_not_called()


class TestAConnectionThatMayNotWrite(unittest.TestCase):
    """The gate has to sit on the connection for the socket protocols.

    A WebSocket server reads its clients itself, so one check covers it.
    TCP, TELNET, SSL and Unix sockets hand each connection the serial
    port and let it write — so every one of them needs to know, and the
    check belongs in the one place they share.
    """

    def _connection(self, cls, can_write=True, **kwargs):
        self.serial = Mock()
        return cls((Mock(), ('192.0.2.1', 5000)), self.serial,
                   log=Mock(), can_write=can_write, **kwargs)

    def test_tcp_writes_by_default(self):
        con = self._connection(ConnectionTcp)
        con.on_received(b'hello')
        self.serial.send.assert_called_once_with(b'hello')

    def test_tcp_read_only_does_not_write(self):
        con = self._connection(ConnectionTcp, can_write=False)
        con.on_received(b'hello')
        self.serial.send.assert_not_called()

    def test_socket_read_only_does_not_write(self):
        con = self._connection(ConnectionSocket, can_write=False)
        con.on_received(b'hello')
        self.serial.send.assert_not_called()

    def test_telnet_read_only_does_not_write(self):
        con = self._connection(ConnectionTelnet, can_write=False)
        con.on_received(b'hello')
        self.serial.send.assert_not_called()

    def test_nothing_is_written_for_empty_input(self):
        con = self._connection(ConnectionTcp)
        con.on_received(b'')
        self.serial.send.assert_not_called()


class TestTheControlWrapperRespectsIt(unittest.TestCase):
    """The escape protocol carries data as well, so it needs the gate."""

    def _connection(self, can_read=True, can_write=True):
        cls = wrap_control(
            ConnectionTcp, {'rts': True}, can_read=can_read,
            can_write=can_write)
        self.serial = Mock()
        return cls((Mock(), ('192.0.2.1', 5000)), self.serial,
                   log=Mock(), can_write=can_write)

    def test_data_reaches_the_device(self):
        con = self._connection()
        con.on_received(b'hi')
        self.serial.send.assert_called_once_with(b'hi')

    def test_read_only_keeps_data_off_the_device(self):
        con = self._connection(can_write=False)
        con.on_received(b'hi')
        self.serial.send.assert_not_called()

    def test_but_a_signal_command_still_works(self):
        """Read-only is about data, not about the control protocol"""
        con = self._connection(can_write=False)
        con.on_received(b'\xff\x01')          # RTS high
        self.serial.set_rts.assert_called_once_with(True)


if __name__ == '__main__':
    unittest.main()
