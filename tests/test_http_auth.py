"""Tests for auth module"""

import time
import unittest

from ser2tcp.http_auth import (
    hash_password, verify_password, ensure_hashed, SessionManager)


class TestHashPassword(unittest.TestCase):
    def test_hash_format(self):
        h = hash_password('secret')
        self.assertTrue(h.startswith('sha256:'))
        parts = h.split(':')
        self.assertEqual(len(parts), 3)
        self.assertEqual(len(parts[1]), 32)  # salt hex
        self.assertEqual(len(parts[2]), 64)  # sha256 hex

    def test_different_salts(self):
        h1 = hash_password('secret')
        h2 = hash_password('secret')
        self.assertNotEqual(h1, h2)

    def test_verify_correct(self):
        h = hash_password('secret')
        self.assertTrue(verify_password('secret', h))

    def test_verify_wrong(self):
        h = hash_password('secret')
        self.assertFalse(verify_password('wrong', h))

    def test_verify_invalid_format(self):
        self.assertFalse(verify_password('x', 'plaintext'))
        self.assertFalse(verify_password('x', 'sha256:'))
        self.assertFalse(verify_password('x', 'sha256:a:b:c'))
        self.assertFalse(verify_password('x', 'md5:salt:hash'))

    def test_ensure_hashed_plain(self):
        result = ensure_hashed('mypass')
        self.assertTrue(result.startswith('sha256:'))
        self.assertTrue(verify_password('mypass', result))

    def test_ensure_hashed_already_hashed(self):
        h = hash_password('mypass')
        self.assertEqual(ensure_hashed(h), h)


