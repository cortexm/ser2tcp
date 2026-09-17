"""Integration tests for an SSL serial server, driven from the loop.

One selectors loop serves every port, every client and the HTTP API, so
anything that blocks in here stops all of it. TLS is where that is
easiest to trigger: the handshake talks to a client that has every
reason not to answer, and its records do not line up with socket reads.
"""

import os
import socket
import ssl
import time
import unittest

import ser2tcp.cert_manager as cert_manager

from tests.integration import base
from tests.integration.test_serial import SerialPtyTestCase, read_device


def _client_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class SslPortTestCase(SerialPtyTestCase):
    """A pty exposed over TLS as well as plain TCP."""

    ssl_port = None

    @classmethod
    def port_servers(cls):
        return [
            {'protocol': 'tcp', 'address': '127.0.0.1', 'port': cls.tcp_port},
            {'protocol': 'ssl', 'address': '127.0.0.1', 'port': cls.ssl_port,
                'ssl': {'bundle': 'web'}},
        ]

    @classmethod
    def prepare(cls, proc):
        cert, key = cert_manager.generate_certificate(
            'localhost', key_type='ec_p256', days=30)
        cert_manager.CertManager(proc.dir).save_files('web', [
            ('cert.pem', cert), ('key.pem', key)])

    @classmethod
    def setUpClass(cls):
        cls.ssl_port = base.free_port()
        super().setUpClass()

    def tls_connect(self, timeout=10):
        """Open a finished TLS connection to the serial port"""
        raw = socket.create_connection(('127.0.0.1', self.ssl_port), timeout)
        self.addCleanup(raw.close)
        tls = _client_context().wrap_socket(raw, server_hostname='localhost')
        self.addCleanup(tls.close)
        self.wait_for_serial()
        return tls

    def api_responds(self, timeout=3):
        """Whether the HTTP API answers within `timeout`"""
        try:
            status, _ = self.get('/api/status', timeout=timeout)
            return status == 200
        except Exception:  # pylint: disable=W0703
            return False


class TestTlsDataPath(SslPortTestCase):
    """The ordinary case still works end to end"""

    def test_client_data_reaches_the_device(self):
        tls = self.tls_connect()
        tls.sendall(b'over tls')
        self.assertEqual(read_device(self.master_fd, 8), b'over tls')

    def test_device_data_reaches_the_client(self):
        tls = self.tls_connect()
        os.write(self.master_fd, b'to the client')
        tls.settimeout(5)
        self.assertEqual(tls.recv(13), b'to the client')

    def test_a_large_record_arrives_whole(self):
        """One TLS record can hold far more than one recv() returns.

        Decrypted bytes past the first read sit in the SSL object, not
        in the socket, so select() never mentions them again - the rest
        of the write used to hang until the client happened to send
        something else.
        """
        tls = self.tls_connect()
        payload = bytes(range(256)) * 48  # 12288 bytes, one sendall
        tls.sendall(payload)
        self.assertEqual(
            read_device(self.master_fd, len(payload), timeout=5), payload)


class TestTlsDoesNotBlockTheLoop(SslPortTestCase):
    """A client that will not handshake must cost only itself.

    The handshake used to run inline on a blocking socket, so a bare TCP
    connect - no certificate, no authentication, nothing - froze every
    serial port and the whole API until that socket went away.
    """

    def test_a_silent_client_does_not_freeze_the_api(self):
        sock = socket.create_connection(('127.0.0.1', self.ssl_port), 5)
        self.addCleanup(sock.close)
        time.sleep(0.3)
        self.assertTrue(self.api_responds())

    def test_a_silent_client_does_not_freeze_the_other_ports(self):
        sock = socket.create_connection(('127.0.0.1', self.ssl_port), 5)
        self.addCleanup(sock.close)
        time.sleep(0.3)
        plain = self.connect()
        plain.sendall(b'still moving')
        self.assertEqual(read_device(self.master_fd, 12), b'still moving')

    def test_several_silent_clients_do_not_freeze_the_api(self):
        for _ in range(5):
            sock = socket.create_connection(('127.0.0.1', self.ssl_port), 5)
            self.addCleanup(sock.close)
        time.sleep(0.3)
        self.assertTrue(self.api_responds())

    def test_half_sent_handshake_does_not_freeze_the_api(self):
        """A few bytes of a ClientHello and then nothing"""
        sock = socket.create_connection(('127.0.0.1', self.ssl_port), 5)
        self.addCleanup(sock.close)
        sock.sendall(b'\x16\x03\x01\x00\x2a\x01')
        time.sleep(0.3)
        self.assertTrue(self.api_responds())

    def test_a_real_client_still_gets_through_alongside_them(self):
        stalled = socket.create_connection(('127.0.0.1', self.ssl_port), 5)
        self.addCleanup(stalled.close)
        time.sleep(0.2)
        tls = self.tls_connect()
        tls.sendall(b'unblocked')
        self.assertEqual(read_device(self.master_fd, 9), b'unblocked')

    def test_a_stalled_handshake_is_eventually_dropped(self):
        """Otherwise a silent connection holds a slot for good"""
        sock = socket.create_connection(('127.0.0.1', self.ssl_port), 5)
        self.addCleanup(sock.close)
        sock.settimeout(20)
        # send_timeout defaults to 5s; the server should hang up on its
        # own well inside that plus a margin.
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                if sock.recv(1) == b'':
                    return
            except socket.timeout:
                break
            except OSError:
                return
        self.fail('server never dropped the stalled handshake')


if __name__ == '__main__':
    unittest.main()
