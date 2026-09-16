"""Tests for Server (socket-level behaviour that needs a real bind)"""

import os
import shutil
import socket
import tempfile
import unittest
from unittest.mock import Mock

from ser2tcp.cert_manager import CertManager, generate_certificate
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
