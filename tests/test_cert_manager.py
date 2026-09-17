"""Tests for cert_manager module"""

import os
import ssl
import stat
import tempfile
import unittest

from ser2tcp.cert_manager import (
    CertManager, CertManagerError,
    resolve_bundle_paths, build_ssl_context, reload_ssl_context,
    inspect_certificate, cert_key_match, load_cert_and_key,
    generate_certificate, generate_private_key, parse_signer)


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
            for fname in ('cert.pem', 'key.pem', 'ca.pem'):
                self.assertFalse(b['files'][fname]['present'])

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


def _generate_test_cert(
        out_dir=None, common_name='test', days=1,
        is_ca=False, san_dns=None, san_ip=None):
    """Generate a real self-signed cert + key for SSL load tests.
    Uses cryptography lib (available as test dep)."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime, ipaddress

    def _now():
        return datetime.datetime.now(datetime.timezone.utc)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    builder = (x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now())
        .not_valid_after(_now() + datetime.timedelta(days=days)))
    if is_ca:
        builder = builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True)
    san_list = []
    for d in (san_dns or []):
        san_list.append(x509.DNSName(d))
    for ip in (san_ip or []):
        san_list.append(x509.IPAddress(ipaddress.ip_address(ip)))
    if san_list:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(san_list), critical=False)
    cert = builder.sign(key, hashes.SHA256())
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


class TestInspectCertificate(unittest.TestCase):
    def test_empty_content(self):
        info = inspect_certificate('')
        self.assertIn('error', info)

    def test_invalid_pem(self):
        info = inspect_certificate('not a cert')
        self.assertIn('error', info)

    def test_server_cert(self):
        cert, _ = _generate_test_cert(common_name='myhost')
        info = inspect_certificate(cert)
        self.assertNotIn('error', info)
        self.assertEqual(info['subject_cn'], 'myhost')
        self.assertEqual(info['issuer_cn'], 'myhost')  # self-signed
        self.assertTrue(info['self_signed'])
        self.assertFalse(info['is_ca'])
        self.assertEqual(info['key_type'], 'RSA')
        self.assertEqual(info['key_size'], 2048)
        self.assertIn('fingerprint_sha256', info)
        # Format: 32 hex pairs separated by colons
        self.assertEqual(info['fingerprint_sha256'].count(':'), 31)
        self.assertIn('not_before', info)
        self.assertIn('not_after', info)
        self.assertGreater(info['not_after'], info['not_before'])

    def test_ca_cert(self):
        cert, _ = _generate_test_cert(common_name='myca', is_ca=True)
        info = inspect_certificate(cert)
        self.assertTrue(info['is_ca'])
        self.assertEqual(info['subject_cn'], 'myca')

    def test_san(self):
        cert, _ = _generate_test_cert(
            common_name='srv',
            san_dns=['example.com', 'www.example.com'],
            san_ip=['127.0.0.1', '192.168.1.1'])
        info = inspect_certificate(cert)
        self.assertEqual(info['san_dns'], ['example.com', 'www.example.com'])
        self.assertEqual(info['san_ip'], ['127.0.0.1', '192.168.1.1'])

    def test_no_san_omitted(self):
        cert, _ = _generate_test_cert(common_name='nosan')
        info = inspect_certificate(cert)
        self.assertNotIn('san_dns', info)
        self.assertNotIn('san_ip', info)


class TestGetBundleIncludesCertInfo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.mgr.create_bundle('b')
        cert, key = _generate_test_cert(common_name='inspected')
        self.mgr.save_file('b', 'cert.pem', cert)
        self.mgr.save_file('b', 'key.pem', key)
        ca_cert, _ = _generate_test_cert(common_name='ca-inspected', is_ca=True)
        self.mgr.save_file('b', 'ca.pem', ca_cert)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cert_info_attached(self):
        info = self.mgr.get_bundle('b')
        cert_info = info['files']['cert.pem']['cert_info']
        self.assertEqual(cert_info['subject_cn'], 'inspected')
        self.assertFalse(cert_info['is_ca'])

    def test_ca_info_attached(self):
        info = self.mgr.get_bundle('b')
        ca_info = info['files']['ca.pem']['cert_info']
        self.assertEqual(ca_info['subject_cn'], 'ca-inspected')
        self.assertTrue(ca_info['is_ca'])

    def test_key_not_parsed(self):
        info = self.mgr.get_bundle('b')
        # key.pem must not carry cert_info — it's not a cert
        self.assertNotIn('cert_info', info['files']['key.pem'])


class TestGenerateKey(unittest.TestCase):
    def test_rsa2048(self):
        key = generate_private_key('rsa2048')
        self.assertEqual(key.key_size, 2048)

    def test_rsa4096(self):
        key = generate_private_key('rsa4096')
        self.assertEqual(key.key_size, 4096)

    def test_ec_p256(self):
        key = generate_private_key('ec_p256')
        self.assertEqual(key.curve.name, 'secp256r1')

    def test_ed25519(self):
        from cryptography.hazmat.primitives.asymmetric import ed25519
        key = generate_private_key('ed25519')
        self.assertIsInstance(key, ed25519.Ed25519PrivateKey)

    def test_unknown_type(self):
        with self.assertRaises(CertManagerError):
            generate_private_key('rsa1024')


class TestGenerateCertificate(unittest.TestCase):
    def test_self_signed_server(self):
        cert_pem, key_pem = generate_certificate(
            cn='myserver', days=30,
            san_dns=['localhost'], san_ip=['127.0.0.1'])
        info = inspect_certificate(cert_pem)
        self.assertEqual(info['subject_cn'], 'myserver')
        self.assertEqual(info['issuer_cn'], 'myserver')
        self.assertTrue(info['self_signed'])
        self.assertFalse(info['is_ca'])
        self.assertEqual(info['san_dns'], ['localhost'])
        self.assertEqual(info['san_ip'], ['127.0.0.1'])
        # Should load as a usable cert via load_pem_private_key
        from cryptography.hazmat.primitives import serialization
        serialization.load_pem_private_key(key_pem.encode(), password=None)

    def test_self_signed_ca(self):
        cert_pem, _ = generate_certificate(
            cn='myca', days=365, is_ca=True)
        info = inspect_certificate(cert_pem)
        self.assertTrue(info['is_ca'])
        self.assertTrue(info['self_signed'])

    def test_signed_by_ca(self):
        ca_cert_pem, ca_key_pem = generate_certificate(
            cn='myca', is_ca=True, days=365)
        signer = parse_signer(ca_cert_pem, ca_key_pem)
        srv_cert_pem, _ = generate_certificate(
            cn='myserver', days=30, signer=signer,
            san_dns=['localhost'])
        info = inspect_certificate(srv_cert_pem)
        self.assertEqual(info['subject_cn'], 'myserver')
        self.assertEqual(info['issuer_cn'], 'myca')
        self.assertFalse(info['self_signed'])
        self.assertFalse(info['is_ca'])

    def test_client_cert(self):
        ca_cert_pem, ca_key_pem = generate_certificate(
            cn='myca', is_ca=True, days=365)
        signer = parse_signer(ca_cert_pem, ca_key_pem)
        client_cert_pem, _ = generate_certificate(
            cn='alice', signer=signer, is_client=True, days=30)
        # We can't easily inspect EKU through inspect_certificate, but
        # we can re-parse and check the extension directly.
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(client_cert_pem.encode())
        eku = cert.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage).value
        self.assertIn(x509.ExtendedKeyUsageOID.CLIENT_AUTH, list(eku))

    def test_server_cert_has_serverauth_eku(self):
        cert_pem, _ = generate_certificate(cn='srv', days=30)
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        eku = cert.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage).value
        self.assertIn(x509.ExtendedKeyUsageOID.SERVER_AUTH, list(eku))

    def test_ca_has_keycertsign(self):
        cert_pem, _ = generate_certificate(cn='ca', is_ca=True, days=365)
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        self.assertTrue(ku.key_cert_sign)
        self.assertTrue(ku.crl_sign)
        self.assertFalse(ku.key_encipherment)

    def test_server_has_digital_signature(self):
        cert_pem, _ = generate_certificate(cn='srv', days=30)
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        self.assertTrue(ku.digital_signature)
        self.assertTrue(ku.key_encipherment)
        self.assertFalse(ku.key_cert_sign)

    def test_validity_period(self):
        cert_pem, _ = generate_certificate(cn='srv', days=10)
        info = inspect_certificate(cert_pem)
        delta = info['not_after'] - info['not_before']
        # ~10 days in seconds
        self.assertAlmostEqual(delta, 10 * 86400, delta=60)

    def test_invalid_days(self):
        with self.assertRaises(CertManagerError):
            generate_certificate(cn='x', days=0)
        with self.assertRaises(CertManagerError):
            generate_certificate(cn='x', days=999999)

    def test_invalid_cn(self):
        with self.assertRaises(CertManagerError):
            generate_certificate(cn='', days=30)
        with self.assertRaises(CertManagerError):
            generate_certificate(cn='x' * 100, days=30)

    def test_invalid_san_ip(self):
        with self.assertRaises(CertManagerError):
            generate_certificate(cn='x', days=30, san_ip=['not.an.ip'])

    def test_ec_key_signed(self):
        # Sanity: full pipeline works with EC keys too
        cert_pem, key_pem = generate_certificate(
            cn='ec-srv', days=30, key_type='ec_p256')
        info = inspect_certificate(cert_pem)
        self.assertEqual(info['key_type'], 'EC')

    def test_ed25519_key_signed(self):
        # Ed25519 signs without a hash algorithm — verify the builder
        # path handles that correctly.
        cert_pem, key_pem = generate_certificate(
            cn='ed-srv', days=30, key_type='ed25519')
        info = inspect_certificate(cert_pem)
        self.assertEqual(info['key_type'], 'Ed25519')

    def test_ed25519_ca_signs_other(self):
        # CA with Ed25519 signing a server cert with RSA — mixed-algorithm
        # chain should still work.
        ca_cert, ca_key = generate_certificate(
            cn='ed-ca', is_ca=True, days=365, key_type='ed25519')
        signer = parse_signer(ca_cert, ca_key)
        srv_cert, _ = generate_certificate(
            cn='srv', signer=signer, key_type='rsa2048', days=30)
        info = inspect_certificate(srv_cert)
        self.assertEqual(info['issuer_cn'], 'ed-ca')


class TestParseSigner(unittest.TestCase):
    def test_non_ca_rejected(self):
        cert_pem, key_pem = generate_certificate(cn='srv', days=30)
        # Not a CA — should reject
        with self.assertRaises(CertManagerError) as cm:
            parse_signer(cert_pem, key_pem)
        self.assertIn('not a CA', str(cm.exception))

    def test_mismatched_key(self):
        cert_pem, _ = generate_certificate(cn='ca', is_ca=True, days=30)
        # Generate a *different* key
        _, other_key = generate_certificate(cn='other', is_ca=True, days=30)
        with self.assertRaises(CertManagerError) as cm:
            parse_signer(cert_pem, other_key)
        self.assertIn('do not match', str(cm.exception))

    def test_invalid_cert(self):
        with self.assertRaises(CertManagerError):
            parse_signer('not pem', 'not pem')


class TestCertKeyMatch(unittest.TestCase):
    def test_matching_pair(self):
        cert, key = generate_certificate('a', key_type='ec_p256')
        self.assertTrue(cert_key_match(cert, key))

    def test_mismatched_pair(self):
        cert, _ = generate_certificate('a', key_type='ec_p256')
        _, other_key = generate_certificate('b', key_type='ec_p256')
        self.assertFalse(cert_key_match(cert, other_key))

    def test_accepts_bytes(self):
        cert, key = generate_certificate('a', key_type='ec_p256')
        self.assertTrue(cert_key_match(cert.encode(), key.encode()))

    def test_unparseable_is_none_not_mismatch(self):
        cert, _ = generate_certificate('a', key_type='ec_p256')
        self.assertIsNone(cert_key_match(cert, KEY_PEM))
        self.assertIsNone(cert_key_match(CERT_PEM, KEY_PEM))

    def test_encrypted_key_is_none(self):
        from cryptography.hazmat.primitives import serialization
        key_obj = generate_private_key('ec_p256')
        encrypted = key_obj.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(
                b'secret')).decode()
        cert, _ = generate_certificate('a', key_type='ec_p256')
        self.assertIsNone(cert_key_match(cert, encrypted))


class TestLoadCertAndKey(unittest.TestCase):
    def test_returns_objects(self):
        cert_pem, key_pem = generate_certificate('a', key_type='ec_p256')
        cert, key = load_cert_and_key(cert_pem, key_pem)
        self.assertIsNotNone(cert.subject)
        self.assertIsNotNone(key.public_key())

    def test_mismatch_rejected(self):
        cert_pem, _ = generate_certificate('a', key_type='ec_p256')
        _, other_key = generate_certificate('b', key_type='ec_p256')
        with self.assertRaises(CertManagerError) as err:
            load_cert_and_key(cert_pem, other_key)
        self.assertIn('do not match', str(err.exception))

    def test_label_used_in_message(self):
        with self.assertRaises(CertManagerError) as err:
            load_cert_and_key('nope', 'nope', 'Signer')
        self.assertIn('Signer cert is invalid', str(err.exception))


class TestSaveFilesPairValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.cert, self.key = generate_certificate('a', key_type='ec_p256')
        self.other_cert, self.other_key = generate_certificate(
            'b', key_type='ec_p256')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pair_saved_together(self):
        self.mgr.save_files('b1', [
            ('cert.pem', self.cert), ('key.pem', self.key)])
        self.assertTrue(self.mgr.get_bundle('b1')['key_match'])

    def test_mismatched_pair_rejected_together(self):
        with self.assertRaises(CertManagerError) as err:
            self.mgr.save_files('b1', [
                ('cert.pem', self.cert), ('key.pem', self.other_key)])
        self.assertIn('does not match', str(err.exception))

    def test_mismatched_half_rejected_against_disk(self):
        self.mgr.save_files('b1', [
            ('cert.pem', self.cert), ('key.pem', self.key)])
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('b1', 'key.pem', self.other_key)
        # the rejected upload left the bundle as it was
        self.assertTrue(self.mgr.get_bundle('b1')['key_match'])

    def test_rotation_replaces_both_at_once(self):
        self.mgr.save_files('b1', [
            ('cert.pem', self.cert), ('key.pem', self.key)])
        self.mgr.save_files('b1', [
            ('cert.pem', self.other_cert), ('key.pem', self.other_key)])
        bundle = self.mgr.get_bundle('b1')
        self.assertTrue(bundle['key_match'])
        self.assertEqual(
            bundle['files']['cert.pem']['cert_info']['subject_cn'], 'b')

    def test_first_half_alone_is_allowed(self):
        self.mgr.save_file('b1', 'cert.pem', self.cert)
        self.assertIsNone(self.mgr.get_bundle('b1')['key_match'])
        self.mgr.save_file('b1', 'key.pem', self.key)
        self.assertTrue(self.mgr.get_bundle('b1')['key_match'])

    def test_ca_pem_is_not_pair_checked(self):
        self.mgr.save_files('b1', [
            ('cert.pem', self.cert), ('key.pem', self.key)])
        self.mgr.save_file('b1', 'ca.pem', self.other_cert)
        self.assertTrue(self.mgr.get_bundle('b1')['key_match'])

    def test_key_match_none_when_half_missing(self):
        self.mgr.create_bundle('empty')
        self.assertIsNone(self.mgr.get_bundle('empty')['key_match'])

    def test_bad_filename_in_set_rejects_everything(self):
        with self.assertRaises(CertManagerError):
            self.mgr.save_files('b1', [
                ('cert.pem', self.cert), ('evil.pem', self.key)])
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, 'certs', 'b1', 'cert.pem')))


class TestReloadSslContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.certs_dir = os.path.join(self.tmp, 'certs')
        cert, key = generate_certificate(
            'before', key_type='ec_p256', days=30)
        self.mgr.save_files(
            'web', [('cert.pem', cert), ('key.pem', key)])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reload_picks_up_new_files(self):
        ctx = build_ssl_context({'bundle': 'web'}, self.certs_dir)
        cert, key = generate_certificate(
            'after', key_type='ec_p256', days=30)
        self.mgr.save_files(
            'web', [('cert.pem', cert), ('key.pem', key)])
        reload_ssl_context(ctx, {'bundle': 'web'}, self.certs_dir)
        loaded = ctx.get_ca_certs()  # empty, but the call must not raise
        self.assertEqual(loaded, [])

    def test_reload_missing_bundle_raises(self):
        ctx = build_ssl_context({'bundle': 'web'}, self.certs_dir)
        with self.assertRaises(CertManagerError):
            reload_ssl_context(ctx, {'bundle': 'gone'}, self.certs_dir)

    def test_reload_after_cert_deleted_raises(self):
        ctx = build_ssl_context({'bundle': 'web'}, self.certs_dir)
        self.mgr.delete_file('web', 'cert.pem')
        with self.assertRaises(CertManagerError):
            reload_ssl_context(ctx, {'bundle': 'web'}, self.certs_dir)

    def test_reload_mtls_requires_ca(self):
        ctx = build_ssl_context({'bundle': 'web'}, self.certs_dir)
        with self.assertRaises(CertManagerError):
            reload_ssl_context(
                ctx, {'bundle': 'web', 'require_client_cert': True},
                self.certs_dir)


def handshake_cert(server_context):
    """The certificate this context actually presents.

    A TLS handshake is the only way to ask - the ssl module offers no
    way to read a loaded cert chain back off a context - and it is also
    the thing being protected, so it is the right question. Done over
    memory BIOs, so no socket or thread is involved.
    """
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.check_hostname = False
    client_context.verify_mode = ssl.CERT_NONE
    server_in, server_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    client_in, client_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    client = client_context.wrap_bio(client_in, client_out)
    done = set()
    for _ in range(40):
        for side, out_bio, peer_in in (
                (client, client_out, server_in),
                (server, server_out, client_in)):
            if side not in done:
                try:
                    side.do_handshake()
                    done.add(side)
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                    pass
            pending = out_bio.read()
            if pending:
                peer_in.write(pending)
        if len(done) == 2:
            return ssl.DER_cert_to_PEM_cert(
                client.getpeercert(binary_form=True))
    raise AssertionError('handshake did not complete')


class TestAFailedReloadLeavesTheServerServing(unittest.TestCase):
    """A reload that cannot finish must change nothing.

    load_cert_chain() installs the certificate, then the key, and only
    then checks that they belong together - so a bundle whose halves do
    not match left the live context holding a new cert over an old key.
    The reload reported the failure and the server went on to refuse
    every handshake made from then on, until a good reload or a
    restart. The files arriving one at a time is exactly what a renewal
    looks like while it is in progress.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.certs_dir = os.path.join(self.tmp, 'certs')
        cert, key = generate_certificate(
            'serving.local', key_type='ec_p256', days=30)
        self.mgr.save_files(
            'web', [('cert.pem', cert), ('key.pem', key)])
        self.context = build_ssl_context({'bundle': 'web'}, self.certs_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, content):
        with open(os.path.join(self.certs_dir, 'web', name), 'w',
                  encoding='utf-8') as file:
            file.write(content)

    def _reload(self, **config):
        config.setdefault('bundle', 'web')
        with self.assertRaises(CertManagerError) as caught:
            reload_ssl_context(self.context, config, self.certs_dir)
        return str(caught.exception)

    def _still_serving(self):
        return inspect_certificate(
            handshake_cert(self.context))['subject_cn']

    def test_the_context_started_out_serving(self):
        self.assertEqual(self._still_serving(), 'serving.local')

    def test_a_cert_that_does_not_match_the_key_is_refused(self):
        """Half a renewal: the new cert.pem landed, key.pem did not"""
        other_cert, _ = generate_certificate(
            'renewed.local', key_type='ec_p256', days=30)
        self._write('cert.pem', other_cert)
        self._reload()

    def test_and_the_old_certificate_is_still_served(self):
        other_cert, _ = generate_certificate(
            'renewed.local', key_type='ec_p256', days=30)
        self._write('cert.pem', other_cert)
        self._reload()
        self.assertEqual(self._still_serving(), 'serving.local')

    def test_an_unreadable_key_leaves_the_certificate_alone(self):
        """The other order: cert.pem loads, key.pem is garbage"""
        other_cert, _ = generate_certificate(
            'renewed.local', key_type='ec_p256', days=30)
        self._write('cert.pem', other_cert)
        self._write('key.pem', 'not a key at all\n')
        self._reload()
        self.assertEqual(self._still_serving(), 'serving.local')

    def test_an_unreadable_ca_leaves_the_certificate_alone(self):
        """ca.pem is read after the chain, and used to be read into it"""
        other_cert, other_key = generate_certificate(
            'renewed.local', key_type='ec_p256', days=30)
        self.mgr.save_files('web', [
            ('cert.pem', other_cert), ('key.pem', other_key)])
        self._write('ca.pem', 'not a certificate\n')
        self._reload(require_client_cert=True)
        self.assertEqual(self._still_serving(), 'serving.local')

    def test_a_good_reload_still_goes_through(self):
        other_cert, other_key = generate_certificate(
            'renewed.local', key_type='ec_p256', days=30)
        self.mgr.save_files('web', [
            ('cert.pem', other_cert), ('key.pem', other_key)])
        reload_ssl_context(self.context, {'bundle': 'web'}, self.certs_dir)
        self.assertEqual(self._still_serving(), 'renewed.local')



