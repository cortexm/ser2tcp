"""Integration tests for the WebSocket data path.

WebSocket connections are accepted by uhttp and driven from the same
selector as everything else, so this covers the handoff ser2tcp makes
when an upgrade request arrives and the frames that follow it. uhttp 3.0
rebuilt WebSocket as a facade over HttpConnection, which makes an
end-to-end frame exchange worth having rather than assumed.
"""

import json
import select
import ssl
import time
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
        """Open a WebSocket, and wait for the device behind it.

        create_connection() returns as soon as the 101 arrives, and
        ser2tcp queues that response before it opens the serial port -
        so without the wait a test can write to the pty master in that
        gap and lose the bytes to the flush that opening a tty does.

        Monitor endpoints only attach a callback and never open the
        device, so there is nothing to wait for there.
        """
        conn = websocket.create_connection(
            f'ws://127.0.0.1:{self.port}{path}', timeout=5)
        self.addCleanup(conn.close)
        if not path.startswith('/ws/monitor/'):
            self.wait_for_serial()
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



class TestWebSocketBackpressure(WebSocketTestCase):
    """A WebSocket client feeding a device slower than itself.

    uhttp owns these sockets, so until it grew pause_reading() there was
    no way to hold such a client back: it was capped by the serial write
    buffer's hard limit and had its data dropped once that filled.
    """

    def _push(self, conn, payload, chunk=8192, seconds=3):
        """Send whole frames until the socket stops taking them.

        A non-blocking send takes what it can, which may be part of a
        frame - so each frame is finished before the next one starts.
        Counting a partial send as a whole frame corrupts the stream,
        and everything after it is unparseable.
        """
        sock = conn.sock
        sock.setblocking(False)
        sent = 0
        deadline = time.time() + seconds
        try:
            while sent < len(payload) and time.time() < deadline:
                piece = payload[sent:sent + chunk]
                frame = websocket.ABNF.create_frame(
                    piece, websocket.ABNF.OPCODE_BINARY).format()
                off = 0
                while off < len(frame) and time.time() < deadline:
                    try:
                        off += sock.send(frame[off:])
                    except (BlockingIOError, ssl.SSLWantWriteError):
                        time.sleep(0.01)
                if off < len(frame):
                    # Out of time mid-frame: finish it so the stream
                    # stays valid, then stop.
                    sock.setblocking(True)
                    sock.sendall(frame[off:])
                    sock.setblocking(False)
                sent += len(piece)
        finally:
            sock.setblocking(True)
        return sent

    def test_the_api_answers_while_the_device_is_behind(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        self._push(conn, b'x' * (512 * 1024))
        started = time.time()
        status, _ = self.get('/api/status', timeout=5)
        self.assertEqual(status, 200)
        self.assertLess(time.time() - started, 3)

    def test_the_client_stays_connected_rather_than_being_dropped(self):
        """Stalling the peer beats closing it or losing its bytes"""
        conn = self.ws_connect('/ws/' + ENDPOINT)
        self._push(conn, b'y' * (512 * 1024))
        self.assertEqual(self.get('/api/status')[0], 200)
        # Let the device drain, then check the connection still works.
        deadline = time.time() + 25
        while time.time() < deadline:
            if not select.select([self.master_fd], [], [], 0.5)[0]:
                break
            os.read(self.master_fd, 65536)
        conn.send_binary(b'still here')
        self.assertEqual(
            read_device(self.master_fd, 10, timeout=10), b'still here')

    def test_what_it_sent_first_reaches_the_device_in_order(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        payload = bytes(range(256)) * 128  # 32 KB, every byte distinct
        sent = self._push(conn, payload, chunk=4096)
        self.assertGreater(sent, 0)
        got = read_device(self.master_fd, sent, timeout=25)
        self.assertEqual(got[:len(got)], payload[:len(got)])


if __name__ == '__main__':
    unittest.main()
