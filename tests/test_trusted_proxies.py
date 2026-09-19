"""Tests for reading the client address from behind a reverse proxy.

Without this, every client behind nginx looks like nginx: `allow`/`deny`
stop distinguishing anyone and the log names the proxy. uhttp resolves
the real client from X-Forwarded-For, but only for connections that
arrive from a listed proxy — so the list is the whole security boundary
and a typo in it must not pass quietly.
"""

import unittest
from unittest.mock import Mock, patch

import ser2tcp.http_server as http_server
from ser2tcp.http_server import FailedHttpServer, HttpServerWrapper
from tests.test_http_server import MockClient, make_wrapper


class TestReadingTheAddress(unittest.TestCase):
    """_client_ip() is the one place that answers "who is asking"."""

    def setUp(self):
        self.wrapper = make_wrapper()

    def test_it_prefers_what_uhttp_resolved(self):
        client = MockClient()
        client.addr = ('10.0.0.1', 5000)        # the proxy
        client.remote_address = '203.0.113.9'   # the client behind it
        self.assertEqual(self.wrapper._client_ip(client), '203.0.113.9')

    def test_it_falls_back_to_the_socket(self):
        """Objects without the property still have to work"""
        client = MockClient()
        client.addr = ('10.0.0.1', 5000)
        self.assertEqual(self.wrapper._client_ip(client), '10.0.0.1')

    def test_a_mock_is_not_an_address(self):
        client = MockClient()
        client.addr = ('10.0.0.1', 5000)
        client.remote_address = Mock()
        self.assertEqual(self.wrapper._client_ip(client), '10.0.0.1')

    def test_nothing_at_all_is_survivable(self):
        self.assertEqual(self.wrapper._client_ip(MockClient()), '-')


class TestTheListReachesTheServer(unittest.TestCase):

    def _built_with(self, config):
        configuration = {'http': [config]}
        with patch('ser2tcp.http_server._uhttp_server.HttpServer') as built:
            HttpServerWrapper(
                [config], [], log=Mock(), configuration=configuration)
        return built.call_args.kwargs

    def test_it_is_passed_on(self):
        kwargs = self._built_with({
            'address': '127.0.0.1', 'port': 8080,
            'trusted_proxies': ['127.0.0.1', '10.0.0.1']})
        self.assertEqual(
            kwargs.get('trusted_proxies'), ['127.0.0.1', '10.0.0.1'])

    def test_without_it_nothing_is_trusted(self):
        kwargs = self._built_with({'address': '127.0.0.1', 'port': 8080})
        self.assertIsNone(kwargs.get('trusted_proxies'))


class TestATypoDoesNotPassQuietly(unittest.TestCase):
    """uhttp accepts an unparseable entry and simply never matches it.

    That is safe — no trust is forged — but silent: the filter goes on
    seeing the proxy and nothing says why. Same shape as D5, so it gets
    the same answer.
    """

    def _validate(self, value):
        return make_wrapper()._validate_http_config({
            'address': '127.0.0.1', 'port': 8080, 'trusted_proxies': value})

    def test_a_good_list_passes(self):
        self.assertIsNone(self._validate(['127.0.0.1', '::1']))

    def test_an_address_with_a_port_passes(self):
        """uhttp normalises these, so refusing them here would differ"""
        self.assertIsNone(self._validate(['127.0.0.1:8080', '[::1]:8080']))

    def test_a_bare_string_is_refused(self):
        error = self._validate('127.0.0.1')
        self.assertIn('list', error)

    def test_an_entry_that_is_not_a_string_is_refused(self):
        self.assertIn('string', self._validate([127]))

    def test_a_typo_is_refused(self):
        error = self._validate(['127.0.0.l'])
        self.assertIn('127.0.0.l', error)

    def test_cidr_is_refused_and_says_so(self):
        """uhttp matches exact addresses; a range would silently never hit"""
        error = self._validate(['10.0.0.0/8'])
        self.assertIn('CIDR', error)

    def test_an_empty_list_is_fine(self):
        self.assertIsNone(self._validate([]))


class TestABadListStopsTheServerVisibly(unittest.TestCase):
    """Startup goes through the same door as `allow`/`deny` (C10)."""

    def test_the_server_keeps_its_place_and_says_why(self):
        config = {'id': 'main', 'address': '127.0.0.1', 'port': 8080,
                  'trusted_proxies': ['nonsense']}
        configuration = {'http': [config]}
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            wrapper = HttpServerWrapper(
                [config], [], log=Mock(), configuration=configuration)
        self.assertEqual(len(wrapper._servers), 1)
        placeholder = wrapper._servers[0][0]
        self.assertIsInstance(placeholder, FailedHttpServer)
        self.assertIn('nonsense', placeholder.error)


class TestTheApiRefusesItToo(unittest.TestCase):

    def test_adding_a_server_with_a_bad_list_is_a_400(self):
        wrapper = make_wrapper()
        client = MockClient(
            method='POST', path='/api/settings/http',
            data={'address': '127.0.0.1', 'port': 8081,
                  'trusted_proxies': ['nonsense']})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)
        self.assertIn('nonsense', client.responded['error'])


if __name__ == '__main__':
    unittest.main()
