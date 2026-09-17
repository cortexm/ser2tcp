"""SSL certificate bundle manager.

A "bundle" is a directory under {config_dir}/certs/{name}/ that holds a
fixed set of PEM files:

  cert.pem  — server certificate (or full chain)
  key.pem   — private key (never exposed via API)
  ca.pem    — optional, CA cert(s) for mTLS client verification

The directory is the source of truth; there is no separate metadata
store in config.json. Listing bundles == listing subdirectories.
"""

import datetime as _datetime
import ipaddress as _ipaddress
import logging as _logging
import os as _os
import re as _re
import shutil as _shutil
import ssl as _ssl
import stat as _stat

from cryptography import x509 as _x509
from cryptography.hazmat.primitives import (
    hashes as _hashes, serialization as _serialization)
from cryptography.hazmat.primitives.asymmetric import (
    rsa as _rsa, ec as _ec, ed25519 as _ed25519, ed448 as _ed448,
    dsa as _dsa)
from cryptography.x509.oid import NameOID as _NameOID


ALLOWED_FILES = ('cert.pem', 'key.pem', 'ca.pem')
PRIVATE_FILES = ('key.pem',)  # never readable via API

# Bundle names: alnum, dot, underscore, dash. Reject `.`, `..`, leading dot.
_NAME_RE = _re.compile(r'^[a-zA-Z0-9_-][a-zA-Z0-9._-]*$')

# Accepted PEM block headers per file kind.
_PEM_TYPES = {
    'cert.pem': ('CERTIFICATE',),
    'ca.pem': ('CERTIFICATE',),
    'key.pem': (
        'PRIVATE KEY', 'RSA PRIVATE KEY', 'EC PRIVATE KEY',
        'DSA PRIVATE KEY', 'ENCRYPTED PRIVATE KEY'),
}

_DIR_MODE = 0o700
_KEY_MODE = 0o600
_PUB_MODE = 0o644


class CertManagerError(Exception):
    """User-facing error from cert manager (validation, not-found, etc)."""


def _as_bytes(pem):
    """Accept a PEM blob as str or bytes, return bytes."""
    return pem.encode('utf-8') if isinstance(pem, str) else pem


def _public_key_der(cert_or_key):
    """SubjectPublicKeyInfo DER of a cert's or private key's public key.

    public_numbers() would do for RSA/EC but not for Ed25519/Ed448 —
    the serialized SPKI form compares equal for every algorithm.
    """
    return cert_or_key.public_key().public_bytes(
        _serialization.Encoding.DER,
        _serialization.PublicFormat.SubjectPublicKeyInfo)


def _key_type_info(public_key):
    """Return (algorithm_name, bit_size_or_curve_name) for a public key."""
    if isinstance(public_key, _rsa.RSAPublicKey):
        return ('RSA', public_key.key_size)
    if isinstance(public_key, _ec.EllipticCurvePublicKey):
        return ('EC', public_key.curve.name)
    if isinstance(public_key, _ed25519.Ed25519PublicKey):
        return ('Ed25519', None)
    if isinstance(public_key, _ed448.Ed448PublicKey):
        return ('Ed448', None)
    if isinstance(public_key, _dsa.DSAPublicKey):
        return ('DSA', public_key.key_size)
    return (type(public_key).__name__, None)


def _name_cn(name):
    """Extract the CN attribute from an x509 Name, or None."""
    try:
        attrs = name.get_attributes_for_oid(_x509.NameOID.COMMON_NAME)
    except Exception:
        return None
    return attrs[0].value if attrs else None


def inspect_certificate(pem_content):
    """Parse a PEM-encoded certificate and return a dict of metadata.

    On parse failure, returns {'error': '...'} — callers can still
    display the file but without parsed info. We never raise — this
    function is best-effort and is called when listing bundles, where a
    single bad file shouldn't break the whole response.

    Loading a certificate is not the same as being able to read it:
    cryptography parses extension values lazily, so a certificate with
    a malformed extension loads cleanly, passes PEM validation, lands
    on disk, and only raises later when something touches .extensions.
    That made a listing fatal for as long as the file stayed there.
    Whatever was read before the failure is kept, with the error beside
    it.
    """
    if not pem_content:
        return {'error': 'empty content'}
    try:
        cert = _x509.load_pem_x509_certificate(_as_bytes(pem_content))
    except (ValueError, TypeError) as err:
        return {'error': f'parse failed: {err}'}
    info = {}
    try:
        _fill_cert_info(info, cert)
    except Exception as err:  # pylint: disable=W0703
        info['error'] = f'inspect failed: {err}'
    return info


