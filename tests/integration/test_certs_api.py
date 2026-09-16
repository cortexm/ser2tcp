"""Integration tests for /api/certs/* over a real HTTP server.

Every path the cert router understands is exercised here. The unit
tests call the handlers directly, so a handler that exists but is
unreachable — the failure mode that actually happened during
development — looks green there and red here.
"""

from tests.integration import base

# EC keys keep the suite fast; the routing under test does
# not depend on the key algorithm.
EC = 'ec_p256'


class TestCertsApiRouting(base.IntegrationTestCase):
    """Each route reachable, each method guarded, no auth configured."""

    def test_list_bundles(self):
        status, body = self.get('/api/certs')
        self.assertEqual(status, 200)
        self.assertIn('certs_dir', body)
        self.assertIsInstance(body['bundles'], list)

    def test_create_get_and_delete_bundle(self):
        status, _ = self.post('/api/certs', {'name': 'crud'})
        self.assertEqual(status, 201)
        status, body = self.get('/api/certs/crud')
        self.assertEqual(status, 200)
        self.assertEqual(body['name'], 'crud')
        self.assertFalse(body['files']['cert.pem']['present'])
        status, _ = self.delete('/api/certs/crud')
        self.assertEqual(status, 200)
        status, _ = self.get('/api/certs/crud')
        self.assertEqual(status, 404)

    def test_generate_self_signed_into_bundle(self):
        """Left on the default key type, so the API default is covered."""
        status, _ = self.post(
            '/api/certs/gen/generate',
            {'mode': 'self_signed', 'cn': 'gen.local', 'days': 30})
        self.assertEqual(status, 201)
        status, body = self.get('/api/certs/gen')
        self.assertEqual(status, 200)
        self.assertTrue(body['files']['cert.pem']['present'])
        self.assertTrue(body['files']['key.pem']['present'])
        self.assertTrue(body['key_match'])
        self.assertEqual(
            body['files']['cert.pem']['cert_info']['subject_cn'], 'gen.local')

    def test_generate_refuses_to_overwrite(self):
        status, _ = self.post(
            '/api/certs/noclobber/generate',
            {'mode': 'self_signed', 'cn': 'first.local', 'key_type': EC})
        self.assertEqual(status, 201)
        status, body = self.post(
            '/api/certs/noclobber/generate',
            {'mode': 'self_signed', 'cn': 'second.local', 'key_type': EC})
        self.assertEqual(status, 400)
        self.assertIn('already has cert.pem', body['error'])

    def test_generate_ca_and_sign_server_cert(self):
        status, _ = self.post(
            '/api/certs/rootca/generate', {'mode': 'ca', 'cn': 'Test Root CA', 'key_type': EC})
        self.assertEqual(status, 201)
        status, body = self.get('/api/certs/rootca')
        self.assertTrue(body['files']['cert.pem']['cert_info']['is_ca'])
        status, _ = self.post(
            '/api/certs/signed/generate',
            {'mode': 'signed_by', 'cn': 'signed.local',
                'signer_bundle': 'rootca', 'san_dns': ['signed.local']})
        self.assertEqual(status, 201)
        status, body = self.get('/api/certs/signed')
        info = body['files']['cert.pem']['cert_info']
        self.assertEqual(info['issuer_cn'], 'Test Root CA')
        self.assertFalse(info['self_signed'])
        self.assertEqual(info['san_dns'], ['signed.local'])
        # signed_by copies the signer cert in, so the bundle is mTLS-ready
        self.assertTrue(body['files']['ca.pem']['present'])

    def test_generate_signed_by_requires_ca_signer(self):
        status, _ = self.post(
            '/api/certs/leaf/generate',
            {'mode': 'self_signed', 'cn': 'leaf.local', 'key_type': EC})
        self.assertEqual(status, 201)
        status, body = self.post(
            '/api/certs/wantsca/generate',
            {'mode': 'signed_by', 'cn': 'x.local', 'signer_bundle': 'leaf', 'key_type': EC})
        self.assertEqual(status, 400)
        self.assertIn('not a CA', body['error'])

    def test_generate_client_returns_pem_and_stores_nothing(self):
        status, _ = self.post(
            '/api/certs/clientca/generate',
            {'mode': 'ca', 'cn': 'Client Issuing CA', 'key_type': EC})
        self.assertEqual(status, 201)
        status, body = self.post(
            '/api/certs/generate-client',
            {'cn': 'operator', 'signer_bundle': 'clientca', 'key_type': EC})
        self.assertEqual(status, 200)
        for field in ('cert_pem', 'key_pem', 'ca_pem'):
            self.assertIn('-----BEGIN', body[field])
        status, listing = self.get('/api/certs')
        names = [b['name'] for b in listing['bundles']]
        self.assertNotIn('operator', names)

    def test_download_public_file_and_reject_key(self):
        status, _ = self.post(
            '/api/certs/dl/generate',
            {'mode': 'self_signed', 'cn': 'dl.local', 'key_type': EC})
        self.assertEqual(status, 201)
        status, body = self.get('/api/certs/dl/files/cert.pem')
        self.assertEqual(status, 200)
        self.assertEqual(body['filename'], 'cert.pem')
        self.assertIn('-----BEGIN CERTIFICATE-----', body['content'])
        status, _ = self.get('/api/certs/dl/files/key.pem')
        self.assertEqual(status, 403)

    def test_upload_and_delete_file(self):
        status, _ = self.post(
            '/api/certs/src/generate',
            {'mode': 'ca', 'cn': 'Upload Source CA', 'key_type': EC})
        self.assertEqual(status, 201)
        _, downloaded = self.get('/api/certs/src/files/cert.pem')
        pem = downloaded['content']
        status, _ = self.post('/api/certs', {'name': 'dst'})
        self.assertEqual(status, 201)
        status, _ = self.post(
            '/api/certs/dst/files', {'filename': 'ca.pem', 'content': pem})
        self.assertEqual(status, 200)
        status, body = self.get('/api/certs/dst')
        self.assertTrue(body['files']['ca.pem']['present'])
        status, _ = self.delete('/api/certs/dst/files/ca.pem')
        self.assertEqual(status, 200)
        status, body = self.get('/api/certs/dst')
        self.assertFalse(body['files']['ca.pem']['present'])

    def test_upload_rejects_mismatched_key(self):
        status, _ = self.post(
            '/api/certs/pair/generate',
            {'mode': 'self_signed', 'cn': 'pair.local', 'key_type': EC})
        self.assertEqual(status, 201)
        status, body = self.post(
            '/api/certs/generate-client',
            {'cn': 'stranger', 'signer_bundle': 'pair', 'key_type': EC})
        self.assertEqual(status, 400)  # pair is not a CA — use a CA instead
        status, _ = self.post(
            '/api/certs/pairca/generate', {'mode': 'ca', 'cn': 'Pair CA', 'key_type': EC})
        self.assertEqual(status, 201)
        _, client = self.post(
            '/api/certs/generate-client',
            {'cn': 'stranger', 'signer_bundle': 'pairca', 'key_type': EC})
        status, body = self.post(
            '/api/certs/pair/files',
            {'filename': 'key.pem', 'content': client['key_pem']})
        self.assertEqual(status, 400)
        self.assertIn('does not match', body['error'])

    def test_reload_unused_bundle(self):
        status, _ = self.post(
            '/api/certs/idle/generate',
            {'mode': 'self_signed', 'cn': 'idle.local', 'key_type': EC})
        self.assertEqual(status, 201)
        status, body = self.post('/api/certs/idle/reload')
        self.assertEqual(status, 200)
        self.assertEqual(body['reloaded'], [])

    def test_reload_missing_bundle(self):
        status, _ = self.post('/api/certs/nosuch/reload')
        self.assertEqual(status, 404)

    def test_unknown_subpath_is_404(self):
        status, _ = self.get('/api/certs/whatever/nonsense')
        self.assertEqual(status, 404)

    def test_method_guards(self):
        status, _ = self.delete('/api/certs')
        self.assertEqual(status, 405)
        status, _ = self.get('/api/certs/generate-client')
        self.assertEqual(status, 405)
