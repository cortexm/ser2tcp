"""Tests for cert_manager module"""

import os
import stat
import tempfile
import unittest

from ser2tcp.cert_manager import (
    CertManager, CertManagerError,
    resolve_bundle_paths, build_ssl_context)


# Minimal valid PEM blocks for testing — content is not parsed
# cryptographically by cert_manager (that's a Phase 2 feature), so any
# well-formed BEGIN/END markers are enough.
CERT_PEM = (
    '-----BEGIN CERTIFICATE-----\n'
    'MIIBkTCCATegAwIBAgIJAKHHIgZ+UVTuMA0GCSqGSIb3DQEBCwUAMBQxEjAQBgNV\n'
    '-----END CERTIFICATE-----\n')
KEY_PEM = (
    '-----BEGIN PRIVATE KEY-----\n'
    'MIIBOQIBAAJAfakekey\n'
    '-----END PRIVATE KEY-----\n')
RSA_KEY_PEM = (
    '-----BEGIN RSA PRIVATE KEY-----\n'
    'MIIBOQIBAAJAfakekey\n'
    '-----END RSA PRIVATE KEY-----\n')


class TestNameValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_names(self):
        for name in ('main', 'le-mydomain', 'a_b.c', 'X1', 'a-b_c.d'):
            self.mgr.create_bundle(name)
            self.mgr.delete_bundle(name)

    def test_invalid_names(self):
        for name in ('', '.', '..', '.hidden', '/abs', 'a/b', 'a\\b',
                'with space', 'with!bang', 'üñìçødé'):
            with self.assertRaises(CertManagerError):
                self.mgr.create_bundle(name)

    def test_path_traversal(self):
        for name in ('../escape', '..', '../../etc'):
            with self.assertRaises(CertManagerError):
                self.mgr.create_bundle(name)


class TestPemValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.mgr.create_bundle('b')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cert_accepts_certificate(self):
        self.mgr.save_file('b', 'cert.pem', CERT_PEM)

    def test_ca_accepts_certificate(self):
        self.mgr.save_file('b', 'ca.pem', CERT_PEM)

    def test_key_accepts_private_key(self):
        self.mgr.save_file('b', 'key.pem', KEY_PEM)

    def test_key_accepts_rsa_private_key(self):
        self.mgr.save_file('b', 'key.pem', RSA_KEY_PEM)

    def test_cert_rejects_private_key(self):
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('b', 'cert.pem', KEY_PEM)

    def test_key_rejects_certificate(self):
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('b', 'key.pem', CERT_PEM)

    def test_empty_content(self):
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('b', 'cert.pem', '')
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('b', 'cert.pem', '   \n  ')

    def test_no_pem_markers(self):
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('b', 'cert.pem', 'just some text')

    def test_mismatched_markers(self):
        bad = (
            '-----BEGIN CERTIFICATE-----\n'
            'MIIBkTCC\n'
            '-----END PRIVATE KEY-----\n')
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('b', 'cert.pem', bad)

    def test_invalid_filename(self):
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('b', 'random.pem', CERT_PEM)
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('b', '../etc/passwd', CERT_PEM)


class TestFilePermissions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.mgr.create_bundle('b')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dir_mode(self):
        path = os.path.join(self.tmp, 'certs', 'b')
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o700)

    def test_key_mode(self):
        self.mgr.save_file('b', 'key.pem', KEY_PEM)
        path = os.path.join(self.tmp, 'certs', 'b', 'key.pem')
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_cert_mode(self):
        self.mgr.save_file('b', 'cert.pem', CERT_PEM)
        path = os.path.join(self.tmp, 'certs', 'b', 'cert.pem')
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode, 0o644)

    def test_certs_dir_mode(self):
        # Force ensure
        self.mgr.list_bundles()
        mode = stat.S_IMODE(os.stat(os.path.join(self.tmp, 'certs')).st_mode)
        self.assertEqual(mode, 0o700)


