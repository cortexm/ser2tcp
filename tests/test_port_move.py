"""POST /api/ports/<id>/move - put a port before or after another one.

Anchored by id, never by a number: "before esp32" still means what the
person dragging meant after somebody else has added a port in another
tab, where "to position 2" or "two steps up" would not.
"""

import unittest
from unittest.mock import Mock, patch

from ser2tcp.http_auth import hash_password
from ser2tcp.http_server import HttpServerWrapper
from tests.test_http_server import MockClient

IDS = ['a', 'b', 'c', 'd']


class MoveTestCase(unittest.TestCase):

    def setUp(self):
        self.ports = [
            {'id': port_id, 'serial': {'port': '/dev/' + port_id},
             'servers': []}
            for port_id in IDS]
        self.proxies = []
        for port in self.ports:
            proxy = Mock()
            proxy.id = port['id']
            self.proxies.append(proxy)
        configuration = {
            'http': [{'address': '127.0.0.1', 'port': 0}],
            'users': [
                {'login': 'admin', 'password': hash_password('secret'),
                 'admin': True},
                {'login': 'viewer', 'password': hash_password('secret'),
                 'admin': False},
            ],
            'ports': self.ports,
        }
        self.manager = Mock()
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            self.wrapper = HttpServerWrapper(
                {'address': '127.0.0.1', 'port': 0}, self.proxies,
                log=Mock(), configuration=configuration,
                server_manager=self.manager)
        self.save = patch.object(self.wrapper, '_save_config').start()
        self.addCleanup(patch.stopall)
        self.token = self._login('admin')

    def _login(self, login):
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': login, 'password': 'secret'})
        self.wrapper._handle_request(client)
        return client.responded['token']

    def move(self, port_id, data, token=None):
        client = MockClient(
            method='POST', path='/api/ports/%s/move' % port_id, data=data,
            headers={'authorization': 'Bearer ' + (token or self.token)})
        self.wrapper._handle_request(client)
        return client

    def config_order(self):
        return [p['id'] for p in self.wrapper._get_ports_config()]

    def runtime_order(self):
        return [p.id for p in self.wrapper._serial_proxies]


class TestWhereItLands(MoveTestCase):

    def test_before_another_port(self):
        client = self.move('d', {'before': 'b'})
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(self.config_order(), ['a', 'd', 'b', 'c'])

    def test_after_another_port(self):
        self.move('a', {'after': 'c'})
        self.assertEqual(self.config_order(), ['b', 'c', 'a', 'd'])

    def test_to_the_front(self):
        self.move('c', {'before': 'a'})
        self.assertEqual(self.config_order(), ['c', 'a', 'b', 'd'])

    def test_to_the_end(self):
        self.move('a', {'after': 'd'})
        self.assertEqual(self.config_order(), ['b', 'c', 'd', 'a'])

    def test_one_step_down_is_after_the_next(self):
        """What an arrow button sends"""
        self.move('b', {'after': 'c'})
        self.assertEqual(self.config_order(), ['a', 'c', 'b', 'd'])

    def test_before_itself_changes_nothing(self):
        client = self.move('b', {'before': 'b'})
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(self.config_order(), IDS)

    def test_where_it_already_is_changes_nothing(self):
        client = self.move('b', {'before': 'c'})
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(self.config_order(), IDS)

    def test_the_answer_is_the_new_order(self):
        """So the page can draw it without waiting for the stream"""
        client = self.move('d', {'before': 'a'})
        self.assertEqual(client.responded['order'], ['d', 'a', 'b', 'c'])


class TestWhatMovesWithIt(MoveTestCase):

    def test_the_running_ports_move_with_the_config(self):
        """The two lists are paired by position; everything that works
        by position after resolving an id depends on it."""
        self.move('d', {'before': 'b'})
        self.assertEqual(self.runtime_order(), self.config_order())

    def test_nothing_is_rebuilt(self):
        """A move is an order, not a change to any port: every proxy is
        the same object, still open, still registered."""
        before = list(self.proxies)
        with patch.object(self.wrapper, '_create_proxy') as create:
            self.move('d', {'before': 'b'})
        create.assert_not_called()
        self.assertEqual(
            sorted(map(id, self.wrapper._serial_proxies)),
            sorted(map(id, before)))
        for proxy in before:
            proxy.close.assert_not_called()
        self.manager.remove_server.assert_not_called()

    def test_it_is_written_down(self):
        self.move('d', {'before': 'b'})
        self.save.assert_called_once()

    def test_a_move_that_changes_nothing_writes_nothing(self):
        self.move('b', {'before': 'b'})
        self.save.assert_not_called()

    def test_a_port_can_still_be_addressed_after_it_moved(self):
        self.move('d', {'before': 'a'})
        client = MockClient(
            method='GET', path='/api/ports/d',
            headers={'authorization': 'Bearer ' + self.token})
        self.wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(client.responded['id'], 'd')


class TestWhatIsRefused(MoveTestCase):

    def assertUnchanged(self):
        self.assertEqual(self.config_order(), IDS)
        self.assertEqual(self.runtime_order(), IDS)
        self.save.assert_not_called()

    def test_an_unknown_port(self):
        client = self.move('nope', {'before': 'a'})
        self.assertEqual(client.respond_status, 404)
        self.assertUnchanged()

    def test_an_anchor_that_is_gone_is_a_conflict(self):
        """Deleted in another tab since this one looked - the list has
        changed under the request, which is what 409 says"""
        client = self.move('a', {'before': 'nope'})
        self.assertEqual(client.respond_status, 409)
        self.assertUnchanged()

    def test_both_directions(self):
        client = self.move('a', {'before': 'b', 'after': 'c'})
        self.assertEqual(client.respond_status, 400)
        self.assertUnchanged()

    def test_no_direction(self):
        client = self.move('a', {})
        self.assertEqual(client.respond_status, 400)
        self.assertUnchanged()

    def test_a_number_is_not_an_anchor(self):
        """Positions are what this replaces"""
        client = self.move('a', {'before': 2})
        self.assertEqual(client.respond_status, 400)
        self.assertUnchanged()

    def test_not_an_object(self):
        client = self.move('a', ['b'])
        self.assertEqual(client.respond_status, 400)
        self.assertUnchanged()

    def test_only_an_admin(self):
        client = self.move('a', {'after': 'd'}, token=self._login('viewer'))
        self.assertEqual(client.respond_status, 403)
        self.assertUnchanged()

    def test_only_post(self):
        client = MockClient(
            method='PUT', path='/api/ports/a/move', data={'after': 'd'},
            headers={'authorization': 'Bearer ' + self.token})
        self.wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 405)
        self.assertUnchanged()


if __name__ == '__main__':
    unittest.main()
