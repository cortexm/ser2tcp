"""Tests for WebSocket virtual server"""

import json
import unittest
from unittest.mock import Mock, MagicMock, patch, call

from ser2tcp.server import ConfigError
from ser2tcp.server_websocket import ServerWebSocket


def make_ws_server(
        endpoint='test', token=None, data=True, control=None,
        max_connections=None):
    """Create ServerWebSocket with mock serial proxy"""
    config = {'protocol': 'websocket', 'endpoint': endpoint}
    if token:
        config['token'] = token
    if not data:
        config['data'] = False
    if control:
        config['control'] = control
    if max_connections is not None:
        config['max_connections'] = max_connections
    serial = make_serial_mock()
    serial.disconnect = Mock()
    serial.send = Mock()
    serial.set_rts = Mock()
    serial.set_dtr = Mock()
    return ServerWebSocket(config, serial, log=Mock())


def make_serial_mock():
    """A mock serial proxy a WebSocket server can greet a client about.

    The greeting frame describes the port, so `info` has to be
    something JSON can carry - a bare Mock is not.
    """
    serial = Mock()
    serial.connect.return_value = True
    serial.can_add_connection.return_value = True
    serial.get_signals.return_value = 0
    serial.is_connected = True
    serial.name = 'test'
    serial.serial_config = {'port': '/dev/null', 'baudrate': 9600}
    serial.info = {'name': 'test', 'device': '/dev/null', 'baudrate': 9600}
    return serial


def make_ws_client(addr=('127.0.0.1', 12345)):
    """Create mock uhttp WebSocket client"""
    client = Mock()
    client.addr = addr
    client.is_websocket = True
    client.ws_send = Mock()
    client.ws_close = Mock()
    client.ws_is_text = False
    client.read_buffer = Mock(return_value=None)
    return client


class TestConfig(unittest.TestCase):
    def test_endpoint_required(self):
        with self.assertRaises(ConfigError):
            make_ws_server(endpoint=None)

    def test_data_false_requires_control(self):
        with self.assertRaises(ConfigError):
            make_ws_server(data=False, control=None)

    def test_data_false_with_control(self):
        srv = make_ws_server(
            data=False, control={'rts': True, 'signals': ['rts']})
        self.assertFalse(srv.data_enabled)

    def test_properties(self):
        srv = make_ws_server(endpoint='dev1', token='secret')
        self.assertEqual(srv.protocol, 'WEBSOCKET')
        self.assertEqual(srv.endpoint, 'dev1')
        self.assertEqual(srv.token, 'secret')
        self.assertTrue(srv.data_enabled)
        self.assertIsNone(srv.control)


class TestConnections(unittest.TestCase):
    def test_add_connection(self):
        srv = make_ws_server()
        client = make_ws_client()
        srv.add_connection(client)
        self.assertEqual(len(srv.connections), 1)
        self.assertTrue(srv.has_connections())
        srv._serial.connect.assert_called_once()

    def test_add_connection_serial_fail(self):
        srv = make_ws_server()
        srv._serial.connect.return_value = False
        client = make_ws_client()
        srv.add_connection(client)
        self.assertEqual(len(srv.connections), 0)
        client.ws_close.assert_called_once()

    def test_remove_connection(self):
        srv = make_ws_server()
        client = make_ws_client()
        srv.add_connection(client)
        srv.remove_connection(client)
        self.assertEqual(len(srv.connections), 0)
        srv._serial.disconnect.assert_called_once()

    def test_remove_unknown_connection(self):
        srv = make_ws_server()
        client = make_ws_client()
        srv.remove_connection(client)  # should not raise

    def test_close_connections(self):
        srv = make_ws_server()
        clients = [make_ws_client(('127.0.0.1', p)) for p in range(3)]
        for c in clients:
            srv.add_connection(c)
        srv.close_connections()
        self.assertEqual(len(srv.connections), 0)
        for c in clients:
            c.ws_close.assert_called_once()

    def test_process_stale_removes_closed(self):
        srv = make_ws_server()
        client = make_ws_client()
        srv.add_connection(client)
        client.is_websocket = False  # simulate closed
        srv.process_stale()
        self.assertEqual(len(srv.connections), 0)


