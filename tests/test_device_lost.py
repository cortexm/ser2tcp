"""Tests for a device that goes away and comes back.

A serial error used to drop every client on the port. The reasoning
was sound while a client had no other way of being told: holding one
open on a port that is gone only feeds it silence.

A WebSocket client is no longer in that position - it has a channel
for `serial`, and `access`/`attach` gave it a reason to stay. So it
keeps its socket, is told the device went away, and is told again
when it comes back. TCP, TELNET, SSL and Unix sockets have no such
channel and keep being dropped.
"""

import unittest
from unittest.mock import Mock

from ser2tcp.serial_proxy import SerialProxy
from ser2tcp.server_websocket import ServerWebSocket

from tests.test_ws_messages import _client, _frames, _last, _serial


def _proxy():
    """A proxy with just enough set up to fail a read and recover"""
    proxy = SerialProxy.__new__(SerialProxy)
    proxy._log = Mock()
    proxy._servers = []
    proxy._monitors = []
    proxy._serial = Mock()
    proxy._serial_config = {'port': '/dev/ttyUSB0', 'baudrate': 9600}
    proxy._match = None
    proxy._name = 'dev'
    proxy._selector = None
    proxy._serial_source = None
    proxy._serial_interest = None
    proxy._reader_thread = None
    proxy._reader_sock_r = None
    proxy._reader_sock_w = None
    proxy._reader_running = False
    proxy._out_buffer = bytearray()
    proxy._read_paused = False
    proxy._last_signals = None
    proxy._last_signal_poll = 0
    proxy._signal_poll_interval = 0.1
    proxy._has_control_servers = False
    proxy._last_drop_warning = 0
    proxy._last_reopen = 0
    proxy._open_warned = False
    return proxy


class TestWhoIsDropped(unittest.TestCase):

    def setUp(self):
        self.proxy = _proxy()

    def test_a_socket_server_still_loses_its_clients(self):
        """It has no channel to be told on; silence is worse."""
        server = Mock()
        server.protocol = 'TCP'
        self.proxy._servers = [server]
        self.proxy._serial_failed()
        server.on_serial_lost.assert_called_once()

    def test_the_device_is_closed_even_though_somebody_wants_it(self):
        """disconnect() keeps a port that clients are holding; after an
        I/O error there is nothing left to hold."""
        server = Mock()
        server.has_connections.return_value = True
        self.proxy._servers = [server]
        self.proxy._serial_failed()
        self.assertIsNone(self.proxy._serial)


class WebSocketDeviceTestCase(unittest.TestCase):

    def setUp(self):
        self.serial = _serial()
        self.server = ServerWebSocket(
            {'protocol': 'websocket', 'endpoint': 'x'},
            self.serial, log=Mock())

    def join(self):
        client = _client()
        self.server.add_connection(client)
        client.ws_send.reset_mock()
        return client


class TestTellingAWebSocketClient(WebSocketDeviceTestCase):

    def test_it_is_not_closed(self):
        client = self.join()
        self.server.on_serial_lost('device disappeared')
        client.ws_close.assert_not_called()
        self.assertIn(client, self.server.connections)

    def test_it_is_told_the_device_went_away(self):
        client = self.join()
        self.server.on_serial_lost('device disappeared')
        serial = _last(client)['serial']
        self.assertIs(serial['connected'], False)
        self.assertEqual(serial['reason'], 'device disappeared')

    def test_it_is_still_attached(self):
        """Attached means wanting the device, which is what makes it
        worth reopening - the client did not change its mind."""
        client = self.join()
        self.server.on_serial_lost('device disappeared')
        self.assertIn(client, self.server.attached)

    def test_and_is_told_when_it_comes_back(self):
        client = self.join()
        self.server.on_serial_lost('device disappeared')
        client.ws_send.reset_mock()
        self.server.on_serial_found()
        self.assertEqual(_last(client), {'serial': {'connected': True}})

    def test_a_detached_client_hears_it_too(self):
        client = self.join()
        self.server.detach(client)
        client.ws_send.reset_mock()
        self.server.on_serial_lost('gone')
        self.assertIn('serial', _last(client))

    def test_the_signal_baseline_is_dropped(self):
        """Whatever the lines read before says nothing about the device
        that comes back, so the next report is a full set again."""
        server = ServerWebSocket(
            {'protocol': 'websocket', 'endpoint': 'x',
             'control': {'signals': ['rts', 'dtr']}},
            self.serial, log=Mock())
        client = _client()
        server.add_connection(client)
        server.send_signal_report(0b01)
        server.on_serial_lost('gone')
        client.ws_send.reset_mock()
        server.send_signal_report(0b01)
        self.assertEqual(
            _last(client), {'signals': {'rts': True, 'dtr': False}})


