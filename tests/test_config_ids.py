"""Tests for the stable identifiers ports and HTTP servers carry.

Addressing either by its position in the config meant an entry that
failed to start, or one removed by somebody else, renumbered the rest:
an edit aimed at one rewrote another. An id is assigned once, written
back to the config, and never changes.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from ser2tcp.http_server import HttpServerWrapper
from tests.test_http_server import MockClient as _MockClient


def _wrapper_for(configuration):
    """A wrapper with no auth, so requests go straight through"""
    return _wrapper(configuration)


def _wrapper(configuration, config_path=None):
    with patch('ser2tcp.http_server._uhttp_server.HttpServer'):
        return HttpServerWrapper(
            configuration.get('http', []), [], log=Mock(),
            config_path=config_path, configuration=configuration)


class TestIdsAreAssigned(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'config.json')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, configuration):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(configuration, f)
        return configuration

    def test_a_port_without_one_gets_an_id(self):
        cfg = self._write({
            'ports': [{'name': 'dev', 'serial': {'port': '/dev/x'},
                       'servers': []}],
            'http': [{'address': '127.0.0.1', 'port': 0}]})
        _wrapper(cfg, self.path)
        self.assertTrue(cfg['ports'][0]['id'])

    def test_an_http_server_without_one_gets_an_id(self):
        cfg = self._write({
            'ports': [], 'http': [{'address': '127.0.0.1', 'port': 0}]})
        _wrapper(cfg, self.path)
        self.assertTrue(cfg['http'][0]['id'])

    def test_ids_are_distinct(self):
        cfg = self._write({
            'ports': [{'name': f'p{i}', 'serial': {'port': '/dev/x'},
                       'servers': []} for i in range(5)],
            'http': [{'address': '127.0.0.1', 'port': 0}]})
        _wrapper(cfg, self.path)
        ids = [p['id'] for p in cfg['ports']]
        self.assertEqual(len(set(ids)), len(ids))

    def test_an_existing_id_is_left_alone(self):
        cfg = self._write({
            'ports': [{'id': 'keepme', 'name': 'dev',
                       'serial': {'port': '/dev/x'}, 'servers': []}],
            'http': [{'address': '127.0.0.1', 'port': 0}]})
        _wrapper(cfg, self.path)
        self.assertEqual(cfg['ports'][0]['id'], 'keepme')

    def test_assigned_ids_are_written_back(self):
        """Otherwise every restart hands out new ones"""
        self._write({
            'ports': [{'name': 'dev', 'serial': {'port': '/dev/x'},
                       'servers': []}],
            'http': [{'address': '127.0.0.1', 'port': 0}]})
        with open(self.path, encoding='utf-8') as f:
            cfg = json.load(f)
        _wrapper(cfg, self.path)
        with open(self.path, encoding='utf-8') as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk['ports'][0]['id'], cfg['ports'][0]['id'])

    def test_a_config_that_needs_nothing_is_not_rewritten(self):
        self._write({
            'ports': [{'id': 'a', 'name': 'dev',
                       'serial': {'port': '/dev/x'}, 'servers': []}],
            'http': [{'id': 'b', 'address': '127.0.0.1', 'port': 0}]})
        with open(self.path, encoding='utf-8') as f:
            cfg = json.load(f)
        before = os.stat(self.path).st_mtime_ns
        _wrapper(cfg, self.path)
        self.assertEqual(os.stat(self.path).st_mtime_ns, before)

    def test_a_duplicate_id_in_the_file_is_replaced(self):
        """Two entries answering to one id is worse than renumbering"""
        cfg = self._write({
            'ports': [
                {'id': 'same', 'name': 'a', 'serial': {'port': '/dev/x'},
                 'servers': []},
                {'id': 'same', 'name': 'b', 'serial': {'port': '/dev/y'},
                 'servers': []},
            ],
            'http': [{'address': '127.0.0.1', 'port': 0}]})
        _wrapper(cfg, self.path)
        self.assertNotEqual(cfg['ports'][0]['id'], cfg['ports'][1]['id'])


class TestLookupById(unittest.TestCase):

    def _wrapper_with_ports(self):
        cfg = {
            'ports': [
                {'id': 'aaa', 'name': 'first', 'serial': {'port': '/dev/a'},
                 'servers': []},
                {'id': 'bbb', 'name': 'second', 'serial': {'port': '/dev/b'},
                 'servers': []},
            ],
            'http': [{'id': 'hhh', 'address': '127.0.0.1', 'port': 0}],
        }
        return _wrapper(cfg), cfg

    def test_a_known_port_id_resolves_to_its_position(self):
        wrapper, _ = self._wrapper_with_ports()
        self.assertEqual(wrapper._port_index('bbb'), 1)

    def test_an_unknown_port_id_resolves_to_nothing(self):
        wrapper, _ = self._wrapper_with_ports()
        self.assertIsNone(wrapper._port_index('nope'))

    def test_a_known_http_id_resolves_to_its_position(self):
        wrapper, _ = self._wrapper_with_ports()
        self.assertEqual(wrapper._http_index('hhh'), 0)

    def test_an_unknown_http_id_resolves_to_nothing(self):
        wrapper, _ = self._wrapper_with_ports()
        self.assertIsNone(wrapper._http_index('nope'))


if __name__ == '__main__':
    unittest.main()


class TestIdValidation(unittest.TestCase):
    """What may be written in the id field.

    It ends up in a URL and in a filename-shaped config key, so it is
    kept to the characters that survive both without escaping.
    """

    def test_plain_names_are_accepted(self):
        from ser2tcp.config_ids import is_valid_id
        for value in ('esp32-bench', 'a', 'Port_1', 'dev.01', '9f3c1a20'):
            self.assertTrue(is_valid_id(value), value)

    def test_spaces_and_non_ascii_are_refused(self):
        from ser2tcp.config_ids import is_valid_id
        for value in ('esp32 bench', 'druhý', 'a/b', 'a?b', '', '   ',
                      'a#b', None, 42, ['a']):
            self.assertFalse(is_valid_id(value), repr(value))

    def test_an_over_long_one_is_refused(self):
        from ser2tcp.config_ids import is_valid_id
        self.assertFalse(is_valid_id('x' * 65))


class TestChoosingAnId(unittest.TestCase):
    """An id may be given rather than generated"""

    def _wrapper(self, ports=None, http=None):
        cfg = {
            'ports': ports if ports is not None else [],
            'http': http if http is not None else [
                {'id': 'httpone', 'address': '127.0.0.1', 'port': 0}],
        }
        return _wrapper_for(cfg), cfg

    def _admin(self, wrapper):
        return None

    def test_a_given_id_is_kept_on_add(self):
        wrapper, cfg = self._wrapper()
        client = _MockClient(
            method='POST', path='/api/ports',
            data={'id': 'my-device', 'serial': {'port': '/dev/ttyUSB0'},
                  'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                               'port': 10001}]})
        with patch.object(wrapper, '_create_proxy', return_value=Mock()):
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 201)
        self.assertEqual(client.responded['id'], 'my-device')
        self.assertEqual(cfg['ports'][0]['id'], 'my-device')

    def test_a_malformed_id_is_refused(self):
        wrapper, _ = self._wrapper()
        client = _MockClient(
            method='POST', path='/api/ports',
            data={'id': 'has spaces', 'serial': {'port': '/dev/ttyUSB0'},
                  'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                               'port': 10001}]})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_an_id_already_taken_is_refused(self):
        wrapper, _ = self._wrapper(ports=[
            {'id': 'taken', 'serial': {'port': '/dev/a'}, 'servers': []}])
        client = _MockClient(
            method='POST', path='/api/ports',
            data={'id': 'taken', 'serial': {'port': '/dev/ttyUSB0'},
                  'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                               'port': 10001}]})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_an_id_taken_by_an_http_server_is_refused(self):
        """One namespace: a URL says which kind, the id must not clash"""
        wrapper, _ = self._wrapper()
        client = _MockClient(
            method='POST', path='/api/ports',
            data={'id': 'httpone', 'serial': {'port': '/dev/ttyUSB0'},
                  'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                               'port': 10001}]})
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 400)

    def test_an_id_can_be_changed_on_update(self):
        wrapper, cfg = self._wrapper(ports=[
            {'id': 'old', 'serial': {'port': '/dev/a'}, 'servers': []}])
        wrapper._serial_proxies = [Mock()]
        client = _MockClient(
            method='PUT', path='/api/ports/old',
            data={'id': 'new-name', 'serial': {'port': '/dev/a'},
                  'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                               'port': 10001}]})
        with patch.object(wrapper, '_create_proxy', return_value=Mock()):
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200, client.responded)
        self.assertEqual(cfg['ports'][0]['id'], 'new-name')

    def test_keeping_your_own_id_on_update_is_not_a_clash(self):
        wrapper, cfg = self._wrapper(ports=[
            {'id': 'mine', 'serial': {'port': '/dev/a'}, 'servers': []}])
        wrapper._serial_proxies = [Mock()]
        client = _MockClient(
            method='PUT', path='/api/ports/mine',
            data={'id': 'mine', 'serial': {'port': '/dev/a'},
                  'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                               'port': 10001}]})
        with patch.object(wrapper, '_create_proxy', return_value=Mock()):
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200, client.responded)

    def test_an_update_without_an_id_keeps_the_old_one(self):
        wrapper, cfg = self._wrapper(ports=[
            {'id': 'keep', 'serial': {'port': '/dev/a'}, 'servers': []}])
        wrapper._serial_proxies = [Mock()]
        client = _MockClient(
            method='PUT', path='/api/ports/keep',
            data={'serial': {'port': '/dev/a'},
                  'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                               'port': 10001}]})
        with patch.object(wrapper, '_create_proxy', return_value=Mock()):
            wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200, client.responded)
        self.assertEqual(cfg['ports'][0]['id'], 'keep')


class TestEntryRevision(unittest.TestCase):
    """A fingerprint of what an entry says, so a save can tell whether
    it is writing over something it has not seen."""

    def test_the_same_entry_gives_the_same_revision(self):
        from ser2tcp.config_ids import entry_rev
        entry = {'id': 'a', 'name': 'dev', 'serial': {'port': '/dev/x'}}
        self.assertEqual(entry_rev(entry), entry_rev(dict(entry)))

    def test_key_order_does_not_matter(self):
        from ser2tcp.config_ids import entry_rev
        first = {'id': 'a', 'name': 'dev'}
        second = {'name': 'dev', 'id': 'a'}
        self.assertEqual(entry_rev(first), entry_rev(second))

    def test_a_changed_value_changes_the_revision(self):
        from ser2tcp.config_ids import entry_rev
        entry = {'id': 'a', 'serial': {'baudrate': 9600}}
        changed = {'id': 'a', 'serial': {'baudrate': 57600}}
        self.assertNotEqual(entry_rev(entry), entry_rev(changed))

    def test_the_revision_itself_is_not_part_of_it(self):
        """Otherwise it would change every time it is reported"""
        from ser2tcp.config_ids import entry_rev
        entry = {'id': 'a', 'name': 'dev'}
        with_rev = dict(entry, rev='whatever')
        self.assertEqual(entry_rev(entry), entry_rev(with_rev))


class TestConcurrentEdits(unittest.TestCase):
    """Two people with the same port open.

    Addressing by id stopped an edit landing on the wrong port. It does
    nothing about two edits of the right one: the second save used to
    overwrite the first without either of them being told.
    """

    def _wrapper(self):
        cfg = {
            'ports': [{'id': 'p1', 'name': 'dev',
                       'serial': {'port': '/dev/a', 'baudrate': 9600},
                       'servers': [{'protocol': 'tcp',
                                    'address': '0.0.0.0',
                                    'port': 10001}]}],
            'http': [{'id': 'h1', 'address': '127.0.0.1', 'port': 8080}],
        }
        wrapper = _wrapper_for(cfg)
        wrapper._serial_proxies = [Mock()]
        return wrapper, cfg

    def _get(self, wrapper, path):
        client = _MockClient(method='GET', path=path)
        wrapper._handle_request(client)
        return client.responded

    def _put(self, wrapper, path, data):
        client = _MockClient(method='PUT', path=path, data=data)
        with patch.object(wrapper, '_create_proxy', return_value=Mock()):
            wrapper._handle_request(client)
        return client

    def test_the_config_comes_with_a_revision(self):
        wrapper, _ = self._wrapper()
        self.assertTrue(self._get(wrapper, '/api/ports/p1')['rev'])

    def test_saving_what_you_read_is_accepted(self):
        wrapper, _ = self._wrapper()
        cfg = self._get(wrapper, '/api/ports/p1')
        cfg['name'] = 'renamed'
        client = self._put(wrapper, '/api/ports/p1', cfg)
        self.assertEqual(client.respond_status, 200, client.responded)

    def test_saving_over_somebody_else_is_refused(self):
        wrapper, _ = self._wrapper()
        mine = self._get(wrapper, '/api/ports/p1')
        theirs = self._get(wrapper, '/api/ports/p1')
        theirs['serial']['baudrate'] = 57600
        self._put(wrapper, '/api/ports/p1', theirs)
        mine['name'] = 'renamed'
        client = self._put(wrapper, '/api/ports/p1', mine)
        self.assertEqual(client.respond_status, 409)
        self.assertIn('changed', client.responded['error'].lower())

    def test_their_change_survives_the_refusal(self):
        wrapper, cfg = self._wrapper()
        mine = self._get(wrapper, '/api/ports/p1')
        theirs = self._get(wrapper, '/api/ports/p1')
        theirs['serial']['baudrate'] = 57600
        self._put(wrapper, '/api/ports/p1', theirs)
        mine['name'] = 'renamed'
        self._put(wrapper, '/api/ports/p1', mine)
        self.assertEqual(cfg['ports'][0]['serial']['baudrate'], 57600)
        self.assertEqual(cfg['ports'][0]['name'], 'dev')

    def test_rereading_lets_you_save(self):
        """The way out is to look at what is there now"""
        wrapper, _ = self._wrapper()
        theirs = self._get(wrapper, '/api/ports/p1')
        theirs['serial']['baudrate'] = 57600
        self._put(wrapper, '/api/ports/p1', theirs)
        fresh = self._get(wrapper, '/api/ports/p1')
        fresh['name'] = 'renamed'
        client = self._put(wrapper, '/api/ports/p1', fresh)
        self.assertEqual(client.respond_status, 200, client.responded)

    def test_a_request_without_a_revision_is_not_checked(self):
        """A script that never read the entry is not fighting anyone"""
        wrapper, _ = self._wrapper()
        client = self._put(wrapper, '/api/ports/p1', {
            'serial': {'port': '/dev/a'},
            'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                         'port': 10001}]})
        self.assertEqual(client.respond_status, 200, client.responded)

    def test_the_revision_is_not_stored_in_the_config(self):
        wrapper, cfg = self._wrapper()
        conf = self._get(wrapper, '/api/ports/p1')
        conf['name'] = 'renamed'
        self._put(wrapper, '/api/ports/p1', conf)
        self.assertNotIn('rev', cfg['ports'][0])

    def test_http_servers_are_guarded_the_same_way(self):
        wrapper, cfg = self._wrapper()
        settings = self._get(wrapper, '/api/settings')
        entry = dict(settings['http'][0])
        stale = dict(entry)
        entry['name'] = 'theirs'
        client = _MockClient(
            method='PUT', path='/api/settings/http/h1', data=entry)
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 200, client.responded)
        stale['name'] = 'mine'
        client = _MockClient(
            method='PUT', path='/api/settings/http/h1', data=stale)
        wrapper._handle_request(client)
        self.assertEqual(client.respond_status, 409)
