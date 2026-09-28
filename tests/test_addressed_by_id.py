"""Nothing the API hands out names a port by its position.

Ports are addressed by id on the way in - every route resolves one at
the edge - but a few things on the way out still said "the port at
index 2": the status stream's deltas, a bundle's used_by, and the
signals list, which carried an optional name and nothing else. A
position means something only until the list changes, and changing
the order of the list is the next thing coming.
"""

import unittest
from unittest.mock import Mock

from ser2tcp.serial_proxy import port_info
from tests.test_http_server import MockClient, make_wrapper, _proxy


def _named(port_id, name=None):
    proxy = _proxy(name=name or port_id, port='/dev/' + port_id)
    proxy.id = port_id
    return proxy


class TestTheStatusStream(unittest.TestCase):

    def _subscribe(self, proxies):
        wrapper = make_wrapper(serial_proxies=proxies)
        wrapper._detect_cache = []
        wrapper._detect_cache_at = float('inf')
        client = MockClient(path='/api/status', query={'stream': '1'})
        wrapper._handle_request(client)
        client.ndjson_lines.clear()
        return wrapper, client

    def test_a_delta_names_its_port_by_id(self):
        first, second = _named('first'), _named('second')
        wrapper, client = self._subscribe([first, second])
        second.is_connected = True
        wrapper._broadcast_status()
        self.assertEqual(len(client.ndjson_lines), 1)
        delta = client.ndjson_lines[0]
        self.assertTrue(delta['_delta'])
        self.assertEqual(delta['id'], 'second')
        self.assertNotIn('port_index', delta)

    def test_a_new_order_is_a_full_snapshot(self):
        """Same ports, same count, different order.

        Deltas by position would say that slot 0 had turned into another
        port. The client would still end up right, by accident - but a
        delta is for a port that changed, and none of these did.
        """
        first, second = _named('first'), _named('second')
        wrapper, client = self._subscribe([first, second])
        wrapper._serial_proxies.reverse()
        wrapper._broadcast_status()
        self.assertEqual(len(client.ndjson_lines), 1)
        line = client.ndjson_lines[0]
        self.assertIn('ports', line)
        self.assertEqual(
            [p['id'] for p in line['ports']], ['second', 'first'])

    def test_a_port_whose_id_changed_is_a_full_snapshot(self):
        """Renaming a port moves its id, so the old one names nothing"""
        port = _named('old')
        wrapper, client = self._subscribe([port])
        port.id = 'new'
        wrapper._broadcast_status()
        line = client.ndjson_lines[0]
        self.assertIn('ports', line)
        self.assertEqual(line['ports'][0]['id'], 'new')

    def test_a_port_without_an_id_is_a_full_snapshot(self):
        """A delta has to say which port it is about, or not be sent"""
        port = _named('x')
        port.id = None
        wrapper, client = self._subscribe([port])
        port.is_connected = True
        wrapper._broadcast_status()
        self.assertIn('ports', client.ndjson_lines[0])


class TestBundleUsage(unittest.TestCase):

    def setUp(self):
        self.wrapper = make_wrapper()
        self.wrapper._configuration['ports'] = [
            {'id': 'plain', 'name': 'plain', 'servers': [
                {'protocol': 'tcp', 'address': '0.0.0.0', 'port': 1}]},
            {'id': 'secure', 'name': 'secure', 'servers': [
                {'protocol': 'tcp', 'address': '0.0.0.0', 'port': 2},
                {'protocol': 'tls', 'address': '0.0.0.0', 'port': 3,
                 'tls': {'bundle': 'main'}}]},
        ]
        self.wrapper._configuration['http'] = [
            {'id': 'web', 'address': '0.0.0.0', 'port': 4},
            {'id': 'tls-web', 'address': '0.0.0.0', 'port': 5,
             'tls': {'bundle': 'main'}},
        ]
        self.usage = self.wrapper._find_bundle_usage('main')

    def test_a_port_server_is_named_by_its_port_id(self):
        entry = next(u for u in self.usage if u['type'] == 'port')
        self.assertEqual(entry['port_id'], 'secure')
        self.assertEqual(entry['server_port'], 3)
        self.assertNotIn('port_index', entry)
        # The server within the port has no id of its own; its protocol
        # address and port already say which one it is.
        self.assertNotIn('server_index', entry)

    def test_an_http_server_is_named_by_its_id(self):
        entry = next(u for u in self.usage if u['type'] == 'http')
        self.assertEqual(entry['http_id'], 'tls-web')
        self.assertNotIn('index', entry)


class TestTheSignalsList(unittest.TestCase):

    def test_each_entry_carries_its_port_id(self):
        """The name is optional; without an id an unnamed port could
        only be told apart by where it sat in the list"""
        unnamed = _named('p1', name='')
        unnamed.name = None
        wrapper = make_wrapper(serial_proxies=[unnamed])
        client = MockClient(path='/api/signals')
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(client.responded[0]['id'], 'p1')


class TestTheWebSocketPortTopic(unittest.TestCase):

    def test_it_carries_the_port_id(self):
        proxy = Mock()
        proxy.id = 'esp32'
        proxy.name = 'ESP32'
        proxy.serial_config = {'port': '/dev/ttyUSB0', 'baudrate': 115200}
        proxy.state = 'online'
        self.assertEqual(port_info(proxy)['id'], 'esp32')


if __name__ == '__main__':
    unittest.main()
