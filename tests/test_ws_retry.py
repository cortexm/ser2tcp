"""ws-retry.js, run through node: when a page tries again, and what it says.

Shared by the terminal, raw and monitor pages. The rules are small and
easy to break without noticing - a refusal retried for ever is exactly
what E3 was - so they are pinned here against the file that ships.

Skipped where node is not installed.
"""

import json
import pathlib
import shutil
import subprocess
import unittest

WS_RETRY_JS = pathlib.Path(__file__).resolve().parent.parent / 'ser2tcp' \
    / 'html' / 'ws-retry.js'

# setTimeout under the test's control: timers are recorded, not run,
# until the script says so.
FAKE_TIMERS = """
const timers = [];
globalThis.setTimeout = (fn, ms) => { timers.push({fn, ms}); return timers.length; };
globalThis.clearTimeout = id => { if (timers[id - 1]) timers[id - 1].fn = null; };
function fire() { const t = timers.shift(); if (t && t.fn) t.fn(); return t && t.ms; }
"""


@unittest.skipUnless(shutil.which('node'), 'node is not installed')
class WsRetryTestCase(unittest.TestCase):

    def run_js(self, script):
        result = subprocess.run(
            ['node', '-e',
             FAKE_TIMERS + WS_RETRY_JS.read_text() + '\n' + script],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class TestWhatTheCloseSays(WsRetryTestCase):

    def said(self, code, reason):
        return self.run_js('console.log(JSON.stringify(wsCloseText(%s)));'
                           % json.dumps({'code': code, 'reason': reason}))

    def test_a_reconfigured_server_says_it_is_coming_back(self):
        self.assertEqual(
            self.said(1012, 'Reconfigured'), 'Reconfigured - reconnecting')

    def test_a_removed_port_says_so(self):
        self.assertEqual(
            self.said(4404, 'Port removed'), 'Disconnected: Port removed')

    def test_a_stopping_server_says_so(self):
        self.assertEqual(self.said(1001, 'Server shutting down'),
                         'Disconnected: Server shutting down')

    def test_nothing_said_is_nothing_to_show(self):
        """A dropped network closes with no reason; the page keeps its
        own words for that"""
        self.assertIsNone(self.said(1006, ''))


class TestWhenItTriesAgain(WsRetryTestCase):

    def test_a_link_that_never_opened_is_not_retried(self):
        """A refusal - a wrong token, no such endpoint - retried every
        couple of seconds for ever is what the old status stream did"""
        got = self.run_js("""
            let calls = 0;
            const r = wsRetry(() => calls++);
            r.schedule(true);
            console.log(JSON.stringify([timers.length, calls]));
        """)
        self.assertEqual(got, [0, 0])

    def test_a_link_that_opened_is_retried_with_backoff(self):
        got = self.run_js("""
            let calls = 0;
            const r = wsRetry(() => calls++, 1000, 15000);
            r.succeeded();
            const waits = [];
            for (let i = 0; i < 6; i++) { r.schedule(true); waits.push(fire()); }
            console.log(JSON.stringify([waits, calls]));
        """)
        self.assertEqual(got, [[1000, 2000, 4000, 8000, 15000, 15000], 6])

    def test_a_success_starts_the_backoff_over(self):
        got = self.run_js("""
            const r = wsRetry(() => {}, 1000, 15000);
            r.succeeded();
            r.schedule(true); fire(); r.schedule(true); fire();
            r.succeeded();
            r.schedule(true);
            console.log(JSON.stringify(fire()));
        """)
        self.assertEqual(got, 1000)

    def test_a_link_hung_up_stays_hung_up(self):
        got = self.run_js("""
            const r = wsRetry(() => {});
            r.succeeded();
            r.schedule(false);
            console.log(JSON.stringify(timers.length));
        """)
        self.assertEqual(got, 0)

    def test_a_port_that_is_gone_is_not_retried(self):
        """4404: the port was deleted. Knocking on it with backoff for as
        long as the tab stays open would be the E3 drumbeat again"""
        got = self.run_js("""
            const r = wsRetry(() => {});
            r.succeeded();
            r.schedule(true, {code: 4404, reason: 'Port removed'});
            console.log(JSON.stringify(timers.length));
        """)
        self.assertEqual(got, 0)

    def test_a_reconfigured_server_is_retried(self):
        got = self.run_js("""
            const r = wsRetry(() => {}, 1000);
            r.succeeded();
            r.schedule(true, {code: 1012, reason: 'Reconfigured'});
            console.log(JSON.stringify(fire()));
        """)
        self.assertEqual(got, 1000)

    def test_cancel_stops_a_pending_retry(self):
        got = self.run_js("""
            let calls = 0;
            const r = wsRetry(() => calls++);
            r.succeeded();
            r.schedule(true);
            r.cancel();
            fire();
            console.log(JSON.stringify(calls));
        """)
        self.assertEqual(got, 0)


if __name__ == '__main__':
    unittest.main()
