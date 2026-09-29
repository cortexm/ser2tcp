"""GET/PUT /api/settings - what applies to the whole process.

Today that is the session timeout. It was written into config.json and
nowhere else: the sessions kept the timeout they started with until a
restart, and the next change to a user or a token wrote the old one
back over it.
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from ser2tcp.http_auth import DEFAULT_SESSION_TIMEOUT, hash_password
from tests.test_http_server import MockClient, make_wrapper


class SettingsTestCase(unittest.TestCase):

    configured = {}

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = os.path.join(self._dir.name, 'config.json')
        auth = {'users': [
            {'login': 'admin', 'password': hash_password('s'), 'admin': True},
            {'login': 'viewer', 'password': hash_password('s'),
             'admin': False},
        ]}
        auth.update(self.configured)
        self.wrapper = make_wrapper(auth_config=auth, config_path=self.path)
        self.token = self.login('admin')

    def call(self, method, path, data=None, token=None):
        client = MockClient(
            method=method, path=path, data=data,
            headers={'authorization': 'Bearer ' + (token or self.token)})
        self.wrapper._handle_request(client)
        return client

    def login(self, login):
        client = MockClient(method='POST', path='/api/login',
            data={'login': login, 'password': 's'})
        self.wrapper._handle_request(client)
        return client.responded['token']

    def put(self, data, token=None):
        return self.call('PUT', '/api/settings', data, token)

    def get(self):
        return self.call('GET', '/api/settings').responded

    def stored(self):
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def session_length(self):
        """How long a session started now lasts"""
        token = self.login('viewer')
        expires = self.wrapper._auth._sessions[token]['expires']
        return round(expires - time.time())


class TestItApplies(SettingsTestCase):

    def test_a_new_timeout_holds_for_the_next_login(self):
        client = self.put({'session_timeout': 120})
        self.assertEqual(client.respond_status, 200, client.responded)
        self.assertEqual(self.session_length(), 120)

    def test_it_is_written_down(self):
        self.put({'session_timeout': 120})
        self.assertEqual(self.stored()['session_timeout'], 120)

    def test_a_change_to_a_user_does_not_write_the_old_one_back(self):
        self.put({'session_timeout': 120})
        self.call('POST', '/api/users',
                  {'login': 'ann', 'password': 'x', 'admin': False})
        self.assertEqual(self.stored()['session_timeout'], 120)
        self.assertEqual(self.session_length(), 120)

    def test_null_goes_back_to_the_default(self):
        self.put({'session_timeout': 120})
        self.put({'session_timeout': None})
        self.assertNotIn('session_timeout', self.stored())
        self.assertEqual(self.session_length(), DEFAULT_SESSION_TIMEOUT)

    def test_the_default_is_not_written_down_by_a_change_to_a_user(self):
        """Absent means "the default", which may change; 3600 written
        into the file is a choice nobody made"""
        self.call('POST', '/api/users',
                  {'login': 'ann', 'password': 'x', 'admin': False})
        self.assertNotIn('session_timeout', self.stored())

    def test_it_is_reported(self):
        self.put({'session_timeout': 120})
        self.assertEqual(self.get()['session_timeout'], 120)

    def test_so_is_what_unset_means(self):
        """The form shows it; a copy of the number there could drift"""
        self.assertEqual(self.get()['defaults']['session_timeout'],
                         DEFAULT_SESSION_TIMEOUT)


class TestTheFirstUser(unittest.TestCase):
    """With no users there is no SessionManager yet; the first one added
    through the API creates it, and has to be given the timeout too."""

    def test_the_configured_timeout_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'config.json')
            wrapper = make_wrapper(
                auth_config={'session_timeout': 600}, config_path=path)
            client = MockClient(method='POST', path='/api/users', data={
                'login': 'admin', 'password': 's', 'admin': True})
            wrapper._handle_request(client)
            self.assertEqual(client.respond_status, 201, client.responded)
            with open(path, encoding='utf-8') as f:
                self.assertEqual(json.load(f)['session_timeout'], 600)
            token = client.responded['token']
            expires = wrapper._auth._sessions[token]['expires']
            self.assertEqual(round(expires - time.time()), 600)


class TestWhatIsRefused(SettingsTestCase):

    configured = {'session_timeout': 900}

    def assertRefused(self, data, status=400):
        client = self.put(data)
        self.assertEqual(client.respond_status, status, client.responded)
        self.assertEqual(self.session_length(), 900)
        self.assertEqual(self.stored().get('session_timeout', 900), 900)

    def test_not_a_number(self):
        self.assertRefused({'session_timeout': '60'})

    def test_a_boolean(self):
        """True is an int to Python, and would have been 1 second"""
        self.assertRefused({'session_timeout': True})

    def test_negative(self):
        self.assertRefused({'session_timeout': -1})

    def test_an_unknown_key(self):
        """Silently ignored, it would read as saved"""
        self.assertRefused({'session_timout': 60})

    def test_a_viewer(self):
        client = self.put({'session_timeout': 60},
                          token=self.login('viewer'))
        self.assertEqual(client.respond_status, 403)


class TestTwoAdmins(SettingsTestCase):
    """The same as an entry's rev: a save made against what was read
    is refused once somebody else has changed it."""

    def test_the_settings_carry_a_rev(self):
        self.assertTrue(self.get().get('rev'))

    def test_a_save_against_what_was_read(self):
        rev = self.get()['rev']
        client = self.put({'session_timeout': 60, 'rev': rev})
        self.assertEqual(client.respond_status, 200, client.responded)

    def test_a_save_against_something_older(self):
        rev = self.get()['rev']
        self.put({'session_timeout': 60})
        client = self.put({'session_timeout': 30, 'rev': rev})
        self.assertEqual(client.respond_status, 409)
        self.assertEqual(self.session_length(), 60)

    def test_the_rev_moves_with_the_value(self):
        before = self.get()['rev']
        self.put({'session_timeout': 60})
        self.assertNotEqual(self.get()['rev'], before)

    def test_http_servers_do_not_move_it(self):
        """They have their own rev each; one list's change is not a
        conflict for a form that does not show it"""
        before = self.get()['rev']
        self.wrapper._http_list()[0]['name'] = 'renamed'
        self.assertEqual(self.get()['rev'], before)


class TestNothingChanged(SettingsTestCase):

    configured = {'session_timeout': 900}

    def test_the_same_value_is_not_written(self):
        with patch.object(self.wrapper, '_save_config') as save:
            client = self.put({'session_timeout': 900})
        self.assertEqual(client.respond_status, 200)
        save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
