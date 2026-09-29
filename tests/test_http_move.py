"""POST /api/settings/http/<id>/move - the HTTP servers' own order.

The same request as a port's move, over the same shape: a list in the
config and a list of running servers paired with it by position. Put
one before or after another by id; nothing stops listening.
"""

import unittest
from unittest.mock import Mock, patch

from ser2tcp.http_auth import hash_password
from ser2tcp.http_server import HttpServerWrapper
from tests.test_http_server import MockClient

IDS = ['main', 'lan', 'tls', 'spare']


class HttpMoveTestCase(unittest.TestCase):

    def setUp(self):
        self.http = [
            {'id': server_id, 'address': '127.0.0.1', 'port': 0}
            for server_id in IDS]
        configuration = {
            'http': self.http,
            'users': [
                {'login': 'admin', 'password': hash_password('s'),
                 'admin': True},
                {'login': 'viewer', 'password': hash_password('s'),
                 'admin': False},
            ],
        }
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            self.wrapper = HttpServerWrapper(
                self.http, [], log=Mock(), configuration=configuration,
                server_manager=Mock())
        self.running = [entry[0] for entry in self.wrapper._servers]
        self.save = patch.object(self.wrapper, '_save_config').start()
        self.addCleanup(patch.stopall)
        self.token = self._login('admin')

    def _login(self, login):
        client = MockClient(method='POST', path='/api/login',
            data={'login': login, 'password': 's'})
        self.wrapper._handle_request(client)
        return client.responded['token']

    def move(self, server_id, data, token=None, method='POST'):
        client = MockClient(
            method=method, path='/api/settings/http/%s/move' % server_id,
            data=data,
            headers={'authorization': 'Bearer ' + (token or self.token)})
        self.wrapper._handle_request(client)
        return client

    def config_order(self):
        return [s['id'] for s in self.wrapper._http_list()]

    def runtime_order(self):
        return [entry[2]['id'] for entry in self.wrapper._servers]


class TestWhereItLands(HttpMoveTestCase):

    def test_before_another(self):
        client = self.move('spare', {'before': 'lan'})
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(self.config_order(), ['main', 'spare', 'lan', 'tls'])
        self.assertEqual(client.responded['order'], self.config_order())

    def test_after_another(self):
        self.move('main', {'after': 'tls'})
        self.assertEqual(self.config_order(), ['lan', 'tls', 'main', 'spare'])

    def test_to_the_front_and_the_end(self):
        self.move('tls', {'before': 'main'})
        self.move('lan', {'after': 'spare'})
        self.assertEqual(self.config_order(), ['tls', 'main', 'spare', 'lan'])

    def test_where_it_already_is_changes_nothing(self):
        client = self.move('lan', {'before': 'tls'})
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(self.config_order(), IDS)
        self.save.assert_not_called()


class TestWhatMovesWithIt(HttpMoveTestCase):

    def test_the_running_servers_move_with_the_config(self):
        """Paired by position: an update or a delete by id would land on
        the wrong server otherwise"""
        self.move('spare', {'before': 'main'})
        self.assertEqual(self.runtime_order(), self.config_order())

    def test_nothing_stops_listening(self):
        self.move('spare', {'before': 'main'})
        for server in self.running:
            server.close.assert_not_called()
        self.assertEqual(
            sorted(map(id, (e[0] for e in self.wrapper._servers))),
            sorted(map(id, self.running)))

    def test_it_is_written_down(self):
        self.move('spare', {'before': 'main'})
        self.save.assert_called_once()

    def test_a_moved_server_is_still_edited_by_its_id(self):
        self.move('spare', {'before': 'main'})
        client = MockClient(
            method='PUT', path='/api/settings/http/spare',
            data={'address': '127.0.0.1', 'port': 8081, 'name': 'renamed'},
            headers={'authorization': 'Bearer ' + self.token})
        with patch.object(self.wrapper, '_create_http_server',
                          return_value=(Mock(), None, {}, None)):
            self.wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200, client.responded)
        self.assertEqual(self.wrapper._http_list()[0].get('name'), 'renamed')


class TestWhatIsRefused(HttpMoveTestCase):

    def assertUnchanged(self):
        self.assertEqual(self.config_order(), IDS)
        self.assertEqual(self.runtime_order(), IDS)
        self.save.assert_not_called()

    def test_an_unknown_server(self):
        self.assertEqual(self.move('nope', {'before': 'main'}).respond_status,
                         404)
        self.assertUnchanged()

    def test_an_anchor_that_is_gone_is_a_conflict(self):
        self.assertEqual(self.move('main', {'before': 'nope'}).respond_status,
                         409)
        self.assertUnchanged()

    def test_a_number_is_not_an_anchor(self):
        self.assertEqual(self.move('main', {'before': 2}).respond_status, 400)
        self.assertUnchanged()

    def test_both_directions(self):
        self.assertEqual(
            self.move('main', {'before': 'lan', 'after': 'tls'}).respond_status,
            400)
        self.assertUnchanged()

    def test_only_an_admin(self):
        client = self.move('main', {'after': 'spare'},
                           token=self._login('viewer'))
        self.assertEqual(client.respond_status, 403)
        self.assertUnchanged()

    def test_only_post(self):
        client = self.move('main', {'after': 'spare'}, method='PUT')
        self.assertEqual(client.respond_status, 405)
        self.assertUnchanged()


if __name__ == '__main__':
    unittest.main()
