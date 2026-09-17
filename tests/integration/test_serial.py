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
        status, body = self.put('/api/ports/0', self.port_config(**extra))
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
