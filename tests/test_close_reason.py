"""A WebSocket client is told why it is being let go.

It used to hear "Server shutting down" whatever happened - the process
stopping, its server changed in the editor, its port deleted. The page
could not tell a restart it should wait out from a server rebuilt under
it that is back in a second. The code says what to do, the reason says
what happened:

- the process stops          1001 Server shutting down
- its server is reconfigured 1012 Reconfigured   (reconnect now)
- its port is deleted        4404 Port removed   (do not reconnect)

4404 is from the range the standard leaves to applications, chosen to
read like the HTTP answer for a thing that is not there. 1001 would have
said the same as a server stopping, and the page would have gone on
knocking on an endpoint that is gone.

A TCP, TELNET, TLS or Unix socket client has no close frame to carry a
reason in, so those servers take the same word and ignore it.
"""

import json
import selectors
import socket
import unittest
from unittest.mock import MagicMock, Mock, patch

from ser2tcp.http_auth import hash_password
from ser2tcp.http_server import HttpServerWrapper
from ser2tcp.serial_proxy import SerialProxy
from ser2tcp.server_websocket import ServerWebSocket
from tests.test_http_server import MockClient
from tests.test_server_websocket import make_ws_client, make_ws_server


class TestTheWebSocketServerSaysWhy(unittest.TestCase):

    def closed_with(self, **kwargs):
        server = make_ws_server()
        client = make_ws_client()
        server.add_connection(client)
        server.close(**kwargs)
        return client.ws_close.call_args[0]

    def test_a_stopping_process(self):
        self.assertEqual(self.closed_with(), (1001, 'Server shutting down'))

    def test_a_server_reconfigured(self):
        """1012 is the code for "restarting - come back": the page
        reconnects on it, and the server is there again at once"""
        self.assertEqual(
            self.closed_with(why='reconfigured'), (1012, 'Reconfigured'))

    def test_a_port_removed(self):
        self.assertEqual(
            self.closed_with(why='removed'), (4404, 'Port removed'))

    def test_an_unknown_why_is_a_mistake(self):
        with self.assertRaises(ValueError):
            self.closed_with(why='bored')


class TestASocketServerTakesTheWordToo(unittest.TestCase):

    def test_it_accepts_why(self):
        """So a proxy can close every kind of server the same way"""
        selector = selectors.DefaultSelector()
        self.addCleanup(selector.close)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        proxy = SerialProxy(
            {'id': 'dev', 'serial': {'port': '/dev/ser2tcp-nowhere'},
             'servers': [{'protocol': 'tcp', 'address': '127.0.0.1',
                          'port': port}]},
            log=MagicMock(), selector=selector)
        self.addCleanup(proxy.close)
        proxy.servers[0].close(why='reconfigured')
        self.assertIsNone(proxy.servers[0]._socket)


class TestTheMonitorSaysWhy(unittest.TestCase):
    """A monitor is let go when its port is rebuilt or deleted - it is
    built for one SerialProxy - and says which"""

    def setUp(self):
        from ser2tcp.server_monitor import ServerMonitor
        proxy = MagicMock()
        proxy.servers = []
        self.monitor = ServerMonitor(proxy, log=MagicMock())
        self.client = make_ws_client()
        self.monitor._connections.append(self.client)

    def test_a_deleted_port(self):
        self.monitor.port_gone()
        self.client.ws_close.assert_called_once_with(4404, 'Port removed')

    def test_a_rebuilt_port(self):
        """Its name is still there, on a new proxy - come back to it"""
        self.monitor.port_gone(why='reconfigured')
        self.client.ws_close.assert_called_once_with(1012, 'Reconfigured')

    def test_both_are_announced_first(self):
        """A close code is a number; the port topic says it in words"""
        self.monitor.port_gone(why='reconfigured')
        sent = [json.loads(c.args[0]) for c in self.client.ws_send.call_args_list]
        self.assertIn({'port': None}, sent)


class TestWhoSaysWhat(unittest.TestCase):
    """Which word each of the paths that close servers passes on"""

    def setUp(self):
        self.selector = selectors.DefaultSelector()
        self.addCleanup(self.selector.close)
        self.old = [
            {'protocol': 'websocket', 'endpoint': 'one'},
            {'protocol': 'websocket', 'endpoint': 'two'},
        ]
        self.proxy = SerialProxy(
            {'id': 'dev', 'serial': {'port': '/dev/ser2tcp-nowhere'},
             'servers': [dict(s) for s in self.old]},
            log=MagicMock(), selector=self.selector)
        self.addCleanup(self.proxy.close)

    def watch(self, index):
        client = make_ws_client()
        self.proxy.servers[index].add_connection(client)
        return client

    def test_a_server_changed_by_a_save_is_reconfigured(self):
        changed = self.watch(1)
        kept = self.watch(0)
        self.proxy.reconcile_servers(
            self.old, [self.old[0], dict(self.old[1], token='new')])
        changed.ws_close.assert_called_once_with(1012, 'Reconfigured')
        kept.ws_close.assert_not_called()

    def test_a_server_taken_out_by_a_save_is_reconfigured(self):
        """The port is still there - the server went in an edit of it"""
        gone = self.watch(1)
        self.proxy.reconcile_servers(self.old, [self.old[0]])
        gone.ws_close.assert_called_once_with(1012, 'Reconfigured')

    def test_a_stopping_proxy_says_so(self):
        client = self.watch(0)
        self.proxy.close()
        client.ws_close.assert_called_once_with(1001, 'Server shutting down')


class TestTheApiSaysWhy(unittest.TestCase):
    """What PUT and DELETE on a port tell the clients they drop"""

    def setUp(self):
        self.stored = {
            'id': 'dev', 'name': 'dev',
            'serial': {'port': '/dev/ttyUSB0', 'baudrate': 115200},
            'servers': [{'protocol': 'websocket', 'endpoint': 'ep'}],
        }
        self.proxy = Mock()
        self.proxy.id = 'dev'
        self.proxy.name = 'dev'
        self.proxy.error = None
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
                server_manager=Mock())
        patch.object(self.wrapper, '_save_config').start()
        patch.object(self.wrapper, '_create_proxy').start()
        self.addCleanup(patch.stopall)
        login = MockClient(method='POST', path='/api/login',
            data={'login': 'admin', 'password': 's'})
        self.wrapper._handle_request(login)
        self.token = login.responded['token']

    def request(self, method, data=None):
        client = MockClient(method=method, path='/api/ports/dev', data=data,
            headers={'authorization': 'Bearer ' + self.token})
        self.wrapper._handle_request(client)
        return client

    def test_a_port_rebuilt_by_a_save_is_reconfigured(self):
        data = {k: v for k, v in self.stored.items() if k != 'id'}
        data['serial'] = dict(data['serial'], baudrate=9600)
        with patch.object(self.wrapper, 'drop_monitor') as drop:
            self.assertEqual(self.request('PUT', data).respond_status, 200)
        self.proxy.close.assert_called_once_with(why='reconfigured')
        drop.assert_called_once_with('dev', why='reconfigured')

    def test_a_deleted_port_is_removed(self):
        with patch.object(self.wrapper, 'drop_monitor') as drop:
            self.assertEqual(self.request('DELETE').respond_status, 200)
        self.proxy.close.assert_called_once_with(why='removed')
        drop.assert_called_once_with('dev', why='removed')


if __name__ == '__main__':
    unittest.main()
