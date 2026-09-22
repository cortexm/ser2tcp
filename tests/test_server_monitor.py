"""Tests for ServerMonitor.

The monitor watches a shared line. Its first byte used to say which
way the data went (1=TX, 2=RX), which answers the wrong question: on
a port with three clients "somebody wrote this" leaves the interesting
part out. It now names the writer, and the peer list says who that is.
"""

import json
import unittest
from unittest.mock import MagicMock

import ser2tcp.server_monitor as monitor


class MockServer:
    """One of the port's servers, holding client connections"""

    def __init__(self, protocol='TCP', control=None):
        self.protocol = protocol
        self.control = control
        self.connections = []


class MockSerialProxy:
    """Mock SerialProxy for testing"""

    def __init__(self, name='test-port', servers=None):
        self._name = name
        self._monitors = []
        self.servers = servers if servers is not None else []
        self.is_connected = True
        self.serial_config = {'port': '/dev/ttyUSB0', 'baudrate': 115200}
        self._signals = 0

    @property
    def name(self):
        return self._name

    @property
    def info(self):
        return {
            'name': self._name,
            'device': self.serial_config.get('port'),
            'baudrate': self.serial_config.get('baudrate'),
        }

    def get_signals(self):
        return self._signals

    def add_monitor(self, mon):
        self._monitors.append(mon)

    def remove_monitor(self, mon):
        if mon in self._monitors:
            self._monitors.remove(mon)

    def notify(self, source, data):
        for mon in list(self._monitors):
            mon.on_data(source, data)

    def notify_signals(self, bitmask):
        self._signals = bitmask
        for mon in list(self._monitors):
            mon.on_signals(bitmask)


class MockClient:
    """Mock uhttp client for testing"""

    def __init__(self):
        self.is_websocket = True
        self.socket = MagicMock()
        self.addr = ('127.0.0.1', 12345)
        self.sent = []
        self.closed = False
        self.close_code = None
        self.close_reason = None

    def ws_send(self, data):
        if self.closed:
            raise OSError("Connection closed")
        self.sent.append(data)

    def ws_close(self, code, reason):
        self.closed = True
        self.close_code = code
        self.close_reason = reason


class MockPeer:
    """A client of the port, as a socket protocol holds one"""

    def __init__(self, address='192.168.1.50', port=51234):
        self._addr = (address, port)

    def get_address(self):
        return self._addr


def binary(client):
    """Every binary frame this client was sent"""
    return [frame for frame in client.sent if isinstance(frame, bytes)]


def frames(client):
    """Every text frame this client was sent, parsed"""
    return [json.loads(frame) for frame in client.sent
            if isinstance(frame, str)]


def last(client):
    """The most recent text frame, parsed"""
    parsed = frames(client)
    if not parsed:
        raise AssertionError('no text frame was sent')
    return parsed[-1]


class TestServerMonitor(unittest.TestCase):
    """Test ServerMonitor initialization"""

    def test_init(self):
        proxy = MockSerialProxy()
        srv = monitor.ServerMonitor(proxy)
        self.assertEqual(srv.connections, [])

    def test_the_device_writes_under_zero(self):
        self.assertEqual(monitor.ServerMonitor.DEVICE, 0)

    def test_the_top_of_the_range_means_no_slot_left(self):
        """Reserved rather than handed out: the 255th client and an
        overflow would otherwise look the same."""
        self.assertEqual(monitor.ServerMonitor.OVERFLOW, 255)
        self.assertEqual(monitor.ServerMonitor.LAST_SLOT, 254)


