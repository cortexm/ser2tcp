"""Saving a port whose servers changed rebuilds only those servers.

The editor sends the whole port, as it always has. What changed is what
the process does with it: a server whose configuration is the same as
before keeps running, and keeps its clients. Only a server that was
changed, added or removed is touched - one that changed is a server
gone and a server added, since without an id there is no telling them
apart, and no need to. A new order moves the servers and nothing else,
and a save that changes nothing does nothing.
"""

import selectors
import socket
import unittest
from unittest.mock import MagicMock, Mock, patch

from ser2tcp.http_auth import hash_password
from ser2tcp.http_server import HttpServerWrapper
from ser2tcp.serial_proxy import SerialProxy
from ser2tcp.server import ConfigError
from tests import requires_bind_conflict
from tests.test_http_server import MockClient


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _tcp(port, **extra):
    return dict(
        {'protocol': 'tcp', 'address': '127.0.0.1', 'port': port}, **extra)


class ReconcileTestCase(unittest.TestCase):

    def setUp(self):
        self.selector = selectors.DefaultSelector()
        self.addCleanup(self.selector.close)
        self.ports = [_free_port() for _ in range(4)]
        self.old = [
            _tcp(self.ports[0]),
            _tcp(self.ports[1]),
            {'protocol': 'websocket', 'endpoint': 'demo'},
        ]
        self.proxy = SerialProxy(
            {'id': 'dev', 'serial': {'port': '/dev/ser2tcp-nowhere'},
             'servers': [dict(s) for s in self.old]},
            log=MagicMock(), selector=self.selector)
        self.addCleanup(self.proxy.close)
        self.before = list(self.proxy.servers)

    def reconcile(self, new):
        return self.proxy.reconcile_servers(self.old, new)

    def listening(self, server):
        return getattr(server, '_socket', None) is not None


class TestWhatIsKept(ReconcileTestCase):

    def test_nothing_changed_touches_nothing(self):
        self.assertFalse(self.reconcile([dict(s) for s in self.old]))
        self.assertEqual(self.proxy.servers, self.before)

    def test_a_config_that_only_spells_itself_differently_is_the_same(self):
        """Key order is not configuration"""
        respelled = [dict(reversed(list(s.items()))) for s in self.old]
        self.assertFalse(self.reconcile(respelled))
        self.assertEqual(self.proxy.servers, self.before)

    def test_a_new_order_moves_the_servers_and_nothing_else(self):
        new = [self.old[2], self.old[0], self.old[1]]
        self.assertTrue(self.reconcile(new))
        self.assertEqual(
            self.proxy.servers,
            [self.before[2], self.before[0], self.before[1]])
        self.assertTrue(all(self.listening(s) for s in self.before[:2]))

    def test_a_changed_server_is_the_only_one_rebuilt(self):
        new = [self.old[0], _tcp(self.ports[1], access='ro'), self.old[2]]
        self.assertTrue(self.reconcile(new))
        self.assertIs(self.proxy.servers[0], self.before[0])
        self.assertIs(self.proxy.servers[2], self.before[2])
        self.assertIsNot(self.proxy.servers[1], self.before[1])
        # The old one let go of its address first, so the new one - on
        # the same address - could take it.
        self.assertFalse(self.listening(self.before[1]))
        self.assertTrue(self.listening(self.proxy.servers[1]))

    def test_an_added_server_lands_where_it_was_put(self):
        new = [self.old[0], _tcp(self.ports[2]), self.old[1], self.old[2]]
        self.assertTrue(self.reconcile(new))
        servers = self.proxy.servers
        self.assertEqual(len(servers), 4)
        self.assertIs(servers[0], self.before[0])
        self.assertIs(servers[2], self.before[1])
        self.assertIs(servers[3], self.before[2])
        self.assertEqual(servers[1].config['port'], self.ports[2])
        self.assertTrue(self.listening(servers[1]))

    def test_a_removed_server_is_closed(self):
        new = [self.old[1], self.old[2]]
        self.assertTrue(self.reconcile(new))
        self.assertEqual(self.proxy.servers, self.before[1:])
        self.assertFalse(self.listening(self.before[0]))

    def test_a_new_server_is_registered_in_the_loop(self):
        """The regression that made a rebuilt port accept nothing: a
        server built after startup has to join the selector too"""
        new = self.old + [_tcp(self.ports[2])]
        self.reconcile(new)
        registered = {
            key.fileobj for key in self.selector.get_map().values()}
        self.assertIn(self.proxy.servers[3]._socket, registered)

    def test_a_removed_server_leaves_the_loop(self):
        gone = self.before[0]._socket
        self.reconcile([self.old[1], self.old[2]])
        registered = {
            key.fileobj for key in self.selector.get_map().values()}
        self.assertNotIn(gone, registered)