class TestConnectingWhileTheDeviceIsAway(WebSocketDeviceTestCase):
    """Being refused with 1011 and being told are the same news; only
    one of them leaves a client able to wait for better."""

    def setUp(self):
        super().setUp()
        self.serial.connect.return_value = False
        self.serial.is_connected = False

    def test_the_client_is_accepted(self):
        client = _client()
        self.server.add_connection(client)
        client.ws_close.assert_not_called()
        self.assertIn(client, self.server.connections)

    def test_and_told_the_device_is_not_there(self):
        client = _client()
        self.server.add_connection(client)
        self.assertEqual(
            _frames(client)[0]['serial'], {'connected': False})

    def test_and_counts_as_wanting_it(self):
        """Somebody has to be waiting, or nothing reopens the port."""
        client = _client()
        self.server.add_connection(client)
        self.assertIn(client, self.server.attached)

    def test_but_the_port_limit_still_refuses(self):
        self.serial.can_add_connection.return_value = False
        client = _client()
        self.server.add_connection(client)
        client.ws_close.assert_called_once()
        self.assertNotIn(client, self.server.connections)


class TestReopeningThePort(unittest.TestCase):

    def setUp(self):
        self.proxy = _proxy()
        self.proxy._serial = None
        self.server = Mock()
        self.server.has_connections.return_value = True
        self.proxy._servers = [self.server]

    def test_it_is_tried_while_somebody_is_waiting(self):
        self.proxy.connect = Mock(return_value=True)
        self.proxy.process_stale()
        self.proxy.connect.assert_called_once()

    def test_and_not_when_nobody_is(self):
        self.server.has_connections.return_value = False
        self.proxy.connect = Mock(return_value=True)
        self.proxy.process_stale()
        self.proxy.connect.assert_not_called()

    def test_nor_when_the_port_is_already_open(self):
        self.proxy._serial = Mock()
        self.proxy.connect = Mock(return_value=True)
        self.proxy.process_stale()
        self.proxy.connect.assert_not_called()

    def test_the_servers_are_told_when_it_works(self):
        self.proxy.connect = Mock(return_value=True)
        self.proxy.process_stale()
        self.server.on_serial_found.assert_called_once()

    def test_and_not_when_it_does_not(self):
        self.proxy.connect = Mock(return_value=False)
        self.proxy.process_stale()
        self.server.on_serial_found.assert_not_called()

    def test_it_is_not_tried_on_every_pass(self):
        """The loop turns over many times a second; a device that is
        unplugged is not going to be back by the next one."""
        self.proxy.connect = Mock(return_value=False)
        self.proxy.process_stale()
        self.proxy.process_stale()
        self.proxy.connect.assert_called_once()


class TestNotSayingItTwice(unittest.TestCase):
    """A device left unplugged must not fill the log.

    The retry runs for as long as somebody is waiting, which can be
    hours, and connect() logs whatever went wrong.
    """

    def setUp(self):
        self.proxy = _proxy()
        self.proxy._serial = None

    def _fail_to_open(self):
        with unittest.mock.patch(
                'ser2tcp.serial_proxy._serial.Serial',
                side_effect=OSError('no such device')):
            return self.proxy.connect()

    def test_the_first_failure_is_a_warning(self):
        self._fail_to_open()
        self.proxy._log.warning.assert_called_once()

    def test_the_next_one_is_not(self):
        self._fail_to_open()
        self.proxy._log.warning.reset_mock()
        self._fail_to_open()
        self.proxy._log.warning.assert_not_called()

    def test_but_it_is_said_again_after_it_worked(self):
        self._fail_to_open()
        self.proxy._open_warned = False      # as a success clears it
        self.proxy._log.warning.reset_mock()
        self._fail_to_open()
        self.proxy._log.warning.assert_called_once()


if __name__ == '__main__':
    unittest.main()
