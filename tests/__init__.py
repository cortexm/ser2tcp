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

ON_WINDOWS = _os.name == 'nt'

# Windows' chmod only toggles the read-only bit; everything reads back
# as 0o777 or 0o666, so `key.pem is 0600` cannot be asserted there -
# and, more to the point, is not true there. The cert manager still
# writes the modes; the filesystem is what does not keep them.
requires_posix_modes = _unittest.skipIf(
    ON_WINDOWS, 'POSIX permission bits are not enforced on Windows')

# Binding a port that is already in use fails on Unix. On Windows
# SO_REUSEADDR means the other thing - it lets a second socket bind an
# address that is actively in use - and both ser2tcp and uhttp set it
# before binding, so a port conflict there is silently allowed instead
# of refused. The tests that watch what a refused bind leaves behind
# have nothing to watch.
requires_bind_conflict = _unittest.skipIf(
    ON_WINDOWS,
    'SO_REUSEADDR lets Windows bind a port already in use, so a bind '
    'conflict cannot be provoked')
