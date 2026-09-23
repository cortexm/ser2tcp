"""Integration tests for HTTPS, mTLS and reloading a bundle in place.

A real TLS handshake is the only thing that proves the bundle wiring is
right: which certificate the server actually presents, whether a client
certificate is demanded, and whether a renewed cert is picked up without
a restart.
"""

import os
import socket
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



class TestAFailedReloadKeepsTheServerServing(base.IntegrationTestCase):
    """A half-finished renewal must not take the HTTPS server down.

    Files arriving one at a time is what a renewal looks like while it
    is in progress - a deploy hook mid-copy, an scp, a symlink swung
    over before its partner. The reload has to refuse that and leave
    the server exactly as it was.

    The API is reached over plain HTTP here on purpose: asking the
    broken server whether it is broken would not work.
    """

    tls_port = None

    @classmethod
    def build_config(cls):
        cls.tls_port = base.free_port()
        return {
            'ports': [],
            'http': [
                {'address': '127.0.0.1', 'port': cls.port},
                {'address': '127.0.0.1', 'port': cls.tls_port,
                 'ssl': {'bundle': 'web'}},
            ],
        }

    @classmethod
    def prepare(cls, proc):
        cert, key = _gen('serving.local')
        cert_manager.CertManager(proc.dir).save_files(
            'web', [('cert.pem', cert), ('key.pem', key)])

    def _cert_path(self, name='cert.pem'):
        return os.path.join(self.proc.dir, 'certs', 'web', name)

    def test_the_server_starts_out_serving_its_bundle(self):
        served = base.tls_peer_cert('127.0.0.1', self.tls_port)
        self.assertEqual(_cn_of(served), 'serving.local')

    def test_zz_a_cert_without_its_key_is_refused_and_changes_nothing(self):
        before = _serial_of(base.tls_peer_cert('127.0.0.1', self.tls_port))

        # Only half the renewal lands on disk.
        renewed, _unused_key = _gen('renewed.local')
        _write(self._cert_path(), renewed)

        status, body = self.post('/api/certs/web/reload')
        self.assertEqual(status, 400, body)

        served = base.tls_peer_cert('127.0.0.1', self.tls_port)
        self.assertEqual(_cn_of(served), 'serving.local')
        self.assertEqual(_serial_of(served), before)

    def test_zzz_the_other_half_arriving_completes_the_renewal(self):
        renewed, renewed_key = _gen('renewed.local')
        _write(self._cert_path(), renewed)
        self.assertEqual(self.post('/api/certs/web/reload')[0], 400)
        _write(self._cert_path('key.pem'), renewed_key)
        status, body = self.post('/api/certs/web/reload')
        self.assertEqual(status, 200, body)
        self.assertEqual(
            _cn_of(base.tls_peer_cert('127.0.0.1', self.tls_port)),
            'renewed.local')


class TestPortSslBundleUsage(base.IntegrationTestCase):
    """A port SSL server reports its bundle usage and accepts a reload.

    The HTTP API here is plain — what is under test is the port server
    on the other side of it.
    """

    ssl_port = None

    @classmethod
    def build_config(cls):
        return {
            'ports': [{
                'name': 'demo',
                'serial': {'port': '/dev/tty.not-a-real-device',
                    'baudrate': 9600},
                'servers': [{
                    'protocol': 'ssl',
                    'address': '127.0.0.1', 'port': cls.ssl_port,
                    'ssl': {'bundle': 'web', 'require_client_cert': True},
                }],
            }],
            'http': [{'address': '127.0.0.1', 'port': cls.port}],
        }

    @classmethod
    def prepare(cls, proc):
        ca_cert, ca_key = _gen('Port CA', is_ca=True)
        signer = cert_manager.parse_signer(ca_cert, ca_key)
        cert, key = _gen('localhost', san_dns=['localhost'], signer=signer)
        cert_manager.CertManager(proc.dir).save_files('web', [
            ('cert.pem', cert), ('key.pem', key), ('ca.pem', ca_cert)])
        client_cert, client_key = _gen(
            'operator', is_client=True, signer=signer)
        cls.client_cert_file = os.path.join(proc.dir, 'port-client-cert.pem')
        cls.client_key_file = os.path.join(proc.dir, 'port-client-key.pem')
        _write(cls.client_cert_file, client_cert)
        _write(cls.client_key_file, client_key)

    @classmethod
    def setUpClass(cls):
        cls.ssl_port = base.free_port()
        super().setUpClass()

    def test_used_by_reports_the_port_server(self):
        status, body = self.get('/api/certs/web')
        self.assertEqual(status, 200)
        self.assertEqual(len(body['used_by']), 1)
        usage = body['used_by'][0]
        self.assertEqual(usage['type'], 'port')
        self.assertEqual(usage['port_name'], 'demo')
        self.assertEqual(usage['server_port'], self.ssl_port)
        # ca.pem is in play for this server, so the UI can warn before
        # anyone deletes it
        self.assertTrue(usage['mtls'])

    def test_bundle_in_use_cannot_be_deleted(self):
        status, body = self.delete('/api/certs/web')
        self.assertEqual(status, 400)
        self.assertIn('in use', body['error'])

    def test_reload_reaches_the_port_server(self):
        status, body = self.post('/api/certs/web/reload')
        self.assertEqual(status, 200)
        self.assertEqual(
            body['reloaded'], [f'port 127.0.0.1:{self.ssl_port}'])

    def test_port_server_serves_the_bundle_cert(self):
        # The configured serial device does not exist, so ser2tcp drops
        # the client right after the handshake — which is far enough to
        # see whose certificate it offered. mTLS rejection itself is
        # covered by TestMutualTls, against a server that stays up.
        pem = base.tls_peer_cert(
            '127.0.0.1', self.ssl_port,
            client_cert=self.client_cert_file,
            client_key=self.client_key_file,
            probe=False)
        self.assertEqual(_cn_of(pem), 'localhost')