class TestSessionManager(unittest.TestCase):
    def _make_manager(self, users=None, tokens=None, session_timeout=3600):
        config = {'session_timeout': session_timeout}
        if users:
            config['users'] = users
        if tokens:
            config['tokens'] = tokens
        return SessionManager(config)

    def _make_user(self, login='admin', password='pass', admin=False,
            session_timeout=None):
        user = {
            'login': login,
            'password': hash_password(password),
            'admin': admin,
        }
        if session_timeout is not None:
            user['session_timeout'] = session_timeout
        return user

    def test_login_success(self):
        mgr = self._make_manager(users=[self._make_user()])
        token = mgr.login('admin', 'pass')
        self.assertIsNotNone(token)
        self.assertEqual(len(token), 64)

    def test_login_wrong_password(self):
        mgr = self._make_manager(users=[self._make_user()])
        self.assertIsNone(mgr.login('admin', 'wrong'))

    def test_login_unknown_user(self):
        mgr = self._make_manager(users=[self._make_user()])
        self.assertIsNone(mgr.login('nobody', 'pass'))

    def test_login_no_users(self):
        mgr = self._make_manager()
        self.assertIsNone(mgr.login('admin', 'pass'))

    def test_authenticate_session(self):
        mgr = self._make_manager(users=[
            self._make_user(admin=True)])
        token = mgr.login('admin', 'pass')
        user = mgr.authenticate(token)
        self.assertIsNotNone(user)
        self.assertEqual(user['login'], 'admin')
        self.assertTrue(user['admin'])

    def test_authenticate_non_admin(self):
        mgr = self._make_manager(users=[
            self._make_user(login='viewer', admin=False)])
        token = mgr.login('viewer', 'pass')
        user = mgr.authenticate(token)
        self.assertFalse(user['admin'])

    def test_authenticate_invalid_token(self):
        mgr = self._make_manager(users=[self._make_user()])
        self.assertIsNone(mgr.authenticate('invalid'))

    def test_authenticate_api_token(self):
        mgr = self._make_manager(tokens=[
            {'token': 'my-api-key', 'name': 'bot', 'admin': False}])
        user = mgr.authenticate('my-api-key')
        self.assertIsNotNone(user)
        self.assertEqual(user['login'], 'bot')
        self.assertFalse(user['admin'])

    def test_authenticate_api_token_admin(self):
        mgr = self._make_manager(tokens=[
            {'token': 'key', 'name': 'admin-bot', 'admin': True}])
        user = mgr.authenticate('key')
        self.assertTrue(user['admin'])

    def test_authenticate_api_token_default_name(self):
        mgr = self._make_manager(tokens=[{'token': 'key'}])
        user = mgr.authenticate('key')
        self.assertEqual(user['login'], 'token')

    def test_logout(self):
        mgr = self._make_manager(users=[self._make_user()])
        token = mgr.login('admin', 'pass')
        mgr.logout(token)
        self.assertIsNone(mgr.authenticate(token))

    def test_logout_unknown_token(self):
        mgr = self._make_manager()
        mgr.logout('nonexistent')  # should not raise

    def test_session_expiration(self):
        mgr = self._make_manager(
            users=[self._make_user()], session_timeout=0.1)
        token = mgr.login('admin', 'pass')
        self.assertIsNotNone(mgr.authenticate(token))
        time.sleep(0.2)
        self.assertIsNone(mgr.authenticate(token))

    def test_session_renewal(self):
        mgr = self._make_manager(
            users=[self._make_user()], session_timeout=0.5)
        token = mgr.login('admin', 'pass')
        time.sleep(0.3)
        # Access renews expiration
        self.assertIsNotNone(mgr.authenticate(token))
        time.sleep(0.3)
        # Still valid because renewed
        self.assertIsNotNone(mgr.authenticate(token))

    def test_per_user_timeout(self):
        mgr = self._make_manager(
            users=[self._make_user(session_timeout=0.1)],
            session_timeout=3600)
        token = mgr.login('admin', 'pass')
        time.sleep(0.2)
        self.assertIsNone(mgr.authenticate(token))

    def test_cleanup(self):
        mgr = self._make_manager(
            users=[self._make_user()], session_timeout=0.1)
        t1 = mgr.login('admin', 'pass')
        time.sleep(0.2)
        mgr.cleanup()
        self.assertEqual(len(mgr._sessions), 0)

    def test_cleanup_keeps_valid(self):
        mgr = self._make_manager(
            users=[self._make_user()], session_timeout=3600)
        t1 = mgr.login('admin', 'pass')
        mgr.cleanup()
        self.assertEqual(len(mgr._sessions), 1)

    def test_multiple_sessions(self):
        mgr = self._make_manager(users=[self._make_user()])
        t1 = mgr.login('admin', 'pass')
        t2 = mgr.login('admin', 'pass')
        self.assertNotEqual(t1, t2)
        self.assertIsNotNone(mgr.authenticate(t1))
        self.assertIsNotNone(mgr.authenticate(t2))
        mgr.logout(t1)
        self.assertIsNone(mgr.authenticate(t1))
        self.assertIsNotNone(mgr.authenticate(t2))

    # User management tests

    def test_add_first_user_is_admin(self):
        mgr = self._make_manager()
        mgr.add_user('new', 'pass123')
        token = mgr.login('new', 'pass123')
        user = mgr.authenticate(token)
        self.assertTrue(user['admin'])

    def test_add_first_user_admin_forced(self):
        mgr = self._make_manager()
        mgr.add_user('new', 'pass123', admin=False)
        token = mgr.login('new', 'pass123')
        user = mgr.authenticate(token)
        self.assertTrue(user['admin'])

    def test_add_second_user_not_admin(self):
        mgr = self._make_manager(users=[self._make_user()])
        mgr.add_user('new', 'pass123')
        token = mgr.login('new', 'pass123')
        user = mgr.authenticate(token)
        self.assertFalse(user['admin'])

    def test_add_user(self):
        mgr = self._make_manager()
        self.assertTrue(mgr.add_user('new', 'pass123'))
        token = mgr.login('new', 'pass123')
        self.assertIsNotNone(token)

    def test_add_user_with_hash(self):
        mgr = self._make_manager()
        h = hash_password('secret')
        self.assertTrue(mgr.add_user('new', h))
        token = mgr.login('new', 'secret')
        self.assertIsNotNone(token)

    def test_add_user_duplicate(self):
        mgr = self._make_manager(users=[self._make_user()])
        self.assertFalse(mgr.add_user('admin', 'other'))

    def test_add_user_with_admin(self):
        mgr = self._make_manager()
        mgr.add_user('new', 'pass', admin=True)
        token = mgr.login('new', 'pass')
        user = mgr.authenticate(token)
        self.assertTrue(user['admin'])

    def test_add_user_with_timeout(self):
        mgr = self._make_manager()
        mgr.add_user('new', 'pass', session_timeout=120)
        token = mgr.login('new', 'pass')
        # The session lapses on the user's own timeout, not the global
        # one - it is read off the user, so nothing is stored here.
        self.assertLess(mgr._sessions[token]['expires'], time.time() + 121)

    def test_update_user_password(self):
        mgr = self._make_manager(users=[self._make_user()])
        self.assertTrue(mgr.update_user('admin', password='newpass'))
        self.assertIsNone(mgr.login('admin', 'pass'))
        self.assertIsNotNone(mgr.login('admin', 'newpass'))

    def test_update_user_password_hash(self):
        mgr = self._make_manager(users=[self._make_user()])
        h = hash_password('hashed')
        mgr.update_user('admin', password=h)
        self.assertIsNotNone(mgr.login('admin', 'hashed'))

    def test_update_user_admin(self):
        mgr = self._make_manager(users=[self._make_user()])
        mgr.update_user('admin', admin=True)
        token = mgr.login('admin', 'pass')
        self.assertTrue(mgr.authenticate(token)['admin'])

    def test_update_user_not_found(self):
        mgr = self._make_manager()
        self.assertFalse(mgr.update_user('nobody', password='x'))

    def test_delete_user(self):
        mgr = self._make_manager(users=[
            self._make_user(login='admin', admin=True),
            self._make_user(login='viewer')])
        self.assertTrue(mgr.delete_user('viewer'))
        self.assertIsNone(mgr.login('viewer', 'pass'))

    def test_delete_user_not_found(self):
        mgr = self._make_manager()
        self.assertFalse(mgr.delete_user('nobody'))

    def test_delete_user_invalidates_sessions(self):
        mgr = self._make_manager(users=[
            self._make_user(login='admin', admin=True),
            self._make_user(login='viewer')])
        token = mgr.login('viewer', 'pass')
        mgr.delete_user('viewer')
        self.assertIsNone(mgr.authenticate(token))

    def test_delete_last_admin_refused(self):
        mgr = self._make_manager(users=[
            self._make_user(login='admin', admin=True)])
        result = mgr.delete_user('admin')
        self.assertIsInstance(result, str)
        self.assertIn('admin', result.lower())

    def test_delete_last_admin_allowed_with_admin_token(self):
        mgr = self._make_manager(
            users=[self._make_user(login='admin', admin=True)],
            tokens=[{'token': 'tok', 'name': 'api', 'admin': True}])
        self.assertTrue(mgr.delete_user('admin'))
        self.assertFalse(mgr.is_empty)

    def test_delete_admin_when_another_exists(self):
        mgr = self._make_manager(users=[
            self._make_user(login='admin1', admin=True),
            self._make_user(login='admin2', admin=True)])
        self.assertTrue(mgr.delete_user('admin1'))

    def test_update_remove_last_admin_refused(self):
        mgr = self._make_manager(users=[
            self._make_user(login='admin', admin=True)])
        result = mgr.update_user('admin', admin=False)
        self.assertIsInstance(result, str)
        token = mgr.login('admin', 'pass')
        self.assertTrue(mgr.authenticate(token)['admin'])

    def test_list_users(self):
        mgr = self._make_manager(users=[
            self._make_user(login='a'),
            self._make_user(login='b', admin=True)])
        users = mgr.list_users()
        self.assertEqual(len(users), 2)
        logins = {u['login'] for u in users}
        self.assertEqual(logins, {'a', 'b'})
        for u in users:
            self.assertNotIn('password', u)

    def test_get_auth_config(self):
        mgr = self._make_manager(
            users=[self._make_user()],
            tokens=[{'token': 'key', 'name': 'bot'}])
        config = mgr.get_auth_config()
        self.assertIn('users', config)
        self.assertIn('tokens', config)
        self.assertEqual(len(config['users']), 1)
        self.assertEqual(config['users'][0]['login'], 'admin')