def _fill_cert_info(info, cert):
    """Fill `info` with everything readable off `cert` (may raise)"""
    info['subject_cn'] = _name_cn(cert.subject)
    info['issuer_cn'] = _name_cn(cert.issuer)
    info['self_signed'] = cert.subject == cert.issuer
    # not_valid_before_utc/after_utc are the timezone-aware variants;
    # fall back to the deprecated naive accessors for older versions.
    try:
        nb = cert.not_valid_before_utc
        na = cert.not_valid_after_utc
    except AttributeError:
        nb = cert.not_valid_before
        na = cert.not_valid_after
    info['not_before'] = int(nb.timestamp())
    info['not_after'] = int(na.timestamp())
    info['serial'] = format(cert.serial_number, 'x')
    # SHA-256 fingerprint, colon-separated hex pairs (standard display form)
    fp = cert.fingerprint(_hashes.SHA256())
    info['fingerprint_sha256'] = ':'.join(f'{b:02x}' for b in fp)
    # Subject Alternative Names — separate DNS / IP / URI lists
    san_dns, san_ip = [], []
    try:
        san_ext = cert.extensions.get_extension_for_class(
            _x509.SubjectAlternativeName).value
        san_dns = san_ext.get_values_for_type(_x509.DNSName)
        san_ip = [str(ip) for ip in
            san_ext.get_values_for_type(_x509.IPAddress)]
    except _x509.ExtensionNotFound:
        pass
    if san_dns:
        info['san_dns'] = san_dns
    if san_ip:
        info['san_ip'] = san_ip
    # CA flag from basicConstraints. Absent = end-entity (CA:FALSE).
    is_ca = False
    try:
        bc = cert.extensions.get_extension_for_class(
            _x509.BasicConstraints).value
        is_ca = bool(bc.ca)
    except _x509.ExtensionNotFound:
        pass
    info['is_ca'] = is_ca
    # Public key type for display
    alg, size = _key_type_info(cert.public_key())
    info['key_type'] = alg
    if size is not None:
        info['key_size'] = size


def resolve_bundle_paths(certs_dir, bundle_name, require_client_cert=False):
    """Resolve a bundle reference to absolute file paths. Raises
    CertManagerError if the bundle or any required file is missing.

    Returns (cert_path, key_path, ca_path_or_None).
    """
    if not bundle_name:
        raise CertManagerError('SSL config missing bundle name')
    CertManager._validate_name(bundle_name)
    bundle_dir = _os.path.join(certs_dir, bundle_name)
    if not _os.path.isdir(bundle_dir):
        raise CertManagerError(f"Cert bundle '{bundle_name}' not found")
    cert_path = _os.path.join(bundle_dir, 'cert.pem')
    key_path = _os.path.join(bundle_dir, 'key.pem')
    ca_path = _os.path.join(bundle_dir, 'ca.pem')
    if not _os.path.isfile(cert_path):
        raise CertManagerError(
            f"Bundle '{bundle_name}' is missing cert.pem")
    if not _os.path.isfile(key_path):
        raise CertManagerError(
            f"Bundle '{bundle_name}' is missing key.pem")
    if require_client_cert:
        if not _os.path.isfile(ca_path):
            raise CertManagerError(
                f"mTLS requires ca.pem in bundle '{bundle_name}'")
        return (cert_path, key_path, ca_path)
    return (cert_path, key_path, None)


KEY_TYPES = ('rsa2048', 'rsa4096', 'ec_p256', 'ed25519')

# Ed25519 uses its native signature scheme — no separate hash algorithm
# is passed to .sign(). RSA and EC use SHA-256.
_HASHLESS_KEYS = (_ed25519.Ed25519PrivateKey,)


