"""Tests for Server (socket-level behaviour that needs a real bind)"""

import os
import shutil
import socket
import tempfile
import time
import unittest
from unittest.mock import Mock

from ser2tcp.cert_manager import CertManager, generate_certificate
from ser2tcp.connection_ssl import SslHandshakeError
from ser2tcp.server import Server


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class TestServerReloadSslContext(unittest.TestCase):
    """reload_ssl_context() re-reads the bundle into the live context."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.certs_dir = os.path.join(self.tmp, 'certs')
        self.mgr = CertManager(self.tmp)
        cert, key = generate_certificate(
            'before', key_type='ec_p256', days=30)
        self.mgr.save_files('web', [('cert.pem', cert), ('key.pem', key)])
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _server(self, config):
        srv = Server(config, Mock(), Mock(), certs_dir=self.certs_dir)
        self.servers.append(srv)
        return srv

    def test_ssl_server_reloads(self):
        srv = self._server({
            'address': '127.0.0.1', 'port': free_port(), 'protocol': 'ssl',
            'ssl': {'bundle': 'web'},
        })
        context = srv._ssl_context
        cert, key = generate_certificate('after', key_type='ec_p256', days=30)
        self.mgr.save_files('web', [('cert.pem', cert), ('key.pem', key)])
        self.assertTrue(srv.reload_ssl_context())
        # Reloaded in place: the same context object serves the new cert,
        # so connections already negotiated are undisturbed.
        self.assertIs(srv._ssl_context, context)

    def test_plain_server_has_nothing_to_reload(self):
        srv = self._server({
            'address': '127.0.0.1', 'port': free_port(), 'protocol': 'tcp',
        })
        self.assertFalse(srv.reload_ssl_context())

    def test_reload_raises_when_bundle_gone(self):
        from ser2tcp.cert_manager import CertManagerError
        srv = self._server({
            'address': '127.0.0.1', 'port': free_port(), 'protocol': 'ssl',
            'ssl': {'bundle': 'web'},
        })
        self.mgr.delete_bundle('web')
        with self.assertRaises(CertManagerError):
            srv.reload_ssl_context()


if __name__ == '__main__':
    unittest.main()


class TestAcceptErrors(unittest.TestCase):
    """accept() can fail, and the listening socket stays readable.

    A connection that failed to be accepted is still queued, so select()
    reports the listener ready again on the very next pass. Logging and
    returning turns one failure into a permanent 100% CPU spin.
    """

    def setUp(self):
        self.log = Mock()
        self.selector = Mock()
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.close()

    def _server(self):
        srv = Server(
            {'address': '127.0.0.1', 'port': free_port(), 'protocol': 'tcp'},
            Mock(), self.log, selector=self.selector)
        self.servers.append(srv)
        return srv

    def _accept_raising(self, err):
        srv = self._server()
        srv._socket = Mock()
        srv._socket.accept.side_effect = err
        return srv

    def test_a_dropped_connection_is_not_fatal(self):
        """The peer gave up between select() and accept()"""
        srv = self._accept_raising(ConnectionAbortedError('gone'))
        srv._client_connect()
        self.assertEqual(srv.connections, [])

    def test_a_spurious_readiness_is_not_fatal(self):
        srv = self._accept_raising(BlockingIOError())
        srv._client_connect()
        self.assertEqual(srv.connections, [])

    def test_running_out_of_descriptors_is_not_fatal(self):
        srv = self._accept_raising(OSError(24, 'Too many open files'))
        srv._client_connect()
        self.assertEqual(srv.connections, [])

    def test_running_out_of_descriptors_stops_the_spin(self):
        """Out of fds, the listener is put down rather than hammered.

        The queued connection cannot be accepted and cannot be drained,
        so the only way off the merry-go-round is to stop watching the
        listening socket for a moment.
        """
        srv = self._accept_raising(OSError(24, 'Too many open files'))
        srv._client_connect()
        self.selector.unregister.assert_called_once_with(srv._socket)

    def test_the_listener_comes_back_on_its_own(self):
        srv = self._accept_raising(OSError(24, 'Too many open files'))
        srv._client_connect()
        self.selector.register.reset_mock()
        # Nothing happens while the cooldown runs...
        srv.process_stale()
        self.selector.register.assert_not_called()
        # ...and the listener is re-armed once it is over.
        srv._accept_paused_until = time.time() - 1
        srv.process_stale()
        self.selector.register.assert_called_once()

    def test_an_ordinary_failure_does_not_pause_the_listener(self):
        """Only descriptor exhaustion can spin; the rest self-clear"""
        srv = self._accept_raising(ConnectionAbortedError('gone'))
        srv._client_connect()
        self.selector.unregister.assert_not_called()

    def test_the_failure_is_logged(self):
        srv = self._accept_raising(OSError(24, 'Too many open files'))
        srv._client_connect()
        self.assertTrue(self.log.warning.called or self.log.error.called)


class TestReadErrors(unittest.TestCase):
    """recv() fails in more ways than a reset connection"""

    def setUp(self):
        self.log = Mock()
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.close()

    def _server_with_connection(self):
        srv = Server(
            {'address': '127.0.0.1', 'port': free_port(), 'protocol': 'tcp'},
            Mock(), self.log)
        self.servers.append(srv)
        con = Mock()
        con.address_str.return_value = '10.0.0.1:1234'
        con.is_closed.return_value = False
        con.needs_handshake.return_value = False
        con.pending.return_value = 0
        sock = Mock()
        con.socket.return_value = sock
        srv._connections.append(con)
        srv._conn_by_socket[sock] = con
        return srv, con, sock

    def _read_raising(self, err):
        srv, con, _sock = self._server_with_connection()
        con.recv.side_effect = err
        srv._read(con)
        return srv

    def test_a_reset_connection_is_dropped(self):
        srv = self._read_raising(ConnectionResetError('reset'))
        self.assertEqual(srv.connections, [])

    def test_a_timeout_is_dropped(self):
        srv = self._read_raising(TimeoutError('timed out'))
        self.assertEqual(srv.connections, [])

    def test_a_closed_descriptor_is_dropped(self):
        """Another handler in the same batch can close this socket"""
        srv = self._read_raising(OSError(9, 'Bad file descriptor'))
        self.assertEqual(srv.connections, [])

    def test_an_aborted_connection_is_dropped(self):
        srv = self._read_raising(ConnectionAbortedError('aborted'))
        self.assertEqual(srv.connections, [])

    def test_a_peer_that_went_away_is_dropped(self):
        srv, con, _ = self._server_with_connection()
        con.recv.return_value = b''
        srv._read(con)
        self.assertEqual(srv.connections, [])

    def test_nothing_to_read_right_now_is_not_an_error(self):
        """A non-blocking socket says "later" by returning None"""
        srv, con, _ = self._server_with_connection()
        con.recv.return_value = None
        srv._read(con)
        self.assertEqual(len(srv.connections), 1)
        con.on_received.assert_not_called()

    def test_a_connection_closed_earlier_in_the_batch_is_reaped(self):
        """Its socket is already None, so recv() would fail on None"""
        srv, con, sock = self._server_with_connection()
        con.is_closed.return_value = True
        con.socket.return_value = None
        srv.handle_event(sock, 1)
        self.assertEqual(srv.connections, [])

    def test_a_healthy_read_is_forwarded(self):
        srv, con, _ = self._server_with_connection()
        con.recv.side_effect = [b'hello', None]
        srv._read(con)
        con.on_received.assert_called_once_with(b'hello')
        self.assertEqual(len(srv.connections), 1)

    def test_buffered_bytes_are_drained_in_the_same_pass(self):
        """TLS keeps the rest of a record where select() cannot see it"""
        srv, con, _ = self._server_with_connection()
        con.recv.side_effect = [b'first', b'second', b'third']
        con.pending.side_effect = [4096, 4096, 0]
        srv._read(con)
        self.assertEqual(
            [c.args[0] for c in con.on_received.call_args_list],
            [b'first', b'second', b'third'])

    def test_draining_stops_when_the_port_takes_the_client_down(self):
        """A serial failure inside on_received drops every client"""
        srv, con, _ = self._server_with_connection()
        con.recv.return_value = b'data'
        con.pending.return_value = 4096
        con.on_received.side_effect = lambda _d: setattr(
            con.is_closed, 'return_value', True)
        srv._read(con)
        con.on_received.assert_called_once()

    def test_a_plain_socket_is_read_once_per_event(self):
        """Nothing is buffered above the kernel, so select() will say"""
        srv, con, _ = self._server_with_connection()
        con.recv.return_value = b'data'
        con.pending.return_value = 0
        srv._read(con)
        self.assertEqual(con.recv.call_count, 1)


class TestHandshakeDispatch(unittest.TestCase):
    """A connection that is still negotiating is not a client yet"""

    def setUp(self):
        self.log = Mock()
        self.serial = Mock()
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.close()

    def _server_with_handshaking_connection(self):
        srv = Server(
            {'address': '127.0.0.1', 'port': free_port(), 'protocol': 'tcp'},
            self.serial, self.log)
        self.servers.append(srv)
        con = Mock()
        con.address_str.return_value = '10.0.0.1:1234'
        con.is_closed.return_value = False
        con.needs_handshake.return_value = True
        sock = Mock()
        con.socket.return_value = sock
        srv._connections.append(con)
        srv._conn_by_socket[sock] = con
        return srv, con, sock

    def test_an_event_advances_the_handshake_and_nothing_else(self):
        srv, con, sock = self._server_with_handshaking_connection()
        con.handshake.return_value = False
        srv.handle_event(sock, 1)
        con.handshake.assert_called_once()
        con.recv.assert_not_called()

    def test_the_device_is_not_opened_until_the_handshake_lands(self):
        srv, con, sock = self._server_with_handshaking_connection()
        con.handshake.return_value = False
        srv.handle_event(sock, 1)
        self.serial.connect.assert_not_called()
        self.assertEqual(len(srv.connections), 1)

    def test_a_finished_handshake_opens_the_device(self):
        srv, con, sock = self._server_with_handshaking_connection()
        con.handshake.return_value = True
        self.serial.connect.return_value = True
        srv.handle_event(sock, 1)
        self.serial.connect.assert_called_once()
        self.assertEqual(len(srv.connections), 1)

    def test_a_failed_handshake_drops_the_connection(self):
        srv, con, sock = self._server_with_handshaking_connection()
        con.handshake.side_effect = SslHandshakeError('no shared cipher')
        srv.handle_event(sock, 1)
        self.assertEqual(srv.connections, [])
        self.serial.connect.assert_not_called()

    def test_a_device_that_will_not_open_drops_the_connection(self):
        srv, con, sock = self._server_with_handshaking_connection()
        con.handshake.return_value = True
        self.serial.connect.return_value = False
        srv.handle_event(sock, 1)
        self.assertEqual(srv.connections, [])


