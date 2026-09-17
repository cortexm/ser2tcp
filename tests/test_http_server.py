"""Tests for HTTP server wrapper"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch

from ser2tcp.cert_manager import CertManager, generate_certificate
from ser2tcp.http_auth import hash_password
from ser2tcp.http_server import (
    FailedHttpServer, HttpServerWrapper, _describe_detected, connection_id)
from ser2tcp.server_websocket import ServerWebSocket


class MockClient:
    """Mock uhttp HttpConnection"""
    def __init__(self, method='GET', path='/', headers=None, query=None,
            data=None):
        self.method = method
        self.path = path
        self.headers = headers or {}
        self.query = query
        self.data = data
        self.responded = None
        self.respond_status = None
        # NDJSON streaming state
        self.ndjson_started = False
        self.ndjson_lines = []
        self.ndjson_alive = True  # set False to simulate peer disconnect

    def respond(self, data=None, status=200, headers=None, cookies=None):
        self.responded = data
        self.respond_status = status

    def respond_file(self, file_name, headers=None):
        self.responded = ('file', file_name)
        self.respond_status = 200

    def response_ndjson(self, headers=None, cookies=None):
        self.ndjson_started = True
        return True

    def send_ndjson(self, obj):
        if not self.ndjson_alive:
            return False
        self.ndjson_lines.append(obj)
        return True

    def close(self):
        self.ndjson_alive = False


def make_wrapper(auth_config=None, serial_proxies=None, config_path=None):
    """Create HttpServerWrapper with mocked uhttp server.

    Pass config_path to keep the cert manager inside a temporary
    directory — without it, it would fall back to ~/.config/ser2tcp.
    """
    http_config = {'address': '127.0.0.1', 'port': 0}
    # Auth config goes at root level of configuration
    configuration = {'http': [http_config]}
    if auth_config:
        if 'users' in auth_config:
            configuration['users'] = auth_config['users']
        if 'tokens' in auth_config:
            configuration['tokens'] = auth_config['tokens']
        if 'session_timeout' in auth_config:
            configuration['session_timeout'] = auth_config['session_timeout']
    proxies = serial_proxies if serial_proxies is not None else []
    with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
        return HttpServerWrapper(http_config, proxies, log=Mock(),
            config_path=config_path, configuration=configuration)


class TestRouting(unittest.TestCase):
    def test_api_status_no_auth(self):
        wrapper = make_wrapper()
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertIn('ports', client.responded)

    def test_api_detect_no_auth(self):
        wrapper = make_wrapper()
        client = MockClient(path='/api/detect')
        with patch('ser2tcp.http_server._list_ports.comports', return_value=[]):
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)

    def test_api_unknown_returns_404(self):
        wrapper = make_wrapper()
        client = MockClient(path='/api/unknown')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_static_index(self):
        wrapper = make_wrapper()
        client = MockClient(path='/')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(client.responded[0], 'file')
        self.assertTrue(client.responded[1].endswith('index.html'))

    def test_static_not_found(self):
        wrapper = make_wrapper()
        client = MockClient(path='/nonexistent.html')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_static_path_traversal(self):
        wrapper = make_wrapper()
        client = MockClient(path='/../../../etc/passwd')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_post_unknown_returns_404(self):
        wrapper = make_wrapper()
        client = MockClient(method='POST', path='/api/unknown')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)


class TestAuth(unittest.TestCase):
    def _auth_config(self):
        return {
            'users': [{
                'login': 'admin',
                'password': hash_password('secret'),
                'admin': True,
            }],
            'tokens': [
                {'token': 'api-key', 'name': 'bot'},
            ],
        }

    def test_api_requires_auth(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 401)

    def test_api_with_bearer(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        # Login first
        login_client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(login_client)
        self.assertEqual(login_client.respond_status, 200)
        token = login_client.responded['token']
        # Use token
        client = MockClient(
            path='/api/status',
            headers={'authorization': f'Bearer {token}'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)

    def test_api_with_query_token(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        login_client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(login_client)
        token = login_client.responded['token']
        client = MockClient(path='/api/status', query={'token': token})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)

    def test_api_with_api_token(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        client = MockClient(
            path='/api/status',
            headers={'authorization': 'Bearer api-key'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)

    def test_invalid_token_401(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        client = MockClient(
            path='/api/status',
            headers={'authorization': 'Bearer invalid'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 401)

    def test_login_wrong_password(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'wrong'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 401)

    def test_login_unknown_user(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'nobody', 'password': 'x'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 401)

    def test_login_invalid_data(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        client = MockClient(
            method='POST', path='/api/login', data='not json')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_login_no_auth_configured(self):
        wrapper = make_wrapper()
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'x'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_logout(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        login_client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(login_client)
        token = login_client.responded['token']
        # Logout
        logout_client = MockClient(
            method='POST', path='/api/logout',
            headers={'authorization': f'Bearer {token}'})
        wrapper._handle_request(logout_client)
        self.assertEqual(logout_client.respond_status, 200)
        # Token no longer valid
        client = MockClient(
            path='/api/status',
            headers={'authorization': f'Bearer {token}'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 401)

    def test_static_no_auth_needed(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        client = MockClient(path='/')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)


class TestApiStatus(unittest.TestCase):
    def _make_proxy(self, port=None, baudrate=None, match=None,
            connected=False, servers=None, name='',
            bytesize=None, parity=None, stopbits=None):
        proxy = Mock()
        cfg = {}
        if port:
            cfg['port'] = port
        if baudrate:
            cfg['baudrate'] = baudrate
        if bytesize:
            cfg['bytesize'] = bytesize
        if parity:
            cfg['parity'] = parity
        if stopbits:
            cfg['stopbits'] = stopbits
        proxy.serial_config = cfg
        proxy.match = match
        proxy.name = name
        proxy.is_connected = connected
        proxy.servers = servers or []
        return proxy

    def _make_server(self, protocol='TCP', address='0.0.0.0', port=21000,
            connections=None, ssl=None):
        server = Mock()
        server.protocol = protocol
        config = {'address': address, 'port': port}
        if ssl:
            config['ssl'] = ssl
        server.config = config
        server.connections = connections or []
        return server

    def test_empty_proxies(self):
        wrapper = make_wrapper(serial_proxies=[])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        self.assertEqual(client.responded['ports'], [])
        self.assertIn('admin', client.responded)

    def test_proxy_with_port(self):
        proxy = self._make_proxy(port='/dev/ttyUSB0', baudrate=115200)
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        serial = client.responded['ports'][0]['serial']
        self.assertEqual(serial['port'], '/dev/ttyUSB0')
        self.assertEqual(serial['baudrate'], 115200)

    def test_proxy_with_match(self):
        proxy = self._make_proxy(
            match={'serial_number': 'abc'}, connected=False)
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        serial = client.responded['ports'][0]['serial']
        self.assertEqual(serial['match'], {'serial_number': 'abc'})
        self.assertFalse(serial['connected'])

    def test_proxy_no_baudrate(self):
        proxy = self._make_proxy(port='/dev/ttyS0')
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        serial = client.responded['ports'][0]['serial']
        self.assertNotIn('baudrate', serial)

    def test_server_with_connections(self):
        con = Mock()
        con.address_str.return_value = '192.168.1.5:54321'
        server = self._make_server(connections=[con])
        proxy = self._make_proxy(port='/dev/ttyUSB0', servers=[server])
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        srv = client.responded['ports'][0]['servers'][0]
        self.assertEqual(srv['protocol'], 'TCP')
        self.assertEqual(srv['port'], 21000)
        self.assertEqual(len(srv['connections']), 1)
        self.assertEqual(srv['connections'][0]['address'], '192.168.1.5:54321')

    def test_socket_server_no_port(self):
        server = self._make_server(protocol='SOCKET', address='/tmp/s.sock')
        proxy = self._make_proxy(port='/dev/ttyS0', servers=[server])
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        srv = client.responded['ports'][0]['servers'][0]
        self.assertNotIn('port', srv)
        self.assertEqual(srv['address'], '/tmp/s.sock')

    def test_proxy_with_name(self):
        proxy = self._make_proxy(port='/dev/ttyUSB0', name='gate2a')
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        self.assertEqual(client.responded['ports'][0]['name'], 'gate2a')

    def test_proxy_without_name(self):
        proxy = self._make_proxy(port='/dev/ttyUSB0')
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        self.assertNotIn('name', client.responded['ports'][0])

    def test_serial_params_in_status(self):
        proxy = self._make_proxy(
            port='/dev/ttyUSB0', baudrate=115200,
            bytesize='SEVENBITS', parity='EVEN', stopbits='TWO')
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        serial = client.responded['ports'][0]['serial']
        self.assertEqual(serial['bytesize'], 'SEVENBITS')
        self.assertEqual(serial['parity'], 'EVEN')
        self.assertEqual(serial['stopbits'], 'TWO')

    def test_ssl_config_in_status(self):
        ssl_cfg = {'bundle': 'main', 'require_client_cert': False}
        server = self._make_server(
            protocol='SSL', port=10443, ssl=ssl_cfg)
        proxy = self._make_proxy(
            port='/dev/ttyUSB0', servers=[server])
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        srv = client.responded['ports'][0]['servers'][0]
        self.assertEqual(srv['ssl'], ssl_cfg)

    def test_no_ssl_config_for_tcp(self):
        server = self._make_server(protocol='TCP')
        proxy = self._make_proxy(
            port='/dev/ttyUSB0', servers=[server])
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        srv = client.responded['ports'][0]['servers'][0]
        self.assertNotIn('ssl', srv)


class TestApiDisconnect(unittest.TestCase):
    def _make_connection(self, address='192.168.1.5:54321'):
        con = Mock()
        con.address_str.return_value = address
        return con

    def _make_server_with_con(self):
        con = self._make_connection()
        server = Mock()
        server.protocol = 'TCP'
        server.config = {'address': '0.0.0.0', 'port': 21000}
        server.connections = [con]
        return server, con

    def _make_proxy_with_con(self):
        server, con = self._make_server_with_con()
        proxy = Mock()
        proxy.serial_config = {'port': '/dev/ttyUSB0'}
        proxy.match = None
        proxy.name = ''
        proxy.is_connected = True
        proxy.servers = [server]
        return proxy, server, con

    def _wrapper_with(self, proxies, port_id='p1'):
        configuration = {
            'http': [{'address': '127.0.0.1', 'port': 0}],
            'ports': [{'id': port_id, 'serial': {'port': '/dev/ttyUSB0'},
                       'servers': []}],
        }
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            return HttpServerWrapper(
                {'address': '127.0.0.1', 'port': 0}, proxies,
                log=Mock(), configuration=configuration)

    def test_disconnect_client(self):
        proxy, server, con = self._make_proxy_with_con()
        wrapper = self._wrapper_with([proxy])
        conn_id = connection_id(con)
        client = MockClient(
            method='DELETE',
            path='/api/ports/p1/connections/' + conn_id)
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        server.disconnect_client.assert_called_once_with(con)

    def test_disconnect_port_not_found(self):
        wrapper = self._wrapper_with([])
        client = MockClient(
            method='DELETE', path='/api/ports/nosuch/connections/1')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_disconnect_connection_not_found(self):
        proxy, _, _ = self._make_proxy_with_con()
        wrapper = self._wrapper_with([proxy])
        client = MockClient(
            method='DELETE', path='/api/ports/p1/connections/nosuch')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_a_connection_keeps_its_id(self):
        """The whole point: it must not move when a neighbour leaves"""
        proxy, _, con = self._make_proxy_with_con()
        self.assertEqual(connection_id(con), connection_id(con))


class TestApiDetect(unittest.TestCase):
    def _make_port_info(self, device='/dev/ttyUSB0', vid=None, pid=None,
            serial_number=None, manufacturer=None, product=None,
            location=None, description=None, hwid=None):
        p = Mock()
        p.device = device
        p.vid = vid
        p.pid = pid
        p.serial_number = serial_number
        p.manufacturer = manufacturer
        p.product = product
        p.location = location
        p.description = description
        p.hwid = hwid
        return p

    def test_empty(self):
        wrapper = make_wrapper()
        client = MockClient(path='/api/detect')
        with patch('ser2tcp.http_server._list_ports.comports',
                return_value=[]):
            wrapper._handle_request(client)
        self.assertEqual(client.responded, [])

    def test_usb_device(self):
        port = self._make_port_info(
            vid=0x303A, pid=0x4001,
            serial_number='abc', manufacturer='Espressif',
            product='ESP32', location='1-1')
        wrapper = make_wrapper()
        client = MockClient(path='/api/detect')
        with patch('ser2tcp.http_server._list_ports.comports',
                return_value=[port]):
            wrapper._handle_request(client)
        info = client.responded[0]
        self.assertEqual(info['device'], '/dev/ttyUSB0')
        self.assertEqual(info['vid'], '0x303A')
        self.assertEqual(info['pid'], '0x4001')
        self.assertEqual(info['serial_number'], 'abc')
        self.assertEqual(info['manufacturer'], 'Espressif')

    def test_non_usb_device(self):
        port = self._make_port_info(
            device='/dev/ttyS0', description='n/a', hwid='n/a')
        wrapper = make_wrapper()
        client = MockClient(path='/api/detect')
        with patch('ser2tcp.http_server._list_ports.comports',
                return_value=[port]):
            wrapper._handle_request(client)
        info = client.responded[0]
        self.assertEqual(info['device'], '/dev/ttyS0')
        self.assertNotIn('vid', info)
        self.assertNotIn('description', info)
        self.assertNotIn('hwid', info)

    def test_description_shown_when_not_na(self):
        port = self._make_port_info(description='USB Serial Port')
        wrapper = make_wrapper()
        client = MockClient(path='/api/detect')
        with patch('ser2tcp.http_server._list_ports.comports',
                return_value=[port]):
            wrapper._handle_request(client)
        self.assertEqual(client.responded[0]['description'], 'USB Serial Port')


class TestApiUsers(unittest.TestCase):
    def _auth_config(self):
        return {
            'users': [{
                'login': 'admin',
                'password': hash_password('secret'),
                'admin': True,
            }],
        }

    def _admin_token(self, wrapper):
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(client)
        return client.responded['token']

    def _auth_client(self, token, method='GET', path='/', data=None):
        return MockClient(
            method=method, path=path, data=data,
            headers={'authorization': f'Bearer {token}'})

    def test_list_users(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        token = self._admin_token(wrapper)
        client = self._auth_client(token, path='/api/users')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(len(client.responded), 1)
        self.assertEqual(client.responded[0]['login'], 'admin')
        self.assertNotIn('password', client.responded[0])

    def test_add_user(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='POST', path='/api/users',
            data={'login': 'new', 'password': 'pass123'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)
        # Verify new user can login
        login = MockClient(
            method='POST', path='/api/login',
            data={'login': 'new', 'password': 'pass123'})
        wrapper._handle_request(login)
        self.assertEqual(login.respond_status, 200)

    def test_add_user_with_hash(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        token = self._admin_token(wrapper)
        h = hash_password('hashed')
        client = self._auth_client(
            token, method='POST', path='/api/users',
            data={'login': 'new', 'password': h})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)
        login = MockClient(
            method='POST', path='/api/login',
            data={'login': 'new', 'password': 'hashed'})
        wrapper._handle_request(login)
        self.assertEqual(login.respond_status, 200)

    def test_add_user_duplicate(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='POST', path='/api/users',
            data={'login': 'admin', 'password': 'x'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_add_user_missing_fields(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='POST', path='/api/users',
            data={'login': 'new'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_add_user_non_admin(self):
        auth = self._auth_config()
        auth['users'].append({
            'login': 'viewer', 'password': hash_password('pass'),
        })
        wrapper = make_wrapper(auth_config=auth)
        login = MockClient(
            method='POST', path='/api/login',
            data={'login': 'viewer', 'password': 'pass'})
        wrapper._handle_request(login)
        token = login.responded['token']
        client = self._auth_client(
            token, method='POST', path='/api/users',
            data={'login': 'x', 'password': 'x'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 403)

    def test_update_user_password(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='PUT', path='/api/users/admin',
            data={'password': 'newpass'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        login = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'newpass'})
        wrapper._handle_request(login)
        self.assertEqual(login.respond_status, 200)

    def test_update_user_not_found(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='PUT', path='/api/users/nobody',
            data={'password': 'x'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_delete_user(self):
        auth = self._auth_config()
        auth['users'].append({
            'login': 'toremove', 'password': hash_password('x'),
        })
        wrapper = make_wrapper(auth_config=auth)
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='DELETE', path='/api/users/toremove')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        login = MockClient(
            method='POST', path='/api/login',
            data={'login': 'toremove', 'password': 'x'})
        wrapper._handle_request(login)
        self.assertEqual(login.respond_status, 401)

    def test_delete_user_not_found(self):
        wrapper = make_wrapper(auth_config=self._auth_config())
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='DELETE', path='/api/users/nobody')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_users_list_no_auth(self):
        wrapper = make_wrapper()
        client = MockClient(path='/api/users')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(client.responded, [])

    def test_bootstrap_add_first_user(self):
        """Add first user without any auth configured"""
        wrapper = make_wrapper()
        client = MockClient(
            method='POST', path='/api/users',
            data={'login': 'admin', 'password': 'secret', 'admin': True})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)
        # Auth is now active - need token
        client2 = MockClient(path='/api/status')
        wrapper._handle_request(client2)
        self.assertEqual(client2.respond_status, 401)
        # Can login with new user
        login = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(login)
        self.assertEqual(login.respond_status, 200)

    def test_bootstrap_empty_auth(self):
        """Add first user when auth section exists but empty"""
        wrapper = make_wrapper(auth_config={})
        client = MockClient(
            method='POST', path='/api/users',
            data={'login': 'admin', 'password': 'pass', 'admin': True})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)


class TestApiPortsCrud(unittest.TestCase):
    """Tests for port configuration CRUD API"""

    def _auth_config(self):
        return {
            'users': [{
                'login': 'admin',
                'password': hash_password('secret'),
                'admin': True,
            }],
        }

    def _admin_token(self, wrapper):
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(client)
        return client.responded['token']

    def _auth_client(self, token, method='GET', path='/', data=None):
        return MockClient(
            method=method, path=path, data=data,
            headers={'authorization': f'Bearer {token}'})

    def _port_config(self, port='/dev/ttyUSB0', baudrate=115200,
            protocol='tcp', address='0.0.0.0', srv_port=10001):
        cfg = {
            'serial': {'port': port, 'baudrate': baudrate},
            'servers': [{'protocol': protocol, 'address': address,
                'port': srv_port}],
        }
        return cfg

    def _make_wrapper_with_ports(self, port_configs=None):
        auth = self._auth_config()
        configuration = {
            'http': [{'address': '127.0.0.1', 'port': 0}],
            'users': auth['users'],
            'ports': port_configs or [],
        }
        proxies = []
        if port_configs:
            for number, cfg in enumerate(port_configs):
                cfg.setdefault('id', 'port%d' % number)
                proxy = Mock()
                proxy.id = cfg['id']
                proxy.error = None
                proxy.serial_config = cfg['serial']
                proxy.match = cfg['serial'].get('match')
                proxy.is_connected = False
                proxy.servers = []
                proxy.close = Mock()
                proxies.append(proxy)
        manager = Mock()
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            wrapper = HttpServerWrapper(
                {'address': '127.0.0.1', 'port': 0}, proxies,
                log=Mock(), configuration=configuration,
                server_manager=manager)
        return wrapper, manager

    def test_add_port(self):
        wrapper, manager = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        cfg = self._port_config()
        with patch.object(wrapper, '_create_proxy') as mock_create:
            mock_create.return_value = Mock()
            client = self._auth_client(
                token, method='POST', path='/api/ports', data=cfg)
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)
        self.assertTrue(client.responded['id'])
        manager.add_server.assert_called_once()

    def test_add_port_missing_serial(self):
        wrapper, _ = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='POST', path='/api/ports',
            data={'servers': [{'protocol': 'tcp', 'port': 10001}]})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_add_port_missing_servers(self):
        wrapper, _ = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='POST', path='/api/ports',
            data={'serial': {'port': '/dev/ttyUSB0'}})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_add_port_empty_servers(self):
        wrapper, _ = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='POST', path='/api/ports',
            data={'serial': {'port': '/dev/ttyUSB0'}, 'servers': []})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_add_port_unknown_protocol(self):
        wrapper, _ = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        cfg = self._port_config()
        cfg['servers'][0]['protocol'] = 'unknown'
        client = self._auth_client(
            token, method='POST', path='/api/ports', data=cfg)
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_add_port_socket_no_port_needed(self):
        wrapper, manager = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        cfg = {
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{'protocol': 'socket', 'address': '/tmp/s.sock'}],
        }
        with patch.object(wrapper, '_create_proxy') as mock_create:
            mock_create.return_value = Mock()
            client = self._auth_client(
                token, method='POST', path='/api/ports', data=cfg)
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)

    def test_add_port_tcp_missing_port(self):
        wrapper, _ = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        cfg = {
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{'protocol': 'tcp', 'address': '0.0.0.0'}],
        }
        client = self._auth_client(
            token, method='POST', path='/api/ports', data=cfg)
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_update_port(self):
        cfg = self._port_config()
        wrapper, manager = self._make_wrapper_with_ports([cfg])
        token = self._admin_token(wrapper)
        new_cfg = self._port_config(baudrate=9600)
        with patch.object(wrapper, '_create_proxy') as mock_create:
            mock_create.return_value = Mock()
            client = self._auth_client(
                token, method='PUT', path='/api/ports/port0', data=new_cfg)
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        manager.remove_server.assert_called_once()
        manager.add_server.assert_called_once()

    def test_update_port_not_found(self):
        wrapper, _ = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        cfg = self._port_config()
        client = self._auth_client(
            token, method='PUT', path='/api/ports/port0', data=cfg)
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_update_an_unknown_port(self):
        wrapper, _ = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='PUT', path='/api/ports/nosuch',
            data=self._port_config())
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_delete_port(self):
        cfg = self._port_config()
        wrapper, manager = self._make_wrapper_with_ports([cfg])
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='DELETE', path='/api/ports/port0')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        manager.remove_server.assert_called_once()
        self.assertEqual(len(wrapper._serial_proxies), 0)

    def test_delete_port_not_found(self):
        wrapper, _ = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='DELETE', path='/api/ports/port0')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_add_port_non_admin(self):
        wrapper, _ = self._make_wrapper_with_ports()
        wrapper._auth.add_user('viewer', 'pass')
        login = MockClient(
            method='POST', path='/api/login',
            data={'login': 'viewer', 'password': 'pass'})
        wrapper._handle_request(login)
        token = login.responded['token']
        cfg = self._port_config()
        client = self._auth_client(
            token, method='POST', path='/api/ports', data=cfg)
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 403)

    def test_add_port_with_match(self):
        wrapper, manager = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        cfg = {
            'serial': {'match': {'vid': '0x303A'}, 'baudrate': 115200},
            'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                'port': 10001}],
        }
        with patch.object(wrapper, '_create_proxy') as mock_create:
            mock_create.return_value = Mock()
            client = self._auth_client(
                token, method='POST', path='/api/ports', data=cfg)
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)

    def test_config_saved_after_add(self):
        wrapper, _ = self._make_wrapper_with_ports()
        token = self._admin_token(wrapper)
        cfg = self._port_config()
        with patch.object(wrapper, '_create_proxy') as mock_create, \
                patch.object(wrapper, '_save_config') as mock_save:
            mock_create.return_value = Mock()
            client = self._auth_client(
                token, method='POST', path='/api/ports', data=cfg)
            wrapper._handle_request(client)
        mock_save.assert_called_once()

    def test_config_saved_after_delete(self):
        cfg = self._port_config()
        wrapper, _ = self._make_wrapper_with_ports([cfg])
        token = self._admin_token(wrapper)
        with patch.object(wrapper, '_save_config') as mock_save:
            client = self._auth_client(
                token, method='DELETE', path='/api/ports/port0')
            wrapper._handle_request(client)
        mock_save.assert_called_once()

    def test_old_proxy_closed_on_update(self):
        cfg = self._port_config()
        wrapper, _ = self._make_wrapper_with_ports([cfg])
        old_proxy = wrapper._serial_proxies[0]
        token = self._admin_token(wrapper)
        new_cfg = self._port_config(baudrate=9600)
        with patch.object(wrapper, '_create_proxy') as mock_create:
            mock_create.return_value = Mock()
            client = self._auth_client(
                token, method='PUT', path='/api/ports/port0', data=new_cfg)
            wrapper._handle_request(client)
        old_proxy.close.assert_called_once()

    def test_old_proxy_closed_on_delete(self):
        cfg = self._port_config()
        wrapper, _ = self._make_wrapper_with_ports([cfg])
        old_proxy = wrapper._serial_proxies[0]
        token = self._admin_token(wrapper)
        client = self._auth_client(
            token, method='DELETE', path='/api/ports/port0')
        wrapper._handle_request(client)
        old_proxy.close.assert_called_once()


class TestControlValidation(unittest.TestCase):
    """Tests for control config validation in port API"""

    def _auth_config(self):
        return {
            'users': [{
                'login': 'admin',
                'password': hash_password('secret'),
                'admin': True,
            }],
        }

    def _admin_token(self, wrapper):
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(client)
        return client.responded['token']

    def _make_wrapper(self):
        auth = self._auth_config()
        configuration = {
            'http': [{'address': '127.0.0.1', 'port': 0}],
            'users': auth['users'],
            'ports': [],
        }
        manager = Mock()
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            wrapper = HttpServerWrapper(
                {'address': '127.0.0.1', 'port': 0}, [],
                log=Mock(), configuration=configuration,
                server_manager=manager)
        return wrapper

    def test_add_port_with_control(self):
        wrapper = self._make_wrapper()
        token = self._admin_token(wrapper)
        cfg = {
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                'port': 10001,
                'control': {'signals': ['rts', 'dtr', 'cts']}}],
        }
        with patch.object(wrapper, '_create_proxy') as mock_create:
            mock_create.return_value = Mock()
            client = MockClient(
                method='POST', path='/api/ports', data=cfg,
                headers={'authorization': f'Bearer {token}'})
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)

    def test_control_rejected_for_telnet(self):
        wrapper = self._make_wrapper()
        token = self._admin_token(wrapper)
        cfg = {
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{'protocol': 'telnet', 'address': '0.0.0.0',
                'port': 10001,
                'control': {'signals': ['cts']}}],
        }
        client = MockClient(
            method='POST', path='/api/ports', data=cfg,
            headers={'authorization': f'Bearer {token}'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)
        self.assertIn('TELNET', client.responded['error'])

    def test_control_unknown_signal(self):
        wrapper = self._make_wrapper()
        token = self._admin_token(wrapper)
        cfg = {
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                'port': 10001,
                'control': {'signals': ['unknown']}}],
        }
        client = MockClient(
            method='POST', path='/api/ports', data=cfg,
            headers={'authorization': f'Bearer {token}'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)
        self.assertIn('Unknown signal', client.responded['error'])

    def test_signals_endpoint(self):
        proxy = Mock()
        proxy.name = 'test'
        proxy.is_connected = True
        proxy.get_signals.return_value = 0b000101  # rts + cts
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/signals')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(len(client.responded), 1)
        signals = client.responded[0]['signals']
        self.assertTrue(signals['rts'])
        self.assertTrue(signals['cts'])
        self.assertFalse(signals['dtr'])

    def test_status_includes_control(self):
        proxy = Mock()
        proxy.name = 'test'
        proxy.is_connected = False
        proxy.serial_config = {'port': '/dev/ttyUSB0'}
        proxy.match = None
        server = Mock()
        server.protocol = 'TCP'
        server.config = {
            'address': '0.0.0.0', 'port': 10001,
            'control': {'signals': ['cts', 'dsr']},
        }
        server.control = {'signals': ['cts', 'dsr']}
        server.connections = []
        proxy.servers = [server]
        wrapper = make_wrapper(serial_proxies=[proxy])
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        srv = client.responded['ports'][0]['servers'][0]
        self.assertEqual(srv['control'], {'signals': ['cts', 'dsr']})


class TestConfigVariants(unittest.TestCase):
    def test_single_dict_config(self):
        with patch('ser2tcp.http_server._uhttp_server.HttpServer') as mock:
            HttpServerWrapper(
                {'address': '0.0.0.0', 'port': 8080}, [], log=Mock())
            mock.assert_called_once()

    def test_list_config(self):
        with patch('ser2tcp.http_server._uhttp_server.HttpServer') as mock:
            HttpServerWrapper([
                {'address': '0.0.0.0', 'port': 8080},
                {'address': '0.0.0.0', 'port': 8081},
            ], [], log=Mock())
            self.assertEqual(mock.call_count, 2)


class TestIpFilterValidation(unittest.TestCase):
    """Test IP filter validation in port config"""

    def test_allow_list_valid(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'port': 10001,
                'allow': ['192.168.1.0/24', '10.0.0.5'],
            }]
        })
        self.assertIsNone(result)

    def test_deny_list_valid(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'port': 10001,
                'deny': ['10.0.0.0/8'],
            }]
        })
        self.assertIsNone(result)

    def test_allow_and_deny_valid(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'port': 10001,
                'allow': ['192.168.0.0/16'],
                'deny': ['192.168.1.100'],
            }]
        })
        self.assertIsNone(result)

    def test_allow_not_list(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'port': 10001,
                'allow': '192.168.1.0/24',
            }]
        })
        self.assertIn('allow must be a list', result)

    def test_deny_not_list(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'port': 10001,
                'deny': '10.0.0.0/8',
            }]
        })
        self.assertIn('deny must be a list', result)

    def test_allow_entry_not_string(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'port': 10001,
                'allow': [123],
            }]
        })
        self.assertIn('allow entries must be strings', result)

    def test_a_rule_that_is_not_an_address_is_refused(self):
        """It used to pass here and be dropped when the filter was built,
        so the server came up enforcing less than the config said."""
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'port': 10001,
                'deny': ['192.168.1.1OO'],
            }]
        })
        self.assertIn('192.168.1.1OO', result)

    def test_an_http_server_rule_is_checked_too(self):
        wrapper = make_wrapper()
        result = wrapper._validate_http_config({
            'address': '127.0.0.1', 'port': 8080, 'allow': 'not-a-list'})
        self.assertIn('allow must be a list', result)

    def test_a_valid_http_server_rule_passes(self):
        wrapper = make_wrapper()
        result = wrapper._validate_http_config({
            'address': '127.0.0.1', 'port': 8080,
            'allow': ['192.168.0.0/16']})
        self.assertIsNone(result)

    def test_websocket_with_ip_filter(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'websocket',
                'endpoint': 'test',
                'allow': ['192.168.1.0/24'],
            }]
        })
        self.assertIsNone(result)


class TestMaxConnectionsValidation(unittest.TestCase):
    """Test max_connections validation"""

    def test_max_connections_valid(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'address': '0.0.0.0',
                'port': 10001,
                'max_connections': 5,
            }]
        })
        self.assertIsNone(result)

    def test_max_connections_zero_unlimited(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'address': '0.0.0.0',
                'port': 10001,
                'max_connections': 0,
            }]
        })
        self.assertIsNone(result)

    def test_max_connections_negative_invalid(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'address': '0.0.0.0',
                'port': 10001,
                'max_connections': -1,
            }]
        })
        self.assertIn('max_connections', result)

    def test_max_connections_string_invalid(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'tcp',
                'address': '0.0.0.0',
                'port': 10001,
                'max_connections': '5',
            }]
        })
        self.assertIn('max_connections', result)

    def test_max_connections_websocket(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'servers': [{
                'protocol': 'websocket',
                'endpoint': 'test',
                'max_connections': 1,
            }]
        })
        self.assertIsNone(result)

    def test_port_level_max_connections_valid(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'max_connections': 10,
            'servers': [{
                'protocol': 'tcp',
                'address': '0.0.0.0',
                'port': 10001,
            }]
        })
        self.assertIsNone(result)

    def test_port_level_max_connections_zero(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'max_connections': 0,
            'servers': [{
                'protocol': 'tcp',
                'address': '0.0.0.0',
                'port': 10001,
            }]
        })
        self.assertIsNone(result)

    def test_port_level_max_connections_invalid(self):
        wrapper = make_wrapper()
        result = wrapper._validate_port_config({
            'serial': {'port': '/dev/ttyUSB0'},
            'max_connections': -5,
            'servers': [{
                'protocol': 'tcp',
                'address': '0.0.0.0',
                'port': 10001,
            }]
        })
        self.assertIn('max_connections', result)


# ===========================================================================
# Helpers for the new-feature tests below
# ===========================================================================

def _proxy(port=None, baudrate=None, match=None, connected=False,
        servers=None, name='', signals=0, error=None):
    proxy = Mock()
    # A started port answers None here; FailedProxy answers with why.
    proxy.error = error
    cfg = {}
    if port:
        cfg['port'] = port
    if baudrate:
        cfg['baudrate'] = baudrate
    proxy.serial_config = cfg
    proxy.match = match
    proxy.name = name
    proxy.is_connected = connected
    proxy.max_connections = 0
    proxy.servers = servers or []
    # get_signals only called when is_connected is True; default to 0 so
    # tests that flip connected mid-run don't TypeError on bitmask logic.
    proxy.get_signals.return_value = signals
    return proxy


def _server(protocol='TCP', address='0.0.0.0', port=10001,
        endpoint=None, connections=None, control=None,
        max_connections=0, data_enabled=True):
    s = Mock()
    s.protocol = protocol
    cfg = {'address': address, 'port': port}
    s.config = cfg
    s.connections = connections or []
    s.endpoint = endpoint
    s.control = control
    s.max_connections = max_connections
    s.data_enabled = data_enabled
    return s


# ===========================================================================
# _describe_detected (module-level helper used by USB plug/unplug logging)
# ===========================================================================
class TestDescribeDetected(unittest.TestCase):
    def test_device_only(self):
        s = _describe_detected({'device': '/dev/ttyUSB0'})
        self.assertEqual(s, '/dev/ttyUSB0')

    def test_with_vid_pid_and_product(self):
        s = _describe_detected({
            'device': '/dev/ttyUSB0', 'vid': '0x303A', 'pid': '0x4001',
            'product': 'ESP32-C6', 'manufacturer': 'Espressif',
            'serial_number': 'abc123',
        })
        self.assertIn('/dev/ttyUSB0', s)
        self.assertIn('0x303A:0x4001', s)
        self.assertIn('ESP32-C6', s)
        self.assertIn('Espressif', s)
        self.assertIn('abc123', s)

    def test_pid_alone_omitted(self):
        # No pid → no vid:pid bracket either (we require both)
        s = _describe_detected({'device': '/dev/x', 'vid': '0x1234'})
        self.assertNotIn('0x1234', s)

    def test_skips_empty_attrs(self):
        s = _describe_detected({
            'device': '/dev/x', 'product': '', 'serial_number': None,
        })
        self.assertEqual(s, '/dev/x')


# ===========================================================================
# _device_matches (USB attribute filter with wildcards)
# ===========================================================================
class TestDeviceMatches(unittest.TestCase):
    def test_exact_match(self):
        d = {'serial_number': 'abc123', 'product': 'X'}
        self.assertTrue(HttpServerWrapper._device_matches(
            d, {'serial_number': 'abc123'}))

    def test_case_insensitive(self):
        d = {'serial_number': 'AbC'}
        self.assertTrue(HttpServerWrapper._device_matches(
            d, {'serial_number': 'abc'}))

    def test_wildcard_prefix(self):
        d = {'product': 'CP2102N'}
        self.assertTrue(HttpServerWrapper._device_matches(
            d, {'product': 'CP210*'}))

    def test_wildcard_no_match(self):
        d = {'product': 'FT232'}
        self.assertFalse(HttpServerWrapper._device_matches(
            d, {'product': 'CP210*'}))

    def test_multi_attr_all_must_match(self):
        d = {'vid': '0x303A', 'pid': '0x4001'}
        self.assertTrue(HttpServerWrapper._device_matches(
            d, {'vid': '0x303A', 'pid': '0x4001'}))
        self.assertFalse(HttpServerWrapper._device_matches(
            d, {'vid': '0x303A', 'pid': '0xDEAD'}))

    def test_missing_attr_in_detected(self):
        # detected has no `serial_number` but match requires it
        d = {'vid': '0x1234'}
        self.assertFalse(HttpServerWrapper._device_matches(
            d, {'serial_number': 'abc'}))

    def test_invalid_regex_falls_back_to_equality(self):
        # Match value with broken regex chars — should still work via the
        # `re.error` except branch (compares as equal-string instead).
        d = {'product': '[unfinished'}
        self.assertTrue(HttpServerWrapper._device_matches(
            d, {'product': '[unfinished'}))


# ===========================================================================
# _compute_port_state — drives port card colors (online/offline/error)
# ===========================================================================
class TestComputePortState(unittest.TestCase):
    def setUp(self):
        self.wrapper = make_wrapper()

    def test_connected_is_online(self):
        proxy = _proxy(port='/dev/ttyUSB0', connected=True)
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, []), 'online')

    def test_device_present_is_offline(self):
        proxy = _proxy(port='/dev/ttyUSB0', connected=False)
        detected = [{'device': '/dev/ttyUSB0'}]
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, detected), 'offline')

    def test_device_missing_is_error(self):
        proxy = _proxy(port='/dev/ttyUSB0', connected=False)
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, []), 'error')

    def test_match_present_is_offline(self):
        proxy = _proxy(match={'serial_number': 'abc'}, connected=False)
        detected = [{'device': '/dev/ttyUSB0', 'serial_number': 'abc'}]
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, detected), 'offline')

    def test_match_missing_is_error(self):
        proxy = _proxy(match={'serial_number': 'abc'}, connected=False)
        detected = [{'device': '/dev/ttyUSB0', 'serial_number': 'xyz'}]
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, detected), 'error')

    def test_no_specific_device_is_offline(self):
        # Proxy with neither port nor match — can't say it's missing.
        proxy = _proxy(port=None, match=None, connected=False)
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, []), 'offline')

    def test_a_device_that_exists_but_is_not_enumerated_is_offline(self):
        """Enumeration is not the only evidence a device is there.

        pyserial's comports() lists USB and built-in serial hardware; a
        pty, a socat pair or a CDC gadget it does not recognise never
        shows up, and those ports open and work perfectly well. Calling
        them missing paints the UI red and - now that the state gates
        the Connect button - would refuse a connection that works.
        """
        master, slave = os.openpty()
        try:
            proxy = _proxy(port=os.ttyname(slave), connected=False)
            self.assertEqual(
                self.wrapper._compute_port_state(proxy, []), 'offline')
        finally:
            os.close(master)
            os.close(slave)

    def test_a_device_that_is_really_gone_is_still_an_error(self):
        proxy = _proxy(port='/dev/ttyUSB-definitely-not-here',
            connected=False)
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, []), 'error')

    def test_a_match_that_finds_nothing_is_still_an_error(self):
        """A match can only ever be answered by enumeration"""
        proxy = _proxy(match={'serial_number': 'abc'}, connected=False)
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, []), 'error')


# ===========================================================================
# `state` field in /api/status payload
# ===========================================================================
class TestStateInPayload(unittest.TestCase):
    def test_state_present_in_each_port(self):
        p1 = _proxy(port='/dev/ttyUSB0', connected=True)
        p2 = _proxy(port='/dev/ttyMISSING', connected=False)
        wrapper = make_wrapper(serial_proxies=[p1, p2])
        wrapper._detect_cache = [{'device': '/dev/ttyUSB0'}]
        wrapper._detect_cache_at = float("inf")  # block re-enum
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        states = [p['state'] for p in client.responded['ports']]
        self.assertEqual(states, ['online', 'error'])


# ===========================================================================
# _find_port_by_filter
# ===========================================================================
class TestFindPortByFilter(unittest.TestCase):
    def setUp(self):
        self.ports = [
            {'name': 'rpi', 'servers': [
                {'protocol': 'TCP', 'port': 10001},
                {'protocol': 'WEBSOCKET', 'endpoint': 'rpi'}]},
            {'name': 'esp', 'servers': [
                {'protocol': 'WEBSOCKET', 'endpoint': 'esp32c6'}]},
        ]

    def test_match_by_name(self):
        port, idx = HttpServerWrapper._find_port_by_filter(
            self.ports, port_name='esp')
        self.assertEqual(idx, 1)
        self.assertEqual(port['name'], 'esp')

    def test_match_by_endpoint(self):
        port, idx = HttpServerWrapper._find_port_by_filter(
            self.ports, endpoint='esp32c6')
        self.assertEqual(idx, 1)

    def test_no_match(self):
        port, idx = HttpServerWrapper._find_port_by_filter(
            self.ports, port_name='nope')
        self.assertIsNone(port)
        self.assertIsNone(idx)

    def test_endpoint_only_matches_websocket(self):
        # TCP server with the same name as an endpoint shouldn't match
        ports = [{'name': 'a', 'servers': [
            {'protocol': 'TCP', 'port': 1234},
            {'protocol': 'WEBSOCKET', 'endpoint': 'b'}]}]
        port, _ = HttpServerWrapper._find_port_by_filter(
            ports, endpoint='1234')
        self.assertIsNone(port)


# ===========================================================================
# /api/status query-param routing (one-shot mode)
# ===========================================================================
class TestStatusFilters(unittest.TestCase):
    def _wrapper(self):
        proxy = _proxy(name='rpi', port='/dev/ttyUSB0',
            servers=[_server(protocol='WEBSOCKET', endpoint='rpi-ep')])
        wrapper = make_wrapper(serial_proxies=[proxy])
        wrapper._detect_cache = [{'device': '/dev/ttyUSB0'}]
        wrapper._detect_cache_at = float("inf")
        return wrapper

    def test_filter_by_port_name(self):
        wrapper = self._wrapper()
        client = MockClient(path='/api/status', query={'port': 'rpi'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertIn('port', client.responded)
        self.assertNotIn('ports', client.responded)
        self.assertEqual(client.responded['port']['name'], 'rpi')

    def test_filter_by_endpoint(self):
        wrapper = self._wrapper()
        client = MockClient(path='/api/status', query={'endpoint': 'rpi-ep'})
        wrapper._handle_request(client)
        self.assertEqual(client.responded['port']['name'], 'rpi')

    def test_filter_unknown_returns_404(self):
        wrapper = self._wrapper()
        client = MockClient(path='/api/status', query={'port': 'nope'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_no_filter_returns_all(self):
        wrapper = self._wrapper()
        client = MockClient(path='/api/status')
        wrapper._handle_request(client)
        self.assertIn('ports', client.responded)


# ===========================================================================
# /api/status?stream=1 — initial NDJSON snapshot + client registration
# ===========================================================================
class TestStreamSnapshot(unittest.TestCase):
    def _wrapper(self):
        proxy = _proxy(name='rpi', port='/dev/ttyUSB0',
            servers=[_server(protocol='WEBSOCKET', endpoint='rpi-ep')])
        w = make_wrapper(serial_proxies=[proxy])
        w._detect_cache = [{'device': '/dev/ttyUSB0'}]
        w._detect_cache_at = float("inf")
        return w

    def test_all_ports_stream_initial_snapshot(self):
        wrapper = self._wrapper()
        client = MockClient(path='/api/status', query={'stream': '1'})
        wrapper._handle_request(client)
        self.assertTrue(client.ndjson_started)
        self.assertEqual(len(client.ndjson_lines), 1)
        snap = client.ndjson_lines[0]
        self.assertIn('ports', snap)
        self.assertIn('detected', snap)
        self.assertIn('admin', snap)
        # Client got registered for future broadcasts.
        self.assertEqual(len(wrapper._stream_clients), 1)
        self.assertEqual(wrapper._stream_clients[0]['mode'], 'all')

    def test_filtered_stream_initial_snapshot_uses_port_key(self):
        wrapper = self._wrapper()
        client = MockClient(
            path='/api/status', query={'stream': '1', 'port': 'rpi'})
        wrapper._handle_request(client)
        self.assertEqual(len(client.ndjson_lines), 1)
        snap = client.ndjson_lines[0]
        self.assertIn('port', snap)
        self.assertEqual(snap['port']['name'], 'rpi')
        self.assertEqual(wrapper._stream_clients[0]['mode'], 'filter')
        self.assertEqual(wrapper._stream_clients[0]['port_name'], 'rpi')

    def test_filtered_stream_unknown_port_sends_null(self):
        # A filter that doesn't match any current port still opens the
        # stream — the entry can pick the port up if it appears later.
        wrapper = self._wrapper()
        client = MockClient(
            path='/api/status', query={'stream': '1', 'port': 'ghost'})
        wrapper._handle_request(client)
        self.assertEqual(client.ndjson_lines[0]['port'], None)
        self.assertEqual(len(wrapper._stream_clients), 1)


# ===========================================================================
# _broadcast_status — delta computation (the heart of the live UI)
# ===========================================================================
class TestBroadcastDeltas(unittest.TestCase):
    def _wrapper_with_client(self, mode='all', port_name=None,
            endpoint=None):
        proxy = _proxy(name='rpi', port='/dev/ttyUSB0',
            servers=[_server(protocol='WEBSOCKET', endpoint='rpi-ep')])
        wrapper = make_wrapper(serial_proxies=[proxy])
        wrapper._detect_cache = [{'device': '/dev/ttyUSB0'}]
        wrapper._detect_cache_at = float("inf")
        # Subscribe a client by going through the real handler so the
        # entry gets the same shape the broadcast loop expects.
        client = MockClient(path='/api/status',
            query={'stream': '1', **({'port': port_name} if port_name else {}),
                   **({'endpoint': endpoint} if endpoint else {})})
        wrapper._handle_request(client)
        client.ndjson_lines.clear()  # discard initial snapshot
        return wrapper, client, proxy

    def test_no_change_no_send(self):
        wrapper, client, _ = self._wrapper_with_client()
        wrapper._broadcast_status()
        self.assertEqual(client.ndjson_lines, [])

    def test_signal_change_emits_delta(self):
        wrapper, client, proxy = self._wrapper_with_client()
        # Flip the proxy state so payload differs from last snapshot.
        proxy.is_connected = True
        proxy.get_signals.return_value = 0  # all off
        wrapper._broadcast_status()
        # A per-port delta with the changed fields (state went online,
        # signals appeared, serial.connected=True).
        self.assertEqual(len(client.ndjson_lines), 1)
        delta = client.ndjson_lines[0]
        self.assertEqual(delta['port_index'], 0)
        self.assertTrue(delta['_delta'])

    def test_port_count_change_sends_full_snapshot(self):
        wrapper, client, _ = self._wrapper_with_client()
        # Add a second proxy → length differs → full snapshot.
        wrapper._serial_proxies.append(
            _proxy(name='extra', port='/dev/x'))
        wrapper._broadcast_status()
        self.assertEqual(len(client.ndjson_lines), 1)
        line = client.ndjson_lines[0]
        self.assertIn('ports', line)
        self.assertEqual(len(line['ports']), 2)

    def test_detected_change_emits_detected_line(self):
        wrapper, client, _ = self._wrapper_with_client()
        wrapper._detect_cache = [
            {'device': '/dev/ttyUSB0'},
            {'device': '/dev/ttyNEW'},
        ]
        wrapper._broadcast_status()
        self.assertEqual(len(client.ndjson_lines), 1)
        line = client.ndjson_lines[0]
        self.assertIn('detected', line)
        self.assertNotIn('ports', line)

    def test_filter_mode_emits_sparse_delta(self):
        wrapper, client, proxy = self._wrapper_with_client(
            mode='filter', port_name='rpi')
        proxy.is_connected = True
        proxy.get_signals.return_value = 0
        wrapper._broadcast_status()
        self.assertEqual(len(client.ndjson_lines), 1)
        delta = client.ndjson_lines[0]
        self.assertTrue(delta['_delta'])
        # Filter mode delta has no port_index — single-port view.
        self.assertNotIn('port_index', delta)
        self.assertNotIn('ports', delta)

    def test_filter_mode_port_disappears_emits_removed(self):
        wrapper, client, _ = self._wrapper_with_client(
            mode='filter', port_name='rpi')
        # Drop the proxy → next tick sees no match.
        wrapper._serial_proxies.clear()
        wrapper._broadcast_status()
        self.assertEqual(client.ndjson_lines, [{'_removed': True}])

    def test_filter_mode_port_reappears_sends_snapshot(self):
        wrapper, client, _ = self._wrapper_with_client(
            mode='filter', port_name='rpi')
        # Pretend the port was missing at subscribe time.
        wrapper._stream_clients[0]['last_port'] = None
        wrapper._broadcast_status()
        self.assertEqual(len(client.ndjson_lines), 1)
        line = client.ndjson_lines[0]
        self.assertIn('port', line)
        self.assertEqual(line['port']['name'], 'rpi')

    def test_dead_client_dropped(self):
        wrapper, client, proxy = self._wrapper_with_client()
        client.ndjson_alive = False  # simulate peer disconnect
        proxy.is_connected = True
        proxy.get_signals.return_value = 0
        wrapper._broadcast_status()
        # Entry removed from registry; nothing crashes.
        self.assertEqual(wrapper._stream_clients, [])

    def test_heartbeat_after_30s_silence(self):
        wrapper, client, _ = self._wrapper_with_client()
        # Backdate last_send to >30s ago.
        wrapper._stream_clients[0]['last_send'] -= 31
        wrapper._broadcast_status()
        self.assertEqual(client.ndjson_lines, [{}])


# ===========================================================================
# _build_detected_payload — caching + plug/unplug logging
# ===========================================================================
class TestDetectedPayload(unittest.TestCase):
    def _fake_port(self, **attrs):
        p = MagicMock()
        p.device = attrs.get('device', '/dev/x')
        p.description = attrs.get('description', 'desc')
        p.hwid = attrs.get('hwid', 'X')
        p.vid = attrs.get('vid')
        p.pid = attrs.get('pid')
        p.serial_number = attrs.get('serial_number')
        p.manufacturer = attrs.get('manufacturer')
        p.product = attrs.get('product')
        p.location = attrs.get('location')
        return p

    def test_cache_returns_same_within_ttl(self):
        wrapper = make_wrapper()
        with patch('ser2tcp.http_server._list_ports.comports',
                return_value=[self._fake_port(device='/dev/a')]):
            first = wrapper._build_detected_payload(force=True)
        # Bump cache_at so the next call hits cache instead of re-enum.
        with patch('ser2tcp.http_server._list_ports.comports') as m:
            second = wrapper._build_detected_payload()
            m.assert_not_called()
        self.assertEqual(first, second)

    def test_force_bypasses_cache(self):
        wrapper = make_wrapper()
        wrapper._detect_cache = [{'device': '/dev/cached'}]
        wrapper._detect_cache_at = float("inf")  # cache valid forever
        with patch('ser2tcp.http_server._list_ports.comports',
                return_value=[self._fake_port(device='/dev/fresh')]):
            fresh = wrapper._build_detected_payload(force=True)
        self.assertEqual(fresh[0]['device'], '/dev/fresh')

    def test_first_build_silent_no_log(self):
        # _detect_cache_at == 0.0 → first build, no plug/unplug log spam
        wrapper = make_wrapper()
        log = wrapper._log
        with patch('ser2tcp.http_server._list_ports.comports',
                return_value=[self._fake_port(device='/dev/a')]):
            wrapper._build_detected_payload(force=True)
        for call in log.info.call_args_list:
            self.assertNotIn('plugged', str(call))
            self.assertNotIn('unplugged', str(call))

    def test_plug_and_unplug_logged_on_diff(self):
        wrapper = make_wrapper()
        log = wrapper._log
        with patch('ser2tcp.http_server._list_ports.comports',
                return_value=[self._fake_port(device='/dev/a')]):
            wrapper._build_detected_payload(force=True)
        # second run: /dev/a unplugged, /dev/b plugged
        with patch('ser2tcp.http_server._list_ports.comports',
                return_value=[self._fake_port(device='/dev/b')]):
            wrapper._build_detected_payload(force=True)
        msgs = [call.args[0] % call.args[1:] for call in log.info.call_args_list
                if 'plugged' in str(call) or 'unplugged' in str(call)]
        joined = ' '.join(msgs)
        self.assertIn('plugged', joined)
        self.assertIn('/dev/b', joined)
        self.assertIn('unplugged', joined)
        self.assertIn('/dev/a', joined)


# ===========================================================================
# HTTP bind error handling — used to traceback on Address-already-in-use
# ===========================================================================
class TestHttpBindError(unittest.TestCase):
    def test_create_http_server_raises_value_error(self):
        wrapper = make_wrapper()
        with patch('ser2tcp.http_server._uhttp_server.HttpServer',
                side_effect=OSError(48, 'Address already in use')):
            with self.assertRaises(ValueError) as ctx:
                wrapper._create_http_server({
                    'address': '127.0.0.1', 'port': 20080,
                })
            self.assertIn('failed to bind', str(ctx.exception))
            self.assertIn('20080', str(ctx.exception))

    def test_init_keeps_the_place_of_a_failed_bind(self):
        # Two HTTP server configs — one fails to bind, the other
        # succeeds. Starting is not refused, and the failed one leaves a
        # placeholder behind: the runtime list is matched to the config
        # by position, so dropping it would point every entry after it
        # at the wrong socket.
        configs = [
            {'address': '127.0.0.1', 'port': 20080},
            {'address': '127.0.0.1', 'port': 20081},
        ]
        configuration = {'http': configs}
        good = MagicMock()
        with patch('ser2tcp.http_server._uhttp_server.HttpServer',
                side_effect=[OSError(48, 'EADDRINUSE'), good]):
            wrapper = HttpServerWrapper(
                configs, [], log=Mock(), configuration=configuration)
        self.assertEqual(len(wrapper._servers), 2)
        self.assertIsInstance(wrapper._servers[0][0], FailedHttpServer)
        self.assertIs(wrapper._servers[1][0], good)


if __name__ == '__main__':
    unittest.main()


class TestApiCertsReloadAndUpload(unittest.TestCase):
    """Reload gating and the multi-file upload form.

    The routing and the actual cert swap are covered end-to-end in
    tests/integration/ — these cover the guards a MockClient can reach.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmp, 'config.json')
        self.wrapper = make_wrapper(
            auth_config=self._auth_config(), config_path=self.config_path)
        self.mgr = CertManager(self.tmp)
        self.cert, self.key = generate_certificate('a', key_type='ec_p256')
        self.other_cert, self.other_key = generate_certificate(
            'b', key_type='ec_p256')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _auth_config(self):
        return {
            'users': [
                {'login': 'admin', 'password': hash_password('secret'),
                    'admin': True},
                {'login': 'viewer', 'password': hash_password('secret'),
                    'admin': False},
            ],
        }

    def _token(self, login):
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': login, 'password': 'secret'})
        self.wrapper._handle_request(client)
        return client.responded['token']

    def _call(self, login, method, path, data=None):
        client = MockClient(
            method=method, path=path, data=data,
            headers={'authorization': f'Bearer {self._token(login)}'})
        self.wrapper._handle_request(client)
        return client

    def test_reload_requires_admin(self):
        self.mgr.save_files(
            'web', [('cert.pem', self.cert), ('key.pem', self.key)])
        client = self._call('viewer', 'POST', '/api/certs/web/reload')
        self.assertEqual(client.respond_status, 403)

    def test_reload_unknown_bundle_is_404(self):
        client = self._call('admin', 'POST', '/api/certs/nope/reload')
        self.assertEqual(client.respond_status, 404)

    def test_reload_unused_bundle_reports_nothing(self):
        self.mgr.save_files(
            'web', [('cert.pem', self.cert), ('key.pem', self.key)])
        client = self._call('admin', 'POST', '/api/certs/web/reload')
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(client.responded['reloaded'], [])

    def test_reload_rejects_get(self):
        client = self._call('admin', 'GET', '/api/certs/web/reload')
        self.assertEqual(client.respond_status, 405)

    def test_upload_single_file(self):
        client = self._call(
            'admin', 'POST', '/api/certs/web/files',
            {'filename': 'cert.pem', 'content': self.cert})
        self.assertEqual(client.respond_status, 200)
        self.assertTrue(
            self.mgr.get_bundle('web')['files']['cert.pem']['present'])

    def test_upload_file_set(self):
        client = self._call(
            'admin', 'POST', '/api/certs/web/files',
            {'files': [
                {'filename': 'cert.pem', 'content': self.cert},
                {'filename': 'key.pem', 'content': self.key},
            ]})
        self.assertEqual(client.respond_status, 200)
        self.assertTrue(self.mgr.get_bundle('web')['key_match'])

    def test_upload_mismatched_set_rejected(self):
        client = self._call(
            'admin', 'POST', '/api/certs/web/files',
            {'files': [
                {'filename': 'cert.pem', 'content': self.cert},
                {'filename': 'key.pem', 'content': self.other_key},
            ]})
        self.assertEqual(client.respond_status, 400)
        self.assertIn('does not match', client.responded['error'])

    def test_upload_entry_without_content_rejected(self):
        client = self._call(
            'admin', 'POST', '/api/certs/web/files',
            {'files': [{'filename': 'cert.pem'}]})
        self.assertEqual(client.respond_status, 400)

    def test_upload_empty_set_rejected(self):
        client = self._call(
            'admin', 'POST', '/api/certs/web/files', {'files': []})
        self.assertEqual(client.respond_status, 400)

    def test_upload_without_filename_rejected(self):
        client = self._call(
            'admin', 'POST', '/api/certs/web/files', {'content': self.cert})
        self.assertEqual(client.respond_status, 400)


