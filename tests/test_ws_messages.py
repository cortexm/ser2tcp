"""Tests for what a WebSocket endpoint says and what it accepts.

Until now a text frame could carry exactly one thing in each
direction: a signal report out, an {"rts": true} in. A client could
not be told what port it had reached, what it was allowed to do, or
that a request of its own had been refused - the server simply
dropped what it would not act on.

Every text frame is now one JSON object whose top-level keys are
topics, so one frame can say several things at once. The first one
carries every topic; later ones carry only what changed.
"""

import json
import unittest
from unittest.mock import Mock

from ser2tcp.serial_proxy import port_info
from ser2tcp.server_websocket import ServerWebSocket


def _serial(**extra):
    """A mock port that can be rendered as JSON.

    A bare Mock cannot: its attributes are Mocks, and the first frame
    puts the port's name and device straight into a frame.
    """
    serial = Mock()
    serial.connect.return_value = True
    serial.can_add_connection.return_value = True
    serial.get_signals.return_value = 0
    serial.is_connected = True
    serial.name = 'esp32'
    serial.info = {
        'name': 'esp32', 'device': '/dev/ttyUSB0', 'baudrate': 115200}
    for key, value in extra.items():
        setattr(serial, key, value)
    return serial


def _client():
    client = Mock()
    client.is_websocket = True
    client.ws_is_text = False
    return client


def _frames(client):
    """Every text frame this client was sent, parsed"""
    return [json.loads(args[0]) for args, _ in client.ws_send.call_args_list
            if isinstance(args[0], str)]


def _last(client):
    """The most recent text frame, parsed"""
    frames = _frames(client)
    if not frames:
        raise AssertionError('no text frame was sent')
    return frames[-1]


class WsTestCase(unittest.TestCase):

    control = None
    access = None

    def setUp(self):
        self.serial = _serial()
        config = {'protocol': 'websocket', 'endpoint': 'esp32'}
        if self.control is not None:
            config['control'] = self.control
        if self.access is not None:
            config['access'] = self.access
        self.server = ServerWebSocket(config, self.serial, log=Mock())

    def join(self):
        client = _client()
        self.server.add_connection(client)
        return client

    def text(self, client, msg):
        """Deliver a JSON frame from the client"""
        client.ws_is_text = True
        client.read_buffer.return_value = json.dumps(msg).encode()
        self.server.process_message(client)

    def binary(self, client, data):
        client.ws_is_text = False
        client.read_buffer.return_value = data
        self.server.process_message(client)


class TestTheFirstFrame(WsTestCase):
    """One frame, so a client never renders a half-built state."""

    def test_a_client_is_greeted_even_without_control(self):
        """It used to be greeted only when signals were configured, so
        a plain data endpoint said nothing at all."""
        client = self.join()
        self.assertTrue(_frames(client))

    def test_it_says_which_port_it_reached(self):
        hello = _last(self.join())
        self.assertEqual(hello['port']['name'], 'esp32')
        self.assertEqual(hello['port']['device'], '/dev/ttyUSB0')
        self.assertEqual(hello['port']['baudrate'], 115200)

    def test_it_says_whether_the_device_is_there(self):
        hello = _last(self.join())
        self.assertEqual(hello['serial'], {'connected': True})

    def test_and_says_so_when_it_is_not(self):
        self.serial.is_connected = False
        hello = _last(self.join())
        self.assertEqual(hello['serial'], {'connected': False})

    def test_it_says_the_client_is_attached(self):
        hello = _last(self.join())
        self.assertIs(hello['attach'], True)

    def test_it_reports_nothing_about_signals_when_none_are_reported(self):
        """A client that sees no signals key shows no indicators, which
        is how an endpoint without control is meant to look."""
        hello = _last(self.join())
        self.assertNotIn('signals', hello)


class TestDescribingThePort(unittest.TestCase):
    """One reader for both greetings, so an endpoint and a monitor
    describe the same port the same way."""

    def _info(self, **config):
        proxy = Mock()
        proxy.name = 'esp32'
        proxy.serial_config = config
        return port_info(proxy)

    def test_the_device_and_its_speed(self):
        info = self._info(port='/dev/ttyUSB0', baudrate=115200)
        self.assertEqual(info['device'], '/dev/ttyUSB0')
        self.assertEqual(info['baudrate'], 115200)

    def test_a_port_found_by_usb_match_has_no_device_yet(self):
        """Resolved at connect time, so it can still be unknown."""
        self.assertIsNone(self._info(baudrate=9600)['device'])


