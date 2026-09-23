"""Helpers for integration tests: run a real ser2tcp process.

The unit tests drive the request handlers directly with a MockClient,
which never touches uhttp's routing, a real socket or a TLS handshake —
exactly the layers where the bugs found during development were. These
tests spawn the process, talk to it over HTTP(S), and shut it down.

Each test class starts one process in setUpClass and shares it, so the
~1 s of startup is paid once per class rather than per test.
"""

import json as _json
import os as _os
import shutil as _shutil
import socket as _socket
import ssl as _ssl
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile
import time as _time
import unittest as _unittest
import urllib.error as _urlerror
import urllib.request as _urlrequest

from tests import (
    requires_pty as _requires_pty,
    requires_bind_conflict as _requires_bind_conflict,
)


# Run ser2tcp out of the source tree rather than relying on a console
# script being on PATH: main.py has no __main__ guard, so -m won't do.
_LAUNCH = 'from ser2tcp.main import main; main()'

STARTUP_TIMEOUT = 15.0

# Re-exported so a test that already has `base` does not need a second
# import for them; the definitions are in tests/__init__.py, because
# the unit tests need the same ones.
requires_pty = _requires_pty
requires_bind_conflict = _requires_bind_conflict


def free_port():
    """Return a port number that was free a moment ago."""
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _decode(raw, content_type):
    text = raw.decode('utf-8', 'replace')
    if content_type and 'json' in content_type:
        try:
            return _json.loads(text)
        except ValueError:
            return text
    return text


def request(
        url, method='GET', data=None, token=None, context=None,
        timeout=10):
    """Perform one HTTP request, returning (status, body).

    An error status is a result, not an exception — these tests assert
    on 401/403/404 as much as on 200.
    """
    body = None
    headers = {}
    if data is not None:
        body = _json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = _urlrequest.Request(url, data=body, headers=headers, method=method)
    try:
        with _urlrequest.urlopen(req, timeout=timeout, context=context) as res:
            return res.status, _decode(
                res.read(), res.headers.get('Content-Type'))
    except _urlerror.HTTPError as err:
        with err:
            return err.code, _decode(
                err.read(), err.headers.get('Content-Type'))


class Ser2tcpProcess():
    """A ser2tcp process running against a throwaway config directory."""

    def __init__(self, config):
        self.dir = _tempfile.mkdtemp(prefix='ser2tcp-it-')
        self.config_path = _os.path.join(self.dir, 'config.json')
        self.certs_dir = _os.path.join(self.dir, 'certs')
        self.write_config(config)
        self._proc = None

    def write_config(self, config):
        """(Re)write config.json — call before start() to adjust it."""
        with open(self.config_path, 'w', encoding='utf-8') as file:
            _json.dump(config, file, indent=2)

    def read_config(self):
        """Read config.json back, to assert on what the API persisted."""
        with open(self.config_path, 'r', encoding='utf-8') as file:
            return _json.load(file)

    def start(self, port, tls_context=None):
        """Spawn the process and wait until `port` answers a request.

        The probe sends a complete request and reads the reply, rather
        than just connecting: a connection that goes away mid-request
        is held until the server's request timeout, and on a TLS port
        the next handshake waits behind it for those same seconds.
        """
        self._proc = _subprocess.Popen(
            [_sys.executable, '-c', _LAUNCH, '-c', self.config_path],
            stdout=_subprocess.PIPE, stderr=_subprocess.STDOUT, text=True)
        deadline = _time.time() + STARTUP_TIMEOUT
        while _time.time() < deadline:
            if self._proc.poll() is not None:
                raise AssertionError(
                    'ser2tcp exited during startup:\n' + self.output())
            if self._probe(port, tls_context):
                return
            _time.sleep(0.05)
        self.stop()
        raise AssertionError(
            f'ser2tcp did not open port {port} within {STARTUP_TIMEOUT}s')

    @staticmethod
    def _probe(port, tls_context):
        """True once the port answers a complete HTTP request."""
        sock = None
        try:
            sock = _socket.create_connection(('127.0.0.1', port), 0.5)
            if tls_context is not None:
                sock = tls_context.wrap_socket(
                    sock, server_hostname='127.0.0.1')
            sock.sendall(b'GET /api/status HTTP/1.0\r\n\r\n')
            return bool(sock.recv(1))
        except OSError:
            return False
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def output(self):
        """Drain whatever the process has written, for failure messages."""
        if self._proc is None or self._proc.stdout is None:
            return ''
        self._proc.terminate()
        try:
            return self._proc.communicate(timeout=5)[0] or ''
        except _subprocess.TimeoutExpired:
            self._proc.kill()
            return self._proc.communicate()[0] or ''

    def stop(self):
        """Terminate the process and remove its config directory."""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.communicate(timeout=5)
            except _subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.communicate()
        self._proc = None
        _shutil.rmtree(self.dir, ignore_errors=True)


