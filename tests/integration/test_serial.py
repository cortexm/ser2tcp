"""Integration tests for the serial data path.

A pty stands in for the serial device, so a real ser2tcp process moves
real bytes between a real socket and a real tty. This is the path the
selectors event loop exists for, and the one no unit test can reach:
accepting a client, arming write interest when a send does not drain in
one go, and reaping the connection when it goes away.
"""

import os
import select
import shutil
import socket
import tempfile
import threading
import time
import unittest

from tests.integration import base


def read_device(fd, size, timeout=3.0):
    """Read from the pty master until `size` bytes arrive or time runs out."""
    out = b''
    deadline = time.time() + timeout
    while len(out) < size and time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            out += os.read(fd, 65536)
    return out


def read_client(sock, size, timeout=5.0):
    """Read from a client socket until `size` bytes arrive or time runs out."""
    sock.settimeout(0.2)
    out = b''
    deadline = time.time() + timeout
    while len(out) < size and time.time() < deadline:
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            continue
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


def read_client_until(sock, marker, timeout=3.0):
    """Read from a client socket until `marker` shows up, or time runs out.

    Returns as soon as the marker lands, so a test does not pay a full
    timeout for bytes it was never going to get.
    """
    sock.settimeout(0.2)
    out = b''
    deadline = time.time() + timeout
    while marker not in out and time.time() < deadline:
        try:
            chunk = sock.recv(65536)
        except TimeoutError:
            continue
        except OSError:
            break
        if not chunk:
            break
        out += chunk
    return out