class TestAServerThatWillNotStart(ReconcileTestCase):

    @requires_bind_conflict
    def test_everything_is_put_back(self):
        """One new server cannot bind: the save fails, and the port runs
        exactly what it ran before - the same servers, in the same order,
        and the one that was being replaced listening again."""
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(('127.0.0.1', self.ports[3]))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        new = [
            self.old[0],
            _tcp(self.ports[1], access='ro'),   # replaces a server
            _tcp(self.ports[3]),                # cannot bind
            self.old[2],
        ]
        with self.assertRaises(ConfigError):
            self.reconcile(new)
        servers = self.proxy.servers
        self.assertEqual(len(servers), 3)
        self.assertIs(servers[0], self.before[0])
        self.assertIs(servers[2], self.before[2])
        self.assertEqual(servers[1].config, self.old[1])
        self.assertTrue(self.listening(servers[1]))
        # Nothing half-built is left in the loop.
        registered = {
            key.fileobj for key in self.selector.get_map().values()}
        self.assertEqual(
            registered,
            {s._socket for s in servers if self.listening(s)})


class WrapperTestCase(unittest.TestCase):
    """What PUT /api/ports/<id> does with it"""

    def setUp(self):
        self.stored = {
            'id': 'dev', 'name': 'dev',
            'serial': {'port': '/dev/ttyUSB0', 'baudrate': 115200},
            'servers': [_tcp(10001), _tcp(10002)],
        }
        self.proxy = Mock()
        self.proxy.id = 'dev'
        self.proxy.name = 'dev'
        self.proxy.error = None
        self.proxy.reconcile_servers.return_value = True
        self.manager = Mock()
        configuration = {
            'http': [{'address': '127.0.0.1', 'port': 0}],
            'users': [{'login': 'admin', 'password': hash_password('s'),
                       'admin': True}],
            'ports': [self.stored],
        }
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            self.wrapper = HttpServerWrapper(
                {'address': '127.0.0.1', 'port': 0}, [self.proxy],
                log=Mock(), configuration=configuration,
                server_manager=self.manager)
        self.save = patch.object(self.wrapper, '_save_config').start()
        self.create = patch.object(self.wrapper, '_create_proxy').start()
        self.addCleanup(patch.stopall)
        login = MockClient(method='POST', path='/api/login',
            data={'login': 'admin', 'password': 's'})
        self.wrapper._handle_request(login)
        self.token = login.responded['token']

    def put(self, data):
        client = MockClient(method='PUT', path='/api/ports/dev', data=data,
            headers={'authorization': 'Bearer ' + self.token})
        self.wrapper._handle_request(client)
        return client

    def edited(self, **changes):
        data = {k: v for k, v in self.stored.items() if k != 'id'}
        data = dict(data, servers=[dict(s) for s in data['servers']])
        data.update(changes)
        return data


class TestWhatASaveRebuilds(WrapperTestCase):

    def test_only_servers_changed_rebuilds_no_port(self):
        new_servers = [_tcp(10002), _tcp(10001)]
        client = self.put(self.edited(servers=new_servers))
        self.assertEqual(client.respond_status, 200)
        self.proxy.reconcile_servers.assert_called_once()
        self.proxy.close.assert_not_called()
        self.create.assert_not_called()
        self.manager.remove_server.assert_not_called()
        self.assertEqual(
            self.wrapper._get_ports_config()[0]['servers'], new_servers)
        self.save.assert_called_once()

    def test_a_save_that_changes_nothing_does_nothing(self):
        """It used to rebuild the whole port - every client dropped for
        opening the editor and pressing Save"""
        self.proxy.reconcile_servers.return_value = False
        client = self.put(self.edited())
        self.assertEqual(client.respond_status, 200)
        self.proxy.close.assert_not_called()
        self.create.assert_not_called()
        self.save.assert_not_called()

    def test_a_port_level_change_still_rebuilds_the_port(self):
        """The serial settings, the name, the limit - those are the port
        itself, and it is rebuilt as before"""
        serial = dict(self.stored['serial'], baudrate=9600)
        client = self.put(self.edited(serial=serial))
        self.assertEqual(client.respond_status, 200)
        self.proxy.reconcile_servers.assert_not_called()
        self.proxy.close.assert_called_once()
        self.create.assert_called_once()

    def test_a_port_that_never_started_is_rebuilt_whole(self):
        """A port that failed to start has no servers running to keep,
        and a save is its chance to start at all"""
        self.proxy.error = 'failed to bind'
        client = self.put(self.edited(servers=[_tcp(10002), _tcp(10001)]))
        self.assertEqual(client.respond_status, 200)
        self.proxy.reconcile_servers.assert_not_called()
        self.create.assert_called_once()

    def test_a_rename_rebuilds_the_port(self):
        """The name is the port's, and its id follows it"""
        client = self.put(self.edited(name='renamed'))
        self.assertEqual(client.respond_status, 200)
        self.proxy.reconcile_servers.assert_not_called()
        self.create.assert_called_once()

    def test_a_server_that_will_not_start_is_a_400_and_nothing_is_saved(self):
        self.proxy.reconcile_servers.side_effect = ConfigError(
            'TCP 127.0.0.1:10003: failed to bind: Address already in use')
        client = self.put(self.edited(servers=[_tcp(10001), _tcp(10003)]))
        self.assertEqual(client.respond_status, 400)
        self.assertIn('Address already in use', client.responded['error'])
        self.save.assert_not_called()
        self.assertEqual(
            self.wrapper._get_ports_config()[0]['servers'],
            [_tcp(10001), _tcp(10002)])


if __name__ == '__main__':
    unittest.main()