class TestConnections(unittest.TestCase):
    """Test connection management"""

    def setUp(self):
        self.proxy = MockSerialProxy('myport')
        self.srv = monitor.ServerMonitor(self.proxy)

    def test_add_connection(self):
        client = MockClient()
        self.srv.add_connection(client)
        self.assertIn(client, self.srv.connections)

    def test_add_connection_registers_monitor(self):
        client = MockClient()
        self.srv.add_connection(client)
        self.assertEqual(len(self.proxy._monitors), 1)

    def test_remove_connection(self):
        client = MockClient()
        self.srv.add_connection(client)
        self.srv.remove_connection(client)
        self.assertNotIn(client, self.srv.connections)

    def test_remove_connection_unregisters_monitor(self):
        client = MockClient()
        self.srv.add_connection(client)
        self.srv.remove_connection(client)
        self.assertEqual(len(self.proxy._monitors), 0)

    def test_multiple_clients_share_monitor(self):
        client1 = MockClient()
        client2 = MockClient()
        self.srv.add_connection(client1)
        self.srv.add_connection(client2)
        # Only one registration
        self.assertEqual(len(self.proxy._monitors), 1)
        # Remove first, still registered
        self.srv.remove_connection(client1)
        self.assertEqual(len(self.proxy._monitors), 1)
        # Remove last, unregistered
        self.srv.remove_connection(client2)
        self.assertEqual(len(self.proxy._monitors), 0)


class MonitorTestCase(unittest.TestCase):
    """A port with one TCP server, watched by one client"""

    control = None

    def setUp(self):
        self.server = MockServer('TCP', control=self.control)
        self.proxy = MockSerialProxy('myport', servers=[self.server])
        self.srv = monitor.ServerMonitor(self.proxy)
        self.client = MockClient()

    def watch(self):
        self.srv.add_connection(self.client)
        return self.client

    def join(self, peer=None):
        """A client arrives on the port and the monitor notices"""
        peer = peer if peer is not None else MockPeer()
        self.server.connections.append(peer)
        self.srv.process_stale()
        return peer

    def leave(self, peer):
        self.server.connections.remove(peer)
        self.srv.process_stale()


class TestTheGreeting(MonitorTestCase):

    def test_it_describes_the_port(self):
        hello = last(self.watch())
        self.assertEqual(hello['port']['name'], 'myport')
        self.assertEqual(hello['port']['device'], '/dev/ttyUSB0')

    def test_it_says_the_device_is_there(self):
        self.assertEqual(last(self.watch())['serial'], {'connected': True})

    def test_it_says_a_watcher_may_not_write(self):
        can = last(self.watch())['can']
        self.assertIs(can['read'], True)
        self.assertIs(can['write'], False)

    def test_and_may_not_attach(self):
        """A monitor holds nothing open, so there is nothing to let go
        of - a generic client should not offer the button."""
        self.assertIs(last(self.watch())['can']['attach'], False)

    def test_it_lists_who_is_already_on_the_line(self):
        self.join()
        peers = last(self.watch())['peers']
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]['address'], '192.168.1.50')
        self.assertEqual(peers[0]['port'], 51234)
        self.assertEqual(peers[0]['protocol'], 'tcp')

    def test_a_peer_already_there_has_a_slot(self):
        self.join()
        self.assertEqual(last(self.watch())['peers'][0]['slot'], 1)

    def test_no_signals_are_reported_when_none_are_configured(self):
        self.assertNotIn('signals', last(self.watch()))


class TestWhoWroteWhat(MonitorTestCase):

    def test_the_device_is_zero(self):
        client = self.watch()
        self.proxy.notify(None, b'hello')
        self.assertEqual(binary(client)[-1], b'\x00hello')

    def test_a_client_is_its_slot(self):
        client = self.watch()
        peer = self.join()
        self.proxy.notify(peer, b'typed')
        self.assertEqual(binary(client)[-1], b'\x01typed')

    def test_two_clients_are_told_apart(self):
        client = self.watch()
        first = self.join()
        second = self.join(MockPeer('10.0.0.7', 40112))
        self.proxy.notify(first, b'a')
        self.proxy.notify(second, b'b')
        self.assertEqual(binary(client)[-2:], [b'\x01a', b'\x02b'])

    def test_a_client_that_writes_before_the_walk_is_announced_first(self):
        """Its slot has to mean something by the time its bytes land."""
        client = self.watch()
        peer = MockPeer()
        self.server.connections.append(peer)
        self.proxy.notify(peer, b'quick')
        self.assertEqual(binary(client)[-1], b'\x01quick')
        self.assertEqual(frames(client)[-1]['peer_connected']['slot'], 1)

    def test_every_watcher_sees_it(self):
        first = self.watch()
        second = MockClient()
        self.srv.add_connection(second)
        self.proxy.notify(None, b'shared')
        self.assertEqual(binary(first)[-1], b'\x00shared')
        self.assertEqual(binary(second)[-1], b'\x00shared')

    def test_a_watcher_that_has_gone_is_dropped(self):
        client = self.watch()
        client.closed = True
        self.proxy.notify(None, b'test')
        self.assertNotIn(client, self.srv.connections)