class SerialPtyTestCase(base.IntegrationTestCase):
    """Runs ser2tcp against a pty.

    The slave fd is held open so the pty survives the whole class, but
    nothing here ever reads it — ser2tcp opens the same device by path
    and has to be the only reader, or the two would race for the bytes.
    """

    tcp_port = None
    master_fd = None
    slave_fd = None
    pty_path = None

    @classmethod
    def port_servers(cls):
        """Server entries for the one configured port"""
        return [{
            'protocol': 'tcp', 'address': '127.0.0.1', 'port': cls.tcp_port,
        }]

    @classmethod
    def build_config(cls):
        return {
            'ports': [{
                'name': 'pty',
                'serial': {'port': cls.pty_path, 'baudrate': 115200},
                'servers': cls.port_servers(),
            }],
            'http': [{'address': '127.0.0.1', 'port': cls.port}],
        }

    @classmethod
    def setUpClass(cls):
        cls.master_fd, cls.slave_fd = os.openpty()
        cls.pty_path = os.ttyname(cls.slave_fd)
        cls.tcp_port = base.free_port()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        for fd in (cls.master_fd, cls.slave_fd):
            try:
                os.close(fd)
            except (OSError, TypeError):
                pass

    def connections_on(self, port):
        """How many clients ser2tcp currently has on that server"""
        body = self.get('/api/status')[1]
        for server in body['ports'][0]['servers']:
            if server.get('port') == port:
                return len(server['connections'])
        return 0

    def wait_for_connections(self, count, port=None, timeout=5.0):
        """Block until ser2tcp reports `count` clients on that server.

        Polling what the server says beats sleeping a guessed interval:
        accepting happens on a loop pass we cannot see from here, and
        the answer usually arrives in a few milliseconds.
        """
        port = port or self.tcp_port
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.connections_on(port) == count:
                return
            time.sleep(0.02)
        raise AssertionError(
            f'server on {port} reports {self.connections_on(port)} '
            f'connections, expected {count}')

    def serial_connected(self):
        """Whether ser2tcp currently holds the device open"""
        body = self.get('/api/status')[1]
        return bool(body['ports'][0]['serial']['connected'])

    def wait_for_serial(self, connected=True, timeout=5.0):
        """Block until ser2tcp has opened (or closed) the device.

        Opening a tty flushes whatever is already sitting in its input
        buffer, so bytes written to the pty master before the port is
        open are simply gone. Anything that writes to the master has to
        wait for this first.

        The answer is authoritative because the loop is single-threaded:
        this request can only be handled after the one that opened the
        port has returned.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.serial_connected() == connected:
                return
            time.sleep(0.02)
        raise AssertionError(
            f'serial reports connected={self.serial_connected()}, '
            f'expected {connected}')

    def connect(self, port=None):
        """Open a client connection, accepted, closing with the test"""
        port = port or self.tcp_port
        expected = self.connections_on(port) + 1
        sock = socket.create_connection(('127.0.0.1', port), 5)
        self.addCleanup(sock.close)
        self.wait_for_connections(expected, port)
        return sock

    def drain_device(self):
        """Throw away anything already sitting in the pty"""
        while select.select([self.master_fd], [], [], 0)[0]:
            os.read(self.master_fd, 65536)


class TestSerialDataPath(SerialPtyTestCase):

    def test_client_data_reaches_the_device(self):
        sock = self.connect()
        sock.sendall(b'from the client')
        self.assertEqual(read_device(self.master_fd, 15), b'from the client')

    def test_device_data_reaches_the_client(self):
        sock = self.connect()
        os.write(self.master_fd, b'from the device')
        self.assertEqual(read_client(sock, 15), b'from the device')

    def test_device_data_reaches_every_client(self):
        first = self.connect()
        second = self.connect()
        os.write(self.master_fd, b'broadcast')
        self.assertEqual(read_client(first, 9), b'broadcast')
        self.assertEqual(read_client(second, 9), b'broadcast')

    def test_both_directions_keep_working_after_a_client_leaves(self):
        first = self.connect()
        second = self.connect()
        first.close()
        self.wait_for_connections(1)
        os.write(self.master_fd, b'still here')
        self.assertEqual(read_client(second, 10), b'still here')
        second.sendall(b'and back')
        self.assertEqual(read_device(self.master_fd, 8), b'and back')

    def test_status_reports_the_connection(self):
        self.connect()
        status, body = self.get('/api/status')
        self.assertEqual(status, 200)
        port = body['ports'][0]
        self.assertEqual(port['name'], 'pty')
        self.assertTrue(port['serial']['connected'])
        self.assertEqual(port['serial']['port'], self.pty_path)
        connections = port['servers'][0]['connections']
        self.assertEqual(len(connections), 1)


class TestSerialPortLifecycle(SerialPtyTestCase):

    def _is_connected(self):
        return self.get('/api/status')[1]['ports'][0]['serial']['connected']

    def test_port_is_closed_until_a_client_arrives(self):
        self.assertFalse(self._is_connected())

    def test_port_opens_for_a_client_and_closes_after_it(self):
        sock = self.connect()
        self.assertTrue(self._is_connected())
        sock.close()
        self.wait_for_connections(0)
        self.assertFalse(self._is_connected())

    def test_port_reopens_for_a_later_client(self):
        first = self.connect()
        first.close()
        self.wait_for_connections(0)
        second = self.connect()
        self.assertTrue(self._is_connected())
        os.write(self.master_fd, b'reopened')
        self.assertEqual(read_client(second, 8), b'reopened')


class TestSlowClientWriteInterest(SerialPtyTestCase):
    """The send buffer and its EVENT_WRITE arming.

    A client that does not read lets ser2tcp's per-connection buffer
    fill, so the data leaves over many loop passes instead of one send().
    That only works if write interest is armed when the buffer grows and
    disarmed when it empties — get either wrong and this either hangs or
    spins.
    """

    PAYLOAD = 256 * 1024

    def test_backlog_is_delivered_in_full_and_in_order(self):
        sock = self.connect()
        expected = bytes(range(256)) * (self.PAYLOAD // 256)
        errors = []

        def feed():
            try:
                os.write(self.master_fd, expected)
            except OSError as err:  # pragma: no cover - diagnostic only
                errors.append(err)

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        # Let the backlog build before anyone drains it.
        time.sleep(0.5)
        received = read_client(sock, len(expected), timeout=20)
        writer.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(len(received), len(expected))
        self.assertEqual(received, expected)

    def test_the_loop_still_serves_others_during_a_backlog(self):
        sock = self.connect()
        threading.Thread(
            target=lambda: os.write(self.master_fd, b'x' * self.PAYLOAD),
            daemon=True).start()
        time.sleep(0.3)
        # The HTTP server shares the loop with the busy connection.
        status, _ = self.get('/api/status')
        self.assertEqual(status, 200)
        read_client(sock, self.PAYLOAD, timeout=20)


class TestTelnetPath(SerialPtyTestCase):
    """TELNET shares the connection machinery but escapes IAC."""

    telnet_port = None

    @classmethod
    def port_servers(cls):
        cls.telnet_port = base.free_port()
        return [
            {'protocol': 'tcp', 'address': '127.0.0.1', 'port': cls.tcp_port},
            {'protocol': 'telnet', 'address': '127.0.0.1',
                'port': cls.telnet_port},
        ]

    def test_telnet_client_exchanges_data(self):
        sock = self.connect(self.telnet_port)
        # uhttp-free path: ser2tcp greets with IAC negotiation, ignore it.
        read_client(sock, 1, timeout=1)
        sock.sendall(b'hello')
        self.assertEqual(read_device(self.master_fd, 5), b'hello')

    def test_iac_byte_is_doubled_towards_the_client(self):
        sock = self.connect(self.telnet_port)
        read_client(sock, 1, timeout=1)
        self.drain_device()
        os.write(self.master_fd, b'a\xffb')
        data = read_client(sock, 4, timeout=3)
        self.assertIn(b'a\xff\xffb', data)

    def test_the_two_servers_share_one_port(self):
        plain = self.connect()
        telnet = self.connect(self.telnet_port)
        read_client(telnet, 1, timeout=1)
        os.write(self.master_fd, b'both')
        self.assertEqual(read_client(plain, 4), b'both')
        self.assertIn(b'both', read_client(telnet, 4, timeout=3))

    def test_a_client_that_answers_the_negotiation_still_works(self):
        """The reply a real telnet client sends is a subnegotiation.

        ser2tcp opens with IAC DO LINEMODE, so this is the first thing
        most clients say back. Mishandled it left the connection stuck
        in the SB state and killed the process on the next keystroke.
        """
        sock = self.connect(self.telnet_port)
        read_client(sock, 1, timeout=1)
        self.drain_device()
        # IAC SB LINEMODE ... IAC SE, then an ordinary keystroke.
        sock.sendall(bytes((0xff, 0xfa, 0x22, 0x03, 0x01, 0xff, 0xf0)))
        sock.sendall(b'typed')
        self.assertEqual(read_device(self.master_fd, 5), b'typed')
        self.assertEqual(self.get('/api/status')[0], 200)

    def test_subnegotiation_contents_never_reach_the_device(self):
        """Negotiation is for the server, not for the wire"""
        sock = self.connect(self.telnet_port)
        read_client(sock, 1, timeout=1)
        self.drain_device()
        sock.sendall(bytes((0xff, 0xfa, 0x22, 0x03, 0x01, 0xff, 0xf0)))
        self.assertEqual(read_device(self.master_fd, 1, timeout=1), b'')


class TestUnixSocketPath(SerialPtyTestCase):
    """AF_UNIX shares the connection machinery with TCP.

    It is a separate accept path and a separate teardown (the socket
    file has to be unlinked), so it gets its own run through the loop.
    """

    sock_path = None

    @classmethod
    def port_servers(cls):
        cls.sock_path = os.path.join(
            tempfile.mkdtemp(prefix='ser2tcp-sock-'), 'port.sock')
        return [
            {'protocol': 'tcp', 'address': '127.0.0.1', 'port': cls.tcp_port},
            {'protocol': 'socket', 'address': cls.sock_path},
        ]

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(os.path.dirname(cls.sock_path), ignore_errors=True)

    def unix_connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.sock_path)
        self.addCleanup(sock.close)
        time.sleep(0.3)
        return sock

    def test_socket_file_is_created(self):
        self.assertTrue(os.path.exists(self.sock_path))

    def test_data_flows_both_ways(self):
        sock = self.unix_connect()
        sock.sendall(b'over unix')
        self.assertEqual(read_device(self.master_fd, 9), b'over unix')
        os.write(self.master_fd, b'back again')
        self.assertEqual(read_client(sock, 10), b'back again')

    def test_unix_and_tcp_clients_share_the_device(self):
        unix_sock = self.unix_connect()
        tcp_sock = self.connect()
        os.write(self.master_fd, b'shared')
        self.assertEqual(read_client(unix_sock, 6), b'shared')
        self.assertEqual(read_client(tcp_sock, 6), b'shared')


class TestPortReconfiguration(SerialPtyTestCase):
    """A port rebuilt through the API has to keep working.

    Everything a port owns — its listening sockets, its serial read
    source — registers itself in the loop's selector at construction.
    Rebuild it without that selector and it still reports itself as
    configured while accepting nothing and reading nothing. The old
    select() loop rescanned every socket each pass and so forgave the
    omission; this is the regression test for the day it did not.
    """

    def port_config(self, **extra):
        """The current port config, optionally with fields changed"""
        config = {
            'name': 'pty',
            'serial': {'port': self.pty_path, 'baudrate': 115200},
            'servers': [{
                'protocol': 'tcp', 'address': '127.0.0.1',
                'port': self.tcp_port,
            }],
        }
        config['servers'][0].update(extra)
        return config

    def update_port(self, **extra):
        status, body = self.put('/api/ports/' + self.port_id(), self.port_config(**extra))
        self.assertEqual(status, 200, body)

    def assert_data_flows(self, marker):
        """A client can talk to the device and hear it answer.

        The marker is read back with room to spare: with the control
        protocol on, the server may prepend a signal report (FF 8x),
        and whether it does depends on the signals having changed. What
        matters here is that the bytes make the round trip at all.
        """
        self.assertNotIn(0xFF, marker, 'marker must not need escaping')
        sock = self.connect()
        sock.sendall(marker)
        self.assertEqual(read_device(self.master_fd, len(marker)), marker)
        os.write(self.master_fd, marker)
        self.assertIn(marker, read_client_until(sock, marker))
        sock.close()
        self.wait_for_connections(0)

    def test_baseline_flows_before_any_change(self):
        """Named to sort first: the class shares one process, so a
        baseline taken after another test rebuilt the port would be
        measuring the damage rather than the starting point."""
        self.assert_data_flows(b'baseline')

    def test_data_flows_after_enabling_control(self):
        self.assert_data_flows(b'before01')
        self.update_port(control={'rts': True, 'dtr': True})
        self.assert_data_flows(b'\x01after1\x02')

    def test_data_flows_after_turning_control_back_off(self):
        self.update_port(control={'rts': True})
        self.update_port()
        self.assert_data_flows(b'restored')

    def test_data_flows_after_an_unrelated_change(self):
        self.update_port()
        self.assert_data_flows(b'unchanged')


if __name__ == '__main__':
    unittest.main()


class TestSlowDeviceBackpressure(SerialPtyTestCase):
    """A device drains at its baud rate; a client does not.

    Writing straight to the port blocked the one loop that serves every
    other port and the HTTP API - 256 KB at 9600 baud is minutes of
    that. Writes are buffered and driven by write interest, and once
    the queue backs up the clients stop being read so TCP makes them
    wait instead.
    """

    PAYLOAD = 512 * 1024

    def _push(self, sock, payload, seconds):
        """Send as much as the socket will take, without blocking"""
        sock.setblocking(False)
        sent = 0
        deadline = time.time() + seconds
        while sent < len(payload) and time.time() < deadline:
            try:
                sent += sock.send(payload[sent:sent + 65536])
            except BlockingIOError:
                time.sleep(0.01)
        sock.setblocking(True)
        return sent

    def test_the_api_answers_while_the_device_is_behind(self):
        sock = self.connect()
        self._push(sock, b'x' * self.PAYLOAD, 2)
        # Nothing has drained the pty, so whatever is queued is stuck.
        started = time.time()
        status, _ = self.get('/api/status', timeout=5)
        self.assertEqual(status, 200)
        self.assertLess(time.time() - started, 3)

    def test_another_port_still_moves_while_the_device_is_behind(self):
        sock = self.connect()
        self._push(sock, b'x' * self.PAYLOAD, 2)
        second = self.connect()
        os.write(self.master_fd, b'')  # no-op, keeps the pty alive
        self.assertEqual(self.connections_on(self.tcp_port), 2)
        second.close()

    def test_a_client_is_held_back_rather_than_losing_data(self):
        """What the server accepted must reach the device, in order"""
        sock = self.connect()
        payload = bytes(range(256)) * 256  # 64 KB, every byte distinct
        accepted = self._push(sock, payload, 2)
        self.assertGreater(accepted, 0)
        got = read_device(self.master_fd, accepted, timeout=20)
        self.assertEqual(got, payload[:len(got)])
        self.assertGreaterEqual(len(got), accepted)

    def _drain_until_quiet(self, timeout=25):
        """Read the pty until nothing more turns up, return the count"""
        total = 0
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not select.select([self.master_fd], [], [], 0.5)[0]:
                return total
            total += len(os.read(self.master_fd, 65536))
        return total

    def test_the_backlog_drains_once_the_device_reads(self):
        sock = self.connect()
        accepted = self._push(sock, b'y' * self.PAYLOAD, 2)
        drained = self._drain_until_quiet()
        self.assertGreaterEqual(drained, accepted)
        # And the port keeps working afterwards.
        sock.sendall(b'after')
        self.assertEqual(read_device(self.master_fd, 5, timeout=10), b'after')

    def test_reading_resumes_after_the_backlog_clears(self):
        """A paused client must not stay paused for good"""
        sock = self.connect()
        self._push(sock, b'z' * self.PAYLOAD, 2)
        self.drain_device()
        deadline = time.time() + 25
        while time.time() < deadline:
            if not select.select([self.master_fd], [], [], 0.1)[0]:
                break
            os.read(self.master_fd, 65536)
        sock.sendall(b'resumed')
        self.assertIn(b'resumed', read_device(self.master_fd, 7, timeout=10))


class FailedPortTestCase(base.IntegrationTestCase):
    """A config whose first port cannot bind, followed by one that can.

    Every port API path addresses a port by its position in the config.
    Dropping a failed one renumbers the rest, so an edit aimed at one
    rewrites another - and the failed port vanishes from the UI with no
    hint that it was ever configured.
    """

    good_port = None
    blocked_port = None
    blocker = None
    master_fd = None
    slave_fd = None

    @classmethod
    def build_config(cls):
        return {
            'ports': [
                {'name': 'wont-start',
                 'serial': {'port': '/dev/tty.not-a-real-device'},
                 'servers': [{'protocol': 'tcp', 'address': '127.0.0.1',
                              'port': cls.blocked_port}]},
                {'name': 'healthy',
                 'serial': {'port': cls.pty_path},
                 'servers': [{'protocol': 'tcp', 'address': '127.0.0.1',
                              'port': cls.good_port}]},
            ],
            'http': [{'address': '127.0.0.1', 'port': cls.port}],
        }

    @classmethod
    def setUpClass(cls):
        cls.good_port = base.free_port()
        cls.blocked_port = base.free_port()
        # The healthy port needs a device it can actually open, or
        # ser2tcp drops its clients as soon as it accepts them.
        cls.master_fd, cls.slave_fd = os.openpty()
        cls.pty_path = os.ttyname(cls.slave_fd)
        # Hold the first port's address so its server cannot bind.
        cls.blocker = socket.socket()
        cls.blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        cls.blocker.bind(('127.0.0.1', cls.blocked_port))
        cls.blocker.listen(1)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if cls.blocker is not None:
            cls.blocker.close()
        for fd in (cls.master_fd, cls.slave_fd):
            try:
                os.close(fd)
            except (OSError, TypeError):
                pass


class TestFailedPortIsStillListed(FailedPortTestCase):
    """What the status says about a port that never started"""

    def test_both_ports_are_listed(self):
        status, body = self.get('/api/status')
        self.assertEqual(status, 200)
        self.assertEqual(
            [p.get('name') for p in body['ports']], ['wont-start', 'healthy'])

    def test_the_failed_port_says_why(self):
        body = self.get('/api/status')[1]
        failed = body['ports'][0]
        self.assertEqual(failed['state'], 'error')
        self.assertTrue(failed.get('error'))

    def test_the_failed_port_serves_nothing(self):
        body = self.get('/api/status')[1]
        self.assertEqual(body['ports'][0]['servers'], [])

    def test_the_healthy_port_still_serves(self):
        sock = socket.create_connection(('127.0.0.1', self.good_port), 5)
        self.addCleanup(sock.close)
        body = self.get('/api/status')[1]
        self.assertEqual(len(body['ports'][1]['servers'][0]['connections']), 1)


class TestEditingAroundAFailedPort(FailedPortTestCase):
    """Editing by index, with a failed port in the list.

    Separate from the read-only checks: these rewrite the config, and
    the whole class shares one process.
    """

    def test_an_edit_reaches_the_port_it_names(self):
        """Index 1 is 'healthy' in the config and must be there too"""
        status, body = self.put('/api/ports/' + self.port_id(1), {
            'name': 'healthy-renamed',
            'serial': {'port': self.pty_path},
            'servers': [{'protocol': 'tcp', 'address': '127.0.0.1',
                         'port': self.good_port}],
        })
        self.assertEqual(status, 200, body)
        on_disk = self.proc.read_config()['ports']
        self.assertEqual(
            [p.get('name') for p in on_disk],
            ['wont-start', 'healthy-renamed'])

    def test_zz_a_failed_port_can_be_repaired_in_place(self):
        """It is visible, so it can be fixed without editing the file.

        Named to sort last: it rewrites port 0, and the class shares
        one process with the test above.
        """
        fresh = base.free_port()
        status, body = self.put('/api/ports/' + self.port_id(0), {
            'name': 'wont-start',
            'serial': {'port': '/dev/tty.not-a-real-device'},
            'servers': [{'protocol': 'tcp', 'address': '127.0.0.1',
                         'port': fresh}],
        })
        self.assertEqual(status, 200, body)
        listing = self.get('/api/status')[1]['ports']
        self.assertNotIn('error', listing[0])
        sock = socket.create_connection(('127.0.0.1', fresh), 5)
        self.addCleanup(sock.close)


class TestEditingKeepsEverySetting(base.IntegrationTestCase):
    """What the editor reads back has to be what was configured.

    It used to read /api/status, which is a different document: it
    reports no IP filters, no WebSocket tokens and no timeouts, and it
    reports serial settings as pyserial received them ('E', 7, 2) rather
    than as they were written ('EVEN', 'SEVENBITS', 'TWO'). Saving that
    back rewrote the port with defaults - 8N1, no filter - without
    saying anything.
    """

    tcp_port = None
    pty_path = None
    master_fd = None
    slave_fd = None

    @classmethod
    def build_config(cls):
        return {
            'ports': [{
                'id': 'fussy',
                'name': 'fussy-device',
                'serial': {
                    'port': cls.pty_path,
                    'baudrate': 19200,
                    'bytesize': 'SEVENBITS',
                    'parity': 'EVEN',
                    'stopbits': 'TWO',
                },
                'max_connections': 3,
                'servers': [{
                    'protocol': 'tcp',
                    'address': '127.0.0.1',
                    'port': cls.tcp_port,
                    'allow': ['127.0.0.0/8'],
                    'deny': ['127.0.0.9'],
                    'send_timeout': 12.5,
                    'buffer_limit': 65536,
                    'max_connections': 2,
                }]},
            ],
            'http': [{'address': '127.0.0.1', 'port': cls.port}],
        }

    @classmethod
    def setUpClass(cls):
        cls.master_fd, cls.slave_fd = os.openpty()
        cls.pty_path = os.ttyname(cls.slave_fd)
        cls.tcp_port = base.free_port()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        for fd in (cls.master_fd, cls.slave_fd):
            try:
                os.close(fd)
            except (OSError, TypeError):
                pass

    def test_the_config_endpoint_returns_what_was_written(self):
        status, cfg = self.get('/api/ports/fussy')
        self.assertEqual(status, 200)
        self.assertEqual(cfg['serial']['parity'], 'EVEN')
        self.assertEqual(cfg['serial']['bytesize'], 'SEVENBITS')
        self.assertEqual(cfg['serial']['stopbits'], 'TWO')
        self.assertEqual(cfg['servers'][0]['allow'], ['127.0.0.0/8'])
        self.assertEqual(cfg['servers'][0]['send_timeout'], 12.5)

    def test_the_status_is_a_different_document(self):
        """Why reading it for the editor was wrong in the first place"""
        body = self.get('/api/status')[1]
        serial = body['ports'][0]['serial']
        # Converted for pyserial, not as configured.
        self.assertEqual(serial['parity'], 'E')
        self.assertEqual(serial['bytesize'], 7)
        # And the filter is simply not reported.
        self.assertNotIn('allow', body['ports'][0]['servers'][0])

    def test_zz_a_rename_changes_the_name_and_nothing_else(self):
        """The round trip the editor makes, named to run last"""
        before = self.get('/api/ports/fussy')[1]
        edited = dict(before)
        edited['name'] = 'fussy-device-renamed'
        status, body = self.put('/api/ports/fussy', edited)
        self.assertEqual(status, 200, body)

        after = self.get('/api/ports/fussy')[1]
        self.assertEqual(after['name'], 'fussy-device-renamed')
        self.assertEqual(after['serial']['parity'], 'EVEN')
        self.assertEqual(after['serial']['bytesize'], 'SEVENBITS')
        self.assertEqual(after['serial']['stopbits'], 'TWO')
        self.assertEqual(after['serial']['baudrate'], 19200)
        self.assertEqual(after['max_connections'], 3)
        server = after['servers'][0]
        self.assertEqual(server['allow'], ['127.0.0.0/8'])
        self.assertEqual(server['deny'], ['127.0.0.9'])
        self.assertEqual(server['send_timeout'], 12.5)
        self.assertEqual(server['buffer_limit'], 65536)
        self.assertEqual(server['max_connections'], 2)
        # And on disk, which is what survives a restart.
        on_disk = self.proc.read_config()['ports'][0]
        self.assertEqual(on_disk['serial']['parity'], 'EVEN')
        self.assertEqual(on_disk['servers'][0]['allow'], ['127.0.0.0/8'])