class TestACombinedPemIsNotACertificate(unittest.TestCase):
    """A file holding a certificate *and* a private key.

    Plenty of tools write one - LE's own naming aside, "just paste the
    pem" usually means a concatenation - and only the first block used
    to be checked. So it passed as cert.pem, was written 0644, and
    GET /api/certs/<bundle>/files/cert.pem handed the private key to
    any authenticated user: only the *filename* key.pem was refused.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        self.cert, self.key = generate_certificate(
            'combined.local', key_type='ec_p256', days=30)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _save(self, filename, content):
        with self.assertRaises(CertManagerError) as caught:
            self.mgr.save_file('x', filename, content)
        return str(caught.exception)

    def test_cert_first_is_refused(self):
        self._save('cert.pem', self.cert + self.key)

    def test_key_first_is_refused_too(self):
        """Checking only the first block missed one order, not both"""
        self._save('cert.pem', self.key + self.cert)

    def test_the_message_says_what_is_wrong_with_it(self):
        message = self._save('cert.pem', self.cert + self.key)
        self.assertIn('PRIVATE KEY', message)

    def test_the_message_says_where_the_key_belongs(self):
        self.assertIn('key.pem', self._save('cert.pem',
                                            self.cert + self.key))

    def test_ca_pem_is_guarded_the_same_way(self):
        self._save('ca.pem', self.cert + self.key)

    def test_nothing_was_written(self):
        self._save('cert.pem', self.cert + self.key)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, 'certs', 'x', 'cert.pem')))

    def test_a_multi_file_upload_is_refused_as_a_whole(self):
        with self.assertRaises(CertManagerError):
            self.mgr.save_files('x', [
                ('cert.pem', self.cert + self.key),
                ('key.pem', self.key)])
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, 'certs', 'x', 'key.pem')))


class TestTheOrdinaryFilesStillGoThrough(unittest.TestCase):
    """The check has to let real files in, including the awkward ones."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_fullchain_is_several_certificates(self):
        leaf, _ = generate_certificate('leaf', key_type='ec_p256', days=30)
        root, _ = generate_certificate(
            'root', key_type='ec_p256', days=30, is_ca=True)
        self.mgr.save_file('x', 'cert.pem', leaf + root)
        self.assertIn('BEGIN CERTIFICATE',
                      self.mgr.read_public_file('x', 'cert.pem'))

    def test_a_key_with_its_ec_parameters_is_an_ordinary_key(self):
        """What `openssl ecparam -genkey` writes, and it is a key.pem"""
        _cert, key = generate_certificate(
            'ec', key_type='ec_p256', days=30)
        params = ('-----BEGIN EC PARAMETERS-----\n'
                  'BggqhkjOPQMBBw==\n'
                  '-----END EC PARAMETERS-----\n')
        self.mgr.save_file('x', 'key.pem', params + key)
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp, 'certs', 'x', 'key.pem')))

    def test_parameters_on_their_own_are_not_a_key(self):
        params = ('-----BEGIN EC PARAMETERS-----\n'
                  'BggqhkjOPQMBBw==\n'
                  '-----END EC PARAMETERS-----\n')
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('x', 'key.pem', params)

    def test_a_certificate_is_not_a_key(self):
        cert, _ = generate_certificate('c', key_type='ec_p256', days=30)
        with self.assertRaises(CertManagerError):
            self.mgr.save_file('x', 'key.pem', cert)


