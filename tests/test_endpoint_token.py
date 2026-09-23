"""Where a terminal page's credential may come from, and where it may
not go.

endpoint-token.js decides three things that are easy to undo by
accident and hard to notice afterwards: a shared token goes to
sessionStorage and never to localStorage (which holds the SPA's session
and outlives the visit), it is wiped from the address bar, and a
browser that refuses storage does not take the terminal down with it.

Run in node, since that is where the file runs. Skipped where node is
not installed - like test_slug_agreement.py, this checks the browser
side and nothing here depends on it.
"""

import json
import pathlib
import shutil
import subprocess
import unittest

SOURCE = pathlib.Path(__file__).resolve().parent.parent / 'ser2tcp' / 'html' \
    / 'endpoint-token.js'

# Enough of a browser for the file to run: the two storages (each able
# to refuse, the way a private window does), a location it can rewrite,
# and a window to hang the listener on.
STUBS = """
const calls = [];
const mkStore = data => ({
  getItem: k => {
    if (broken) throw new Error('storage is not available');
    return k in data ? data[k] : null;
  },
  setItem: (k, v) => {
    if (broken) throw new Error('storage is not available');
    data[k] = v;
  },
});
const sessionStorage = mkStore(sessionData);
const localStorage = mkStore(localData);
const history = {
  replaceState: (state, title, url) => {
    calls.push(url);
    location.hash = '';
  },
};
const window = { addEventListener: (name, fn) => calls.push('on:' + name) };
"""


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class TestEndpointToken(unittest.TestCase):

    def _run(self, expression, hash_='', session=None, local=None,
             broken=False):
        script = '\n'.join((
            'const sessionData = %s;' % json.dumps(session or {}),
            'const localData = %s;' % json.dumps(local or {}),
            'const broken = %s;' % ('true' if broken else 'false'),
            "const location = {hash: %s, pathname: '/xterm/demo', "
            "search: ''};" % json.dumps(hash_),
            STUBS,
            SOURCE.read_text(),
            'console.log(JSON.stringify(%s));' % expression,
        ))
        result = subprocess.run(
            ['node', '-e', script],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_fragment_is_the_token(self):
        got = self._run(
            "[endpointToken('demo'), sessionData, localData]",
            hash_='#token=s3cr3t')
        self.assertEqual(got[0], 's3cr3t')
        self.assertEqual(got[1], {'ser2tcp_ep_token:demo': 's3cr3t'})
        # localStorage holds the SPA's session and every page of this
        # origin reads it. A shared endpoint token is not a session.
        self.assertEqual(got[2], {})

    def test_the_fragment_is_wiped_from_the_url(self):
        got = self._run(
            "(() => { endpointToken('demo'); return ["
            "calls.filter(c => !String(c).startsWith('on:')),"
            "location.hash]; })()",
            hash_='#token=s3cr3t')
        self.assertEqual(got[0], ['/xterm/demo'])
        self.assertEqual(got[1], '')

    def test_a_reload_still_has_it(self):
        got = self._run(
            "endpointToken('demo')",
            session={'ser2tcp_ep_token:demo': 'kept'})
        self.assertEqual(got, 'kept')

    def test_another_endpoint_is_another_token(self):
        got = self._run(
            "endpointToken('other')",
            session={'ser2tcp_ep_token:demo': 'kept'})
        self.assertIsNone(got)

    def test_the_signed_in_session_when_no_link_was_used(self):
        got = self._run(
            "endpointToken('demo')", local={'ser2tcp_token': 'session'})
        self.assertEqual(got, 'session')

    def test_an_explicit_link_beats_an_ambient_session(self):
        got = self._run(
            "endpointToken('demo')",
            hash_='#token=s3cr3t', local={'ser2tcp_token': 'session'})
        self.assertEqual(got, 's3cr3t')

    def test_a_fragment_that_is_not_a_token(self):
        got = self._run("endpointToken('demo')", hash_='#anchor')
        self.assertIsNone(got)

    def test_a_browser_that_refuses_storage(self):
        # A private window throws on the accessor itself. The page has
        # the token in hand either way - forgetting it on reload beats
        # not opening at all.
        got = self._run(
            "endpointToken('demo')", hash_='#token=s3cr3t', broken=True)
        self.assertEqual(got, 's3cr3t')

    def test_a_pasted_link_reloads_the_page(self):
        # Same-document navigation: nothing loads by itself, so the
        # listener has to be there to notice.
        got = self._run("calls.filter(c => String(c).startsWith('on:'))")
        self.assertEqual(got, ['on:hashchange'])


if __name__ == '__main__':
    unittest.main()
