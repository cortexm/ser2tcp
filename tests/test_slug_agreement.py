"""The browser's copy of the id rule has to answer the same as ours.

config_ids.py fills an id in for a caller that sends none; app.js has
to *show* one in the editor before the save that would create it. Two
implementations of one rule, which is what this project otherwise
avoids - so they are pinned to the same table of cases from both
sides, and drift fails here rather than in somebody's config.

Skipped where node is not installed: the rule is still tested on the
Python side, this only checks the two agree.
"""

import json
import pathlib
import re
import shutil
import subprocess
import unittest

from ser2tcp.config_ids import FALLBACK_SLUG, SLUG_MAX

from tests.test_config_ids_slug import SLUG_CASES

APP_JS = pathlib.Path(__file__).resolve().parent.parent / 'ser2tcp' / 'html' \
    / 'app.js'

# The three the rule is made of. Pulled out of app.js rather than
# duplicated here, so the test reads what actually ships.
WANTED = ('slugId', 'uniqueId', 'entrySlug')


def _extract(source, name):
    """The text of one top-level `function name(...) { ... }`"""
    start = source.index('function %s(' % name)
    depth = 0
    for index in range(source.index('{', start), len(source)):
        if source[index] == '{':
            depth += 1
        elif source[index] == '}':
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError('unbalanced braces reading %s' % name)


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class TestBothSidesAgree(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        source = APP_JS.read_text()
        constants = 'const SLUG_MAX = %d;\nconst FALLBACK_SLUG = %r;\n' % (
            SLUG_MAX, FALLBACK_SLUG)
        cls.prelude = constants + '\n'.join(
            _extract(source, name) for name in WANTED)
        # The constants must match ours too, not just the functions.
        for name, value in (('SLUG_MAX', SLUG_MAX),
                            ('FALLBACK_SLUG', FALLBACK_SLUG)):
            found = re.search(
                r'^const %s = (.+);$' % name, source, re.M)
            assert found, name
            assert json.loads(found.group(1).replace("'", '"')) == value, name

    def _run(self, script):
        result = subprocess.run(
            ['node', '-e', self.prelude + '\n' + script],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_same_table_of_names(self):
        cases = json.dumps([name for name, _ in SLUG_CASES])
        got = self._run(
            'console.log(JSON.stringify(%s.map(slugId)));' % cases)
        self.assertEqual(got, [expected for _, expected in SLUG_CASES])

    def test_the_same_truncation(self):
        got = self._run(
            "console.log(JSON.stringify(["
            "slugId('x'.repeat(200)).length,"
            "slugId('x'.repeat(%d) + ' tail')]));" % (SLUG_MAX - 1))
        self.assertEqual(got[0], SLUG_MAX)
        self.assertFalse(got[1].endswith('-'))

    def test_the_same_numbering(self):
        got = self._run(
            "const taken = new Set(['esp32', 'esp32-2']);"
            "console.log(JSON.stringify(["
            "uniqueId('esp32', new Set()),"
            "uniqueId('esp32', new Set(['esp32'])),"
            "uniqueId('esp32', taken),"
            "uniqueId('', new Set())]));")
        self.assertEqual(
            got, ['esp32', 'esp32-1', 'esp32-1', FALLBACK_SLUG])

    def test_the_same_fallbacks(self):
        got = self._run(
            "console.log(JSON.stringify(["
            "entrySlug('My Port', '/dev/ttyUSB0'),"
            "entrySlug('', '/dev/ttyUSB0'),"
            "entrySlug('###', '/dev/ttyS3'),"
            "entrySlug('', '')]));")
        self.assertEqual(
            got, ['my-port', 'ttyusb0', 'ttys3', FALLBACK_SLUG])


if __name__ == '__main__':
    unittest.main()
