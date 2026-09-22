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
import tempfile
import time
import os
import unittest

import websocket

from tests.integration import base
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


def recv_json(conn, timeout=5, key=None):
    """Return the next text frame, decoded, skipping binary ones.

    With `key`, skip on until a frame carries that topic: opening the
    port samples the signal lines, so a report can arrive between what
    a test asked for and what it is waiting to see.
    """
    conn.settimeout(timeout)
    for _ in range(10):
        frame = conn.recv()
        if not isinstance(frame, str):
            continue
        message = json.loads(frame)
        if key is None or key in message:
            return message
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


class TestTheGreetingOverTheWire(WebSocketTestCase):
    """The first frame has to arrive unasked, before anything else.

    Everything the terminal pages need used to come from a second
    connection - a filtered NDJSON stream with its own authentication.
    """

    def test_it_describes_the_port(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        hello = recv_json(conn)
        self.assertEqual(hello['port']['name'], 'pty')
        self.assertEqual(hello['port']['device'], self.pty_path)

    def test_it_says_the_device_is_there(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        self.assertEqual(recv_json(conn)['serial'], {'connected': True})

    def test_it_says_what_this_client_may_do(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        can = recv_json(conn)['can']
        self.assertIs(can['read'], True)
        self.assertIs(can['write'], True)
        self.assertEqual(sorted(can['signals']), ['dtr', 'rts'])

    def test_and_that_it_is_attached(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        self.assertIs(recv_json(conn)['attach'], True)


class TestAttachingOverTheWire(WebSocketTestCase):
    """Letting go of the device without closing the socket.

    Detaching the only client closes the port, so the answer arrives
    alongside a `serial` frame saying it went - hence `key=`.
    """

    def detach(self, conn):
        conn.send(json.dumps({'attach': False}))
        return recv_json(conn, key='attach')

    def test_a_detached_client_stops_receiving(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        self.assertEqual(self.detach(conn), {'attach': False})
        os.write(self.master_fd, b'not for you')
        conn.settimeout(1)
        with self.assertRaises(Exception):
            conn.recv()

    def test_and_starts_again_when_it_comes_back(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        self.detach(conn)
        conn.send(json.dumps({'attach': True}))
        self.assertEqual(recv_json(conn, key='attach')['attach'], True)
        os.write(self.master_fd, b'back')
        self.assertEqual(recv_binary(conn), b'back')

    def test_what_a_detached_client_types_is_not_forwarded(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        self.drain_device()
        self.detach(conn)
        conn.send_binary(b'ignored')
        self.assertEqual(read_device(self.master_fd, 7, timeout=1), b'')

    def test_and_it_is_told_why(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        self.detach(conn)
        conn.send_binary(b'ignored')
        self.assertEqual(
            recv_json(conn, key='error')['error']['request'], 'data')

    def test_the_port_closes_when_the_last_client_detaches(self):
        """The whole point: the socket stays, the device does not."""
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        self.assertTrue(self.serial_connected())
        self.detach(conn)
        self.wait_for_serial(connected=False)


class TestBeingRefusedOverTheWire(WebSocketTestCase):

    def test_a_line_that_may_not_be_set_is_answered(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        conn.send(json.dumps({'signals': {'cts': True}}))
        error = recv_json(conn)['error']
        self.assertEqual(error['request'], 'signals')
        self.assertIn('cts', error['reason'])

    def test_a_frame_that_is_not_json_is_answered(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        conn.send('not json{')
        self.assertIn('error', recv_json(conn))


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
    """The read-only monitor tags each frame with whoever wrote it."""

    DEVICE = 0

    def test_the_device_writes_under_zero(self):
        monitor = self.ws_connect('/ws/monitor/pty')
        self.connect()
        os.write(self.master_fd, b'rx side')
        frame = recv_binary(monitor)
        self.assertEqual(frame[0], self.DEVICE)
        self.assertEqual(frame[1:], b'rx side')

    def test_a_client_writes_under_its_slot(self):
        monitor = self.ws_connect('/ws/monitor/pty')
        sock = self.connect()
        sock.sendall(b'tx side')
        frame = recv_binary(monitor)
        self.assertNotEqual(frame[0], self.DEVICE)
        self.assertEqual(frame[1:], b'tx side')

    def test_two_clients_are_told_apart(self):
        """What a direction byte could never say on a shared line."""
        monitor = self.ws_connect('/ws/monitor/pty')
        first = self.connect()
        second = self.connect()
        first.sendall(b'from one')
        one = recv_binary(monitor)
        second.sendall(b'from two')
        two = recv_binary(monitor)
        self.assertEqual(one[1:], b'from one')
        self.assertEqual(two[1:], b'from two')
        self.assertNotEqual(one[0], two[0])

    def test_the_peer_list_arrives_with_the_greeting(self):
        self.connect()
        monitor = self.ws_connect('/ws/monitor/pty')
        hello = recv_json(monitor)
        self.assertEqual(hello['port']['name'], 'pty')
        self.assertIs(hello['can']['write'], False)
        self.assertEqual(len(hello['peers']), 1)
        self.assertEqual(hello['peers'][0]['protocol'], 'tcp')

    def test_a_client_joining_is_announced(self):
        monitor = self.ws_connect('/ws/monitor/pty')
        recv_json(monitor)
        self.connect()
        message = recv_json(monitor, key='peer_connected')
        self.assertEqual(len(message['peers']), 1)

    def test_and_one_leaving(self):
        monitor = self.ws_connect('/ws/monitor/pty')
        recv_json(monitor)
        sock = self.connect()
        recv_json(monitor, key='peer_connected')
        sock.close()
        self.wait_for_connections(0)
        message = recv_json(monitor, key='peer_disconnected')
        self.assertEqual(message['peers'], [])

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

        status, body = self.put('/api/ports/' + self.port_id(), self.port_config())
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


class TestAQuietConnection(WebSocketTestCase):
    """uhttp closes a WebSocket that has been quiet for its keep-alive
    timeout, 15 s. A terminal in a background tab is quiet - a browser
    throttles its timers - so it was closed for it.

    The client here sends nothing at all and does not even look at the
    socket: the pong that keeps it open comes from the websocket
    library's own read of the ping, which is as close as a test gets to
    a browser answering in its network stack.
    """

    def test_it_is_not_closed_for_being_quiet(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        deadline = time.time() + 20
        while time.time() < deadline:
            # recv() answers pings on its own; anything it returns is
            # incidental. Timing out is the point - nothing is sent.
            conn.settimeout(2)
            try:
                conn.recv()
            except Exception:
                pass
            if not conn.connected:
                self.fail('the server closed a connection that answered')
        os.write(self.master_fd, b'still here')
        self.assertEqual(recv_binary(conn), b'still here')


class TestTheDeviceGoesAway(WebSocketTestCase):
    """Closing the pty master makes the slave unreadable, which is what
    unplugging a USB adapter looks like from here.

    The device can only be taken away once, and the process is shared
    by the whole class - so this is one test watching one event from
    the three sides that answer it differently.
    """

    def unplug(self):
        """Take the device away and wait for ser2tcp to notice"""
        os.close(self.master_fd)
        type(self).master_fd = None
        deadline = time.time() + 5
        while time.time() < deadline:
            if not self.serial_connected():
                return
            time.sleep(0.05)
        raise AssertionError('ser2tcp still thinks the device is there')

    def test_who_is_told_and_who_is_dropped(self):
        conn = self.ws_connect('/ws/' + ENDPOINT)
        recv_json(conn)
        monitor = self.ws_connect('/ws/monitor/pty')
        recv_json(monitor)
        sock = self.connect()

        self.unplug()

        # The WebSocket client keeps its socket and is told why.
        message = recv_json(conn, key='serial')
        self.assertIs(message['serial']['connected'], False)
        self.assertTrue(message['serial'].get('reason'))
        # And still answers, which is the whole reason to keep it.
        conn.send(json.dumps({'attach': False}))
        self.assertEqual(recv_json(conn, key='attach'), {'attach': False})

        # A monitor watches the port, so it hears about it too.
        self.assertIs(
            recv_json(monitor, key='serial')['serial']['connected'], False)

        # TCP has no channel to be told on: closing is the message.
        sock.settimeout(5)
        self.assertEqual(sock.recv(64), b'')


class TestTheDeviceArrivesLate(base.IntegrationTestCase):
    """A port configured for a device that is not plugged in yet.

    Being refused and being told are the same news, but only one of
    them leaves a client able to wait for better - and something has
    to be waiting, or nothing would reopen the port.
    """

    endpoint = 'late'

    @classmethod
    def build_config(cls):
        cls.device = os.path.join(
            tempfile.mkdtemp(prefix='ser2tcp-late-'), 'device')
        return {
            'ports': [{
                'name': 'late',
                'serial': {'port': cls.device, 'baudrate': 115200},
                'servers': [
                    {'protocol': 'websocket', 'endpoint': cls.endpoint},
                ],
            }],
            'http': [{'address': '127.0.0.1', 'port': cls.port}],
        }

    def plug_in(self):
        """Make the configured path lead to a real tty"""
        master_fd, slave_fd = os.openpty()
        self.addCleanup(os.close, slave_fd)
        self.addCleanup(os.close, master_fd)
        os.symlink(os.ttyname(slave_fd), self.device)
        self.addCleanup(os.unlink, self.device)
        return master_fd

    def ws_connect(self):
        conn = websocket.create_connection(
            f'ws://127.0.0.1:{self.port}/ws/{self.endpoint}', timeout=10)
        self.addCleanup(conn.close)
        return conn

    def test_the_client_is_accepted_and_told(self):
        conn = self.ws_connect()
        hello = recv_json(conn)
        self.assertEqual(hello['serial'], {'connected': False})

    def test_and_told_again_once_the_device_turns_up(self):
        conn = self.ws_connect()
        recv_json(conn)
        master_fd = self.plug_in()
        self.assertEqual(
            recv_json(conn, timeout=15, key='serial')['serial'],
            {'connected': True})
        os.write(master_fd, b'at last')
        self.assertEqual(recv_binary(conn), b'at last')


if __name__ == '__main__':
    unittest.main()