class TestPasswordHelpersRejectNonStrings(unittest.TestCase):
    """JSON can carry any type into these; none of them may raise.

    Every one of these values arrives from a request body, and an
    unhandled TypeError in an API handler is a 500 at best.
    """

    def test_verify_password_refuses_a_non_string_password(self):
        stored = hash_password('secret')
        for value in (42, None, ['secret'], {'p': 1}, True):
            self.assertFalse(verify_password(value, stored))

    def test_verify_password_refuses_a_non_string_hash(self):
        for stored in (42, None, ['sha256:a:b']):
            self.assertFalse(verify_password('secret', stored))

    def test_verify_password_still_works_normally(self):
        stored = hash_password('secret')
        self.assertTrue(verify_password('secret', stored))
        self.assertFalse(verify_password('wrong', stored))

    def test_hash_password_rejects_a_non_string(self):
        for value in (42, None, ['secret']):
            with self.assertRaises(TypeError):
                hash_password(value)

    def test_ensure_hashed_rejects_a_non_string(self):
        for value in (42, None, ['secret']):
            with self.assertRaises(TypeError):
                ensure_hashed(value)

    def test_login_with_a_non_string_password_just_fails(self):
        manager = SessionManager({'users': [
            {'login': 'a', 'password': hash_password('secret')}]})
        self.assertIsNone(manager.login('a', 42))
        self.assertIsNone(manager.login(42, 'secret'))


