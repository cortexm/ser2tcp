"""Integration tests for the client address behind a reverse proxy.

Everything here rides on a header the client could have written itself,
so nothing short of a real request proves which address the filter ends
up comparing. The proxy is played by the test: it connects from
127.0.0.1 and sets X-Forwarded-For, which is all nginx does.
"""

import json
import unittest
import urllib.error
import urllib.request

from tests.integration import base


CLIENT = '203.0.113.5'
OTHER = '198.51.100.1'


def get(port, forwarded=None, path='/api/status'):
    """One request, optionally carrying an X-Forwarded-For chain."""
    headers = {}
    if forwarded is not None:
        headers['X-Forwarded-For'] = forwarded
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}{path}', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as err:
        with err:
            return err.code, err.read()


class TestWithoutTrustedProxies(base.IntegrationTestCase):
    """The header is ignored, because anyone could have sent it."""

    filtered_port = None

    @classmethod
    def build_config(cls):
        cls.filtered_port = base.free_port()
        return {
            'ports': [],
            'http': [
                {'address': '127.0.0.1', 'port': cls.port},
                {'address': '127.0.0.1', 'port': cls.filtered_port,
                 'deny': [CLIENT]},
            ],
        }

    def test_a_forwarded_header_from_nobody_changes_nothing(self):
        self.assertEqual(get(self.filtered_port, CLIENT)[0], 200)

    def test_and_the_socket_address_is_what_applies(self):
        """Denying 127.0.0.1 would lock the test out, so deny is on the
        forwarded address and must simply not match."""
        self.assertEqual(get(self.filtered_port)[0], 200)


class TestWithTrustedProxies(base.IntegrationTestCase):
    """Now the header is honoured, and the filter sees the real client."""

    filtered_port = None

    @classmethod
    def build_config(cls):
        cls.filtered_port = base.free_port()
        return {
            'ports': [],
            'http': [
                {'address': '127.0.0.1', 'port': cls.port},
                {'address': '127.0.0.1', 'port': cls.filtered_port,
                 'trusted_proxies': ['127.0.0.1'],
                 'deny': [CLIENT]},
            ],
        }

    def test_the_forwarded_client_is_denied(self):
        self.assertEqual(get(self.filtered_port, CLIENT)[0], 403)

    def test_someone_else_still_gets_through(self):
        self.assertEqual(get(self.filtered_port, OTHER)[0], 200)

    def test_no_header_falls_back_to_the_socket(self):
        self.assertEqual(get(self.filtered_port)[0], 200)

    def test_a_prepended_address_does_not_get_anyone_banned(self):
        """Right-to-left: the client's own entry is not what is used.

        nginx's usual snippet appends, so a client that sends its own
        X-Forwarded-For ends up left of the address the proxy added.
        Reading from the left would take the forged one.
        """
        self.assertEqual(get(self.filtered_port, f'{CLIENT}, {OTHER}')[0], 200)


class TestSpoofingTheWayThrough(base.IntegrationTestCase):
    """The same chain, with the deny rule on the entry that is real."""

    filtered_port = None

    @classmethod
    def build_config(cls):
        cls.filtered_port = base.free_port()
        return {
            'ports': [],
            'http': [
                {'address': '127.0.0.1', 'port': cls.port},
                {'address': '127.0.0.1', 'port': cls.filtered_port,
                 'trusted_proxies': ['127.0.0.1'],
                 'deny': [OTHER]},
            ],
        }

    def test_the_rightmost_untrusted_hop_is_the_client(self):
        self.assertEqual(get(self.filtered_port, f'{CLIENT}, {OTHER}')[0], 403)

    def test_prepending_anything_does_not_help(self):
        """What an attacker would try: name a permitted address first"""
        self.assertEqual(
            get(self.filtered_port, f'10.0.0.1, {OTHER}')[0], 403)


class TestABadListIsVisible(base.IntegrationTestCase):
    """A typo must not leave a server that silently filters the proxy."""

    @classmethod
    def build_config(cls):
        return {
            'ports': [],
            'http': [
                {'id': 'main', 'address': '127.0.0.1', 'port': cls.port},
                {'id': 'broken', 'address': '127.0.0.1',
                 'port': base.free_port(),
                 'trusted_proxies': ['127.0.0.l']},   # letter l, not one
            ],
        }

    def test_the_server_does_not_start_and_says_why(self):
        status, body = self.get('/api/settings')
        self.assertEqual(status, 200)
        broken = [s for s in body['http'] if s['id'] == 'broken'][0]
        self.assertIn('127.0.0.l', broken['error'])

    def test_the_other_server_is_unaffected(self):
        body = self.get('/api/settings')[1]
        main = [s for s in body['http'] if s['id'] == 'main'][0]
        self.assertNotIn('error', main)


if __name__ == '__main__':
    unittest.main()
