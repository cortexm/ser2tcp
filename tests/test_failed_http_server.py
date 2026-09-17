"""Tests for the placeholder standing in for an HTTP server that failed.

A server that will not bind used to be dropped from the runtime list
while staying in the configuration, so the two drifted apart: the entry
after it was edited, closed and rebuilt the wrong socket, and the
failing server itself was listed as though it were fine.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from ser2tcp.http_server import FailedHttpServer, HttpServerWrapper
from tests.test_http_server import MockClient


class TestFailedHttpServer(unittest.TestCase):
    """Inert, and quiet about it"""

    def test_it_carries_the_reason(self):
        self.assertIn('bind', FailedHttpServer('cannot bind').error)

    def test_the_loop_can_drive_it(self):
        """Every pass calls these on everything in the list"""
        placeholder = FailedHttpServer('cannot bind')
        placeholder.maintenance()
        placeholder.close()


class FailedHttpServerTestCase(unittest.TestCase):
    """Two servers configured, the first one refusing to bind."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'config.json')
        self.config = {
            'ports': [],
            'http': [
                {'id': 'broken', 'address': '127.0.0.1', 'port': 8080},
                {'id': 'working', 'address': '127.0.0.1', 'port': 8081},
            ],
        }
        self._write()
        self.built = []
        self.wrapper = self._wrapper()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f)

    def _sockets(self, fail_ports=(8080,)):
        """A socket per bind, except on the ports that must fail"""
        def build(address=None, port=None, **_kwargs):
            if port in fail_ports:
                raise OSError(48, 'Address already in use')
            server = Mock(name=f'server:{address}:{port}')
            self.built.append(server)
            return server
        return build

    def _wrapper(self, fail_ports=(8080,)):
        with patch('ser2tcp.http_server._uhttp_server.HttpServer',
                   side_effect=self._sockets(fail_ports)):
            return HttpServerWrapper(
                self.config['http'], [], log=Mock(),
                config_path=self.path, configuration=self.config)

    def _request(self, method, path, data=None, fail_ports=(8080,)):
        client = MockClient(method=method, path=path, data=data)
        with patch('ser2tcp.http_server._uhttp_server.HttpServer',
                   side_effect=self._sockets(fail_ports)):
            self.wrapper._handle_request(client)
        return client

    def _stored(self):
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)


class TestTheListsStayAligned(FailedHttpServerTestCase):

    def test_the_failed_server_keeps_its_place(self):
        self.assertEqual(len(self.wrapper._servers), 2)

    def test_the_working_server_is_where_its_config_is(self):
        self.assertIs(self.wrapper._servers[1][0], self.built[0])

    def test_editing_the_working_one_closes_its_own_socket(self):
        """It used to close the socket of the entry before it"""
        running = self.built[0]
        self._request('PUT', '/api/settings/http/working', {
            'address': '127.0.0.1', 'port': 8082})
        running.close.assert_called_once()

    def test_editing_the_working_one_really_moves_it(self):
        self._request('PUT', '/api/settings/http/working', {
            'address': '127.0.0.1', 'port': 8082})
        self.assertEqual(self._stored()['http'][1]['port'], 8082)
        self.assertIs(self.wrapper._servers[1][0], self.built[-1])

    def test_a_failed_server_that_is_fixed_starts_running(self):
        self._request('PUT', '/api/settings/http/broken', {
            'address': '127.0.0.1', 'port': 8083})
        self.assertIs(self.wrapper._servers[0][0], self.built[-1])
        self.assertNotIsInstance(
            self.wrapper._servers[0][0], FailedHttpServer)

    def test_deleting_the_failed_one_leaves_the_other_running(self):
        running = self.built[0]
        self._request('DELETE', '/api/settings/http/broken')
        self.assertEqual(len(self.wrapper._servers), 1)
        self.assertIs(self.wrapper._servers[0][0], running)
        running.close.assert_not_called()

    def test_a_reload_keeps_the_place_too(self):
        with patch('ser2tcp.http_server._uhttp_server.HttpServer',
                   side_effect=self._sockets()):
            self.wrapper.reload_http_servers()
        self.assertEqual(len(self.wrapper._servers), 2)
        self.assertIsInstance(self.wrapper._servers[0][0], FailedHttpServer)


class TestTheFailureIsVisible(FailedHttpServerTestCase):

    def _settings(self):
        client = MockClient(method='GET', path='/api/settings')
        self.wrapper._handle_request(client)
        return client.responded['http']

    def test_the_failed_server_says_why(self):
        entry = self._settings()[0]
        self.assertIn('Address already in use', entry['error'])

    def test_the_working_server_says_nothing(self):
        self.assertNotIn('error', self._settings()[1])

    def test_the_reason_is_not_written_to_the_config(self):
        self.assertNotIn('error', self._stored()['http'][0])

    def test_saving_the_entry_back_does_not_store_the_reason(self):
        """The UI sends back what it was given"""
        entry = dict(self._settings()[0])
        entry['name'] = 'renamed'
        self._request('PUT', '/api/settings/http/broken', entry)
        self.assertNotIn('error', self._stored()['http'][0])

    def test_a_server_that_could_not_be_put_back_says_so(self):
        """The rollback can fail too - something took the port meanwhile.

        Closed and not rebuilt, it went on being listed as running.
        """
        self._request('PUT', '/api/settings/http/working', {
            'address': '127.0.0.1', 'port': 8084},
            fail_ports=(8080, 8081, 8084))
        self.assertIn('error', self._settings()[1])

    def test_an_edit_that_fails_again_still_says_why(self):
        self._request('PUT', '/api/settings/http/broken', {
            'address': '127.0.0.1', 'port': 8084}, fail_ports=(8080, 8084))
        self.assertIn('error', self._settings()[0])


if __name__ == '__main__':
    unittest.main()