class TestDownloadRefusesAFileHoldingAKey(unittest.TestCase):
    """The upload check is not the only way a file gets into a bundle.

    Bundles are a directory: an scp, a symlink into a Let's Encrypt
    live/ directory, an editor - none of them go past save_file(). The
    endpoint that hands the file out is the last place to notice, and
    the only one that sees what is actually on disk.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = CertManager(self.tmp)
        cert, self.key = generate_certificate(
            'combined.local', key_type='ec_p256', days=30)
        self.mgr.save_file('x', 'cert.pem', cert)
        self.path = os.path.join(self.tmp, 'certs', 'x', 'cert.pem')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, content):
        with open(self.path, 'w', encoding='utf-8') as file:
            file.write(content)

    def test_the_plain_certificate_downloads(self):
        self.assertIn('BEGIN CERTIFICATE',
                      self.mgr.read_public_file('x', 'cert.pem'))

    def test_a_key_appended_on_disk_is_not_handed_out(self):
        self._write(open(self.path, encoding='utf-8').read() + self.key)
        with self.assertRaises(CertManagerError):
            self.mgr.read_public_file('x', 'cert.pem')

    def test_the_refusal_names_the_file(self):
        self._write(open(self.path, encoding='utf-8').read() + self.key)
        with self.assertRaises(CertManagerError) as caught:
            self.mgr.read_public_file('x', 'cert.pem')
        self.assertIn('cert.pem', str(caught.exception))

    def test_the_listing_says_so_without_being_asked(self):
        """Otherwise the only symptom is a download that will not work"""
        self._write(open(self.path, encoding='utf-8').read() + self.key)
        info = self.mgr.get_bundle('x')['files']['cert.pem']
        self.assertTrue(info['private_key'])

    def test_a_clean_file_is_not_flagged(self):
        info = self.mgr.get_bundle('x')['files']['cert.pem']
        self.assertFalse(info.get('private_key'))

    def test_key_pem_is_not_flagged_for_holding_a_key(self):
        self.mgr.save_file('x', 'key.pem', self.key)
        info = self.mgr.get_bundle('x')['files']['key.pem']
        self.assertFalse(info.get('private_key'))


if __name__ == '__main__':
    unittest.main()


def make_cert_with_a_broken_extension():
    """A certificate that loads but whose extensions cannot be parsed.

    cryptography parses extension *values* lazily, so a cert like this
    passes load_pem_x509_certificate() and only blows up when something
    reads .extensions - which is exactly what makes it dangerous: it
    also sails through PEM validation and lands on disk.
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import datetime

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'broken.test')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=5))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName('sanhost.example')]),
            critical=False)
        .sign(key, hashes.SHA256()))
    der = bytearray(cert.public_bytes(serialization.Encoding.DER))
    # Overstate the length of the DNSName inside the SAN extension.
    index = der.find(b'sanhost.example')
    der[index - 1] = 0x60
    broken = x509.load_der_x509_certificate(bytes(der))
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()
    return broken.public_bytes(serialization.Encoding.PEM).decode(), key_pem


