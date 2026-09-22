"""Tests for keeping a quiet WebSocket open.

uhttp measures a WebSocket by the same clock as an idle HTTP keep-alive
connection - 15 s by default - and a browser throttles a background
tab's timers to about a minute. A terminal left in another tab was
therefore closed for being quiet, and the page showed "offline" and did
nothing about it.

The server pings instead. A browser answers a ping in its own network
stack rather than in JavaScript, so the answer is not throttled: a live
but quiet peer keeps its connection, and one that has gone still ages
out, because sending does not count as activity - only receiving does.
"""

import unittest
from unittest.mock import Mock, patch

from ser2tcp.http_server import HttpServerWrapper


def _wrapper():
    """A wrapper with nothing built but the WebSocket bookkeeping"""
    wrapper = HttpServerWrapper.__new__(HttpServerWrapper)
    wrapper._log = Mock()
    wrapper._servers = []
    wrapper._monitor_servers = {}
    wrapper._ws_clients = {}
    wrapper._auth = None
    wrapper._pending_reload = False
    wrapper._last_ws_ping = 0
    return wrapper


class TestPingingTheQuietOnes(unittest.TestCase):

    def setUp(self):
        self.wrapper = _wrapper()
        self.client = Mock()
        self.wrapper._ws_clients[self.client] = Mock()

    def test_a_websocket_is_pinged(self):
        self.wrapper.ping_websockets()
        self.client.ws_ping.assert_called_once()

    def test_not_on_every_pass(self):
        """The loop turns over many times a second."""
        self.wrapper.ping_websockets()
        self.client.ws_ping.reset_mock()
        self.wrapper.ping_websockets()
        self.client.ws_ping.assert_not_called()

    def test_but_often_enough_to_beat_the_deadline(self):
        """A ping that arrives after the connection was closed for
        being idle is no use to anybody."""
        self.assertLess(
            HttpServerWrapper.WS_PING_INTERVAL,
            HttpServerWrapper.ws_keep_alive_timeout())

    def test_a_socket_that_has_gone_is_not_a_problem(self):
        self.client.ws_ping.side_effect = OSError('gone')
        self.wrapper.ping_websockets()      # must not raise

    def test_every_websocket_gets_one(self):
        second = Mock()
        self.wrapper._ws_clients[second] = Mock()
        self.wrapper.ping_websockets()
        self.client.ws_ping.assert_called_once()
        second.ws_ping.assert_called_once()

    def test_it_rides_along_with_the_rest_of_the_housekeeping(self):
        with patch.object(HttpServerWrapper, 'ping_websockets') as ping:
            with patch.object(HttpServerWrapper, '_broadcast_status'):
                self.wrapper.process_stale()
        ping.assert_called_once()


if __name__ == '__main__':
    unittest.main()