def generate_private_key(key_type='rsa2048'):
    """Generate a private key of the requested type. Returns a
    cryptography private key object (used by generate_certificate)."""
    if key_type == 'rsa2048':
        return _rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if key_type == 'rsa4096':
        return _rsa.generate_private_key(public_exponent=65537, key_size=4096)
    if key_type == 'ec_p256':
        return _ec.generate_private_key(_ec.SECP256R1())
    if key_type == 'ed25519':
        return _ed25519.Ed25519PrivateKey.generate()
    raise CertManagerError(
        f"Unknown key_type '{key_type}' (allowed: {', '.join(KEY_TYPES)})")


def _serialize_key_pem(key):
    return key.private_bytes(
        encoding=_serialization.Encoding.PEM,
        format=_serialization.PrivateFormat.PKCS8,
        encryption_algorithm=_serialization.NoEncryption()).decode('utf-8')


def _serialize_cert_pem(cert):
    return cert.public_bytes(_serialization.Encoding.PEM).decode('utf-8')


def cert_key_match(cert_pem, key_pem):
    """Return True/False whether a cert and private key belong together.

    Returns None when either side cannot be parsed — an encrypted key,
    a dangling symlink or a certificate whose key cannot be read is not
    a mismatch, and the caller decides what to do with a file it cannot
    read. "Cannot tell" is an answer here; raising is not.
    """
    try:
        cert = _x509.load_pem_x509_certificate(_as_bytes(cert_pem))
        key = _serialization.load_pem_private_key(
            _as_bytes(key_pem), password=None)
        return _public_key_der(cert) == _public_key_der(key)
    except Exception:  # pylint: disable=W0703
        return None


def load_cert_and_key(cert_pem, key_pem, what='Cert'):
    """Load a PEM cert + private key, verifying they belong together.

    Returns (cert, key) as cryptography objects; raises CertManagerError
    with a user-facing message on a parse failure or a mismatch.
    """
    try:
        cert = _x509.load_pem_x509_certificate(_as_bytes(cert_pem))
    except (ValueError, TypeError) as err:
        raise CertManagerError(f"{what} cert is invalid: {err}") from err
    try:
        key = _serialization.load_pem_private_key(
            _as_bytes(key_pem), password=None)
    except (ValueError, TypeError) as err:
        raise CertManagerError(f"{what} key is invalid: {err}") from err
    if _public_key_der(cert) != _public_key_der(key):
        raise CertManagerError(
            f"{what} cert and key do not match (different public keys)")
    return cert, key


def parse_signer(signer_cert_pem, signer_key_pem):
    """Load a CA's cert + key from PEM for signing. Used when generating
    end-entity certs against an existing bundle."""
    cert, key = load_cert_and_key(signer_cert_pem, signer_key_pem, 'Signer')
    try:
        bc = cert.extensions.get_extension_for_class(
            _x509.BasicConstraints).value
        if not bc.ca:
            raise CertManagerError(
                'Signer cert is not a CA (basicConstraints CA:FALSE)')
    except _x509.ExtensionNotFound:
        raise CertManagerError(
            'Signer cert is not a CA (no basicConstraints)') from None
    return cert, key