class TestPeersComingAndGoing(MonitorTestCase):

    def test_a_new_peer_is_announced(self):
        client = self.watch()
        self.join()
        message = last(client)
        self.assertEqual(message['peer_connected']['slot'], 1)
        self.assertEqual(len(message['peers']), 1)

    def test_and_a_departing_one(self):
        client = self.watch()
        peer = self.join()
        self.leave(peer)
        message = last(client)
        self.assertEqual(message['peer_disconnected']['slot'], 1)
        self.assertEqual(message['peers'], [])

    def test_the_event_and_the_list_travel_together(self):
        """One frame carries what to log and what to render, so a
        client needs to remember nothing between them."""
        client = self.watch()
        self.join()
        message = last(client)
        self.assertIn('peer_connected', message)
        self.assertIn('peers', message)

    def test_nothing_is_said_when_nothing_changed(self):
        client = self.watch()
        self.join()
        before = len(client.sent)
        self.srv.process_stale()
        self.assertEqual(len(client.sent), before)

    def test_a_slot_is_reused_after_its_holder_leaves(self):
        self.watch()
        first = self.join()
        second = self.join(MockPeer('10.0.0.7', 40112))
        self.leave(first)
        third = self.join(MockPeer('10.0.0.8', 40113))
        peers = {peer['address']: peer['slot']
                 for peer in self._peers()}
        self.assertEqual(peers['10.0.0.7'], 2)
        self.assertEqual(peers['10.0.0.8'], 1)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)

    def test_a_departure_is_reported_before_its_slot_is_handed_on(self):
        """Data and events share one WebSocket and are therefore
        ordered; that is the whole reason reuse is safe."""
        client = self.watch()
        first = self.join()
        self.leave(first)
        self.join(MockPeer('10.0.0.9', 5000))
        events = [message for message in frames(client)
                  if 'peer_disconnected' in message
                  or 'peer_connected' in message]
        self.assertIn('peer_disconnected', events[-2])
        self.assertIn('peer_connected', events[-1])

    def test_an_id_matches_what_the_api_reports(self):
        client = self.watch()
        peer = self.join()
        from ser2tcp.connection import connection_id
        self.assertEqual(
            last(client)['peer_connected']['id'], connection_id(peer))

    def _peers(self):
        return self.srv._peer_list()


