"""Tests for how often the signal lines get sampled.

A port can have several servers, each with its own `control` and its
own `poll_interval`. The rate used to come from a plain assignment in
a loop over the configured servers - so the **last one written in the
config** won, whichever it was, and connecting to the other one made
no difference. A client asking for 100 ms got 500 because somebody
else's entry came after it in the file.

The rate now comes from whoever is waiting to hear: the shortest
interval among the servers that have clients on them and report some
line. A server nobody is connected to asks for nothing.
"""

import unittest

from ser2tcp.connection_control import (
    DEFAULT_POLL_INTERVAL, poll_interval, reported_signals)


class TestWhichLinesAreReported(unittest.TestCase):

    def test_none_without_control(self):
        self.assertEqual(reported_signals(None), ())

    def test_none_when_control_names_none(self):
        """Control with no `signals` can still set RTS; it just has
        nobody waiting to be told about the lines."""
        self.assertEqual(reported_signals({'rts': True}), ())

    def test_in_the_order_given(self):
        self.assertEqual(
            reported_signals({'signals': ['cts', 'rts']}), ('cts', 'rts'))

    def test_case_and_duplicates_are_settled(self):
        self.assertEqual(
            reported_signals({'signals': ['RTS', 'rts']}), ('rts',))


class TestHowOften(unittest.TestCase):

    def test_the_configured_interval(self):
        self.assertEqual(poll_interval({'poll_interval': 0.5}), 0.5)

    def test_a_default_when_none_is_given(self):
        self.assertEqual(
            poll_interval({'signals': ['rts']}), DEFAULT_POLL_INTERVAL)

    def test_and_without_control_at_all(self):
        self.assertEqual(poll_interval(None), DEFAULT_POLL_INTERVAL)

    def test_zero_is_not_taken_literally(self):
        """It would mean an ioctl on every pass of the loop - which is
        not a rate anybody chose, it is the absence of one."""
        self.assertEqual(poll_interval({'poll_interval': 0}),
                         DEFAULT_POLL_INTERVAL)

    def test_nor_is_a_negative_one(self):
        self.assertEqual(poll_interval({'poll_interval': -1}),
                         DEFAULT_POLL_INTERVAL)

    def test_nor_something_that_is_not_a_number(self):
        """It used to go straight into a comparison, where it raised
        TypeError once per pass of the loop for ever, and the signals
        were never reported at all."""
        for value in ('0.5', None, [], {}, True):
            self.assertEqual(poll_interval({'poll_interval': value}),
                             DEFAULT_POLL_INTERVAL, value)


if __name__ == '__main__':
    unittest.main()
