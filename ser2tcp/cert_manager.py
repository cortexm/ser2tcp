"""SSL certificate bundle manager.

A "bundle" is a directory under {config_dir}/certs/{name}/ that holds a
fixed set of PEM files:

  cert.pem  — server certificate (or full chain)
  key.pem   — private key (never exposed via API)
  ca.pem    — optional, CA cert(s) for mTLS client verification

The directory is the source of truth; there is no separate metadata
store in config.json. Listing bundles == listing subdirectories.
"""

import logging as _logging
import os as _os
import re as _re
import shutil as _shutil
import ssl as _ssl
import stat as _stat


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


def build_ssl_context(ssl_config, certs_dir):
    """Build an SSLContext from a server SSL config dict + certs_dir.

    Expected config: {"bundle": "<name>", "require_client_cert": bool?}

    Raises CertManagerError on missing/invalid config or missing files.
    """
    if not isinstance(ssl_config, dict):
        raise CertManagerError('ssl config must be an object')
    bundle = ssl_config.get('bundle')
    mtls = bool(ssl_config.get('require_client_cert'))
    cert_path, key_path, ca_path = resolve_bundle_paths(
        certs_dir, bundle, require_client_cert=mtls)
    context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    # Translate OpenSSL load errors into CertManagerError so callers
    # don't need to know about the ssl module's exception types.
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
        """Return [{name, path, files: {cert.pem: bool, ...}}, ...]"""
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
            files = {}
            for fname in ALLOWED_FILES:
                files[fname] = _os.path.isfile(_os.path.join(path, fname))
            out.append({'name': entry, 'path': path, 'files': files})
        return out

    def get_bundle(self, name):
        """Return detailed info about one bundle. Raises CertManagerError
        if missing."""
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
            files[fname] = info
        return {'name': name, 'path': path, 'files': files}

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
        _shutil.rmtree(path)
        self._log.info("Cert bundle deleted: %s", name)

    def save_file(self, name, filename, content):
        """Write content into bundle/{filename}. Creates bundle dir if
        missing. Validates PEM. Sets correct mode (0600 for key, 0644
        for cert/ca)."""
        self._validate_filename(filename)
        self._validate_pem(filename, content)
        self._ensure_certs_dir()
        path = self._bundle_path(name)
        if not _os.path.isdir(path):
            _os.makedirs(path, mode=_DIR_MODE)
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
        except Exception:
            if _os.path.exists(tmp):
                try:
                    _os.unlink(tmp)
                except OSError:
                    pass
            raise
        self._log.info("Cert file saved: %s/%s", name, filename)

    def delete_file(self, name, filename):
        """Remove a single file from a bundle. The bundle directory
        stays (use delete_bundle to remove it)."""
        fpath = self._file_path(name, filename)
        if not _os.path.lexists(fpath):
            raise CertManagerError(
                f"File '{filename}' not found in bundle '{name}'")
        _os.unlink(fpath)
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
        with open(fpath, 'r', encoding='utf-8') as f:
            return f.read()
