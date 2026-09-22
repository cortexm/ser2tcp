"""Tests for holding the serial port, separately from being connected.

Opening a WebSocket used to be opening the port: add_connection() called
connect() and remove_connection() called disconnect(). So a client that
wanted to stop using the device had to close the socket, and with it
every way of being told anything.

Attaching is now its own state. A connection is a control channel; the
port is held open by whoever is *attached*, and a detached client stays
connected and keeps hearing about the device.

The port's lifetime is the dangerous part: released too eagerly it is
closed under someone still using it, released never and it is pinned
open forever. Most of what is here watches that one number.
"""

import unittest
from unittest.mock import Mock

from ser2tcp.server_websocket import ServerWebSocket


def _serial():
    """A port that opens when asked and remembers what it was told."""
    serial = Mock()
    serial.connect.return_value = True
    serial.can_add_connection.return_value = True
    # Read when a control-enabled endpoint greets a new client; a Mock
    # here would be bit-shifted and raise.
    serial.get_signals.return_value = 0
    # The greeting frame describes the port, so these have to be
    # something JSON can carry.
    serial.is_connected = True
    serial.name = 'test'
    serial.info = {'name': 'test', 'device': '/dev/null', 'baudrate': 9600}
    return serial


def _client():
    client = Mock()
    client.is_websocket = True
    return client


class AttachTestCase(unittest.TestCase):

    def setUp(self):
        self.serial = _serial()
        self.server = ServerWebSocket(
            {'protocol': 'websocket', 'endpoint': 'x'},
            self.serial, log=Mock())

    def join(self):
        """A client that connects the ordinary way"""
        client = _client()
        self.server.add_connection(client)
        return client


class TestConnectingAttaches(AttachTestCase):
    """The default has to be what it always was, or every existing
    client changes behaviour on upgrade."""

    def test_a_new_client_is_attached(self):
        client = self.join()
        self.assertIn(client, self.server.attached)

    def test_and_counts_as_a_connection(self):
        client = self.join()
        self.assertIn(client, self.server.connections)

    def test_and_the_port_is_opened(self):
        self.join()
        self.serial.connect.assert_called()

    def test_it_receives_the_device(self):
        client = self.join()
        self.server.send(b'hello')
        client.ws_send.assert_called_with(b'hello')


class TestDetaching(AttachTestCase):

    def test_a_detached_client_is_still_connected(self):
        client = self.join()
        self.server.detach(client)
        self.assertIn(client, self.server.connections)
        self.assertNotIn(client, self.server.attached)

    def test_it_stops_receiving_the_device(self):
        client = self.join()
        self.server.detach(client)
        client.ws_send.reset_mock()
        self.server.send(b'hello')
        client.ws_send.assert_not_called()

    def test_what_it_sends_no_longer_reaches_the_device(self):
        client = self.join()
        self.server.detach(client)
        client.ws_is_text = False
        client.read_buffer.return_value = b'typed'
        self.server.process_message(client)
        self.serial.send.assert_not_called()

    def test_attaching_again_brings_it_back(self):
        client = self.join()
        self.server.detach(client)
        self.server.attach(client)
        self.server.send(b'hello')
        client.ws_send.assert_called_with(b'hello')

    def test_detaching_twice_is_harmless(self):
        client = self.join()
        self.server.detach(client)
        self.server.detach(client)
        self.assertIn(client, self.server.connections)

    def test_attaching_twice_does_not_double_count(self):
        client = self.join()
        self.server.attach(client)
        self.assertEqual(len(self.server.attached), 1)


