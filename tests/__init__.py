"""Shared test support.

What lives here is what both the unit tests and the integration tests
need, so it has one definition rather than one per package.
"""

import os as _os
import unittest as _unittest


# What stands in for a serial device in these tests is a pty: a real
# tty ser2tcp can open by path, with the other end in the test's hand.
# Windows has no equivalent, so those tests do not run there rather
# than being rewritten around something that is not a serial port.
HAS_PTY = hasattr(_os, 'openpty')
requires_pty = _unittest.skipUnless(
    HAS_PTY, 'needs a pty as a stand-in serial device (Unix only)')
