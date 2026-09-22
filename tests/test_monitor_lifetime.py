"""Tests for what happens to a monitor when its port is taken apart.

A ServerMonitor holds the SerialProxy it was built for, and the
wrapper keys them by port name and kept them for ever. Rebuilding a
port through the API makes a *new* proxy - so both the watchers and
anyone connecting afterwards were left bound to the one that had been
closed, watching a device nothing drives. Neither ever saw another
byte, and nothing said so.
"""

import unittest
from unittest.mock import Mock

from ser2tcp.http_server import HttpServerWrapper
from ser2tcp.server_monitor import ServerMonitor

from tests.test_server_monitor import MockClient, MockSerialProxy, last


def _wrapper():
    """A wrapper with nothing built but the WebSocket bookkeeping"""
    wrapper = HttpServerWrapper.__new__(HttpServerWrapper)
    wrapper._log = Mock()
    wrapper._servers = []
    wrapper._monitor_servers = {}
    wrapper._ws_clients = {}
    wrapper._last_ws_ping = 0
    return wrapper


class TestTellingTheWatchers(unittest.TestCase):

    def setUp(self):
        self.proxy = MockSerialProxy('pty')
        self.monitor = ServerMonitor(self.proxy, log=Mock())
        self.client = MockClient()
        self.monitor.add_connection(self.client)
        self.client.sent.clear()

    def test_they_are_told_the_port_is_gone(self):
        self.monitor.port_gone()
        self.assertIsNone(last(self.client)['port'])

    def test_and_then_closed(self):
        self.monitor.port_gone()
        self.assertTrue(self.client.closed)

    def test_the_message_comes_before_the_close(self):
        """A close code is a number; a client watching a named port
        deserves to hear the name stopped meaning anything."""
        order = []
        self.client.ws_send = lambda data: order.append('sent')
        self.client.ws_close = lambda code, reason: order.append('closed')
        self.monitor.port_gone()
        self.assertEqual(order, ['sent', 'closed'])

    def test_the_close_says_the_server_is_going_away(self):
        """1001, so a page can tell being hung up on from a link that
        dropped - and not reconnect to something that is not there."""
        self.monitor.port_gone()
        self.assertEqual(self.client.close_code, 1001)

    def test_it_stops_watching_the_port(self):
        self.monitor.port_gone()
        self.assertEqual(self.proxy._monitors, [])

    def test_a_socket_that_has_already_gone_is_not_a_problem(self):
        self.client.closed = True
        self.monitor.port_gone()      # must not raise


class TestDroppingItFromTheWrapper(unittest.TestCase):

    def setUp(self):
        self.wrapper = _wrapper()
        self.proxy = MockSerialProxy('pty')
        self.monitor = ServerMonitor(self.proxy, log=Mock())
        self.client = MockClient()
        self.monitor.add_connection(self.client)
        self.wrapper._monitor_servers['pty'] = self.monitor
        self.wrapper._ws_clients[self.client] = self.monitor

    def test_the_entry_goes(self):
        """A cached monitor is what a later watcher picks up, so
        leaving it behind blinds the next one as well as this one."""
        self.wrapper.drop_monitor('pty')
        self.assertNotIn('pty', self.wrapper._monitor_servers)

    def test_its_clients_are_told_and_closed(self):
        self.wrapper.drop_monitor('pty')
        self.assertIsNone(last(self.client)['port'])
        self.assertTrue(self.client.closed)

    def test_and_forgotten_by_the_wrapper_too(self):
        """Left behind they would be pinged for ever."""
        self.wrapper.drop_monitor('pty')
        self.assertEqual(self.wrapper._ws_clients, {})

    def test_a_port_nobody_watched_is_not_a_problem(self):
        self.wrapper.drop_monitor('never-watched')     # must not raise

    def test_an_unnamed_port_is_not_a_problem(self):
        self.wrapper.drop_monitor('')
        self.wrapper.drop_monitor(None)


if __name__ == '__main__':
    unittest.main()