class TestApiDisconnectWebSocket(unittest.TestCase):
    """Disconnecting a WebSocket client goes through a different object.

    A WEBSOCKET server holds uhttp connections, which have none of the
    Connection API the TCP path uses. The handler used to reach for
    address_str() and take the whole process down with it.
    """

    def _make_ws_proxy(self):
        serial = Mock()
        serial.can_add_connection.return_value = True
        serial.connect.return_value = True
        ws_server = ServerWebSocket(
            {'protocol': 'websocket', 'endpoint': 'dev'}, serial, log=Mock())
        ws_client = Mock()
        ws_client.addr = ('192.168.1.9', 4444)
        ws_server.add_connection(ws_client)
        proxy = Mock()
        proxy.id = 'wsport'
        proxy.error = None
        proxy.serial_config = {'port': '/dev/ttyUSB0'}
        proxy.match = None
        proxy.name = 'dev'
        proxy.is_connected = False
        proxy.servers = [ws_server]
        return proxy, ws_server, ws_client

    def _wrapper_for(self, proxy):
        configuration = {
            'http': [{'address': '127.0.0.1', 'port': 0}],
            'ports': [{'id': 'wsport', 'serial': {'port': '/dev/ttyUSB0'},
                       'servers': [{'protocol': 'websocket',
                                    'endpoint': 'dev'}]}],
        }
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            return HttpServerWrapper(
                {'address': '127.0.0.1', 'port': 0}, [proxy],
                log=Mock(), configuration=configuration)

    def test_disconnect_websocket_client(self):
        """The client is closed and dropped, and the API answers 200"""
        proxy, ws_server, ws_client = self._make_ws_proxy()
        wrapper = self._wrapper_for(proxy)
        client = MockClient(
            method='DELETE',
            path='/api/ports/wsport/connections/' + connection_id(ws_client))
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(ws_server.connections, [])
        ws_client.ws_close.assert_called_once()

    def test_disconnect_websocket_client_out_of_range(self):
        """An index past the end is still a 404, not a crash"""
        proxy, _, _ = self._make_ws_proxy()
        wrapper = self._wrapper_for(proxy)
        client = MockClient(
            method='DELETE', path='/api/ports/wsport/connections/nosuch')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 404)

    def test_disconnect_websocket_survives_a_dead_socket(self):
        """A client whose socket already went away is still reaped"""
        proxy, ws_server, ws_client = self._make_ws_proxy()
        ws_client.ws_close.side_effect = OSError('gone')
        wrapper = self._wrapper_for(proxy)
        client = MockClient(
            method='DELETE',
            path='/api/ports/wsport/connections/' + connection_id(ws_client))
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(ws_server.connections, [])