class TestMalformedAuthConfig(unittest.TestCase):
    """A broken users/tokens block must fail loudly, never quietly.

    Skipping the entries that cannot be read would leave a server with
    no users at all - which is not "locked down", it is wide open,
    because is_empty turns authentication off entirely.
    """

    def test_user_without_a_login_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            SessionManager({'users': [{'password': 'x'}]})
        self.assertIn('login', str(caught.exception))

    def test_user_without_a_password_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            SessionManager({'users': [{'login': 'a'}]})
        self.assertIn('password', str(caught.exception))

    def test_user_with_a_non_string_login_is_rejected(self):
        with self.assertRaises(ValueError):
            SessionManager({'users': [{'login': ['a'], 'password': 'x'}]})

    def test_user_entry_that_is_not_an_object_is_rejected(self):
        with self.assertRaises(ValueError):
            SessionManager({'users': ['admin']})

    def test_user_with_a_bad_session_timeout_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            SessionManager({'users': [
                {'login': 'a', 'password': 'x', 'session_timeout': 'soon'}]})
        self.assertIn('session_timeout', str(caught.exception))

    def test_token_without_a_token_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            SessionManager({'tokens': [{'name': 'monitoring'}]})
        self.assertIn('token', str(caught.exception))

    def test_token_with_a_non_string_token_is_rejected(self):
        with self.assertRaises(ValueError):
            SessionManager({'tokens': [{'token': ['k'], 'name': 'x'}]})

    def test_global_session_timeout_must_be_a_number(self):
        with self.assertRaises(ValueError) as caught:
            SessionManager({'session_timeout': 'never'})
        self.assertIn('session_timeout', str(caught.exception))

    def test_a_good_config_is_still_accepted(self):
        manager = SessionManager({
            'session_timeout': 60,
            'users': [{'login': 'a', 'password': hash_password('x'),
                       'admin': True, 'session_timeout': 30}],
            'tokens': [{'token': 'k', 'name': 'monitoring'}],
        })
        self.assertFalse(manager.is_empty)
        self.assertIsNotNone(manager.login('a', 'x'))
        self.assertEqual(manager.authenticate('k')['login'], 'monitoring')