class TestWhatThisClientMayDo(WsTestCase):

    def test_read_and_write_by_default(self):
        can = _last(self.join())['can']
        self.assertIs(can['read'], True)
        self.assertIs(can['write'], True)

    def test_it_may_ask_to_attach(self):
        self.assertIs(_last(self.join())['can']['attach'], True)

    def test_nothing_is_settable_without_control(self):
        self.assertEqual(_last(self.join())['can']['signals'], [])


class TestAReadOnlyEndpoint(WsTestCase):

    access = 'ro'

    def test_says_it_may_not_write(self):
        can = _last(self.join())['can']
        self.assertIs(can['read'], True)
        self.assertIs(can['write'], False)


class TestWithControl(WsTestCase):

    control = {'rts': True, 'signals': ['rts', 'cts']}

    def test_the_settable_lines_are_listed(self):
        """Not the same set as the reported ones - cts is reported and
        can never be set, and that is exactly the distinction the UI
        had no way to make."""
        self.assertEqual(_last(self.join())['can']['signals'], ['rts'])

    def test_the_reported_lines_arrive_with_their_state(self):
        self.serial.get_signals.return_value = 0b000101    # rts + cts
        hello = _last(self.join())
        self.assertEqual(hello['signals'], {'rts': True, 'cts': True})

    def test_in_the_order_the_config_named_them(self):
        self.assertEqual(
            list(_last(self.join())['signals']), ['rts', 'cts'])

    def test_a_line_that_is_not_reported_stays_out(self):
        self.serial.get_signals.return_value = 0b111111
        self.assertNotIn('dtr', _last(self.join())['signals'])


class TestReportingAChange(WsTestCase):
    """Only what moved: the full set was handed over on arrival."""

    control = {'signals': ['rts', 'dtr']}

    def test_only_the_line_that_changed_is_sent(self):
        client = self.join()                       # both low
        client.ws_send.reset_mock()
        self.server.send_signal_report(0b01)       # rts high
        self.assertEqual(_last(client), {'signals': {'rts': True}})

    def test_a_report_that_changes_nothing_sends_nothing(self):
        client = self.join()
        self.server.send_signal_report(0b01)
        client.ws_send.reset_mock()
        self.server.send_signal_report(0b01)
        client.ws_send.assert_not_called()

    def test_the_first_report_carries_everything(self):
        client = self.join()
        client.ws_send.reset_mock()
        self.server.send_signal_report(0b11)
        self.assertEqual(
            _last(client), {'signals': {'rts': True, 'dtr': True}})

    def test_everybody_connected_hears_it(self):
        first = self.join()
        second = self.join()
        first.ws_send.reset_mock()
        second.ws_send.reset_mock()
        self.server.send_signal_report(0b10)
        self.assertEqual(_last(first), {'signals': {'dtr': True}})
        self.assertEqual(_last(second), {'signals': {'dtr': True}})

    def test_including_a_detached_one(self):
        """Giving up the data is not giving up the channel."""
        client = self.join()
        self.server.detach(client)
        client.ws_send.reset_mock()
        self.server.send_signal_report(0b01)
        self.assertEqual(_last(client), {'signals': {'rts': True}})


class TestAskingToAttach(WsTestCase):

    def test_a_client_can_let_go(self):
        client = self.join()
        self.text(client, {'attach': False})
        self.assertNotIn(client, self.server.attached)

    def test_and_is_told_so(self):
        client = self.join()
        self.text(client, {'attach': False})
        self.assertEqual(_last(client), {'attach': False})

    def test_and_can_come_back(self):
        client = self.join()
        self.text(client, {'attach': False})
        self.text(client, {'attach': True})
        self.assertIn(client, self.server.attached)
        self.assertEqual(_last(client), {'attach': True})

    def test_it_stays_connected_while_detached(self):
        client = self.join()
        self.text(client, {'attach': False})
        self.assertIn(client, self.server.connections)

    def test_a_refused_attach_says_why_and_admits_it_failed(self):
        client = self.join()
        self.text(client, {'attach': False})
        self.serial.can_add_connection.return_value = False
        self.text(client, {'attach': True})
        answer = _last(client)
        self.assertIs(answer['attach'], False)
        self.assertEqual(answer['error']['request'], 'attach')

    def test_detaching_twice_is_not_an_error(self):
        client = self.join()
        self.text(client, {'attach': False})
        self.text(client, {'attach': False})
        self.assertEqual(_last(client), {'attach': False})