class TestPortConfigTypeValidation(unittest.TestCase):
    """JSON of the wrong shape must come back as 400, never as a crash.

    Everything here reached code that assumed a string or an int -
    .upper(), a dict key, socket.bind() - and an uncaught TypeError in
    an API handler used to kill every serial port in the process.
    """

    def _admin_token(self, wrapper):
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(client)
        return client.responded['token']

    def _make_wrapper(self):
        configuration = {
            'http': [{'address': '127.0.0.1', 'port': 0}],
            'users': [{
                'login': 'admin',
                'password': hash_password('secret'),
                'admin': True,
            }],
            'ports': [],
        }
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            return HttpServerWrapper(
                {'address': '127.0.0.1', 'port': 0}, [],
                log=Mock(), configuration=configuration,
                server_manager=Mock())

    def _post(self, servers, serial=None):
        """POST a port config and return the responding MockClient"""
        wrapper = self._make_wrapper()
        token = self._admin_token(wrapper)
        cfg = {
            'serial': serial if serial else {'port': '/dev/ttyUSB0'},
            'servers': servers,
        }
        client = MockClient(
            method='POST', path='/api/ports', data=cfg,
            headers={'authorization': f'Bearer {token}'})
        wrapper._handle_request(client)
        return client

    def _assert_rejected(self, servers, serial=None):
        client = self._post(servers, serial=serial)
        self.assertEqual(client.respond_status, 400)
        return client.responded['error']

    def test_protocol_must_be_a_string(self):
        self._assert_rejected([{'protocol': 5}])

    def test_protocol_may_not_be_a_list(self):
        self._assert_rejected([{'protocol': ['tcp']}])

    def test_server_address_must_be_a_string(self):
        self._assert_rejected(
            [{'protocol': 'tcp', 'address': 1234, 'port': 10001}])

    def test_socket_path_must_be_a_string(self):
        self._assert_rejected([{'protocol': 'socket', 'address': 7}])

    def test_server_port_must_be_an_integer(self):
        self._assert_rejected(
            [{'protocol': 'tcp', 'address': '0.0.0.0', 'port': 'abc'}])

    def test_server_port_must_be_in_range(self):
        self._assert_rejected(
            [{'protocol': 'tcp', 'address': '0.0.0.0', 'port': 99999}])

    def test_server_port_may_not_be_a_bool(self):
        self._assert_rejected(
            [{'protocol': 'tcp', 'address': '0.0.0.0', 'port': True}])

    def test_websocket_endpoint_must_be_a_string(self):
        self._assert_rejected([{'protocol': 'websocket', 'endpoint': ['a']}])

    def test_control_signal_entries_must_be_strings(self):
        self._assert_rejected([{
            'protocol': 'tcp', 'address': '0.0.0.0', 'port': 10001,
            'control': {'signals': [7]}}])

    def test_port_name_must_be_a_string(self):
        wrapper = self._make_wrapper()
        token = self._admin_token(wrapper)
        client = MockClient(
            method='POST', path='/api/ports',
            data={
                'name': ['dev'],
                'serial': {'port': '/dev/ttyUSB0'},
                'servers': [{
                    'protocol': 'tcp', 'address': '0.0.0.0', 'port': 10001}],
            },
            headers={'authorization': f'Bearer {token}'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_serial_config_rejects_a_non_string_port(self):
        self._assert_rejected(
            [{'protocol': 'tcp', 'address': '0.0.0.0', 'port': 10001}],
            serial={'port': 42})

    def test_valid_config_is_still_accepted(self):
        """The guards must not reject anything that used to work"""
        wrapper = self._make_wrapper()
        token = self._admin_token(wrapper)
        cfg = {
            'name': 'dev',
            'serial': {'port': '/dev/ttyUSB0', 'baudrate': 115200},
            'servers': [{
                'protocol': 'tcp', 'address': '0.0.0.0', 'port': 10001,
                'control': {'signals': ['rts', 'dtr']}}],
        }
        with patch.object(wrapper, '_create_proxy', return_value=Mock()):
            client = MockClient(
                method='POST', path='/api/ports', data=cfg,
                headers={'authorization': f'Bearer {token}'})
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)


class TestHttpConfigTypeValidation(unittest.TestCase):
    """Same class of bug on the HTTP server endpoints"""

    def _make_wrapper(self):
        configuration = {
            'http': [{'address': '127.0.0.1', 'port': 0}],
            'users': [{
                'login': 'admin',
                'password': hash_password('secret'),
                'admin': True,
            }],
        }
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            return HttpServerWrapper(
                {'address': '127.0.0.1', 'port': 0}, [],
                log=Mock(), configuration=configuration,
                server_manager=Mock())

    def _post(self, data):
        wrapper = self._make_wrapper()
        login = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(login)
        token = login.responded['token']
        client = MockClient(
            method='POST', path='/api/settings/http', data=data,
            headers={'authorization': f'Bearer {token}'})
        wrapper._handle_request(client)
        return client

    def test_address_must_be_a_string(self):
        client = self._post({'address': 80, 'port': 8080})
        self.assertEqual(client.respond_status, 400)

    def test_name_must_be_a_string(self):
        client = self._post({'address': '0.0.0.0', 'port': 8080,
            'name': ['main']})
        self.assertEqual(client.respond_status, 400)


class TestAuthFieldTypeValidation(unittest.TestCase):
    """Users and tokens take the same treatment as port configs.

    A login is a dict key, a password is concatenated with a salt, and
    a session timeout is added to time.time() - none of which survives
    the wrong type, and all of which come straight from a request body.
    """

    def _make_wrapper(self):
        configuration = {
            'http': [{'address': '127.0.0.1', 'port': 0}],
            'users': [{
                'login': 'admin',
                'password': hash_password('secret'),
                'admin': True,
            }],
            'tokens': [{'token': 'key', 'name': 'monitoring'}],
        }
        with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
            return HttpServerWrapper(
                {'address': '127.0.0.1', 'port': 0}, [],
                log=Mock(), configuration=configuration,
                server_manager=Mock())

    def _token(self, wrapper):
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(client)
        return client.responded['token']

    def _call(self, method, path, data, wrapper=None):
        wrapper = wrapper or self._make_wrapper()
        client = MockClient(
            method=method, path=path, data=data,
            headers={'authorization': f'Bearer {self._token(wrapper)}'})
        wrapper._handle_request(client)
        return client

    def _assert_400(self, method, path, data):
        client = self._call(method, path, data)
        self.assertEqual(client.respond_status, 400, client.responded)

    # --- users ---

    def test_add_user_password_must_be_a_string(self):
        self._assert_400('POST', '/api/users', {'login': 'x', 'password': 42})

    def test_add_user_login_must_be_a_string(self):
        self._assert_400('POST', '/api/users', {'login': ['x'],
            'password': 'y'})

    def test_add_user_login_must_not_be_empty(self):
        self._assert_400('POST', '/api/users', {'login': '', 'password': 'y'})

    def test_add_user_session_timeout_must_be_a_number(self):
        self._assert_400('POST', '/api/users', {
            'login': 'x', 'password': 'y', 'session_timeout': 'soon'})

    def test_add_user_session_timeout_must_not_be_negative(self):
        self._assert_400('POST', '/api/users', {
            'login': 'x', 'password': 'y', 'session_timeout': -1})

    def test_update_user_password_must_be_a_string(self):
        self._assert_400('PUT', '/api/users/admin', {'password': 42})

    def test_update_user_session_timeout_must_be_a_number(self):
        self._assert_400('PUT', '/api/users/admin',
            {'session_timeout': [60]})

    def test_update_user_session_timeout_may_be_null(self):
        """null clears the override rather than breaking the session"""
        wrapper = self._make_wrapper()
        client = self._call('PUT', '/api/users/admin',
            {'session_timeout': None}, wrapper=wrapper)
        self.assertEqual(client.respond_status, 200)
        login = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 'secret'})
        wrapper._handle_request(login)
        self.assertEqual(login.respond_status, 200)

    def test_adding_a_user_with_a_timeout_still_works(self):
        client = self._call('POST', '/api/users', {
            'login': 'x', 'password': 'y', 'session_timeout': 60})
        self.assertEqual(client.respond_status, 201)

    # --- tokens ---

    def test_add_token_must_be_a_string(self):
        self._assert_400('POST', '/api/tokens', {'token': ['k'],
            'name': 'n'})

    def test_add_token_must_not_be_empty(self):
        self._assert_400('POST', '/api/tokens', {'token': '', 'name': 'n'})

    def test_add_token_name_must_be_a_string(self):
        self._assert_400('POST', '/api/tokens', {'token': 'k', 'name': 7})

    def test_update_token_must_be_a_string(self):
        self._assert_400('PUT', '/api/tokens/key', {'token': ['new']})

    def test_update_token_name_must_be_a_string(self):
        self._assert_400('PUT', '/api/tokens/key', {'name': 7})

    def test_adding_a_valid_token_still_works(self):
        client = self._call('POST', '/api/tokens', {'token': 'k2',
            'name': 'n'})
        self.assertEqual(client.respond_status, 201)

    # --- login ---

    def test_login_with_a_non_string_password_is_refused(self):
        wrapper = self._make_wrapper()
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': 'admin', 'password': 42})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 401)

    def test_login_with_a_non_string_login_is_refused(self):
        wrapper = self._make_wrapper()
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': ['admin'], 'password': 'secret'})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 401)