def generate_certificate(
        cn, key_type='rsa2048', days=365, is_ca=False,
        san_dns=None, san_ip=None,
        is_client=False, signer=None):
    """Generate a (cert, key) pair as PEM strings.

    Parameters:
      cn         — Subject Common Name
      key_type   — one of KEY_TYPES
      days       — validity in days
      is_ca      — if True, marks the cert as a CA (basicConstraints CA:TRUE,
                   keyUsage keyCertSign+cRLSign). Otherwise marks as
                   end-entity with digitalSignature+keyEncipherment.
      san_dns    — list of DNS names for Subject Alternative Name
      san_ip     — list of IP address strings for SAN
      is_client  — if True (and not is_ca), use extendedKeyUsage clientAuth
                   instead of serverAuth (for mTLS client certs)
      signer     — None for self-signed, or (cert, key) tuple from
                   `parse_signer()` to sign with an existing CA

    Returns (cert_pem: str, key_pem: str).
    """
    if days < 1 or days > 365 * 100:
        raise CertManagerError('days must be between 1 and 36500')
    if not cn or not isinstance(cn, str):
        raise CertManagerError('CN is required')
    if len(cn) > 64:
        raise CertManagerError('CN max length is 64 characters')

    key = generate_private_key(key_type)
    subject = _x509.Name([_x509.NameAttribute(_NameOID.COMMON_NAME, cn)])

    if signer is not None:
        signer_cert, signer_key = signer
        issuer = signer_cert.subject
        sign_key = signer_key
    else:
        issuer = subject
        sign_key = key

    now = _datetime.datetime.now(_datetime.timezone.utc)
    builder = (_x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(_x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + _datetime.timedelta(days=days)))

    # basicConstraints
    builder = builder.add_extension(
        _x509.BasicConstraints(ca=is_ca, path_length=None),
        critical=True)

    # keyUsage — different sets for CA vs end-entity
    if is_ca:
        ku = _x509.KeyUsage(
            digital_signature=False, content_commitment=False,
            key_encipherment=False, data_encipherment=False,
            key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False)
    else:
        ku = _x509.KeyUsage(
            digital_signature=True, content_commitment=False,
            key_encipherment=True, data_encipherment=False,
            key_agreement=False, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False)
    builder = builder.add_extension(ku, critical=True)

    # extendedKeyUsage — only for end-entity certs (server or client)
    if not is_ca:
        eku_oid = _x509.ExtendedKeyUsageOID.CLIENT_AUTH if is_client \
            else _x509.ExtendedKeyUsageOID.SERVER_AUTH
        builder = builder.add_extension(
            _x509.ExtendedKeyUsage([eku_oid]), critical=False)

    # SAN — collect DNS + IP entries
    san_entries = []
    for d in (san_dns or []):
        if not isinstance(d, str) or not d.strip():
            raise CertManagerError('SAN DNS entries must be non-empty strings')
        san_entries.append(_x509.DNSName(d.strip()))
    for ip in (san_ip or []):
        try:
            san_entries.append(_x509.IPAddress(_ipaddress.ip_address(ip)))
        except (ValueError, TypeError) as err:
            raise CertManagerError(
                f"Invalid IP address in SAN: {ip}") from err
    if san_entries:
        builder = builder.add_extension(
            _x509.SubjectAlternativeName(san_entries), critical=False)

    # subjectKeyIdentifier helps cert chain validation tools
    builder = builder.add_extension(
        _x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
        critical=False)
    if signer is not None:
        builder = builder.add_extension(
            _x509.AuthorityKeyIdentifier.from_issuer_public_key(
                signer[0].public_key()),
            critical=False)

    # Ed25519 uses its native signature scheme and requires algorithm=None.
    # RSA/EC use SHA-256.
    algo = None if isinstance(sign_key, _HASHLESS_KEYS) else _hashes.SHA256()
    cert = builder.sign(sign_key, algo)
    return _serialize_cert_pem(cert), _serialize_key_pem(key)


def reload_ssl_context(context, ssl_config, certs_dir):
    """Re-read a bundle's files into an existing SSLContext.

    Handshakes made from now on use the new cert; connections already
    established keep the one they negotiated with. This is what lets a
    renewed certificate take effect without restarting the process.

    load_verify_locations() adds to the context's trust store and
    OpenSSL exposes no way to clear it, so a CA *removed* from ca.pem
    stays trusted until restart; an added one takes effect at once.
    """
    cert_path, key_path, ca_path, bundle = _bundle_for(ssl_config, certs_dir)
    # Load into a throwaway context first. load_cert_chain() installs
    # the certificate, then the key, and only then checks that they
    # belong together, so a bundle caught mid-renewal - new cert.pem on
    # disk, key.pem still the old one - left the live context holding a
    # cert it had no key for. The reload reported the failure and the
    # server refused every handshake from then on, until a good reload
    # or a restart. Doing it twice is the cheapest way to be sure: the
    # rehearsal is the same operation, so it fails in the same places.
    _load_bundle(_ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER),
                 bundle, cert_path, key_path, ca_path)
    _load_bundle(context, bundle, cert_path, key_path, ca_path)


def _bundle_for(ssl_config, certs_dir):
    """Resolve an ssl config to (cert, key, ca_or_None, bundle_name)"""
    if not isinstance(ssl_config, dict):
        raise CertManagerError('ssl config must be an object')
    bundle = ssl_config.get('bundle')
    mtls = bool(ssl_config.get('require_client_cert'))
    cert_path, key_path, ca_path = resolve_bundle_paths(
        certs_dir, bundle, require_client_cert=mtls)
    return cert_path, key_path, ca_path, bundle