class TestInspectCertificateNeverRaises(unittest.TestCase):
    """The contract says best-effort; a listing must not die on one file.

    A bundle holding a cert like this made every GET /api/certs from any
    logged-in user kill the process - and since the file stays on disk,
    it did so again after every restart.
    """

    def setUp(self):
        self.pem, self.key = make_cert_with_a_broken_extension()

    def test_a_broken_extension_is_reported_not_raised(self):
        info = inspect_certificate(self.pem)
        self.assertIn('error', info)

    def test_what_could_be_read_is_still_returned(self):
        info = inspect_certificate(self.pem)
        self.assertEqual(info.get('subject_cn'), 'broken.test')
        self.assertIn('not_after', info)

    def test_garbage_is_still_reported(self):
        info = inspect_certificate('not a certificate at all')
        self.assertIn('error', info)

    def test_a_good_certificate_is_unaffected(self):
        pem, _key = generate_certificate(cn='fine.test', days=5,
            key_type='ec_p256')
        info = inspect_certificate(pem)
        self.assertNotIn('error', info)
        self.assertEqual(info['subject_cn'], 'fine.test')

    def test_pairing_still_works_on_a_cert_that_cannot_be_inspected(self):
        """A broken extension says nothing about the public key"""
        self.assertIs(cert_key_match(self.pem, self.key), True)

    def test_pairing_reports_a_real_mismatch(self):
        _pem, other = generate_certificate(cn='other.test', days=5,
            key_type='ec_p256')
        self.assertIs(cert_key_match(self.pem, other), False)

    def test_pairing_cannot_tell_with_garbage(self):
        self.assertIsNone(cert_key_match('garbage', self.key))