class IntegrationTestCase(_unittest.TestCase):
    """Base class: one process per test class, plain HTTP, no auth.

    Subclasses override build_config() to add HTTPS, auth or ports.
    """

    proc = None
    port = None
    scheme = 'http'
    # SSLContext used for this class's own API calls (https subclasses).
    tls_context = None

    @classmethod
    def build_config(cls):
        """Return the config.json contents for this class's process."""
        return {
            'ports': [],
            'http': [{
                'name': 'main', 'address': '127.0.0.1', 'port': cls.port,
            }],
        }

    @classmethod
    def prepare(cls, proc):
        """Hook: runs after the config dir exists, before the process
        starts — used to plant cert bundles on disk."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.proc = Ser2tcpProcess(cls.build_config())
        cls.prepare(cls.proc)
        try:
            cls.proc.start(
                cls.port,
                cls.tls_context if cls.scheme == 'https' else None)
        except AssertionError:
            cls.proc.stop()
            raise
        cls.url = f'{cls.scheme}://127.0.0.1:{cls.port}'

    @classmethod
    def tearDownClass(cls):
        if cls.proc is not None:
            cls.proc.stop()

    def port_id(self, index=0):
        """The id of the port at this position in the config.

        Ports are addressed by id, not by position; tests know where
        they put a port in the file, so they look its id up here.
        """
        body = self.get('/api/status')[1]
        return body['ports'][index]['id']

    def http_id(self, index=0):
        """The id of the HTTP server at this position in the config"""
        body = self.get('/api/settings')[1]
        servers = body['http']
        if isinstance(servers, dict):
            servers = [servers]
        return servers[index]['id']

    def get(self, path, **kwargs):
        kwargs.setdefault('context', self.tls_context)
        return request(self.url + path, **kwargs)

    def post(self, path, data=None, **kwargs):
        kwargs.setdefault('context', self.tls_context)
        return request(self.url + path, 'POST', data, **kwargs)

    def put(self, path, data=None, **kwargs):
        kwargs.setdefault('context', self.tls_context)
        return request(self.url + path, 'PUT', data, **kwargs)

    def delete(self, path, **kwargs):
        kwargs.setdefault('context', self.tls_context)
        return request(self.url + path, 'DELETE', **kwargs)


def unverified_context():
    """A client context that accepts any certificate.

    These tests check *which* cert a server presents and whether a
    handshake happens at all; chain validation gets its own test with a
    real CA file.
    """
    context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = _ssl.CERT_NONE
    return context


def tls_peer_cert(
        host, port, cafile=None, client_cert=None, client_key=None,
        probe=True):
    """Open a TLS connection and return the peer certificate as PEM.

    With cafile the chain and hostname are verified (so a bad chain
    raises); without it the handshake is unverified and the cert is
    fetched only to identify which one the server presented.

    probe=False skips the post-handshake exchange, for a server that is
    expected to hang up straight after the handshake.
    """
    context = _ssl.create_default_context(cafile=cafile) if cafile \
        else unverified_context()
    if client_cert:
        context.load_cert_chain(client_cert, client_key)
    with _socket.create_connection((host, port), 10) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            pem = _ssl.DER_cert_to_PEM_cert(tls.getpeercert(binary_form=True))
            # Under TLS 1.3 the client finishes the handshake before the
            # server has checked its certificate, so a rejected client
            # only finds out on the first read. Exchange a byte to make
            # that rejection surface here rather than nowhere.
            if probe:
                tls.sendall(b'GET /api/status HTTP/1.0\r\n\r\n')
                tls.recv(1)
            return pem
