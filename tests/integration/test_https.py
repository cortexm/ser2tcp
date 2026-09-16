"""Integration tests for HTTPS, mTLS and reloading a bundle in place.

A real TLS handshake is the only thing that proves the bundle wiring is
right: which certificate the server actually presents, whether a client
certificate is demanded, and whether a renewed cert is picked up without
a restart.
"""

import os
import ssl
import unittest

import ser2tcp.cert_manager as cert_manager



from tests.integration import base


def _gen(cn, **kwargs):
    """Generate a certificate for a test.

    EC keys keep the suite several times faster than RSA and nothing
    here depends on the key algorithm.
    """
    kwargs.setdefault('key_type', 'ec_p256')
    kwargs.setdefault('days', 30)
    return cert_manager.generate_certificate(cn, **kwargs)


def _cn_of(pem):
    return cert_manager.inspect_certificate(pem)['subject_cn']


def _serial_of(pem):
    return cert_manager.inspect_certificate(pem)['serial']


def _write(path, content):
    with open(path, 'w', encoding='utf-8') as file:
        file.write(content)


class TestHttpsServer(base.IntegrationTestCase):
    """A bundle-backed HTTPS server serves that bundle's certificate."""

    scheme = 'https'
    tls_context = None

    @classmethod
    def build_config(cls):
        return {
            'ports': [],
            'http': [{
                'name': 'secure', 'address': '127.0.0.1', 'port': cls.port,
                'ssl': {'bundle': 'web'},
            }],
        }

    @classmethod
    def prepare(cls, proc):
        cls.tls_context = base.unverified_context()
        cert, key = _gen(
            'server.local', san_dns=['localhost', 'server.local'])
        cert_manager.CertManager(proc.dir).save_files(
            'web', [('cert.pem', cert), ('key.pem', key)])

    def test_serves_the_bundle_certificate(self):
        pem = base.tls_peer_cert('127.0.0.1', self.port)
        self.assertEqual(_cn_of(pem), 'server.local')

    def test_api_works_over_tls(self):
        status, body = self.get('/api/status')
        self.assertEqual(status, 200)
        self.assertIn('ports', body)


class TestCertChainValidates(base.IntegrationTestCase):
    """A server cert signed by a CA validates against that CA, hostname
    checked against the SAN."""

    scheme = 'https'
    tls_context = None

    @classmethod
    def build_config(cls):
        return {
            'ports': [],
            'http': [{
                'address': '127.0.0.1', 'port': cls.port,
                'ssl': {'bundle': 'web'},
            }],
        }

    @classmethod
    def prepare(cls, proc):
        cls.tls_context = base.unverified_context()
        ca_cert, ca_key = _gen('Integration Root CA', is_ca=True)
        signer = cert_manager.parse_signer(ca_cert, ca_key)
        cert, key = _gen(
            'localhost', san_dns=['localhost'],
            san_ip=['127.0.0.1'], signer=signer)
        cert_manager.CertManager(proc.dir).save_files(
            'web', [('cert.pem', cert), ('key.pem', key)])
        cls.ca_file = os.path.join(proc.dir, 'ca-for-client.pem')
        _write(cls.ca_file, ca_cert)

    def test_chain_and_hostname_verify(self):
        pem = base.tls_peer_cert('localhost', self.port, cafile=self.ca_file)
        self.assertEqual(_cn_of(pem), 'localhost')

    def test_unrelated_ca_is_rejected(self):
        other_cert, _ = _gen('Unrelated CA', is_ca=True)
        other_file = os.path.join(self.proc.dir, 'unrelated.pem')
        _write(other_file, other_cert)
        with self.assertRaises(ssl.SSLError):
            base.tls_peer_cert('localhost', self.port, cafile=other_file)


class TestMutualTls(base.IntegrationTestCase):
    """require_client_cert makes the server demand a client certificate."""

    scheme = 'https'
    tls_context = None

    @classmethod
    def build_config(cls):
        return {
            'ports': [],
            'http': [{
                'address': '127.0.0.1', 'port': cls.port,
                'ssl': {'bundle': 'web', 'require_client_cert': True},
            }],
        }

    @classmethod
    def prepare(cls, proc):
        ca_cert, ca_key = _gen('mTLS Root CA', is_ca=True)
        signer = cert_manager.parse_signer(ca_cert, ca_key)
        cert, key = _gen('localhost', san_dns=['localhost'], signer=signer)
        cert_manager.CertManager(proc.dir).save_files('web', [
            ('cert.pem', cert), ('key.pem', key), ('ca.pem', ca_cert)])
        client_cert, client_key = _gen(
            'operator', is_client=True, signer=signer)
        cls.client_cert_file = os.path.join(proc.dir, 'client-cert.pem')
        cls.client_key_file = os.path.join(proc.dir, 'client-key.pem')
        _write(cls.client_cert_file, client_cert)
        _write(cls.client_key_file, client_key)
        cls.tls_context = base.unverified_context()
        cls.tls_context.load_cert_chain(
            cls.client_cert_file, cls.client_key_file)

    def test_handshake_without_client_cert_fails(self):
        with self.assertRaises(OSError):
            base.tls_peer_cert('127.0.0.1', self.port)

    def test_handshake_with_client_cert_succeeds(self):
        pem = base.tls_peer_cert(
            '127.0.0.1', self.port,
            client_cert=self.client_cert_file,
            client_key=self.client_key_file)
        self.assertEqual(_cn_of(pem), 'localhost')

    def test_api_works_with_client_cert(self):
        status, _ = self.get('/api/status')
        self.assertEqual(status, 200)


class TestBundleReload(base.IntegrationTestCase):
    """POST /api/certs/<bundle>/reload swaps the live certificate."""

    scheme = 'https'
    tls_context = None

    @classmethod
    def build_config(cls):
        return {
            'ports': [],
            'http': [{
                'address': '127.0.0.1', 'port': cls.port,
                'ssl': {'bundle': 'web'},
            }],
        }

    @classmethod
    def prepare(cls, proc):
        cls.tls_context = base.unverified_context()
        cert, key = _gen('before.local')
        cert_manager.CertManager(proc.dir).save_files(
            'web', [('cert.pem', cert), ('key.pem', key)])

    def test_renewed_cert_takes_effect_without_restart(self):
        served = base.tls_peer_cert('127.0.0.1', self.port)
        self.assertEqual(_cn_of(served), 'before.local')
        first_serial = _serial_of(served)

        new_cert, new_key = _gen('after.local')
        status, _ = self.post(
            '/api/certs/web/files',
            {'files': [
                {'filename': 'cert.pem', 'content': new_cert},
                {'filename': 'key.pem', 'content': new_key},
            ]})
        self.assertEqual(status, 200)

        # On disk, but the running server still holds the old context.
        still_old = base.tls_peer_cert('127.0.0.1', self.port)
        self.assertEqual(_serial_of(still_old), first_serial)

        status, body = self.post('/api/certs/web/reload')
        self.assertEqual(status, 200)
        self.assertEqual(body['reloaded'], [f'http 127.0.0.1:{self.port}'])

        served = base.tls_peer_cert('127.0.0.1', self.port)
        self.assertEqual(_cn_of(served), 'after.local')
        self.assertNotEqual(_serial_of(served), first_serial)


if __name__ == '__main__':
    unittest.main()