class TestMaxConnections(unittest.TestCase):
    def test_default_max_connections_is_0(self):
        srv = make_ws_server()
        self.assertEqual(srv._max_connections, 0)

    def test_max_connections_limit_enforced(self):
        srv = make_ws_server(max_connections=2)
        c1 = make_ws_client(('127.0.0.1', 1))
        c2 = make_ws_client(('127.0.0.1', 2))
        c3 = make_ws_client(('127.0.0.1', 3))
        srv.add_connection(c1)
        srv.add_connection(c2)
        srv.add_connection(c3)
        self.assertEqual(len(srv.connections), 2)
        c3.ws_close.assert_called_once_with(1013, 'Server limit reached')

    def test_max_connections_zero_unlimited(self):
        srv = make_ws_server(max_connections=0)
        clients = [make_ws_client(('127.0.0.1', p)) for p in range(10)]
        for c in clients:
            srv.add_connection(c)
        self.assertEqual(len(srv.connections), 10)

    def test_max_connections_one(self):
        srv = make_ws_server(max_connections=1)
        c1 = make_ws_client(('127.0.0.1', 1))
        c2 = make_ws_client(('127.0.0.1', 2))
        srv.add_connection(c1)
        srv.add_connection(c2)
        self.assertEqual(len(srv.connections), 1)
        c2.ws_close.assert_called_once()

    def test_port_level_limit(self):
        """Port-level max_connections limits total across servers"""
        serial = make_serial_mock()
        # A port that counts its users, like the real one. A fixed list
        # of answers would run out: the limit is asked once to take a
        # slot and again to explain a refusal.
        users = []
        serial.can_add_connection = Mock(side_effect=lambda: len(users) < 2)
        serial.connect = Mock(side_effect=lambda: users.append(1) or True)
        config = {'protocol': 'websocket', 'endpoint': 'test', 'max_connections': 0}
        srv = ServerWebSocket(config, serial)
        c1 = make_ws_client(('127.0.0.1', 1))
        c2 = make_ws_client(('127.0.0.1', 2))
        c3 = make_ws_client(('127.0.0.1', 3))
        srv.add_connection(c1)
        srv.add_connection(c2)
        srv.add_connection(c3)
        self.assertEqual(len(srv.connections), 2)
        c3.ws_close.assert_called_once_with(1013, 'Port limit reached')


class TestDataForwarding(unittest.TestCase):
    def test_send_binary_to_clients(self):
        srv = make_ws_server()
        c1 = make_ws_client(('127.0.0.1', 1))
        c2 = make_ws_client(('127.0.0.1', 2))
        srv.add_connection(c1)
        srv.add_connection(c2)
        srv.send(b'\x01\x02\x03')
        c1.ws_send.assert_called_with(b'\x01\x02\x03')
        c2.ws_send.assert_called_with(b'\x01\x02\x03')

    def test_send_skipped_when_data_disabled(self):
        srv = make_ws_server(
            data=False, control={'rts': True, 'signals': ['rts']})
        client = make_ws_client()
        srv.add_connection(client)
        client.ws_send.reset_mock()  # clear initial signal report
        srv.send(b'\x01\x02')
        client.ws_send.assert_not_called()

    def test_receive_binary_forwards_to_serial(self):
        srv = make_ws_server()
        client = make_ws_client()
        client.read_buffer.return_value = b'\x01\x02\x03'
        client.ws_is_text = False
        srv.add_connection(client)
        srv.process_message(client)
        # Named as the source, so a monitor can say who wrote it.
        srv._serial.send.assert_called_with(b'\x01\x02\x03', client)

    def test_receive_binary_ignored_when_data_disabled(self):
        srv = make_ws_server(
            data=False, control={'rts': True, 'signals': ['rts']})
        client = make_ws_client()
        client.read_buffer.return_value = b'\x01\x02'
        client.ws_is_text = False
        srv.add_connection(client)
        srv.process_message(client)
        srv._serial.send.assert_not_called()

    def test_send_removes_failed_connection(self):
        srv = make_ws_server()
        client = make_ws_client()
        client.ws_send.side_effect = OSError
        srv.add_connection(client)
        srv.send(b'\x01')
        self.assertEqual(len(srv.connections), 0)