if __name__ == '__main__':
    unittest.main()


class TestEditingAnHttpServer(base.IntegrationTestCase):
    """What an edit through the API does to an HTTP server entry.

    The entry was rebuilt from a handful of known keys, so anything else
    it carried - an IP filter, most of all - was dropped by an edit that
    never mentioned it. And the config file was written before the
    server was rebuilt, so a rebuild that failed left the file already
    saying something the process was not doing.
    """

    second_port = None

    @classmethod
    def build_config(cls):
        return {
            'ports': [],
            'http': [
                {'id': 'main', 'name': 'main',
                 'address': '127.0.0.1', 'port': cls.port},
                {'id': 'guarded', 'name': 'guarded',
                 'address': '127.0.0.1', 'port': cls.second_port,
                 'allow': ['127.0.0.0/8'], 'deny': ['127.0.0.9']},
            ],
        }

    @classmethod
    def setUpClass(cls):
        cls.second_port = base.free_port()
        super().setUpClass()

    def _entry(self, server_id):
        servers = self.get('/api/settings')[1]['http']
        if isinstance(servers, dict):
            servers = [servers]
        for server in servers:
            if server.get('id') == server_id:
                return server
        raise AssertionError(f'no HTTP server {server_id!r}')

    # Numbered: the class shares one process and the later tests
    # rewrite the entry the earlier ones read.
    def test_1_the_filter_is_reported(self):
        self.assertEqual(self._entry('guarded')['allow'], ['127.0.0.0/8'])

    def test_2_a_rename_keeps_the_filter(self):
        entry = dict(self._entry('guarded'))
        entry['name'] = 'guarded-renamed'
        status, body = self.put('/api/settings/http/guarded', entry)
        self.assertEqual(status, 200, body)
        after = self._entry('guarded')
        self.assertEqual(after['name'], 'guarded-renamed')
        self.assertEqual(after['allow'], ['127.0.0.0/8'])
        self.assertEqual(after['deny'], ['127.0.0.9'])

    def test_4_a_filter_can_be_removed_on_purpose(self):
        """Carrying settings over must not make them impossible to drop"""
        entry = dict(self._entry('guarded'))
        entry.pop('allow', None)
        entry.pop('deny', None)
        status, body = self.put('/api/settings/http/guarded', entry)
        self.assertEqual(status, 200, body)
        after = self._entry('guarded')
        self.assertNotIn('allow', after)
        self.assertNotIn('deny', after)

    @base.requires_bind_conflict
    def test_3_a_rebuild_that_fails_does_not_get_written(self):
        """The file must not promise what the process could not do"""
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(('127.0.0.1', 0))
        blocker.listen(1)
        taken = blocker.getsockname()[1]
        try:
            entry = dict(self._entry('guarded'))
            entry['port'] = taken
            status, _body = self.put('/api/settings/http/guarded', entry)
            self.assertEqual(status, 400)
            on_disk = [s for s in self.proc.read_config()['http']
                       if s.get('id') == 'guarded'][0]
            self.assertEqual(on_disk['port'], self.second_port)
            # And the server it had is still answering.
            probe = socket.create_connection(
                ('127.0.0.1', self.second_port), 5)
            probe.close()
        finally:
            blocker.close()