def _load_bundle(context, bundle, cert_path, key_path, ca_path):
    """Read a bundle's files into one context.

    Translates OpenSSL load errors into CertManagerError so callers
    don't need to know about the ssl module's exception types.
    """
    try:
        context.load_cert_chain(cert_path, key_path)
    except (_ssl.SSLError, OSError) as err:
        raise CertManagerError(
            f"Bundle '{bundle}': failed to load cert/key — {err}") from err
    if ca_path:
        try:
            context.load_verify_locations(ca_path)
        except (_ssl.SSLError, OSError) as err:
            raise CertManagerError(
                f"Bundle '{bundle}': failed to load ca.pem — {err}") from err
        context.verify_mode = _ssl.CERT_REQUIRED


def build_ssl_context(ssl_config, certs_dir):
    """Build an SSLContext from a server SSL config dict + certs_dir.

    Expected config: {"bundle": "<name>", "require_client_cert": bool?}

    Raises CertManagerError on missing/invalid config or missing files.
    There is nothing serving yet, so this loads once - the rehearsal in
    reload_ssl_context() is there to protect a context already in use.
    """
    cert_path, key_path, ca_path, bundle = _bundle_for(ssl_config, certs_dir)
    context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    _load_bundle(context, bundle, cert_path, key_path, ca_path)
    return context