class TestFailedPortInStatus(unittest.TestCase):
    """A port that never started still has to be reported"""

    def setUp(self):
        self.wrapper = make_wrapper()

    def test_it_is_marked_as_an_error(self):
        proxy = _proxy(port='/dev/ttyUSB0', error='failed to bind')
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, []), 'error')

    def test_the_reason_reaches_the_payload(self):
        proxy = _proxy(port='/dev/ttyUSB0', name='dev',
            error='failed to bind')
        wrapper = make_wrapper(serial_proxies=[proxy])
        payload = wrapper._build_ports_payload(detected=[])
        self.assertEqual(payload[0]['error'], 'failed to bind')

    def test_a_started_port_carries_no_reason(self):
        proxy = _proxy(port='/dev/ttyUSB0', name='dev', connected=True)
        wrapper = make_wrapper(serial_proxies=[proxy])
        payload = wrapper._build_ports_payload(detected=[])
        self.assertNotIn('error', payload[0])

    def test_a_present_device_does_not_excuse_the_failure(self):
        """The device being there says nothing about the port starting"""
        proxy = _proxy(port='/dev/ttyUSB0', error='failed to bind')
        detected = [{'device': '/dev/ttyUSB0'}]
        self.assertEqual(
            self.wrapper._compute_port_state(proxy, detected), 'error')


