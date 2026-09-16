"""Integration tests for the WebSocket data path.

WebSocket connections are accepted by uhttp and driven from the same
selector as everything else, so this covers the handoff ser2tcp makes
when an upgrade request arrives and the frames that follow it. uhttp 3.0
rebuilt WebSocket as a facade over HttpConnection, which makes an
end-to-end frame exchange worth having rather than assumed.
"""

import json
import os
import unittest

import websocket

from tests.integration.test_serial import SerialPtyTestCase, read_device

ENDPOINT = 'demo'


def recv_binary(conn, timeout=5):
    """Return the next binary frame, skipping JSON control frames."""
    conn.settimeout(timeout)
    for _ in range(10):
        frame = conn.recv()
        if isinstance(frame, bytes):
            return frame
    raise AssertionError('no binary frame arrived')


def recv_json(conn, timeout=5):
    """Return the next text frame, decoded, skipping binary ones."""
    conn.settimeout(timeout)
    for _ in range(10):
        frame = conn.recv()
        if isinstance(frame, str):
            return json.loads(frame)
    raise AssertionError('no text frame arrived')


class WebSocketTestCase(SerialPtyTestCase):
    """A pty-backed port exposed over WebSocket as well as TCP."""

    @classmethod
    def port_servers(cls):
        return [
            {'protocol': 'tcp', 'address': '127.0.0.1', 'port': cls.tcp_port},
            {
                'protocol': 'websocket', 'endpoint': ENDPOINT, 'data': True,
                'control': {
                    'rts': True, 'dtr': True,
                    'signals': ['rts', 'dtr', 'cts', 'dsr'],
                },
            },
        ]

    def ws_connect(self, path):
        conn = websocket.create_connection(
            f'ws://127.0.0.1:{self.port}{path}', timeout=5)
        self.addCleanup(conn.close)
        return conn


class TestWebSocketDataPath(WebSocketTestCase):

    def test_client_frame_reaches_the_device(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        conn.send_binary(b'over websocket')
        self.assertEqual(
            read_device(self.master_fd, 14), b'over websocket')

    def test_device_data_reaches_the_client(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        os.write(self.master_fd, b'to websocket')
        self.assertEqual(recv_binary(conn), b'to websocket')

    def test_several_frames_from_one_read_all_arrive(self):
        """One recv() can carry several frames; the loop has to drain
        them rather than wait for another readable event."""
        conn = self.ws_connect('/ws/' + ENDPOINT)
        for index in range(5):
            conn.send_binary(b'frame%d' % index)
        received = read_device(self.master_fd, 30)
        for index in range(5):
            self.assertIn(b'frame%d' % index, received)

    def test_websocket_and_tcp_client_both_receive(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        sock = self.connect()
        os.write(self.master_fd, b'to both')
        self.assertEqual(recv_binary(conn), b'to both')
        self.assertEqual(sock.recv(7), b'to both')

    def test_unknown_endpoint_is_refused(self):
        with self.assertRaises(Exception):
            websocket.create_connection(
                f'ws://127.0.0.1:{self.port}/ws/nosuch', timeout=5)


class TestWebSocketControl(WebSocketTestCase):

    def test_signals_are_sent_on_connect(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        message = recv_json(conn)
        self.assertIn('signals', message)
        self.assertEqual(
            set(message['signals']), {'rts', 'dtr', 'cts', 'dsr'})

    def test_a_signal_the_device_cannot_do_is_survivable(self):
        """A pty has no modem control lines, so the ioctl fails.

        The request comes from a client, so it has to be refused and
        logged — unhandled it took the whole process down with it.
        """
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        conn.send(json.dumps({'rts': False}))
        conn.send(json.dumps({'dtr': False}))
        os.write(self.master_fd, b'alive')
        self.assertEqual(recv_binary(conn), b'alive')
        self.assertEqual(self.get('/api/status')[0], 200)

    def test_signals_are_readable_over_the_api(self):
        self.ws_connect('/ws/' + ENDPOINT)
        status, body = self.get('/api/signals')
        self.assertEqual(status, 200)
        self.assertEqual(body[0]['name'], 'pty')
        self.assertIn('rts', body[0]['signals'])


class TestMonitorEndpoint(WebSocketTestCase):
    """The read-only monitor tags each frame with its direction."""

    DIR_TX = 1
    DIR_RX = 2

    def test_monitor_sees_both_directions(self):
        monitor = self.ws_connect('/ws/monitor/pty')
        sock = self.connect()
        sock.sendall(b'tx side')
        frame = recv_binary(monitor)
        self.assertEqual(frame[0], self.DIR_TX)
        self.assertEqual(frame[1:], b'tx side')
        os.write(self.master_fd, b'rx side')
        frame = recv_binary(monitor)
        self.assertEqual(frame[0], self.DIR_RX)
        self.assertEqual(frame[1:], b'rx side')

    def test_monitor_does_not_write_to_the_device(self):
        monitor = self.ws_connect('/ws/monitor/pty')
        self.connect()
        self.drain_device()
        monitor.send_binary(b'ignored')
        self.assertEqual(read_device(self.master_fd, 7, timeout=1), b'')


class TestWebSocketAfterReconfiguration(WebSocketTestCase):
    """The exact shape of a bug once reported from the web UI.

    uhttp owns the WebSocket socket and registers it itself, so after a
    port is rebuilt through the API the upgrade still succeeds and
    keystrokes still reach the device. Only the serial read source goes
    unregistered — which makes WebSocket the quietest way for that to
    show up: a terminal that connects, accepts typing, and never echoes.
    """

    def port_config(self):
        """The port entry as currently configured, for a PUT"""
        return self.build_config()['ports'][0]

    def test_device_output_survives_a_port_update(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        os.write(self.master_fd, b'before')
        self.assertEqual(recv_binary(conn), b'before')
        conn.close()

        status, body = self.put('/api/ports/0', self.port_config())
        self.assertEqual(status, 200, body)

        conn = self.ws_connect('/ws/' + ENDPOINT)
        conn.send_binary(b'keystroke')
        self.assertEqual(read_device(self.master_fd, 9), b'keystroke')
        os.write(self.master_fd, b'echo')
        self.assertEqual(recv_binary(conn), b'echo')


if __name__ == '__main__':
    unittest.main()