class TestWhoHoldsThePortOpen(AttachTestCase):
    """The number that decides whether the device stays open."""

    def test_an_attached_client_holds_it(self):
        self.join()
        self.assertTrue(self.server.has_connections())

    def test_a_detached_one_does_not(self):
        client = self.join()
        self.server.detach(client)
        self.assertFalse(self.server.has_connections())

    def test_detaching_releases_the_port(self):
        client = self.join()
        self.serial.disconnect.reset_mock()
        self.server.detach(client)
        self.serial.disconnect.assert_called_once()

    def test_but_not_while_somebody_else_is_attached(self):
        first = self.join()
        self.join()
        self.assertTrue(self.server.has_connections())
        self.server.detach(first)
        self.assertTrue(self.server.has_connections())

    def test_attaching_opens_it_again(self):
        client = self.join()
        self.server.detach(client)
        self.serial.connect.reset_mock()
        self.server.attach(client)
        self.serial.connect.assert_called_once()

    def test_leaving_altogether_releases_it_too(self):
        client = self.join()
        self.serial.disconnect.reset_mock()
        self.server.remove_connection(client)
        self.serial.disconnect.assert_called_once()

    def test_shutting_the_endpoint_down_leaves_nobody_holding_it(self):
        self.join()
        self.server.close_connections()
        self.assertFalse(self.server.has_connections())
        self.assertEqual(self.server.attached, [])

    def test_and_leaving_after_detaching_does_not_release_it_twice(self):
        """Releasing a port nobody holds is how one gets closed under
        the client that does hold it."""
        client = self.join()
        self.server.detach(client)
        self.serial.disconnect.reset_mock()
        self.server.remove_connection(client)
        self.serial.disconnect.assert_not_called()


class TestADetachedClientStillHears(unittest.TestCase):
    """Detaching gives up the data and the port, not the channel.

    Everything that is not serial data still has to arrive, or there
    would be no reason to stay connected instead of closing the socket.
    """

    def setUp(self):
        self.serial = _serial()
        self.server = ServerWebSocket(
            {'protocol': 'websocket', 'endpoint': 'x',
             'control': {'rts': True, 'signals': ['rts']}},
            self.serial, log=Mock())
        self.client = _client()
        self.server.add_connection(self.client)
        self.server.detach(self.client)
        self.client.ws_send.reset_mock()

    def test_signal_reports_still_arrive(self):
        self.server.send_signal_report(0b01)
        self.client.ws_send.assert_called_once()

    def test_but_serial_data_does_not(self):
        self.server.send(b'device output')
        self.client.ws_send.assert_not_called()


class TestTheLimits(AttachTestCase):
    """Each limit guards what its name says."""

    def test_the_port_limit_is_asked_when_attaching(self):
        client = self.join()
        self.server.detach(client)
        self.serial.can_add_connection.return_value = False
        self.assertFalse(self.server.attach(client))
        self.assertNotIn(client, self.server.attached)

    def test_a_refused_attach_leaves_the_client_connected(self):
        client = self.join()
        self.server.detach(client)
        self.serial.can_add_connection.return_value = False
        self.server.attach(client)
        self.assertIn(client, self.server.connections)

    def test_the_endpoint_limit_counts_connections_not_attachments(self):
        """It guards this endpoint's capacity; detaching does not free a
        slot on it, because the socket is still there."""
        server = ServerWebSocket(
            {'protocol': 'websocket', 'endpoint': 'x', 'max_connections': 1},
            self.serial, log=Mock())
        first = _client()
        server.add_connection(first)
        server.detach(first)
        second = _client()
        server.add_connection(second)
        self.assertNotIn(second, server.connections)


class TestWhatTheDeviceSeesWithNobodyAttached(AttachTestCase):

    def test_a_detached_client_is_not_sent_anything(self):
        client = self.join()
        self.server.detach(client)
        client.ws_send.reset_mock()     # clear the greeting
        self.server.send(b'noise')
        self.assertEqual(client.ws_send.call_count, 0)

    def test_and_send_does_not_fail_with_an_empty_set(self):
        client = self.join()
        self.server.detach(client)
        self.server.send(b'noise')      # must not raise


if __name__ == '__main__':
    unittest.main()