class TestSessionTimeoutOverride(unittest.TestCase):
    """null means "use the default", not "expire at the epoch"."""

    def _manager(self):
        return SessionManager({'session_timeout': 1000})

    def test_adding_a_user_without_an_override(self):
        manager = self._manager()
        manager.add_user('a', 'x', session_timeout=None)
        token = manager.login('a', 'x')
        self.assertIsNotNone(manager.authenticate(token))

    def test_clearing_an_override_falls_back_to_the_default(self):
        manager = self._manager()
        manager.add_user('a', 'x', session_timeout=30)
        manager.update_user('a', session_timeout=None)
        self.assertNotIn('session_timeout', manager.list_users()[0])
        token = manager.login('a', 'x')
        self.assertIsNotNone(manager.authenticate(token))

    def test_an_override_is_still_honoured(self):
        manager = self._manager()
        manager.add_user('a', 'x', session_timeout=30)
        self.assertEqual(manager.list_users()[0]['session_timeout'], 30)


class TestAnOpenSessionFollowsTheUser(unittest.TestCase):
    """What a session grants is looked up, not remembered.

    A session used to carry a copy of the admin flag and the timeout
    taken at login. Demoting somebody therefore did nothing until they
    logged out of their own accord - and since every request pushed the
    expiry further out, that could be never.
    """

    def _manager(self, **kwargs):
        user = {
            'login': 'ann', 'password': hash_password('pass'),
            'admin': True,
        }
        user.update(kwargs)
        return SessionManager({'session_timeout': 3600, 'users': [
            user,
            {'login': 'bob', 'password': hash_password('pass'),
             'admin': True},
        ]})

    def test_demoting_an_admin_takes_effect_at_once(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        mgr.update_user('ann', admin=False)
        self.assertFalse(mgr.authenticate(token)['admin'])

    def test_demoting_does_not_log_them_out(self):
        """They are still who they were, with less to do"""
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        mgr.update_user('ann', admin=False)
        self.assertEqual(mgr.authenticate(token)['login'], 'ann')

    def test_promoting_takes_effect_at_once_too(self):
        mgr = self._manager(admin=False)
        token = mgr.login('ann', 'pass')
        mgr.update_user('ann', admin=True)
        self.assertTrue(mgr.authenticate(token)['admin'])

    def test_a_shortened_timeout_applies_to_the_open_session(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        mgr.update_user('ann', session_timeout=60)
        mgr.authenticate(token)
        self.assertLess(mgr._sessions[token]['expires'], time.time() + 61)

    def test_a_session_whose_user_is_gone_is_not_accepted(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        del mgr._users['ann']
        self.assertIsNone(mgr.authenticate(token))

    def test_the_session_of_a_vanished_user_is_dropped(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        del mgr._users['ann']
        mgr.authenticate(token)
        self.assertNotIn(token, mgr._sessions)


class TestChangingAPasswordEndsTheSessions(unittest.TestCase):
    """Changing a password is how a compromised account is shut out.

    Leaving the sessions open made it useless for that: whoever was
    already in stayed in, and every request they made renewed them.
    """

    def _manager(self):
        return SessionManager({'session_timeout': 3600, 'users': [
            {'login': 'ann', 'password': hash_password('pass'),
             'admin': True},
            {'login': 'bob', 'password': hash_password('pass')},
        ]})

    def test_the_sessions_of_that_user_stop_working(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        mgr.update_user('ann', password='different')
        self.assertIsNone(mgr.authenticate(token))

    def test_every_session_of_that_user_goes(self):
        """Signed in from three places, locked out of all three"""
        mgr = self._manager()
        tokens = [mgr.login('ann', 'pass') for _ in range(3)]
        mgr.update_user('ann', password='different')
        for token in tokens:
            self.assertIsNone(mgr.authenticate(token))

    def test_other_users_are_left_alone(self):
        mgr = self._manager()
        theirs = mgr.login('bob', 'pass')
        mgr.update_user('ann', password='different')
        self.assertIsNotNone(mgr.authenticate(theirs))

    def test_the_new_password_works(self):
        mgr = self._manager()
        mgr.update_user('ann', password='different')
        self.assertIsNotNone(mgr.login('ann', 'different'))

    def test_a_change_that_is_not_the_password_keeps_the_session(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        mgr.update_user('ann', session_timeout=120)
        self.assertIsNotNone(mgr.authenticate(token))

    def test_a_refused_update_leaves_the_session_alone(self):
        """The last admin cannot be demoted - and must stay logged in"""
        mgr = SessionManager({'session_timeout': 3600, 'users': [
            {'login': 'ann', 'password': hash_password('pass'),
             'admin': True}]})
        token = mgr.login('ann', 'pass')
        self.assertIsInstance(mgr.update_user('ann', admin=False), str)
        self.assertTrue(mgr.authenticate(token)['admin'])


class TestReadingASessionWithoutRenewingIt(unittest.TestCase):
    """What a long-lived connection is allowed to ask.

    The status stream stays open for hours and has to notice when the
    account behind it changes. It cannot use authenticate() for that:
    every check would count as activity and hold the session open for
    as long as the page is, which is the opposite of a timeout.
    """

    def _manager(self, **kwargs):
        user = {'login': 'ann', 'password': hash_password('pass'),
                'admin': True}
        user.update(kwargs)
        return SessionManager({
            'session_timeout': 3600,
            'users': [user, {'login': 'bob',
                             'password': hash_password('pass'),
                             'admin': True}],
            'tokens': [{'token': 'bot-key', 'name': 'bot', 'admin': True}],
        })

    def test_it_reports_who_and_what(self):
        mgr = self._manager()
        state = mgr.session_state(mgr.login('ann', 'pass'))
        self.assertEqual(state['login'], 'ann')
        self.assertTrue(state['admin'])

    def test_it_does_not_push_the_expiry_out(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        expires = mgr._sessions[token]['expires']
        mgr.session_state(token)
        self.assertEqual(mgr._sessions[token]['expires'], expires)

    def test_it_follows_a_demotion(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        mgr.update_user('ann', admin=False)
        self.assertFalse(mgr.session_state(token)['admin'])

    def test_it_reports_nothing_after_a_password_change(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        mgr.update_user('ann', password='different')
        self.assertIsNone(mgr.session_state(token))

    def test_it_reports_nothing_for_a_deleted_user(self):
        mgr = self._manager()
        token = mgr.login('ann', 'pass')
        mgr.delete_user('ann')
        self.assertIsNone(mgr.session_state(token))

    def test_an_expired_session_reports_nothing(self):
        mgr = self._manager(session_timeout=0)
        token = mgr.login('ann', 'pass')
        self.assertIsNone(mgr.session_state(token))

    def test_an_expired_session_is_not_thrown_away_here(self):
        """Reaping belongs to cleanup(), which runs every pass anyway"""
        mgr = self._manager(session_timeout=0)
        token = mgr.login('ann', 'pass')
        mgr.session_state(token)
        self.assertIn(token, mgr._sessions)

    def test_an_api_token_works_the_same_way(self):
        mgr = self._manager()
        self.assertEqual(mgr.session_state('bot-key'),
                         {'login': 'bot', 'admin': True})

    def test_an_unknown_token_reports_nothing(self):
        self.assertIsNone(self._manager().session_state('nonsense'))
