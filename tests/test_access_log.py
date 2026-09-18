"""Tests for what the log says about a request.

This is the application log, not an access log — nginx does that one.
Its job is to let you read what happened in order, and to let the
infrastructure that *is* meant to stop attacks (fail2ban, a firewall)
see who is knocking.

So the request is logged when it arrives, ahead of whatever it causes,
and a failure adds a second line. A success adds nothing: the absence
of an error is the answer, and the operational lines in between say
what was done.
"""

import logging
import unittest
from unittest.mock import Mock, patch

import ser2tcp.http_server as http_server
from ser2tcp.http_auth import hash_password
from tests.test_http_server import MockClient, make_wrapper


LOGGER = 'ser2tcp.test_access_log'


class AccessLogTestCase(unittest.TestCase):

    auth_config = None

    def setUp(self):
        self.log = logging.getLogger(LOGGER)
        self.log.setLevel(logging.DEBUG)
        self.wrapper = make_wrapper(
            log=self.log, auth_config=self.auth_config)

    def _request(self, method='GET', path='/api/status', data=None,
                 addr=('192.0.2.7', 51234), headers=None):
        client = MockClient(
            method=method, path=path, data=data, headers=headers)
        client.addr = addr
        with self.assertLogs(LOGGER, level='DEBUG') as captured:
            self.wrapper._handle_request(client)
        return client, captured.output

    def _lines(self, output, level):
        return [line for line in output if line.startswith(level + ':')]


class TestTheRequestLine(AccessLogTestCase):

    def test_it_says_who_is_asking(self):
        _client, output = self._request()
        self.assertTrue(
            any('192.0.2.7' in line for line in self._lines(output, 'INFO')),
            output)

    def test_it_says_what_they_asked_for(self):
        _client, output = self._request('GET', '/api/settings')
        line = self._lines(output, 'INFO')[0]
        self.assertIn('GET', line)
        self.assertIn('/api/settings', line)

    def test_it_comes_first(self):
        """Ahead of anything the request causes, so the log reads in order"""
        _client, output = self._request()
        self.assertIn('/api/status', output[0])


class TestAFailureAddsALine(AccessLogTestCase):

    def test_a_4xx_is_a_warning(self):
        _client, output = self._request(path='/api/nosuch')
        self.assertTrue(self._lines(output, 'WARNING'), output)

    def test_it_carries_the_status(self):
        _client, output = self._request(path='/api/nosuch')
        self.assertIn('404', self._lines(output, 'WARNING')[0])

    def test_it_carries_the_address(self):
        """fail2ban matches one line at a time, so it has to be here too"""
        _client, output = self._request(path='/api/nosuch')
        self.assertIn('192.0.2.7', self._lines(output, 'WARNING')[0])

    def test_it_carries_the_method_and_path(self):
        _client, output = self._request('DELETE', '/api/nosuch')
        line = self._lines(output, 'WARNING')[0]
        self.assertIn('DELETE', line)
        self.assertIn('/api/nosuch', line)

    def test_it_carries_the_reason(self):
        _client, output = self._request(path='/api/nosuch')
        self.assertIn('Not found', self._lines(output, 'WARNING')[0])


class TestAFailedLoginIsBannable(AccessLogTestCase):
    """The line fail2ban is meant to match."""

    auth_config = {'users': [
        {'login': 'ann', 'password': hash_password('secret'), 'admin': True}]}

    def _login(self, password, addr=('198.51.100.9', 40001)):
        return self._request(
            'POST', '/api/login',
            {'login': 'ann', 'password': password}, addr=addr)

    def test_a_wrong_password_is_logged_with_the_address(self):
        client, output = self._login('wrong')
        self.assertEqual(client.respond_status, 401)
        line = self._lines(output, 'WARNING')[0]
        self.assertIn('198.51.100.9', line)
        self.assertIn('401', line)

    def test_the_login_that_was_tried_is_in_it(self):
        _client, output = self._login('wrong')
        self.assertIn('ann', self._lines(output, 'WARNING')[0])

    def test_a_good_password_leaves_no_warning(self):
        client, output = self._login('secret')
        self.assertEqual(client.respond_status, 200)
        self.assertFalse(self._lines(output, 'WARNING'), output)


class TestASuccessSaysNothingExtra(AccessLogTestCase):

    def test_no_second_line_for_a_success(self):
        client, output = self._request()
        self.assertEqual(client.respond_status, 200)
        self.assertEqual(len(self._lines(output, 'WARNING')), 0)
        self.assertEqual(len(self._lines(output, 'ERROR')), 0)

    def test_the_request_line_is_all_there_is(self):
        _client, output = self._request()
        self.assertEqual(len(self._lines(output, 'INFO')), 1, output)


class TestAnAddressThatIsNotThere(unittest.TestCase):
    """The address must never be the reason a request fails.

    Tests and uhttp both hand over objects whose `addr` is missing or is
    a Mock, and a log line is not worth raising over.
    """

    def setUp(self):
        self.log = logging.getLogger(LOGGER)
        self.log.setLevel(logging.DEBUG)
        self.wrapper = make_wrapper(log=self.log)

    def _handle(self, client):
        with self.assertLogs(LOGGER, level='DEBUG') as captured:
            self.wrapper._handle_request(client)
        return captured.output

    def test_a_client_without_an_address_still_works(self):
        client = MockClient(path='/api/status')   # no addr attribute at all
        self._handle(client)
        self.assertEqual(client.respond_status, 200)

    def test_an_address_that_is_a_mock_is_not_printed_as_one(self):
        client = MockClient(path='/api/status')
        client.addr = Mock()
        output = self._handle(client)
        self.assertNotIn('Mock', output[0])

    def test_an_address_that_is_none_is_survivable(self):
        client = MockClient(path='/api/nosuch')
        client.addr = None
        output = self._handle(client)
        self.assertTrue(output)


class TestTheIpFilterRejectionIsLoggedTheSameWay(unittest.TestCase):
    """It already logged the address — now it does it at the same level."""

    def test_a_blocked_client_is_a_warning_with_its_address(self):
        log = logging.getLogger(LOGGER)
        log.setLevel(logging.DEBUG)
        wrapper = make_wrapper(log=log)
        blocked = Mock()
        blocked.is_allowed.return_value = False
        client = MockClient(path='/api/status')
        client.addr = ('203.0.113.4', 5555)
        client.event = http_server._uhttp_server.EVENT_REQUEST
        # handle_client only looks at real uhttp connections, so the
        # isinstance gate has to see one.
        with patch.object(http_server._uhttp_server, 'HttpConnection',
                          MockClient), \
                patch.object(wrapper, '_ip_filter_for', return_value=blocked):
            with self.assertLogs(LOGGER, level='DEBUG') as captured:
                wrapper.handle_client(client)
        self.assertEqual(client.respond_status, 403)
        warnings = [l for l in captured.output if l.startswith('WARNING:')]
        self.assertTrue(warnings, captured.output)
        self.assertIn('203.0.113.4', warnings[0])


if __name__ == '__main__':
    unittest.main()
