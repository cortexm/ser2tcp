"""Tests for the port state a page colours the port's name by.

Three states, not two. `serial.connected` cannot tell "the device is
unplugged" from "the device is there and nobody has opened it", and
those are the red and the blue of the port card. Telling them apart
needs the USB enumeration, which HttpServerWrapper caches - so it
works the state out and hands it to the proxy, which passes it on.

A fourth, grey, belongs to the page alone: no WebSocket, so nothing
about the port can be vouched for.
"""

import tempfile
import unittest
from unittest.mock import Mock

from ser2tcp.http_server import HttpServerWrapper
from ser2tcp.serial_proxy import FailedProxy

from tests.test_device_lost import _proxy as _base_proxy


def _proxy():
    """A proxy complete enough to be torn down without complaining"""
    proxy = _base_proxy()
    proxy._state = 'offline'
    proxy._serial = None
    return proxy


class TestPassingItOn(unittest.TestCase):

    def setUp(self):
        self.proxy = _proxy()
        self.server = Mock()
        self.monitor = Mock()
        self.proxy._servers = [self.server]
        self.proxy._monitors = [self.monitor]

    def test_a_change_reaches_the_servers(self):
        self.proxy.set_state('online')
        self.server.on_port_changed.assert_called_once()
        self.assertEqual(
            self.server.on_port_changed.call_args[0][0]['state'], 'online')

    def test_and_the_monitors(self):
        self.proxy.set_state('error')
        self.monitor.on_port_changed.assert_called_once()

    def test_the_same_state_is_not_repeated(self):
        self.proxy.set_state('offline')      # what it already was
        self.server.on_port_changed.assert_not_called()

    def test_a_monitor_that_throws_does_not_stop_the_rest(self):
        self.monitor.on_port_changed.side_effect = RuntimeError('boom')
        self.proxy.set_state('online')       # must not raise
        self.assertEqual(self.proxy.state, 'online')

    def test_what_is_passed_on_is_the_whole_description(self):
        self.proxy.set_state('online')
        info = self.server.on_port_changed.call_args[0][0]
        self.assertEqual(
            sorted(info), ['baudrate', 'device', 'name', 'state'])


class TestAPortThatNeverStarted(unittest.TestCase):

    def test_it_is_in_error_and_stays_there(self):
        proxy = FailedProxy({'name': 'broken'}, 'bad config')
        self.assertEqual(proxy.state, 'error')
        proxy.set_state('online')
        self.assertEqual(proxy.state, 'error')

    def test_and_says_so_in_its_description(self):
        proxy = FailedProxy({'name': 'broken'}, 'bad config')
        self.assertEqual(proxy.info['state'], 'error')


class TestWorkingItOut(unittest.TestCase):
    """_compute_port_state is the one reader; refresh_port_states()
    exists so a monitor open on its own still gets a current answer -
    the status broadcast only runs while an NDJSON client listens."""

    def setUp(self):
        self.wrapper = HttpServerWrapper.__new__(HttpServerWrapper)
        self.wrapper._log = Mock()
        self.wrapper._detect_cache = []
        self.wrapper._detect_cache_at = 0.0
        self.proxy = _proxy()
        self.wrapper._serial_proxies = [self.proxy]

    def test_a_missing_device_is_an_error(self):
        self.proxy._serial_config = {'port': '/dev/nonexistent-device'}
        self.wrapper.refresh_port_states()
        self.assertEqual(self.proxy.state, 'error')

    def test_an_open_port_is_online(self):
        self.proxy._serial = Mock()
        self.wrapper.refresh_port_states()
        self.assertEqual(self.proxy.state, 'online')

    def test_a_device_that_exists_but_is_shut_is_offline(self):
        """Present is not the same as open, and the difference is the
        one the two colours are for.

        Any path that exists will do, and a temporary file is one on
        every platform - which is the rule being tested: a configured
        path that is there is taken as present, whoever put it there.
        It used to be /dev/null, which is only a file on Unix.
        """
        with tempfile.NamedTemporaryFile(suffix='-device') as device:
            self.proxy._serial_config = {'port': device.name}
            self.wrapper.refresh_port_states()
            self.assertEqual(self.proxy.state, 'offline')

    def test_the_clients_are_told_without_anyone_asking_for_status(self):
        server = Mock()
        self.proxy._servers = [server]
        self.proxy._serial_config = {'port': '/dev/nonexistent-device'}
        self.wrapper.refresh_port_states()
        server.on_port_changed.assert_called_once()
        self.assertEqual(
            server.on_port_changed.call_args[0][0]['state'], 'error')


if __name__ == '__main__':
    unittest.main()