class TestBundleListingSurvivesABrokenCert(unittest.TestCase):
    """get_bundle()/list_bundles() have to ride over one bad file"""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mgr = CertManager(self.dir)
        self.mgr.create_bundle('broken')
        path = os.path.join(self.mgr.certs_dir, 'broken', 'cert.pem')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(make_cert_with_a_broken_extension()[0])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_get_bundle_reports_the_problem(self):
        bundle = self.mgr.get_bundle('broken')
        self.assertIn('error', bundle['files']['cert.pem']['cert_info'])

    def test_list_bundles_still_lists_it(self):
        names = [b['name'] for b in self.mgr.list_bundles()]
        self.assertIn('broken', names)

    def test_a_good_bundle_alongside_it_is_unaffected(self):
        pem, key = generate_certificate(cn='fine.test', days=5,
            key_type='ec_p256')
        self.mgr.save_files('fine', [('cert.pem', pem), ('key.pem', key)])
        bundles = {b['name']: b for b in self.mgr.list_bundles()}
        self.assertNotIn(
            'error', bundles['fine']['files']['cert.pem']['cert_info'])


class TestFilesystemErrorsBecomeCertManagerError(unittest.TestCase):
    """Every caller catches CertManagerError and nothing else.

    A bare OSError out of these reaches the event loop, so a symlinked
    bundle or a stray file where a directory belongs used to be enough
    to take the process down.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mgr = CertManager(self.dir)
        self.certs = self.mgr.certs_dir
        os.makedirs(self.certs, mode=0o700, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_deleting_a_symlinked_bundle_removes_only_the_link(self):
        """Symlinked bundles are a documented Let's Encrypt pattern.

        rmtree refuses a symlink outright, which used to raise OSError
        straight past every caller. Removing the link is both what the
        request means and the only safe reading of it - the target may
        be /etc/letsencrypt/live/... and must survive untouched.
        """
        target = os.path.join(self.dir, 'elsewhere')
        os.makedirs(target)
        with open(os.path.join(target, 'keepme'), 'w',
                  encoding='utf-8') as f:
            f.write('still here')
        os.symlink(target, os.path.join(self.certs, 'linked'))
        self.mgr.delete_bundle('linked')
        self.assertFalse(os.path.lexists(os.path.join(self.certs, 'linked')))
        self.assertTrue(os.path.isfile(os.path.join(target, 'keepme')))

    def test_saving_into_a_name_taken_by_a_file(self):
        with open(os.path.join(self.certs, 'taken'), 'w',
                  encoding='utf-8') as f:
            f.write('not a directory')
        with self.assertRaises(CertManagerError):
            self.mgr.save_files('taken', [('cert.pem', CERT_PEM)])

    def test_deleting_a_file_that_is_really_a_directory(self):
        self.mgr.create_bundle('odd')
        os.makedirs(os.path.join(self.certs, 'odd', 'cert.pem'))
        with self.assertRaises(CertManagerError):
            self.mgr.delete_file('odd', 'cert.pem')

    def test_reading_a_file_that_is_not_utf8(self):
        self.mgr.create_bundle('binary')
        path = os.path.join(self.certs, 'binary', 'cert.pem')
        with open(path, 'wb') as f:
            f.write(b'\xff\xfe\x00\x01 not text')
        with self.assertRaises(CertManagerError):
            self.mgr.read_public_file('binary', 'cert.pem')

    def test_writing_into_a_directory_that_cannot_be_written(self):
        self.mgr.create_bundle('locked')
        path = os.path.join(self.certs, 'locked')
        os.chmod(path, 0o500)
        try:
            with self.assertRaises(CertManagerError):
                self.mgr.save_files('locked', [('cert.pem', CERT_PEM)])
        finally:
            os.chmod(path, 0o700)

    def test_normal_operations_still_work(self):
        self.mgr.create_bundle('fine')
        self.mgr.save_files('fine', [('cert.pem', CERT_PEM)])
        self.assertIn('BEGIN CERTIFICATE',
            self.mgr.read_public_file('fine', 'cert.pem'))
        self.mgr.delete_file('fine', 'cert.pem')
        self.mgr.delete_bundle('fine')
        self.assertEqual(self.mgr.list_bundles(), [])