class TestControl(unittest.TestCase):
    def test_rts_command(self):
        srv = make_ws_server(control={'rts': True, 'signals': ['rts']})
        client = make_ws_client()
        client.read_buffer.return_value = json.dumps({'rts': True}).encode()
        client.ws_is_text = True
        srv.add_connection(client)
        srv.process_message(client)
        srv._serial.set_rts.assert_called_with(True)

    def test_dtr_command(self):
        srv = make_ws_server(control={'dtr': True, 'signals': ['dtr']})
        client = make_ws_client()
        client.read_buffer.return_value = json.dumps({'dtr': False}).encode()
        client.ws_is_text = True
        srv.add_connection(client)
        srv.process_message(client)
        srv._serial.set_dtr.assert_called_with(False)

    def test_rts_ignored_when_not_enabled(self):
        srv = make_ws_server(control={'rts': False, 'signals': ['rts']})
        client = make_ws_client()
        client.read_buffer.return_value = json.dumps({'rts': True}).encode()
        client.ws_is_text = True
        srv.add_connection(client)
        srv.process_message(client)
        srv._serial.set_rts.assert_not_called()

    def test_control_ignored_without_config(self):
        srv = make_ws_server()  # no control
        client = make_ws_client()
        client.read_buffer.return_value = json.dumps({'rts': True}).encode()
        client.ws_is_text = True
        srv.add_connection(client)
        srv.process_message(client)
        srv._serial.set_rts.assert_not_called()

    def test_invalid_json_ignored(self):
        srv = make_ws_server(control={'rts': True, 'signals': ['rts']})
        client = make_ws_client()
        client.read_buffer.return_value = b'not json{'
        client.ws_is_text = True
        srv.add_connection(client)
        srv.process_message(client)  # should not raise

    def test_signal_report_on_connect(self):
        srv = make_ws_server(
            control={'signals': ['rts', 'cts']})
        srv._serial.get_signals.return_value = 0b000101  # rts + cts
        client = make_ws_client()
        srv.add_connection(client)
        # Should have sent signal report
        client.ws_send.assert_called_once()
        msg = json.loads(client.ws_send.call_args[0][0])
        self.assertEqual(msg['signals']['rts'], True)
        self.assertEqual(msg['signals']['cts'], True)

    def test_signal_report_filters_configured(self):
        srv = make_ws_server(
            control={'signals': ['rts']})
        srv._serial.get_signals.return_value = 0b111111  # all signals
        client = make_ws_client()
        srv.add_connection(client)
        msg = json.loads(client.ws_send.call_args[0][0])
        self.assertIn('rts', msg['signals'])
        self.assertNotIn('dtr', msg['signals'])
        self.assertNotIn('cts', msg['signals'])

    def test_send_signal_report_to_all(self):
        srv = make_ws_server(
            control={'signals': ['rts', 'dtr']})
        c1 = make_ws_client(('127.0.0.1', 1))
        c2 = make_ws_client(('127.0.0.1', 2))
        srv.add_connection(c1)
        srv.add_connection(c2)
        # Reset initial signal report calls
        c1.ws_send.reset_mock()
        c2.ws_send.reset_mock()
        srv.send_signal_report(0b01)  # rts only
        msg1 = json.loads(c1.ws_send.call_args[0][0])
        msg2 = json.loads(c2.ws_send.call_args[0][0])
        # Only the line that moved: both were told the whole set when
        # they arrived, so dtr staying low is not news.
        self.assertEqual(msg1, {'signals': {'rts': True}})
        self.assertEqual(msg1, msg2)

    def test_send_signal_report_no_control(self):
        srv = make_ws_server()  # no control
        client = make_ws_client()
        srv.add_connection(client)
        client.ws_send.reset_mock()  # clear the greeting
        srv.send_signal_report(0b01)  # should be no-op
        client.ws_send.assert_not_called()


class TestSocketOwnership(unittest.TestCase):
    """A WebSocket server owns no sockets of its own.

    Its connections arrive already accepted by uhttp, which registers
    them in the shared selector and drives their reads and writes. The
    only thing the event loop asks of this class is the periodic tick.
    """

    def test_is_not_a_selector_owner(self):
        srv = make_ws_server()
        self.assertFalse(hasattr(srv, 'handle_event'))

    def test_offers_the_periodic_hook(self):
        srv = make_ws_server()
        srv.process_stale()  # should not raise
        self.assertTrue(callable(srv.close))


class TestWebSocketBackpressure(unittest.TestCase):
    """WebSocket clients can be held back like any other, since uhttp 3.1.

    Before that there was no handle on these sockets - uhttp owns them
    and their selector registration - so a WebSocket client feeding a
    slow device was capped by the serial write buffer's hard limit and
    had its data dropped once it filled. pause_reading() stops the
    socket being read, its kernel buffer fills, and TCP stalls the peer
    instead.
    """

    def _server(self):
        return ServerWebSocket(
            {'protocol': 'websocket', 'endpoint': 'dev'},
            make_serial_mock(), log=Mock())

    def _client(self):
        client = Mock()
        client.addr = ('10.0.0.1', 4444)
        return client

    def test_pausing_stops_the_socket_being_read(self):
        server = self._server()
        client = self._client()
        server.add_connection(client)
        server.set_read_paused(True)
        client.pause_reading.assert_called_once()

    def test_resuming_starts_it_again(self):
        server = self._server()
        client = self._client()
        server.add_connection(client)
        server.set_read_paused(True)
        server.set_read_paused(False)
        client.resume_reading.assert_called_once()

    def test_every_client_on_the_endpoint_is_held_back(self):
        server = self._server()
        clients = [self._client() for _ in range(3)]
        for client in clients:
            server.add_connection(client)
        server.set_read_paused(True)
        for client in clients:
            client.pause_reading.assert_called_once()

    def test_asking_twice_changes_nothing(self):
        server = self._server()
        client = self._client()
        server.add_connection(client)
        server.set_read_paused(True)
        server.set_read_paused(True)
        client.pause_reading.assert_called_once()

    def test_a_client_that_joins_while_paused_waits_like_the_others(self):
        server = self._server()
        server.set_read_paused(True)
        client = self._client()
        server.add_connection(client)
        client.pause_reading.assert_called_once()

    def test_sending_to_a_paused_client_is_untouched(self):
        """Backpressure is about the direction that is backed up"""
        server = self._server()
        client = self._client()
        server.add_connection(client)
        server.set_read_paused(True)
        server.send(b'from the device')
        client.ws_send.assert_called_with(b'from the device')

    def test_a_socket_that_went_away_is_not_a_problem(self):
        server = self._server()
        client = self._client()
        client.pause_reading.side_effect = OSError('gone')
        server.add_connection(client)
        server.set_read_paused(True)  # must not raise

    def test_an_older_uhttp_without_the_call_is_survivable(self):
        server = self._server()
        client = self._client()
        del client.pause_reading
        server.add_connection(client)
        server.set_read_paused(True)  # must not raise