class CertManager():
    """Manage SSL certificate bundles on disk under {config_dir}/certs/."""

    def __init__(self, config_dir, log=None):
        self._log = log if log else _logging.getLogger(__name__)
        self._certs_dir = _os.path.join(config_dir, 'certs')

    @property
    def certs_dir(self):
        return self._certs_dir

    def _ensure_certs_dir(self):
        if not _os.path.exists(self._certs_dir):
            _os.makedirs(self._certs_dir, mode=_DIR_MODE)
            return
        # Tighten perms if someone created it loosely.
        try:
            _os.chmod(self._certs_dir, _DIR_MODE)
        except OSError:
            pass

    @staticmethod
    def _validate_name(name):
        if not isinstance(name, str) or not name:
            raise CertManagerError('Bundle name is required')
        if name in ('.', '..') or '/' in name or '\\' in name:
            raise CertManagerError('Invalid bundle name')
        if not _NAME_RE.match(name):
            raise CertManagerError(
                'Bundle name must match [a-zA-Z0-9._-] (no leading dot)')

    @staticmethod
    def _validate_filename(filename):
        if filename not in ALLOWED_FILES:
            raise CertManagerError(
                f"File must be one of: {', '.join(ALLOWED_FILES)}")

    @staticmethod
    def _validate_pem(filename, content):
        if not isinstance(content, str):
            raise CertManagerError('PEM content must be a string')
        text = content.strip()
        if not text:
            raise CertManagerError('Empty PEM content')
        expected_types = _PEM_TYPES[filename]
        # Match: -----BEGIN <TYPE>----- ... -----END <TYPE>-----
        begins = _re.findall(r'-----BEGIN ([A-Z ]+)-----', text)
        ends = _re.findall(r'-----END ([A-Z ]+)-----', text)
        if not begins or not ends:
            raise CertManagerError(
                'Not a valid PEM file (missing BEGIN/END markers)')
        if begins != ends:
            raise CertManagerError(
                'PEM BEGIN/END markers do not match')
        # First block must be acceptable for this file kind.
        first = begins[0]
        if first not in expected_types:
            raise CertManagerError(
                f"Expected PEM type one of {expected_types}, got '{first}'")

    def _bundle_path(self, name):
        self._validate_name(name)
        return _os.path.join(self._certs_dir, name)

    def _file_path(self, name, filename):
        self._validate_filename(filename)
        return _os.path.join(self._bundle_path(name), filename)

    def list_bundles(self):
        """Return list of bundles with per-file info matching get_bundle()
        shape: [{name, path, files: {fname: {present, cert_info?, ...}}}]
        Listing is best-effort — a bundle whose get_bundle() fails is
        skipped silently."""
        self._ensure_certs_dir()
        out = []
        try:
            entries = sorted(_os.listdir(self._certs_dir))
        except OSError:
            return out
        for entry in entries:
            path = _os.path.join(self._certs_dir, entry)
            if not _os.path.isdir(path):
                continue
            if not _NAME_RE.match(entry):
                continue
            try:
                out.append(self.get_bundle(entry))
            except CertManagerError:
                continue
        return out

    def get_bundle(self, name):
        """Return detailed info about one bundle. Raises CertManagerError
        if missing.

        For cert.pem and ca.pem, the response includes a `cert_info`
        sub-dict from `inspect_certificate()` — best-effort parsing.
        key.pem is read only to compare its public key against cert.pem
        (`key_match`); nothing derived from it is ever returned.
        """
        path = self._bundle_path(name)
        if not _os.path.isdir(path):
            raise CertManagerError(f"Bundle '{name}' not found")
        files = {}
        for fname in ALLOWED_FILES:
            fpath = _os.path.join(path, fname)
            info = {'present': _os.path.isfile(fpath)}
            if info['present']:
                try:
                    st = _os.lstat(fpath)
                    info['mtime'] = int(st.st_mtime)
                    info['size'] = st.st_size
                    if _stat.S_ISLNK(st.st_mode):
                        info['symlink'] = _os.readlink(fpath)
                except OSError:
                    pass
                # Parse cert metadata for public files
                if fname in ('cert.pem', 'ca.pem'):
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        info['cert_info'] = inspect_certificate(content)
                    except (OSError, UnicodeDecodeError) as err:
                        info['cert_info'] = {'error': str(err)}
            files[fname] = info
        return {
            'name': name, 'path': path, 'files': files,
            'key_match': self._key_match(path),
        }

    @staticmethod
    def _key_match(path):
        """True/False if cert.pem and key.pem in this bundle belong
        together, None if either is missing or cannot be parsed.

        A bundle can reach a mismatched state without going through
        save_file() — a symlink, an scp, a half-finished rotation — so
        the UI gets the answer with every listing, not just on upload.
        """
        cert_path = _os.path.join(path, 'cert.pem')
        key_path = _os.path.join(path, 'key.pem')
        if not (_os.path.isfile(cert_path) and _os.path.isfile(key_path)):
            return None
        try:
            with open(cert_path, 'rb') as f:
                cert_pem = f.read()
            with open(key_path, 'rb') as f:
                key_pem = f.read()
        except OSError:
            return None
        return cert_key_match(cert_pem, key_pem)

    def create_bundle(self, name):
        """Create an empty bundle directory. Raises if it exists."""
        self._ensure_certs_dir()
        path = self._bundle_path(name)
        if _os.path.exists(path):
            raise CertManagerError(f"Bundle '{name}' already exists")
        _os.makedirs(path, mode=_DIR_MODE)
        self._log.info("Cert bundle created: %s", name)

    def delete_bundle(self, name):
        """Remove a bundle directory and all its files."""
        path = self._bundle_path(name)
        if not _os.path.isdir(path):
            raise CertManagerError(f"Bundle '{name}' not found")
        try:
            # A symlinked bundle dir passes isdir() but rmtree refuses
            # it outright, and permissions can fail here too.
            if _os.path.islink(path):
                _os.unlink(path)
            else:
                _shutil.rmtree(path)
        except OSError as err:
            raise CertManagerError(
                f"Cannot delete bundle '{name}': {err}") from err
        self._log.info("Cert bundle deleted: %s", name)

    def save_file(self, name, filename, content):
        """Write content into bundle/{filename}. Creates bundle dir if
        missing. Validates PEM. Sets correct mode (0600 for key, 0644
        for cert/ca)."""
        self.save_files(name, [(filename, content)])

    def save_files(self, name, items):
        """Write several files into a bundle, validating them as a set.

        `items` is a sequence of (filename, content) pairs. Rotating a
        certificate means replacing cert.pem and key.pem together, and
        checking each one on its own against what is still on disk would
        reject whichever half arrived first — so the pair is resolved
        across this call before anything is written.

        Not atomic across files: each file lands atomically, but a
        failure partway leaves the earlier ones written.
        """
        items = list(items)
        for filename, content in items:
            self._validate_filename(filename)
            self._validate_pem(filename, content)
        self._check_pair(name, dict(items))
        self._ensure_certs_dir()
        path = self._bundle_path(name)
        if not _os.path.isdir(path):
            try:
                _os.makedirs(path, mode=_DIR_MODE)
            except OSError as err:
                # Typically a plain file sitting where the bundle
                # directory belongs, or a permission problem.
                raise CertManagerError(
                    f"Cannot create bundle '{name}': {err}") from err
        for filename, content in items:
            self._write_file(path, name, filename, content)

    def _check_pair(self, name, pending):
        """Reject a cert.pem/key.pem combination that does not match.

        Each half is taken from this upload if present, otherwise from
        the bundle on disk. Without the check the mismatch only surfaces
        as an OpenSSL error at the next TLS handshake — long after the
        upload reported success, on a server that may not be restarted
        for weeks.
        """
        if not ({'cert.pem', 'key.pem'} & set(pending)):
            return
        halves = {}
        for filename in ('cert.pem', 'key.pem'):
            if filename in pending:
                halves[filename] = pending[filename]
                continue
            fpath = _os.path.join(self._bundle_path(name), filename)
            if not _os.path.isfile(fpath):
                return
            try:
                with open(fpath, 'rb') as file:
                    halves[filename] = file.read()
            except OSError:
                return
        if cert_key_match(halves['cert.pem'], halves['key.pem']) is False:
            uploaded = ' and '.join(sorted(set(pending) & set(halves)))
            raise CertManagerError(
                f"{uploaded} does not match the cert/key pair in bundle "
                f"'{name}' — upload cert.pem and key.pem together, or "
                "delete the other half first")

    def _write_file(self, path, name, filename, content):
        """Atomically write one validated file into the bundle dir."""
        fpath = _os.path.join(path, filename)
        # Normalize content: ensure trailing newline.
        text = content.strip() + '\n'
        # Write via temp file + rename to be atomic. Set mode before
        # rename so the final file never appears world-readable.
        tmp = fpath + '.tmp'
        mode = _KEY_MODE if filename in PRIVATE_FILES else _PUB_MODE
        # If existing target is a symlink, replace it (don't follow).
        if _os.path.islink(fpath):
            _os.unlink(fpath)
        try:
            fd = _os.open(
                tmp, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, mode)
            try:
                _os.write(fd, text.encode('utf-8'))
            finally:
                _os.close(fd)
            _os.chmod(tmp, mode)
            _os.replace(tmp, fpath)
        except Exception as err:
            if _os.path.exists(tmp):
                try:
                    _os.unlink(tmp)
                except OSError:
                    pass
            if isinstance(err, OSError):
                raise CertManagerError(
                    f"Cannot write {name}/{filename}: {err}") from err
            raise
        self._log.info("Cert file saved: %s/%s", name, filename)

    def delete_file(self, name, filename):
        """Remove a single file from a bundle. The bundle directory
        stays (use delete_bundle to remove it)."""
        fpath = self._file_path(name, filename)
        if not _os.path.lexists(fpath):
            raise CertManagerError(
                f"File '{filename}' not found in bundle '{name}'")
        try:
            _os.unlink(fpath)
        except OSError as err:
            # A directory under that name, or no write access here.
            raise CertManagerError(
                f"Cannot delete {name}/{filename}: {err}") from err
        self._log.info("Cert file deleted: %s/%s", name, filename)

    def read_public_file(self, name, filename):
        """Read a public file (cert.pem or ca.pem) for download. Never
        allows key.pem to keep private keys non-exfiltratable."""
        if filename in PRIVATE_FILES:
            raise CertManagerError(
                f"File '{filename}' is private and cannot be downloaded")
        fpath = self._file_path(name, filename)
        if not _os.path.isfile(fpath):
            raise CertManagerError(
                f"File '{filename}' not found in bundle '{name}'")
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                return f.read()
        except (OSError, UnicodeDecodeError) as err:
            # Nothing guarantees what is on disk is text: this endpoint
            # is reachable by any authenticated user, so a binary file
            # dropped in by hand must not be fatal.
            raise CertManagerError(
                f"Cannot read {name}/{filename}: {err}") from err