class TestBundleOperations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_empty(self):
        self.assertEqual(self.mgr.list_bundles(), [])

    def test_list_after_create(self):
        self.mgr.create_bundle('alpha')
        self.mgr.create_bundle('beta')
        bundles = self.mgr.list_bundles()
        names = [b['name'] for b in bundles]
        self.assertEqual(names, ['alpha', 'beta'])
        for b in bundles:
            self.assertEqual(b['files'],
                {'cert.pem': False, 'key.pem': False, 'ca.pem': False})

    def test_list_skips_non_dirs_and_bad_names(self):
        certs_dir = os.path.join(self.tmp, 'certs')
        os.makedirs(certs_dir)
        os.makedirs(os.path.join(certs_dir, '.hidden'))
        with open(os.path.join(certs_dir, 'loose.txt'), 'w') as f:
            f.write('x')
        self.mgr.create_bundle('good')
        names = [b['name'] for b in self.mgr.list_bundles()]
        self.assertEqual(names, ['good'])

    def test_create_duplicate(self):
        self.mgr.create_bundle('x')
        with self.assertRaises(CertManagerError):
            self.mgr.create_bundle('x')

    def test_delete_missing(self):
        with self.assertRaises(CertManagerError):
            self.mgr.delete_bundle('nope')

    def test_delete_removes_files(self):
        self.mgr.create_bundle('x')
        self.mgr.save_file('x', 'cert.pem', CERT_PEM)
        self.mgr.delete_bundle('x')
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'certs', 'x')))

    def test_get_bundle_missing(self):
        with self.assertRaises(CertManagerError):
            self.mgr.get_bundle('nope')

    def test_get_bundle_reports_files(self):
        self.mgr.create_bundle('x')
        self.mgr.save_file('x', 'cert.pem', CERT_PEM)
        self.mgr.save_file('x', 'key.pem', KEY_PEM)
        info = self.mgr.get_bundle('x')
        self.assertEqual(info['name'], 'x')
        self.assertTrue(info['files']['cert.pem']['present'])
        self.assertTrue(info['files']['key.pem']['present'])
        self.assertFalse(info['files']['ca.pem']['present'])
        self.assertIn('mtime', info['files']['cert.pem'])
        self.assertIn('size', info['files']['cert.pem'])

    def test_save_creates_bundle(self):
        # save_file should create the bundle dir on demand
        self.mgr.save_file('newbundle', 'cert.pem', CERT_PEM)
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, 'certs', 'newbundle', 'cert.pem')))

    def test_delete_file(self):
        self.mgr.create_bundle('x')
        self.mgr.save_file('x', 'cert.pem', CERT_PEM)
        self.mgr.delete_file('x', 'cert.pem')
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, 'certs', 'x', 'cert.pem')))
        # Bundle dir stays
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, 'certs', 'x')))

    def test_delete_missing_file(self):
        self.mgr.create_bundle('x')
        with self.assertRaises(CertManagerError):
            self.mgr.delete_file('x', 'cert.pem')

    def test_save_overwrites(self):
        self.mgr.create_bundle('x')
        self.mgr.save_file('x', 'cert.pem', CERT_PEM)
        cert2 = (
            '-----BEGIN CERTIFICATE-----\n'
            'replacement\n'
            '-----END CERTIFICATE-----\n')
        self.mgr.save_file('x', 'cert.pem', cert2)
        with open(os.path.join(self.tmp, 'certs', 'x', 'cert.pem')) as f:
            self.assertIn('replacement', f.read())


class TestReadPublicFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.mgr.create_bundle('x')
        self.mgr.save_file('x', 'cert.pem', CERT_PEM)
        self.mgr.save_file('x', 'key.pem', KEY_PEM)
        self.mgr.save_file('x', 'ca.pem', CERT_PEM)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_cert(self):
        content = self.mgr.read_public_file('x', 'cert.pem')
        self.assertIn('BEGIN CERTIFICATE', content)

    def test_read_ca(self):
        content = self.mgr.read_public_file('x', 'ca.pem')
        self.assertIn('BEGIN CERTIFICATE', content)

    def test_read_key_forbidden(self):
        with self.assertRaises(CertManagerError):
            self.mgr.read_public_file('x', 'key.pem')

    def test_read_missing_bundle(self):
        with self.assertRaises(CertManagerError):
            self.mgr.read_public_file('nope', 'cert.pem')

    def test_read_missing_file(self):
        self.mgr.create_bundle('y')
        with self.assertRaises(CertManagerError):
            self.mgr.read_public_file('y', 'cert.pem')


class TestResolveBundlePaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.certs_dir = os.path.join(self.tmp, 'certs')
        self.mgr.create_bundle('main')
        self.mgr.save_file('main', 'cert.pem', CERT_PEM)
        self.mgr.save_file('main', 'key.pem', KEY_PEM)
        # bundle without key
        self.mgr.create_bundle('incomplete')
        self.mgr.save_file('incomplete', 'cert.pem', CERT_PEM)
        # bundle with ca for mTLS
        self.mgr.create_bundle('mtls')
        self.mgr.save_file('mtls', 'cert.pem', CERT_PEM)
        self.mgr.save_file('mtls', 'key.pem', KEY_PEM)
        self.mgr.save_file('mtls', 'ca.pem', CERT_PEM)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_basic_resolve(self):
        cert, key, ca = resolve_bundle_paths(self.certs_dir, 'main')
        self.assertTrue(cert.endswith('main/cert.pem'))
        self.assertTrue(key.endswith('main/key.pem'))
        self.assertIsNone(ca)

    def test_mtls_requires_ca(self):
        with self.assertRaises(CertManagerError) as cm:
            resolve_bundle_paths(
                self.certs_dir, 'main', require_client_cert=True)
        self.assertIn('mTLS requires ca.pem', str(cm.exception))

    def test_mtls_with_ca_ok(self):
        cert, key, ca = resolve_bundle_paths(
            self.certs_dir, 'mtls', require_client_cert=True)
        self.assertIsNotNone(ca)
        self.assertTrue(ca.endswith('mtls/ca.pem'))

    def test_missing_bundle(self):
        with self.assertRaises(CertManagerError):
            resolve_bundle_paths(self.certs_dir, 'nope')

    def test_missing_key(self):
        with self.assertRaises(CertManagerError) as cm:
            resolve_bundle_paths(self.certs_dir, 'incomplete')
        self.assertIn('key.pem', str(cm.exception))

    def test_empty_bundle_name(self):
        with self.assertRaises(CertManagerError):
            resolve_bundle_paths(self.certs_dir, '')

    def test_path_traversal_name(self):
        with self.assertRaises(CertManagerError):
            resolve_bundle_paths(self.certs_dir, '../escape')


def _generate_test_cert(out_dir):
    """Generate a real self-signed cert + key for SSL load tests.
    Uses cryptography lib (available as test dep)."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'test'),
    ])
    cert = (x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow()
            + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()
    return cert_pem, key_pem


class TestBuildSslContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.certs_dir = os.path.join(self.tmp, 'certs')
        cert, key = _generate_test_cert(self.tmp)
        self.mgr.create_bundle('valid')
        self.mgr.save_file('valid', 'cert.pem', cert)
        self.mgr.save_file('valid', 'key.pem', key)
        self.mgr.save_file('valid', 'ca.pem', cert)  # reuse cert as CA
        # Bundle with bad PEM files (passes filename validation but won't load)
        self.mgr.create_bundle('badcert')
        self.mgr.save_file('badcert', 'cert.pem', CERT_PEM)  # fake CERT_PEM
        self.mgr.save_file('badcert', 'key.pem', KEY_PEM)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_context_ok(self):
        ctx = build_ssl_context({'bundle': 'valid'}, self.certs_dir)
        import ssl
        self.assertIsInstance(ctx, ssl.SSLContext)
        self.assertNotEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_build_context_mtls(self):
        ctx = build_ssl_context(
            {'bundle': 'valid', 'require_client_cert': True},
            self.certs_dir)
        import ssl
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_missing_bundle_field(self):
        with self.assertRaises(CertManagerError):
            build_ssl_context({}, self.certs_dir)

    def test_bundle_not_found(self):
        with self.assertRaises(CertManagerError):
            build_ssl_context({'bundle': 'nope'}, self.certs_dir)

    def test_load_failure_wrapped(self):
        # Fake PEM files pass our marker validation but OpenSSL rejects them.
        # The error must surface as CertManagerError, not ssl.SSLError.
        with self.assertRaises(CertManagerError) as cm:
            build_ssl_context({'bundle': 'badcert'}, self.certs_dir)
        self.assertIn("Bundle 'badcert'", str(cm.exception))


if __name__ == '__main__':
    unittest.main()