class TestARevokedAdminIsRevokedAtOnce(unittest.TestCase):
    """The API side of a permission change to a signed-in account.

    Demoting somebody used to leave their open session holding the
    admin flag it copied at login - and since every request pushed the
    expiry out, they kept it until they chose to sign out.
    """

    def setUp(self):
        self.wrapper = make_wrapper(auth_config={'users': [
            {'login': 'ann', 'password': hash_password('secret'),
             'admin': True},
            {'login': 'bob', 'password': hash_password('secret'),
             'admin': True},
        ]})
        self.token = self._login('ann')

    def _login(self, login):
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': login, 'password': 'secret'})
        self.wrapper._handle_request(client)
        return client.responded['token']

    def _as(self, token, method='GET', path='/api/status', data=None):
        client = MockClient(
            method=method, path=path, data=data,
            headers={'authorization': f'Bearer {token}'})
        self.wrapper._handle_request(client)
        return client

    def _update(self, login, **fields):
        return self._as(
            self._login('bob'), 'PUT', f'/api/users/{login}', fields)

    def test_admin_only_work_is_refused_after_the_demotion(self):
        self.assertEqual(self._as(self.token, path='/api/users').
                         respond_status, 200)
        self._update('ann', admin=False)
        self.assertEqual(self._as(self.token, path='/api/users').
                         respond_status, 403)

    def test_they_can_still_read_what_any_user_may(self):
        self._update('ann', admin=False)
        self.assertEqual(self._as(self.token).respond_status, 200)

    def test_a_new_password_signs_them_out(self):
        self._update('ann', password='different')
        self.assertEqual(self._as(self.token).respond_status, 401)

    def test_deleting_them_signs_them_out(self):
        self._as(self._login('bob'), 'DELETE', '/api/users/ann')
        self.assertEqual(self._as(self.token).respond_status, 401)