class TestAPeerBehindAProxy(MonitorTestCase):
    """uhttp resolves a trusted X-Forwarded-For chain for us."""

    def _ws_peer(self):
        peer = MockClient()
        peer.addr = ('10.0.0.1', 443)
        peer.remote_address = '192.168.1.9'
        peer.socket_address = '10.0.0.1:443'
        peer.remote_addresses = ['192.168.1.9', '10.0.0.1']
        return peer

    def setUp(self):
        super().setUp()
        self.server.protocol = 'WEBSOCKET'

    def test_the_client_is_reported_not_the_proxy(self):
        self.join(self._ws_peer())
        self.assertEqual(
            last(self.watch())['peers'][0]['address'], '192.168.1.9')

    def test_what_actually_connected_is_reported_too(self):
        self.join(self._ws_peer())
        self.assertEqual(
            last(self.watch())['peers'][0]['socket'], '10.0.0.1:443')

    def test_the_socket_port_is_not_passed_off_as_the_clients(self):
        self.join(self._ws_peer())
        self.assertNotIn('port', last(self.watch())['peers'][0])

    def test_the_chain_is_reported(self):
        self.join(self._ws_peer())
        self.assertEqual(
            last(self.watch())['peers'][0]['forwarded'],
            ['192.168.1.9', '10.0.0.1'])

    def test_a_direct_client_keeps_its_port(self):
        peer = MockClient()
        peer.addr = ('192.168.1.9', 51500)
        peer.remote_address = '192.168.1.9'
        peer.socket_address = '192.168.1.9:51500'
        peer.remote_addresses = ['192.168.1.9']
        self.join(peer)
        entry = last(self.watch())['peers'][0]
        self.assertEqual(entry['port'], 51500)
        self.assertNotIn('socket', entry)
        self.assertNotIn('forwarded', entry)


class TestSignalsOnTheMonitor(MonitorTestCase):

    control = {'signals': ['rts', 'cts']}

    def test_the_configured_lines_arrive_with_the_greeting(self):
        self.proxy._signals = 0b000101       # rts + cts
        hello = last(self.watch())
        self.assertEqual(hello['signals'], {'rts': True, 'cts': True})

    def test_a_change_carries_only_what_moved(self):
        client = self.watch()
        self.proxy.notify_signals(0b000001)  # rts only
        self.assertEqual(last(client), {'signals': {'rts': True}})

    def test_a_line_no_server_reports_stays_out(self):
        self.proxy._signals = 0b111111
        self.assertNotIn('dtr', last(self.watch())['signals'])

    def test_a_report_that_changes_nothing_says_nothing(self):
        client = self.watch()
        self.proxy.notify_signals(0b000001)
        before = len(client.sent)
        self.proxy.notify_signals(0b000001)
        self.assertEqual(len(client.sent), before)


class TestSignalsNobodyAskedFor(MonitorTestCase):
    """No control anywhere on the port means no indicators at all."""

    def test_nothing_is_reported(self):
        client = self.watch()
        before = len(client.sent)
        self.proxy.notify_signals(0b111111)
        self.assertEqual(len(client.sent), before)


class TestProcessStale(unittest.TestCase):
    """Test stale connection cleanup"""

    def setUp(self):
        self.proxy = MockSerialProxy()
        self.srv = monitor.ServerMonitor(self.proxy)

    def test_removes_non_websocket(self):
        client = MockClient()
        self.srv.add_connection(client)
        client.is_websocket = False
        self.srv.process_stale()
        self.assertNotIn(client, self.srv.connections)

    def test_removes_closed_socket(self):
        client = MockClient()
        self.srv.add_connection(client)
        client.socket = None
        self.srv.process_stale()
        self.assertNotIn(client, self.srv.connections)

    def test_keeps_active(self):
        client = MockClient()
        self.srv.add_connection(client)
        self.srv.process_stale()
        self.assertIn(client, self.srv.connections)


class TestClose(unittest.TestCase):
    """Test server close"""

    def test_close_all_connections(self):
        proxy = MockSerialProxy()
        srv = monitor.ServerMonitor(proxy)
        client1 = MockClient()
        client2 = MockClient()
        srv.add_connection(client1)
        srv.add_connection(client2)
        srv.close()
        self.assertEqual(srv.connections, [])
        self.assertTrue(client1.closed)
        self.assertTrue(client2.closed)
        self.assertEqual(client1.close_code, 1001)

    def test_close_unregisters_monitor(self):
        proxy = MockSerialProxy()
        srv = monitor.ServerMonitor(proxy)
        client = MockClient()
        srv.add_connection(client)
        srv.close()
        self.assertEqual(len(proxy._monitors), 0)


if __name__ == '__main__':
    unittest.main()
