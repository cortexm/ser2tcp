"""Tests for the placeholder that stands in for a port that failed.

Ports are addressed by their position in the config, so leaving a
failed one out shifts every port after it: an edit meant for one
rewrites another, and the port itself disappears from the UI with no
explanation.
"""

import unittest

from ser2tcp.serial_proxy import FailedProxy


class TestFailedProxy(unittest.TestCase):

    def _proxy(self, config=None, error='bind failed'):
        return FailedProxy(
            config or {'name': 'dev', 'serial': {'port': '/dev/ttyUSB9'}},
            error)

    def test_it_reports_the_name_from_the_config(self):
        self.assertEqual(self._proxy().name, 'dev')

    def test_it_reports_the_configured_device(self):
        self.assertEqual(
            self._proxy().serial_config.get('port'), '/dev/ttyUSB9')

    def test_it_reports_a_match_filter_when_there_is_one(self):
        proxy = self._proxy({'serial': {'match': {'vid': '0x1234'}}})
        self.assertEqual(proxy.match, {'vid': '0x1234'})

    def test_it_is_never_connected(self):
        self.assertFalse(self._proxy().is_connected)

    def test_it_serves_nothing(self):
        self.assertEqual(self._proxy().servers, [])

    def test_it_carries_the_reason(self):
        self.assertIn('bind failed', self._proxy().error)

    def test_it_has_no_signals(self):
        self.assertEqual(self._proxy().get_signals(), 0)

    def test_the_loop_can_drive_it(self):
        """ServersManager calls these on everything it holds"""
        proxy = self._proxy()
        proxy.process_stale()
        proxy.close()

    def test_a_config_without_a_serial_block_is_survivable(self):
        """It stands in for configs too broken to build, after all"""
        proxy = FailedProxy({}, 'serial config required')
        self.assertEqual(proxy.name, '')
        self.assertEqual(proxy.serial_config, {})
        self.assertIsNone(proxy.match)


if __name__ == '__main__':
    unittest.main()