class TestAskingForASignal(WsTestCase):

    control = {'rts': True, 'signals': ['rts']}

    def test_the_line_is_set(self):
        client = self.join()
        self.text(client, {'signals': {'rts': False}})
        self.serial.set_rts.assert_called_once_with(False)

    def test_the_older_top_level_spelling_still_works(self):
        client = self.join()
        self.text(client, {'rts': True})
        self.serial.set_rts.assert_called_once_with(True)

    def test_a_line_that_may_not_be_set_is_refused_by_name(self):
        client = self.join()
        self.text(client, {'signals': {'dtr': True}})
        self.serial.set_dtr.assert_not_called()
        error = _last(client)['error']
        self.assertEqual(error['request'], 'signals')
        self.assertIn('dtr', error['reason'])

    def test_one_refused_line_does_not_stop_the_others(self):
        client = self.join()
        self.text(client, {'signals': {'rts': True, 'dtr': True}})
        self.serial.set_rts.assert_called_once_with(True)
        self.assertIn('error', _last(client))

    def test_a_signals_value_of_the_wrong_shape_is_refused(self):
        client = self.join()
        self.text(client, {'signals': ['rts']})
        self.assertEqual(_last(client)['error']['request'], 'signals')


class TestASettableLineNobodyReports(WsTestCase):
    """Settable and reported are different sets, so a client can ask
    for a line whose change it would otherwise never hear about."""

    control = {'rts': True, 'signals': ['cts']}

    def test_the_asker_is_answered_directly(self):
        client = self.join()
        self.text(client, {'signals': {'rts': False}})
        self.assertEqual(_last(client), {'signals': {'rts': False}})

    def test_but_a_reported_line_is_left_to_the_broadcast(self):
        """set_rts() reports it to everyone; answering as well would
        show the asker the same change twice."""
        server = ServerWebSocket(
            {'protocol': 'websocket', 'endpoint': 'x',
             'control': {'rts': True, 'signals': ['rts']}},
            self.serial, log=Mock())
        client = _client()
        server.add_connection(client)
        client.ws_send.reset_mock()
        client.ws_is_text = True
        client.read_buffer.return_value = json.dumps(
            {'signals': {'rts': False}}).encode()
        server.process_message(client)
        client.ws_send.assert_not_called()


class TestAFrameThatCannotBeRead(WsTestCase):

    def test_broken_json_is_answered(self):
        client = self.join()
        client.ws_is_text = True
        client.read_buffer.return_value = b'not json{'
        self.server.process_message(client)
        self.assertIn('error', _last(client))

    def test_a_json_array_is_answered(self):
        client = self.join()
        self.text(client, ['rts'])
        self.assertIn('error', _last(client))

    def test_an_empty_frame_says_nothing(self):
        """The keep-alive a client sends to stay open."""
        client = self.join()
        client.ws_send.reset_mock()
        self.text(client, {})
        client.ws_send.assert_not_called()

    def test_an_unknown_key_is_ignored(self):
        """What makes the format extensible: an old server and a new
        client have to be able to talk."""
        client = self.join()
        client.ws_send.reset_mock()
        self.text(client, {'something_later': 1})
        client.ws_send.assert_not_called()


class TestDataThatWillNotBeForwarded(WsTestCase):

    access = 'ro'

    def test_the_client_is_told_once(self):
        client = self.join()
        self.binary(client, b'typed')
        self.serial.send.assert_not_called()
        self.assertEqual(_last(client)['error']['request'], 'data')

    def test_and_not_again(self):
        """A client that ignores can.write may stream for as long as it
        likes; answering every frame turns its mistake into our flood."""
        client = self.join()
        self.binary(client, b'typed')
        client.ws_send.reset_mock()
        self.binary(client, b'more')
        client.ws_send.assert_not_called()


class TestDataFromADetachedClient(WsTestCase):

    def test_it_is_told_it_is_not_attached(self):
        client = self.join()
        self.text(client, {'attach': False})
        self.binary(client, b'typed')
        self.serial.send.assert_not_called()
        self.assertEqual(_last(client)['error']['request'], 'data')

    def test_and_is_told_again_after_attaching_and_letting_go(self):
        """The reason it was told the first time no longer holds once
        it has attached, so the next refusal is news again."""
        client = self.join()
        self.text(client, {'attach': False})
        self.binary(client, b'typed')
        self.text(client, {'attach': True})
        self.text(client, {'attach': False})
        client.ws_send.reset_mock()
        self.binary(client, b'typed')
        self.assertEqual(_last(client)['error']['request'], 'data')


if __name__ == '__main__':
    unittest.main()