class TestAnOpenStreamFollowsTheAccount(unittest.TestCase):
    """The status stream is a connection, not a request.

    It is opened once and fed for hours, so the admin flag it was
    handed at subscribe time is another copy that outlives the account
    it describes - and a stream opened before a password change went on
    delivering live port status to whoever was holding it.
    """

    def setUp(self):
        self.wrapper = make_wrapper(auth_config={'users': [
            {'login': 'ann', 'password': hash_password('secret'),
             'admin': True},
            {'login': 'bob', 'password': hash_password('secret'),
             'admin': True},
        ]})
        self.wrapper._detect_cache = []
        self.wrapper._detect_cache_at = float('inf')
        self.token = self._login('ann')
        self.client = self._subscribe(self.token)

    def _login(self, login):
        client = MockClient(
            method='POST', path='/api/login',
            data={'login': login, 'password': 'secret'})
        self.wrapper._handle_request(client)
        return client.responded['token']

    def _subscribe(self, token):
        client = MockClient(
            path='/api/status', query={'stream': '1'},
            headers={'authorization': f'Bearer {token}'})
        self.wrapper._handle_request(client)
        client.ndjson_lines.clear()
        return client

    def _update(self, login, **fields):
        client = MockClient(
            method='PUT', path=f'/api/users/{login}', data=fields,
            headers={'authorization': f'Bearer {self._login("bob")}'})
        self.wrapper._handle_request(client)
        return client

    def test_it_starts_out_saying_admin(self):
        self.assertTrue(self.wrapper._stream_clients[0]['admin'])

    def test_a_demotion_reaches_the_open_stream(self):
        self._update('ann', admin=False)
        self.wrapper._broadcast_status()
        snapshot = [l for l in self.client.ndjson_lines if 'ports' in l]
        self.assertTrue(snapshot, self.client.ndjson_lines)
        self.assertFalse(snapshot[-1]['admin'])

    def test_a_promotion_reaches_it_too(self):
        self._update('ann', admin=False)
        self.wrapper._broadcast_status()
        self.client.ndjson_lines.clear()
        self._update('ann', admin=True)
        self.wrapper._broadcast_status()
        snapshot = [l for l in self.client.ndjson_lines if 'ports' in l]
        self.assertTrue(snapshot[-1]['admin'])

    def test_nothing_is_resent_while_nothing_changes(self):
        self.wrapper._broadcast_status()
        self.assertEqual(self.client.ndjson_lines, [])

    def test_a_password_change_closes_the_stream(self):
        self._update('ann', password='different')
        self.wrapper._broadcast_status()
        self.assertEqual(self.wrapper._stream_clients, [])

    def test_a_closed_stream_stops_being_fed(self):
        self._update('ann', password='different')
        self.wrapper._broadcast_status()
        self.wrapper._broadcast_status()
        self.assertFalse(self.client.ndjson_alive)

    def test_deleting_the_user_closes_the_stream(self):
        client = MockClient(
            method='DELETE', path='/api/users/ann',
            headers={'authorization': f'Bearer {self._login("bob")}'})
        self.wrapper._handle_request(client)
        self.wrapper._broadcast_status()
        self.assertEqual(self.wrapper._stream_clients, [])

    def test_somebody_elses_stream_is_left_alone(self):
        theirs = self._subscribe(self._login('bob'))
        self._update('ann', password='different')
        self.wrapper._broadcast_status()
        self.assertTrue(theirs.ndjson_alive)
        self.assertEqual(len(self.wrapper._stream_clients), 1)

    def test_without_auth_configured_streams_are_left_alone(self):
        wrapper = make_wrapper()
        wrapper._detect_cache = []
        wrapper._detect_cache_at = float('inf')
        client = MockClient(path='/api/status', query={'stream': '1'})
        wrapper._handle_request(client)
        wrapper._broadcast_status()
        self.assertEqual(len(wrapper._stream_clients), 1)
