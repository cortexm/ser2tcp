"""Tests for how the command line maps onto a logging level.

One step per level, so each flag adds exactly one kind of message:

    -q        errors only
    (none)    and warnings
    -v        and the request log
    -vv       and debug

The bottom of that ladder is the point. `-q` used to mean CRITICAL, and
nothing in ser2tcp logs CRITICAL, so it was total silence — including
"Failed to create port" and an HTTP server that could not bind. The
process came up, served nothing, and said nothing.
"""

import logging
import unittest

from ser2tcp.main import log_level


class TestTheLadder(unittest.TestCase):

    def test_quiet_is_errors_only(self):
        self.assertEqual(log_level(verbose=0, quiet=True), logging.ERROR)

    def test_default_adds_warnings(self):
        self.assertEqual(log_level(verbose=0, quiet=False), logging.WARNING)

    def test_v_adds_the_request_log(self):
        self.assertEqual(log_level(verbose=1, quiet=False), logging.INFO)

    def test_vv_adds_debug(self):
        self.assertEqual(log_level(verbose=2, quiet=False), logging.DEBUG)

    def test_each_step_lets_more_through(self):
        levels = [log_level(verbose=0, quiet=True),
                  log_level(verbose=0, quiet=False),
                  log_level(verbose=1, quiet=False),
                  log_level(verbose=2, quiet=False)]
        self.assertEqual(levels, sorted(levels, reverse=True))
        self.assertEqual(len(set(levels)), 4, 'a step that changes nothing')


class TestTheEdges(unittest.TestCase):

    def test_more_than_two_v_is_still_debug(self):
        """There is nothing below debug to ask for"""
        self.assertEqual(log_level(verbose=7, quiet=False), logging.DEBUG)

    def test_quiet_wins_over_verbose(self):
        """argparse keeps them apart, but the answer should not depend on it"""
        self.assertEqual(log_level(verbose=2, quiet=True), logging.ERROR)


class TestWhatQuietMustNotHide(unittest.TestCase):
    """A port that will not open has to be reported at every level.

    That is the one thing the old CRITICAL setting got wrong, and it is
    the reason this has a test of its own rather than being read off the
    table above.
    """

    def test_an_error_survives_quiet(self):
        self.assertLessEqual(log_level(verbose=0, quiet=True), logging.ERROR)

    def test_a_warning_does_not(self):
        """Quiet has to be quiet about something, or it is not a flag"""
        self.assertGreater(log_level(verbose=0, quiet=True), logging.WARNING)


if __name__ == '__main__':
    unittest.main()
