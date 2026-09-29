"""The editor sends a server back as it was written while it means the same.

A save rebuilds only a server whose configuration changed, compared as
JSON - so a form that re-spells what it did not change (a default made
explicit, the reported lines reordered, a false left out) drops that
server's clients for nothing. `_serverMeaning` in app.js decides when
two spellings are the same thing; these are the pairs it has to get
right, both ways, run through node against the copy that ships.

Skipped where node is not installed.
"""

import json
import pathlib
import re
import shutil
import subprocess
import unittest

from ser2tcp.connection_control import DEFAULT_POLL_INTERVAL
from tests.test_slug_agreement import _extract

APP_JS = pathlib.Path(__file__).resolve().parent.parent / 'ser2tcp' / 'html' \
    / 'app.js'

TCP = {'protocol': 'tcp', 'address': '0.0.0.0', 'port': 10001}


def _with(**changes):
    return dict(TCP, **changes)


# Two spellings of one server: a save must leave it alone.
SAME = [
    ('key order', _with(), dict(reversed(list(_with().items())))),
    ('protocol case', _with(protocol='TCP'), _with()),
    ('the default poll interval, spelled out',
     _with(control={'signals': ['cts'], 'poll_interval': 0.1}),
     _with(control={'signals': ['cts']})),
    ('reported lines in another order',
     _with(control={'signals': ['dsr', 'rts']}),
     _with(control={'signals': ['rts', 'dsr']})),
    ('a line not settable, said or not',
     _with(control={'rts': True, 'dtr': False, 'signals': ['rts']}),
     _with(control={'rts': True, 'signals': ['rts']})),
    ('no client limit, said as zero',
     _with(max_connections=0), _with()),
    ('read and write, said or not', _with(access='rw'), _with()),
    ('the old data flag, on', _with(data=True), _with()),
    ('the old data flag, off, is access none',
     _with(data=False, control={'signals': ['cts']}),
     _with(access='none', control={'signals': ['cts']})),
    ('no mTLS, said or not',
     _with(protocol='tls', tls={'bundle': 'main',
                                'require_client_cert': False}),
     _with(protocol='tls', tls={'bundle': 'main'})),
    ('empty address rules', _with(allow=[], deny=[]), _with()),
]

# Two different servers: a save must send the change.
DIFFERENT = [
    ('another port', _with(), _with(port=10002)),
    ('a poll interval that is not the default',
     _with(control={'signals': ['cts'], 'poll_interval': 0.25}),
     _with(control={'signals': ['cts']})),
    ('a line made settable',
     _with(control={'rts': True, 'signals': ['rts']}),
     _with(control={'signals': ['rts']})),
    ('another set of reported lines',
     _with(control={'signals': ['cts']}),
     _with(control={'signals': ['cts', 'dsr']})),
    ('control on', _with(control={'signals': ['cts']}), _with()),
    ('read only', _with(access='ro'), _with()),
    ('a client limit', _with(max_connections=3), _with()),
    ('an address rule', _with(allow=['10.0.0.5']), _with()),
    ('mTLS',
     _with(protocol='tls', tls={'bundle': 'main',
                                'require_client_cert': True}),
     _with(protocol='tls', tls={'bundle': 'main'})),
    ('a setting the form has no field for',
     _with(send_timeout=5.0), _with()),
]


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class TestServerMeaning(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        source = APP_JS.read_text()
        found = re.search(
            r'^const DEFAULT_POLL_INTERVAL = ([0-9.]+);', source, re.M)
        assert found, 'DEFAULT_POLL_INTERVAL is not in app.js'
        cls.js_default = float(found.group(1))
        cls.prelude = (
            'const DEFAULT_POLL_INTERVAL = %s;\n' % found.group(1)
            + _extract(source, '_serverMeaning'))

    def _meanings(self, pairs):
        script = 'console.log(JSON.stringify(%s.map(' \
            '([a, b]) => _serverMeaning(a) === _serverMeaning(b))));' \
            % json.dumps([[a, b] for _, a, b in pairs])
        result = subprocess.run(
            ['node', '-e', self.prelude + '\n' + script],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(zip((name for name, _, _ in pairs),
                        json.loads(result.stdout)))

    def test_the_default_is_the_servers_default(self):
        """What the form leaves out has to be what the server assumes"""
        self.assertEqual(self.js_default, DEFAULT_POLL_INTERVAL)

    def test_two_spellings_of_one_server_are_the_same(self):
        got = self._meanings(SAME)
        self.assertEqual(
            [name for name, same in got.items() if not same], [])

    def test_two_different_servers_are_not(self):
        got = self._meanings(DIFFERENT)
        self.assertEqual(
            [name for name, same in got.items() if same], [])


if __name__ == '__main__':
    unittest.main()
