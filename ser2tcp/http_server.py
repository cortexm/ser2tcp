"""HTTP server integration with uhttp"""

import itertools as _itertools
import json as _json
import logging as _logging
import os as _os
import pathlib as _pathlib
import re as _re
import ssl as _ssl
import time as _time

import serial.tools.list_ports as _list_ports

import uhttp.server as _uhttp_server

import ser2tcp.cert_manager as _cert_manager
import ser2tcp.config_ids as _config_ids
import ser2tcp.http_auth as _http_auth
import ser2tcp.connection_control as _control
import ser2tcp.ip_filter as _ip_filter
import ser2tcp.serial_proxy as _serial_proxy
import ser2tcp.server as _server
import ser2tcp.server_monitor as _server_monitor

HTML_DIR = _pathlib.Path(__file__).parent / 'html'

_CONNECTION_IDS = _itertools.count(1)
_CONNECTION_ID_ATTR = '_ser2tcp_conn_id'


def connection_id(con):
    """A stable id for one client connection, assigned on first sight.

    Connections cannot be addressed by position: a client hanging up
    shifts every connection after it, so a request to drop one lands on
    another. The id is stuck to the object, which is why this works the
    same for the connections we own and for the uhttp ones behind a
    WebSocket.
    """
    existing = getattr(con, _CONNECTION_ID_ATTR, None)
    if isinstance(existing, str) and existing:
        return existing
    assigned = str(next(_CONNECTION_IDS))
    try:
        setattr(con, _CONNECTION_ID_ATTR, assigned)
    except (AttributeError, TypeError):
        # Nothing to attach it to; it will not be addressable, but the
        # listing still has to say something.
        return ''
    return assigned


def _describe_detected(info):
    """Format a detected USB device for log output: device path + the
    most identifying attributes if present (vid/pid + product / serial)."""
    parts = [info['device']]
    extras = []
    if 'vid' in info and 'pid' in info:
        extras.append(f"{info['vid']}:{info['pid']}")
    for key in ('product', 'manufacturer', 'serial_number'):
        if info.get(key):
            extras.append(info[key])
    if extras:
        parts.append('(' + ' '.join(extras) + ')')
    return ' '.join(parts)


class HttpServerWrapper():
    """Wrapper around uhttp.HttpServer compatible with ServersManager"""

    def __init__(self, configs, serial_proxies, log=None,
            config_path=None, configuration=None,
            server_manager=None, selector=None):
        self._log = log if log else _logging.getLogger(__name__)
        self._serial_proxies = serial_proxies
        self._server_manager = server_manager
        # uhttp registers its listening and client sockets here itself;
        # we only hand it the selector the main loop runs on.
        self._selector = selector
        if selector is None and server_manager is not None:
            self._selector = server_manager.selector
        self._config_path = config_path
        self._configuration = configuration if configuration else {}
        if isinstance(configs, dict):
            configs = [configs]
        # Auth config at root level (users, tokens, session_timeout)
        # Migrate from old format (auth inside http config) if needed
        auth_config = {}
        if self._configuration.get('users'):
            auth_config['users'] = self._configuration['users']
        if self._configuration.get('tokens'):
            auth_config['tokens'] = self._configuration['tokens']
        if 'session_timeout' in self._configuration:
            auth_config['session_timeout'] = self._configuration['session_timeout']
        # Backward compatibility: migrate auth from http config to root
        if not auth_config:
            for config in configs:
                if 'auth' in config:
                    auth_config = config['auth']
                    break
        self._auth = _http_auth.SessionManager(auth_config) if auth_config else None
        # Cert manager — lives next to config.json (or under default config
        # dir if running without a config path, e.g. tests).
        cfg_dir = _os.path.dirname(config_path) if config_path \
            else _os.path.expanduser('~/.config/ser2tcp')
        self._cert_manager = _cert_manager.CertManager(cfg_dir, log=self._log)
        self._ws_clients = {}  # uhttp client -> ServerWebSocket or ServerMonitor
        self._monitor_servers = {}  # port name -> ServerMonitor
        # (HttpServer, IpFilter or None, config, SSLContext or None)
        self._servers = []
        self._pending_reload = False
        # NDJSON status streaming clients.
        # Each entry: {client, last_ports, admin, last_send}
        # `last_ports` mirrors what we last delivered so we can compute deltas.
        self._stream_clients = []
        # Cache for /api/detect — USB enumeration via pyserial isn't free;
        # throttle to ~1 s so the stream can include detected devices
        # without hitting the kernel on every 100 ms select tick.
        self._detect_cache = []
        self._detect_cache_at = 0.0
        if _config_ids.assign_ids(self._configuration):
            # Written back so they survive a restart; a bookmarked URL
            # or an open editor would otherwise point somewhere else
            # after every start.
            self._save_config()
        for config in configs:
            try:
                self._servers.append(self._create_http_server(config))
            except ValueError as err:
                self._log.error("%s, skipping", err)

    def _fresh_id(self):
        """An id no entry in this configuration is using"""
        return _config_ids.fresh_id(self._configuration)

    def _claim_id(self, data, current=None):
        """Settle the id for an entry being written.

        Returns (id, error). A given one is honoured - a readable id is
        worth having and is what the field in the editor is for - as
        long as nothing else answers to it. Ports and HTTP servers share
        one namespace: the URL says which kind is meant, but an id that
        means two things is confusing whatever the URL says.
        """
        wanted = data.get('id')
        if wanted is None or wanted == current:
            return (current or self._fresh_id()), None
        if wanted in _config_ids.taken_ids(self._configuration):
            return None, f"id '{wanted}' is already in use"
        return wanted, None

    def _port_index(self, port_id):
        """Position of the port with this id, or None.

        Ids are resolved here at the edge; everything inside still
        works by position, which is safe because the runtime list and
        the config list are kept the same length.
        """
        for index, port in enumerate(self._get_ports_config()):
            if isinstance(port, dict) and port.get('id') == port_id:
                return index
        return None

    def _http_index(self, server_id):
        """Position of the HTTP server with this id, or None"""
        http_list = self._configuration.get('http', [])
        if isinstance(http_list, dict):
            http_list = [http_list]
        for index, server in enumerate(http_list):
            if isinstance(server, dict) and server.get('id') == server_id:
                return index
        return None

    def _create_http_server(self, config):
        """Create one HTTP server from config.

        Returns (server, ip_filter, config, ssl_context). The config and
        the context are kept alongside the server so a cert bundle can
        later be reloaded into it without rebuilding the socket.
        """
        address = config.get('address', '0.0.0.0')
        port = config.get('port', 8080)
        ssl_context = None
        if 'ssl' in config:
            try:
                ssl_context = _cert_manager.build_ssl_context(
                    config['ssl'], self._cert_manager.certs_dir)
            except _cert_manager.CertManagerError as err:
                raise ValueError(f"HTTPS {address}:{port}: {err}") from err
            self._log.info("HTTPS server: %s:%d", address, port)
        else:
            self._log.info("HTTP server: %s:%d", address, port)
        ip_flt = _ip_filter.create_filter(config, log=self._log)
        try:
            server = _uhttp_server.HttpServer(
                address=address, port=port, ssl_context=ssl_context,
                event_mode=True, selector=self._selector)
        except OSError as err:
            raise ValueError(
                f"HTTP {address}:{port}: failed to bind: "
                f"{err.strerror or err}") from err
        return (server, ip_flt, config, ssl_context)

    def add_http_server(self, config):
        """Add a new HTTP server dynamically"""
        srv_tuple = self._create_http_server(config)
        self._servers.append(srv_tuple)
        return len(self._servers) - 1

    def remove_http_server(self, index):
        """Remove HTTP server by index"""
        if index < 0 or index >= len(self._servers):
            raise ValueError("Invalid server index")
        server = self._servers[index][0]
        server.close()
        del self._servers[index]

    def reload_http_servers(self):
        """Reload all HTTP servers from current configuration"""
        # Close all existing servers
        for server, *_ in self._servers:
            server.close()
        self._servers.clear()
        # Create new servers from config
        configs = self._configuration.get('http', [])
        if isinstance(configs, dict):
            configs = [configs]
        for config in configs:
            try:
                srv_tuple = self._create_http_server(config)
                self._servers.append(srv_tuple)
            except ValueError as e:
                self._log.error("Failed to create HTTP server: %s", e)

    def schedule_reload(self):
        """Schedule HTTP servers reload for next process_stale cycle"""
        self._pending_reload = True

    def _ip_filter_for(self, client):
        """The IP filter of the server that accepted this connection.

        uhttp hands a ready connection straight to the loop, so which
        server it belongs to has to be recovered from the local end of
        its socket. Ports are unique across servers, addresses are not
        (0.0.0.0 shows up as the real interface here).
        """
        try:
            port = client.socket.getsockname()[1]
        except (OSError, AttributeError, TypeError, IndexError):
            return None
        for _server, ip_flt, cfg, _ctx in self._servers:
            if cfg.get('port', 8080) == port:
                return ip_flt
        return None

    def handle_client(self, client):
        """Handle one uhttp connection the event loop handed back"""
        if not isinstance(client, _uhttp_server.HttpConnection):
            return
        # Filter on the events that start a request; a body chunk of an
        # already-accepted request is not worth re-checking.
        if client.event in (
                _uhttp_server.EVENT_REQUEST,
                _uhttp_server.EVENT_HEADERS,
                _uhttp_server.EVENT_WS_REQUEST):
            ip_flt = self._ip_filter_for(client)
            client_ip = client.addr[0] \
                if isinstance(client.addr, tuple) else None
            if ip_flt and client_ip and not ip_flt.is_allowed(client_ip):
                self._log.info("HTTP rejected (IP filter): %s", client_ip)
                client.respond({'error': 'Forbidden'}, status=403)
                return
        if client.event == _uhttp_server.EVENT_WS_REQUEST:
            self._handle_ws_upgrade(client)
        elif client.event in (
                _uhttp_server.EVENT_WS_MESSAGE,
                _uhttp_server.EVENT_WS_CHUNK_FIRST,
                _uhttp_server.EVENT_WS_CHUNK_NEXT,
                _uhttp_server.EVENT_WS_CHUNK_LAST):
            ws_server = self._ws_clients.get(client)
            if ws_server:
                ws_server.process_message(client)
        elif client.event == _uhttp_server.EVENT_WS_CLOSE:
            ws_server = self._ws_clients.pop(client, None)
            if ws_server:
                ws_server.remove_connection(client)
        elif client.event == _uhttp_server.EVENT_HEADERS:
            client.accept_body()
        elif client.event in (
                _uhttp_server.EVENT_COMPLETE, _uhttp_server.EVENT_REQUEST):
            self._handle_request(client)

    def process_stale(self):
        """Cleanup expired sessions and handle pending reload"""
        # Keep-alive and header timeouts have no event to ride on, so
        # uhttp needs a tick every pass; it throttles its own scans.
        for server, *_ in self._servers:
            server.maintenance()
        if self._auth:
            self._auth.cleanup()
        for monitor in list(self._monitor_servers.values()):
            monitor.process_stale()
        if self._pending_reload:
            self._pending_reload = False
            self.reload_http_servers()
        self._broadcast_status()

    def close(self):
        """Close all HTTP servers"""
        for server, *_ in self._servers:
            server.close()

    def _get_ws_endpoints(self):
        """Build mapping of endpoint name -> ServerWebSocket"""
        endpoints = {}
        for proxy in self._serial_proxies:
            for server in proxy.servers:
                if server.protocol == 'WEBSOCKET':
                    endpoints[server.endpoint] = server
        return endpoints

    def _handle_ws_upgrade(self, client):
        """Handle WebSocket upgrade request"""
        path = client.path
        if not path.startswith('/ws/'):
            client.respond({'error': 'Not found'}, status=404)
            return
        endpoint_name = path[4:]
        # Monitor endpoint: /ws/monitor/<port-name>
        if endpoint_name.startswith('monitor/'):
            self._handle_ws_monitor(client, endpoint_name[8:])
            return
        endpoints = self._get_ws_endpoints()
        ws_server = endpoints.get(endpoint_name)
        if not ws_server:
            client.respond({'error': 'Not found'}, status=404)
            return
        # IP filter check
        if ws_server.ip_filter:
            client_ip = client.addr[0] if isinstance(client.addr, tuple) else None
            if client_ip and not ws_server.ip_filter.is_allowed(client_ip):
                self._log.info(
                    "WebSocket rejected (IP filter): %s", client_ip)
                client.respond({'error': 'Forbidden'}, status=403)
                return
        # Auth: per-server token, global auth, or both
        # No auth configured and no per-server token → allow
        token = self._get_bearer_token(client)
        if ws_server.token and token == ws_server.token:
            pass  # per-server token matches
        elif self._auth and not self._auth.is_empty:
            if not token:
                client.respond(
                    {'error': 'Authorization required'}, status=401)
                return
            # Try global auth first, then per-server token
            user = self._auth.authenticate(token)
            if not user and token != ws_server.token:
                client.respond(
                    {'error': 'Invalid or expired token'}, status=401)
                return
        elif ws_server.token:
            # No global auth, but server has token
            if token != ws_server.token:
                client.respond(
                    {'error': 'Authorization required'}, status=401)
                return
        client.accept_websocket()
        self._ws_clients[client] = ws_server
        ws_server.add_connection(client)

    def _handle_ws_monitor(self, client, port_name):
        """Handle WebSocket monitor upgrade request"""
        # Find serial proxy by name
        proxy = None
        for p in self._serial_proxies:
            if p.name == port_name:
                proxy = p
                break
        if not proxy:
            client.respond({'error': 'Port not found'}, status=404)
            return
        # Auth check (same as regular endpoints)
        token = self._get_bearer_token(client)
        if self._auth and not self._auth.is_empty:
            if not token:
                client.respond(
                    {'error': 'Authorization required'}, status=401)
                return
            user = self._auth.authenticate(token)
            if not user:
                client.respond(
                    {'error': 'Invalid or expired token'}, status=401)
                return
        # Get or create monitor server for this port
        if port_name not in self._monitor_servers:
            self._monitor_servers[port_name] = _server_monitor.ServerMonitor(
                proxy, log=self._log)
        monitor = self._monitor_servers[port_name]
        client.accept_websocket()
        self._ws_clients[client] = monitor
        monitor.add_connection(client)

    def _get_bearer_token(self, client):
        """Extract token from Authorization header or query parameter"""
        auth = client.headers.get('authorization', '')
        if auth.startswith('Bearer '):
            return auth[7:]
        if client.query:
            return client.query.get('token')
        return None

    def _error(self, client, error, status):
        """Log warning and send error response"""
        self._log.warning("%s", error)
        client.respond({'error': error}, status=status)

    def _require_auth(self, client):
        """Check authentication, return user info or None (sends 401)"""
        if not self._auth or self._auth.is_empty:
            return {'login': None, 'admin': True}
        token = self._get_bearer_token(client)
        if not token:
            self._error(client, 'Authorization required', 401)
            return None
        user = self._auth.authenticate(token)
        if not user:
            self._error(client, 'Invalid or expired token', 401)
            return None
        return user

    def _handle_request(self, client):
        """Handle HTTP request"""
        if self._log.isEnabledFor(_logging.INFO):
            self._log.info("%s %s", client.method, client.path)
        # Login endpoint - no auth required
        if client.method == 'POST' and client.path == '/api/login':
            self._handle_api_login(client)
            return
        # Logout endpoint
        if client.method == 'POST' and client.path == '/api/logout':
            self._handle_api_logout(client)
            return
        # WebSocket terminal clients
        if client.method == 'GET' \
                and client.path.startswith('/xterm/'):
            client.respond_file(str(HTML_DIR / 'xterm.html'))
            return
        if client.method == 'GET' \
                and client.path.startswith('/raw/'):
            client.respond_file(str(HTML_DIR / 'raw.html'))
            return
        if client.method == 'GET' \
                and client.path.startswith('/monitor/'):
            client.respond_file(str(HTML_DIR / 'monitor.html'))
            return
        # Static files - no auth
        if client.method == 'GET' and not client.path.startswith('/api/'):
            self._handle_static(client)
            return
        # All API endpoints require auth
        user = self._require_auth(client)
        if not user:
            return
        if client.method == 'GET' and client.path == '/api/status':
            self._handle_api_status(client, user)
        elif client.method == 'GET' and client.path == '/api/detect':
            self._handle_api_detect(client)
        elif client.path == '/api/ports':
            if client.method == 'POST':
                self._handle_api_ports_add(client, user)
            else:
                self._error(client, 'Method not allowed', 405)
        elif client.method == 'GET' and client.path == '/api/signals':
            self._handle_api_signals(client)
        elif client.path.startswith('/api/ports/'):
            self._route_api_ports_item(client, user)
        elif client.path == '/api/users':
            if client.method == 'GET':
                self._handle_api_users_list(client, user)
            elif client.method == 'POST':
                self._handle_api_users_add(client, user)
            else:
                self._error(client, 'Method not allowed', 405)
        elif client.path.startswith('/api/users/'):
            login = client.path[len('/api/users/'):]
            if client.method == 'PUT':
                self._handle_api_users_update(client, user, login)
            elif client.method == 'DELETE':
                self._handle_api_users_delete(client, user, login)
            else:
                self._error(client, 'Method not allowed', 405)
        elif client.path == '/api/tokens':
            if client.method == 'GET':
                self._handle_api_tokens_list(client, user)
            elif client.method == 'POST':
                self._handle_api_tokens_add(client, user)
            else:
                self._error(client, 'Method not allowed', 405)
        elif client.path.startswith('/api/tokens/'):
            token_id = client.path[len('/api/tokens/'):]
            if client.method == 'PUT':
                self._handle_api_tokens_update(client, user, token_id)
            elif client.method == 'DELETE':
                self._handle_api_tokens_delete(client, user, token_id)
            else:
                self._error(client, 'Method not allowed', 405)
        elif client.path == '/api/certs':
            if client.method == 'GET':
                self._handle_api_certs_list(client)
            elif client.method == 'POST':
                self._handle_api_certs_create(client, user)
            else:
                self._error(client, 'Method not allowed', 405)
        elif client.path == '/api/certs/generate-client':
            if client.method == 'POST':
                self._handle_api_certs_generate_client(client, user)
            else:
                self._error(client, 'Method not allowed', 405)
        elif client.path.startswith('/api/certs/'):
            self._route_api_certs_item(client, user)
        elif client.path == '/api/settings':
            if client.method == 'GET':
                self._handle_api_settings_get(client)
            elif client.method == 'PUT':
                self._handle_api_settings_update(client, user)
            else:
                self._error(client, 'Method not allowed', 405)
        elif client.path == '/api/settings/http':
            if client.method == 'POST':
                self._handle_api_http_add(client, user)
            else:
                self._error(client, 'Method not allowed', 405)
        elif client.path.startswith('/api/settings/http/'):
            index = self._http_index(client.path[len('/api/settings/http/'):])
            if index is None:
                self._error(client, 'HTTP server not found', 404)
                return
            if client.method == 'PUT':
                self._handle_api_http_update(client, user, index)
            elif client.method == 'DELETE':
                self._handle_api_http_delete(client, user, index)
            else:
                self._error(client, 'Method not allowed', 405)
        else:
            self._error(client, 'Not found', 404)

    def _handle_static(self, client):
        """Serve static files from html directory"""
        path = client.path.lstrip('/')
        if not path:
            path = 'index.html'
        file_path = (HTML_DIR / path).resolve()
        if not str(file_path).startswith(str(HTML_DIR)):
            self._error(client, 'Not found', 404)
            return
        if not file_path.is_file():
            self._error(client, 'Not found', 404)
            return
        client.respond_file(str(file_path))

    @staticmethod
    def _device_matches(detected, match):
        """True if a detected device matches every key in `match` (case-
        insensitive, `*` is a wildcard)."""
        for k, v in match.items():
            pv = (detected.get(k) or '').upper()
            mv = str(v).upper().replace('*', '.*')
            try:
                if not _re.match('^' + mv + '$', pv):
                    return False
            except _re.error:
                if pv != mv:
                    return False
        return True

    def _compute_port_state(self, proxy, detected):
        """High-level port state used by the UI for color coding:
          'online'  — serial proxy is open and data is flowing
          'offline' — device is present on the system, just no active link
          'error'   — configured device is missing (USB unplugged?)

        The UI colours the port by this and disables Connect on 'error',
        so a false 'error' costs the user a connection that would have
        worked. Enumeration alone is not enough evidence: comports()
        lists USB and built-in serial hardware, and never sees a pty, a
        socat pair or a CDC gadget it does not recognise — all of which
        open and carry data fine. A configured path that exists is
        therefore taken as present, whoever put it there.
        """
        if proxy.error:
            return 'error'
        if proxy.is_connected:
            return 'online'
        match = proxy.match
        device = proxy.serial_config.get('port')
        if match:
            for d in detected:
                if self._device_matches(d, match):
                    return 'offline'
            return 'error'
        if device:
            for d in detected:
                if d.get('device') == device:
                    return 'offline'
            try:
                if _os.path.exists(device):
                    return 'offline'
            except (OSError, TypeError, ValueError):
                pass
            return 'error'
        # No specific device configured — can't say it's missing.
        return 'offline'

    def _build_ports_payload(self, detected=None):
        """Build per-port status payloads (used for /api/status and stream).

        `detected` is the current USB list — used only to compute the
        `state` field. Defaults to the cached value so callers in the
        broadcast tick don't re-enumerate USB just for state.
        """
        if detected is None:
            detected = self._detect_cache
        ports = []
        for proxy in self._serial_proxies:
            serial_cfg = proxy.serial_config
            serial_info = {
                'port': serial_cfg.get('port'),
                'connected': proxy.is_connected,
            }
            for key in ('baudrate', 'bytesize', 'parity', 'stopbits'):
                if key in serial_cfg:
                    serial_info[key] = serial_cfg[key]
            port_info = {'serial': serial_info}
            if proxy.id:
                port_info['id'] = proxy.id
            if proxy.name:
                port_info['name'] = proxy.name
            if proxy.max_connections:
                port_info['max_connections'] = proxy.max_connections
            if proxy.match:
                port_info['serial']['match'] = proxy.match
            servers = []
            for server in proxy.servers:
                if server.protocol == 'WEBSOCKET':
                    srv_info = {
                        'protocol': server.protocol,
                        'endpoint': server.endpoint,
                        'connections': [],
                    }
                    for con in server.connections:
                        try:
                            addr = con.addr
                            if isinstance(addr, tuple) and len(addr) >= 2:
                                shown = '%s:%d' % (addr[0], addr[1])
                            else:
                                shown = str(addr)
                        except Exception:
                            shown = 'unknown'
                        srv_info['connections'].append(
                            {'address': shown, 'id': connection_id(con)})
                else:
                    srv_info = {
                        'protocol': server.protocol,
                        'address': server.config['address'],
                        'connections': [
                            {'address': con.address_str(),
                             'id': connection_id(con)}
                            for con in server.connections
                        ],
                    }
                    if server.protocol != 'SOCKET':
                        srv_info['port'] = server.config['port']
                    if 'ssl' in server.config:
                        srv_info['ssl'] = server.config['ssl']
                if not server.data_enabled:
                    srv_info['data'] = False
                if server.control:
                    srv_info['control'] = server.control
                if server.max_connections:
                    srv_info['max_connections'] = server.max_connections
                servers.append(srv_info)
            port_info['servers'] = servers
            if proxy.is_connected:
                bitmask = proxy.get_signals()
                signals = {}
                for name in _control.SIGNAL_NAMES:
                    bit = _control.SIGNAL_BITS[name]
                    signals[name] = bool(bitmask & (1 << bit))
                port_info['signals'] = signals
            port_info['state'] = self._compute_port_state(proxy, detected)
            if proxy.error:
                # A port that never started. Say why, where the person
                # who has to fix it will see it.
                port_info['error'] = proxy.error
            ports.append(port_info)
        return ports

    @staticmethod
    def _find_port_by_filter(ports, port_name=None, endpoint=None):
        """Find a single port matching a name or WS endpoint. Returns
        (payload, index) or (None, None)."""
        for i, p in enumerate(ports):
            if port_name and p.get('name') == port_name:
                return p, i
            if endpoint:
                for s in p.get('servers', []):
                    if s.get('protocol') == 'WEBSOCKET' \
                            and s.get('endpoint') == endpoint:
                        return p, i
        return None, None

    def _handle_api_status(self, client, user):
        """Runtime status with optional NDJSON streaming and filtering.

        Query parameters:
          stream=1        upgrade response to NDJSON live stream
          port=<name>     restrict output to a single port (matched by
                          `name`); wire format becomes {port: <payload>}
                          instead of {ports: [...]}
          endpoint=<ep>   like `port` but matches by WebSocket endpoint
                          (used by /xterm/<ep> and /raw/<ep> pages which
                          know their endpoint, not the port name)

        One-shot wire format (no `stream`):
          {ports: [...], admin: bool}              all ports
          {port: <payload>, admin: bool}           filtered (single port)

        Streaming wire format (`stream=1`):
          all-ports mode:
            {ports: [...], detected: [...], admin: bool}   full snapshot
            {port_index: i, _delta: true, ...changed}      per-port delta
            {detected: [...]}                              USB plug/unplug
            {}                                             heartbeat
          filtered mode (`port=` or `endpoint=`):
            {port: <payload>, admin: bool}                 full snapshot
            {_delta: true, ...changed_top_level}           sparse delta
            {_removed: true}                               port disappeared
            {}                                             heartbeat
        """
        q = client.query or {}
        is_stream = bool(q.get('stream'))
        port_name = q.get('port')
        endpoint = q.get('endpoint')
        is_admin = user.get('admin', False) if user else False

        detected = self._build_detected_payload()
        ports = self._build_ports_payload(detected=detected)

        if port_name or endpoint:
            match, _idx = self._find_port_by_filter(
                ports, port_name=port_name, endpoint=endpoint)
            if not is_stream:
                if not match:
                    self._error(client, 'Port not found', 404)
                    return
                client.respond({'port': match, 'admin': is_admin})
                return
            # Filtered streaming mode below.
            if not client.response_ndjson(headers={
                    'X-Content-Type-Options': 'nosniff',
                    'X-Accel-Buffering': 'no'}):
                return
            if not client.send_ndjson({'port': match, 'admin': is_admin}):
                return
            self._stream_clients.append({
                'client': client,
                'mode': 'filter',
                'port_name': port_name,
                'endpoint': endpoint,
                'last_port': dict(match) if match else None,
                'admin': is_admin,
                'last_send': _time.time(),
            })
            self._log.debug(
                "filtered status stream client registered (%d total)",
                len(self._stream_clients))
            return

        # All-ports mode.
        if not is_stream:
            client.respond({'ports': ports, 'admin': is_admin})
            return
        if not client.response_ndjson(headers={
                'X-Content-Type-Options': 'nosniff',
                'X-Accel-Buffering': 'no'}):
            return
        if not client.send_ndjson(
                {'ports': ports, 'detected': detected, 'admin': is_admin}):
            return
        self._stream_clients.append({
            'client': client,
            'mode': 'all',
            'last_ports': [dict(p) for p in ports],
            'last_detected': [dict(d) for d in detected],
            'admin': is_admin,
            'last_send': _time.time(),
        })
        self._log.debug(
            "status stream client registered (%d total)",
            len(self._stream_clients))

    def _broadcast_status(self):
        """Push pending status updates to NDJSON streaming clients.

        - Computes the current per-port payload once, then diffs against
          each client's last-sent state.
        - Falls back to a full snapshot when port count or order changed
          (config edits) — simpler than tracking insertion / deletion.
        - Drops clients whose `send_ndjson()` returns False (socket gone).
        """
        if not self._stream_clients:
            return
        now = _time.time()
        # Build once per tick — both modes consume the same source data.
        current_detected = self._build_detected_payload()
        current_ports = self._build_ports_payload(detected=current_detected)
        alive = []
        for entry in self._stream_clients:
            ok = self._broadcast_to_entry(
                entry, now, current_ports, current_detected)
            if ok:
                alive.append(entry)
            else:
                self._log.debug("status stream client disconnected")
        self._stream_clients = alive

    def _broadcast_to_entry(self, entry, now, current_ports, current_detected):
        """Send pending updates to one stream client. Returns False if the
        client's socket is gone (caller drops the entry).

        Peer-EOF detection is now handled inside uhttp itself (its
        process_request_event drains a non-blocking recv on streaming
        connections and raises HttpDisconnected on close), so subsequent
        send_ndjson() returns False and the entry is dropped naturally.
        """
        client = entry['client']
        if entry.get('mode') == 'filter':
            return self._broadcast_filter(
                entry, client, now, current_ports)
        return self._broadcast_all(
            entry, client, now, current_ports, current_detected)

    def _broadcast_all(self, entry, client, now,
            current_ports, current_detected):
        last_ports = entry['last_ports']
        last_detected = entry['last_detected']
        ok = True
        sent_anything = False
        if len(last_ports) != len(current_ports):
            # Port added/removed — replay full snapshot. Cheaper than
            # diffing across mismatched indices.
            ok = client.send_ndjson({
                'ports': current_ports,
                'detected': current_detected,
                'admin': entry['admin']})
            sent_anything = True
        else:
            for idx, (last, cur) in enumerate(
                    zip(last_ports, current_ports)):
                if last == cur:
                    continue
                delta = {'port_index': idx, '_delta': True}
                keys = set(last) | set(cur)
                for k in keys:
                    if last.get(k) != cur.get(k):
                        delta[k] = cur.get(k)
                ok = client.send_ndjson(delta)
                sent_anything = True
                if not ok:
                    break
            if ok and last_detected != current_detected:
                ok = client.send_ndjson({'detected': current_detected})
                sent_anything = True
        if ok and not sent_anything and (now - entry['last_send']) > 30:
            ok = client.send_ndjson({})
            sent_anything = True
        if not ok:
            return False
        if sent_anything:
            entry['last_send'] = now
        entry['last_ports'] = [dict(p) for p in current_ports]
        entry['last_detected'] = [dict(d) for d in current_detected]
        return True

    def _broadcast_filter(self, entry, client, now, current_ports):
        match, _idx = self._find_port_by_filter(
            current_ports,
            port_name=entry.get('port_name'),
            endpoint=entry.get('endpoint'))
        last = entry['last_port']
        ok = True
        sent_anything = False
        if match is None and last is not None:
            # Filtered port disappeared (config edit / rename) — let the
            # client know once, then keep the connection open in case it
            # comes back (cheaper than client reconnects).
            ok = client.send_ndjson({'_removed': True})
            sent_anything = True
        elif match is not None and last is None:
            # Port re-appeared (or was missing at subscribe). Send full
            # payload as a fresh snapshot.
            ok = client.send_ndjson({'port': match, 'admin': entry['admin']})
            sent_anything = True
        elif match is not None and match != last:
            delta = {'_delta': True}
            keys = set(last) | set(match)
            for k in keys:
                if last.get(k) != match.get(k):
                    delta[k] = match.get(k)
            ok = client.send_ndjson(delta)
            sent_anything = True
        if ok and not sent_anything and (now - entry['last_send']) > 30:
            ok = client.send_ndjson({})
            sent_anything = True
        if not ok:
            return False
        if sent_anything:
            entry['last_send'] = now
        entry['last_port'] = dict(match) if match else None
        return True

    DETECT_CACHE_TTL = 1.0  # seconds — see _detect_cache comment in __init__

    def _build_detected_payload(self, force=False):
        """Enumerate currently-attached USB serial devices.

        Cached for DETECT_CACHE_TTL seconds so the stream tick can call
        this every ~100 ms cheaply. Pass `force=True` to bypass the cache
        (used by the REST endpoint which expects a fresh read).

        Logs INFO lines on plug / unplug by diffing against the previous
        cache contents (keyed on device path).
        """
        now = _time.time()
        if not force and (now - self._detect_cache_at) < self.DETECT_CACHE_TTL:
            return self._detect_cache
        ports = []
        for port in _list_ports.comports():
            info = {'device': port.device}
            if port.description and port.description != 'n/a':
                info['description'] = port.description
            if port.hwid and port.hwid != 'n/a':
                info['hwid'] = port.hwid
            if port.vid is not None:
                info['vid'] = f'0x{port.vid:04X}'
                info['pid'] = f'0x{port.pid:04X}'
                if port.serial_number:
                    info['serial_number'] = port.serial_number
                if port.manufacturer:
                    info['manufacturer'] = port.manufacturer
                if port.product:
                    info['product'] = port.product
                if port.location:
                    info['location'] = port.location
            ports.append(info)
        # Skip the diff log on the very first build so we don't spam the
        # log with every device that was already plugged in at startup.
        if self._detect_cache_at:
            prev_by_dev = {p['device']: p for p in self._detect_cache}
            cur_by_dev = {p['device']: p for p in ports}
            for dev, info in cur_by_dev.items():
                if dev not in prev_by_dev:
                    self._log.info(
                        "USB device plugged: %s", _describe_detected(info))
            for dev, info in prev_by_dev.items():
                if dev not in cur_by_dev:
                    self._log.info(
                        "USB device unplugged: %s", _describe_detected(info))
        self._detect_cache = ports
        self._detect_cache_at = now
        return ports

    def _handle_api_detect(self, client):
        """Return list of available serial ports (REST, fresh read)"""
        client.respond(self._build_detected_payload(force=True))

    def _handle_api_signals(self, client):
        """Return signal states for all ports"""
        result = []
        for proxy in self._serial_proxies:
            bitmask = proxy.get_signals()
            signals = {}
            for name in _control.SIGNAL_NAMES:
                bit = _control.SIGNAL_BITS[name]
                signals[name] = bool(bitmask & (1 << bit))
            result.append({
                'name': proxy.name,
                'connected': proxy.is_connected,
                'signals': signals,
            })
        client.respond(result)

    def _save_config(self):
        """Save configuration to config file"""
        if not self._config_path or not self._configuration:
            return
        with open(self._config_path, 'w', encoding='utf-8') as f:
            _json.dump(self._configuration, f, indent=4)
            f.write('\n')

    def _get_ports_config(self):
        """Get ports list from configuration"""
        ports = self._configuration.get('ports', [])
        if isinstance(self._configuration, list):
            ports = self._configuration
        return ports

    def _route_api_ports_item(self, client, user):
        """Route /api/ports/<id>/... requests.

        The id is resolved to a position once, here; everything below
        works by position.
        """
        rest = client.path[len('/api/ports/'):]
        parts = rest.split('/')
        index = self._port_index(parts[0])
        if index is None:
            self._error(client, 'Port not found', 404)
            return
        if len(parts) == 1:
            if client.method == 'GET':
                self._handle_api_ports_get(client, index)
            elif client.method == 'PUT':
                self._handle_api_ports_update(client, user, index)
            elif client.method == 'DELETE':
                self._handle_api_ports_delete(client, user, index)
            else:
                self._error(client, 'Method not allowed', 405)
        elif len(parts) == 2 and parts[1] == 'signals' \
                and client.method == 'PUT':
            self._handle_api_set_signals(client, user, index)
        elif len(parts) == 3 and parts[1] == 'connections' \
                and client.method == 'DELETE':
            self._handle_api_disconnect(client, user, index, parts[2])
        else:
            self._error(client, 'Not found', 404)

    def _handle_api_ports_get(self, client, index):
        """Return one port's stored configuration.

        The configuration, not the runtime status: the editor needs
        what was written down, and /api/status reports what is
        happening instead. Reading the status for this dropped every
        setting it does not report - IP filters, tokens, timeouts - and
        handed back serial settings already converted for pyserial, so
        saving the form rewrote the port with different ones.
        """
        ports = self._get_ports_config()
        client.respond(ports[index])

    @staticmethod
    def _is_port_number(value):
        """True for a usable TCP port number.

        bool is a subclass of int, and socket.bind() takes True as 1,
        so it has to be excluded explicitly.
        """
        return isinstance(value, int) and not isinstance(value, bool) \
            and 1 <= value <= 65535

    def _validate_port_config(self, data):
        """Validate port configuration, return error string or None.

        Type checks are not pedantry here: past this point the values
        reach .upper(), dict keys and socket.bind(), all of which raise
        TypeError on the wrong type. Nothing between an API handler and
        the event loop catches that, so a malformed request used to
        take every serial port in the process down with it.
        """
        if not isinstance(data, dict):
            return f'Expected JSON object, got {type(data).__name__}'
        if 'name' in data and not isinstance(data['name'], str):
            return 'name must be a string'
        if 'id' in data and not _config_ids.is_valid_id(data['id']):
            return ('id may only contain letters, digits, dot, dash and '
                    'underscore')
        if 'serial' not in data:
            return 'serial config required'
        serial = data['serial']
        if not isinstance(serial, dict):
            return 'Invalid serial config'
        if 'port' not in serial and 'match' not in serial:
            return "serial config must have 'port' or 'match'"
        if 'port' in serial and not isinstance(serial['port'], str):
            return 'serial port must be a string'
        if 'match' in serial and not isinstance(serial['match'], dict):
            return 'serial match must be an object'
        # Validate port-level max_connections (0 = unlimited, default)
        if 'max_connections' in data:
            max_conn = data['max_connections']
            if not isinstance(max_conn, int) or max_conn < 0:
                return 'max_connections must be 0 or positive integer'
        if 'servers' not in data or not isinstance(data['servers'], list):
            return 'servers list required'
        if not data['servers']:
            return 'At least one server required'
        for srv in data['servers']:
            if not isinstance(srv, dict):
                return 'Invalid server config'
            if 'protocol' not in srv:
                return 'Server protocol required'
            if not isinstance(srv['protocol'], str):
                return 'Server protocol must be a string'
            proto = srv['protocol'].upper()
            if proto not in ('TCP', 'TELNET', 'SSL', 'SOCKET', 'WEBSOCKET'):
                return f'Unknown protocol: {srv["protocol"]}'
            if not srv.get('data', True) and 'control' not in srv:
                return '"data": false requires "control" config'
            if proto == 'WEBSOCKET':
                if 'endpoint' not in srv:
                    return 'WebSocket endpoint required'
                # Endpoints are dict keys in the routing table, so a
                # list here is an unhashable type, not just wrong.
                if not isinstance(srv['endpoint'], str):
                    return 'WebSocket endpoint must be a string'
                if 'token' in srv and not isinstance(srv['token'], str):
                    return 'WebSocket token must be a string'
            elif proto == 'SOCKET':
                if 'address' not in srv:
                    return 'Socket path (address) required'
                if not isinstance(srv['address'], str):
                    return 'Socket path (address) must be a string'
            else:
                if 'port' not in srv:
                    return 'Server port required'
                if not self._is_port_number(srv['port']):
                    return 'Server port must be an integer 1-65535'
                if 'address' in srv \
                        and not isinstance(srv['address'], str):
                    return 'Server address must be a string'
            if proto == 'SSL':
                if 'ssl' not in srv:
                    return 'SSL protocol requires ssl config'
                err = self._validate_ssl_config(srv['ssl'])
                if err:
                    return err
            if 'control' in srv:
                if proto == 'TELNET':
                    return 'Control not supported with TELNET'
                ctl = srv['control']
                if not isinstance(ctl, dict):
                    return 'Invalid control config'
                if 'signals' in ctl:
                    if not isinstance(ctl['signals'], list):
                        return 'control.signals must be a list'
                    for sig in ctl['signals']:
                        if not isinstance(sig, str):
                            return 'control.signals entries must be strings'
                        if sig.lower() not in _control.SIGNAL_BITS:
                            return f'Unknown signal: {sig}'
            # Validate IP filter config
            for key in ('allow', 'deny'):
                if key in srv:
                    if not isinstance(srv[key], list):
                        return f'{key} must be a list'
                    for network in srv[key]:
                        if not isinstance(network, str):
                            return f'{key} entries must be strings'
            # Validate max_connections (0 = unlimited)
            if 'max_connections' in srv:
                max_conn = srv['max_connections']
                if not isinstance(max_conn, int) or max_conn < 0:
                    return 'max_connections must be 0 or positive integer'
        return None

    def _get_used_endpoints(self, exclude_index=None):
        """Return set of endpoint names used across all proxies"""
        endpoints = set()
        for i, proxy in enumerate(self._serial_proxies):
            if i == exclude_index:
                continue
            for server in proxy.servers:
                if server.protocol == 'WEBSOCKET':
                    endpoints.add(server.endpoint)
        return endpoints

    def _validate_endpoints(self, data, exclude_index=None):
        """Check for duplicate endpoints, return error or None"""
        used = self._get_used_endpoints(exclude_index)
        seen = set()
        for srv in data.get('servers', []):
            proto = srv.get('protocol', '').upper()
            if proto != 'WEBSOCKET':
                continue
            ep = srv.get('endpoint')
            if ep in seen:
                return f'Duplicate endpoint in config: {ep}'
            if ep in used:
                return f'Endpoint already in use: {ep}'
            seen.add(ep)
        return None

    def _create_proxy(self, config):
        """Create SerialProxy from config.

        The selector must be passed on: a port rebuilt through the API
        registers its listening sockets and its serial source itself,
        and without them it accepts nothing and reads nothing while
        still looking configured.
        """
        return _serial_proxy.SerialProxy(
            config, self._log, certs_dir=self._cert_manager.certs_dir,
            selector=self._selector)

    def _handle_api_ports_add(self, client, user):
        """Add new port configuration"""
        if not self._require_admin(client, user):
            return
        data = client.data
        error = self._validate_port_config(data)
        if not error:
            error = self._validate_endpoints(data)
        if error:
            self._error(client, error, 400)
            return
        data['id'], id_error = self._claim_id(data)
        if id_error:
            self._error(client, id_error, 400)
            return
        try:
            proxy = self._create_proxy(data)
        except (ValueError, KeyError, OSError, _server.ConfigError) as err:
            self._error(client, str(err), 400)
            return
        self._serial_proxies.append(proxy)
        if self._server_manager:
            self._server_manager.add_server(proxy)
        ports = self._get_ports_config()
        ports.append(data)
        if 'ports' not in self._configuration:
            self._configuration['ports'] = ports
        self._save_config()
        self._log.info("Port added: %s", data.get('id'))
        client.respond({'ok': True, 'id': data.get('id')}, status=201)

    def _handle_api_ports_update(self, client, user, index):
        """Update port configuration"""
        if not self._require_admin(client, user):
            return
        ports = self._get_ports_config()
        if index < 0 or index >= len(ports):
            self._error(client, 'Port not found', 404)
            return
        data = client.data
        error = self._validate_port_config(data)
        if not error:
            error = self._validate_endpoints(data, exclude_index=index)
        if error:
            self._error(client, error, 400)
            return
        # Close old proxy first to release ports
        # The id is settled before the proxy is built, so it carries
        # it; left out of the request it stays as it was.
        data['id'], id_error = self._claim_id(
            data, ports[index].get('id'))
        if id_error:
            self._error(client, id_error, 400)
            return
        old_proxy = self._serial_proxies[index]
        old_proxy.close()
        if self._server_manager:
            self._server_manager.remove_server(old_proxy)
        try:
            new_proxy = self._create_proxy(data)
        except (ValueError, KeyError, OSError, _server.ConfigError) as err:
            # Rollback: recreate old proxy
            try:
                old_proxy = self._create_proxy(ports[index])
                self._serial_proxies[index] = old_proxy
                if self._server_manager:
                    self._server_manager.add_server(old_proxy)
            except Exception:
                pass
            self._error(client, str(err), 400)
            return
        if self._server_manager:
            self._server_manager.add_server(new_proxy)
        self._serial_proxies[index] = new_proxy
        ports[index] = data
        self._save_config()
        self._log.info("Port updated: %d", index)
        client.respond({'ok': True})

    def _handle_api_ports_delete(self, client, user, index):
        """Delete port configuration"""
        if not self._require_admin(client, user):
            return
        ports = self._get_ports_config()
        if index < 0 or index >= len(ports):
            self._error(client, 'Port not found', 404)
            return
        old_proxy = self._serial_proxies[index]
        old_proxy.close()
        if self._server_manager:
            self._server_manager.remove_server(old_proxy)
        del self._serial_proxies[index]
        del ports[index]
        self._save_config()
        self._log.info("Port deleted: %d", index)
        client.respond({'ok': True})

    def _handle_api_set_signals(self, client, user, index):
        """Set RTS/DTR signals on a port"""
        if not self._require_admin(client, user):
            return
        if index < 0 or index >= len(self._serial_proxies):
            self._error(client, 'Port not found', 404)
            return
        proxy = self._serial_proxies[index]
        if not proxy.is_connected:
            self._error(client, 'Port not connected', 400)
            return
        data = client.data
        if not isinstance(data, dict):
            self._error(client, 'Invalid request', 400)
            return
        if 'rts' in data:
            proxy.set_rts(bool(data['rts']))
        if 'dtr' in data:
            proxy.set_dtr(bool(data['dtr']))
        client.respond({'ok': True})

    def _handle_api_disconnect(self, client, user, port_idx, conn_id):
        """Disconnect one client of this port, named by its id.

        A position would not do: a connection index shifts every time
        some other client on the same server hangs up, so the request
        to drop one would land on another.
        """
        if port_idx < 0 or port_idx >= len(self._serial_proxies):
            self._error(client, 'Port not found', 404)
            return
        proxy = self._serial_proxies[port_idx]
        for server in proxy.servers:
            for con in list(server.connections):
                if connection_id(con) != conn_id:
                    continue
                addr = server.disconnect_client(con)
                self._log.info("Disconnected: %s", addr)
                client.respond({'ok': True})
                return
        self._error(client, 'Connection not found', 404)

    def _handle_api_login(self, client):
        """Authenticate user and return session token"""
        if not self._auth:
            self._error(client, 'Auth not configured', 404)
            return
        data = client.data
        if not isinstance(data, dict):
            self._error(client, 'Invalid request', 400)
            return
        login = data.get('login', '')
        password = data.get('password', '')
        token = self._auth.login(login, password)
        if not token:
            self._error(client, f'Login failed: {login}', 401)
            return
        self._log.info("Login: %s", login)
        client.respond({'token': token})

    def _handle_api_logout(self, client):
        """Invalidate session"""
        if not self._auth:
            self._error(client, 'Auth not configured', 404)
            return
        token = self._get_bearer_token(client)
        if token:
            self._auth.logout(token)
        client.respond({'ok': True})

    @staticmethod
    def _validate_auth_fields(data, fields):
        """Type-check user/token fields, return error string or None.

        `fields` maps a key to 'string', 'nonempty' or 'timeout'. Only
        keys actually present are checked, so one table serves both the
        add endpoints (where the caller has already established what is
        required) and the update ones, which take any subset.

        These are not cosmetic checks: a login and a token are dict
        keys, a password is concatenated with a salt, and a session
        timeout is added to time.time().
        """
        for key, kind in fields.items():
            if key not in data:
                continue
            value = data[key]
            if kind == 'timeout':
                # null means "use the default", same as globally.
                if value is None:
                    continue
                if isinstance(value, bool) \
                        or not isinstance(value, (int, float)) \
                        or value < 0:
                    return f'{key} must be a non-negative number or null'
            elif not isinstance(value, str):
                return f'{key} must be a string'
            elif kind == 'nonempty' and not value:
                return f'{key} must not be empty'
        return None

    def _require_admin(self, client, user):
        """Check if user is admin, send 403 if not"""
        if not user.get('admin'):
            self._error(client, 'Admin access required', 403)
            return False
        return True

    def _ensure_auth(self):
        """Create auth if not exists, return SessionManager"""
        if not self._auth:
            self._auth = _http_auth.SessionManager({})
        return self._auth

    def _save_auth_config(self):
        """Save auth config to config file (users, tokens at root level)"""
        if not self._config_path or not self._configuration:
            return
        auth_config = self._auth.get_auth_config()
        # Save at root level
        if auth_config.get('users'):
            self._configuration['users'] = auth_config['users']
        elif 'users' in self._configuration:
            del self._configuration['users']
        if auth_config.get('tokens'):
            self._configuration['tokens'] = auth_config['tokens']
        elif 'tokens' in self._configuration:
            del self._configuration['tokens']
        if 'session_timeout' in auth_config:
            self._configuration['session_timeout'] = auth_config['session_timeout']
        # Remove old auth from http configs (migration)
        http_configs = self._configuration.get('http', [])
        if isinstance(http_configs, dict):
            http_configs = [http_configs]
        for config in http_configs:
            config.pop('auth', None)
        self._save_config()

    def _handle_api_users_list(self, client, user):
        """List users (without passwords)"""
        if not self._require_admin(client, user):
            return
        if not self._auth:
            client.respond([])
            return
        client.respond(self._auth.list_users())

    def _handle_api_users_add(self, client, user):
        """Add new user"""
        if not self._require_admin(client, user):
            return
        data = client.data
        if not isinstance(data, dict) or 'login' not in data \
                or 'password' not in data:
            self._error(client, 'login and password required', 400)
            return
        error = self._validate_auth_fields(data, {
            'login': 'nonempty', 'password': 'string',
            'session_timeout': 'timeout'})
        if error:
            self._error(client, error, 400)
            return
        kwargs = {}
        if 'admin' in data:
            kwargs['admin'] = bool(data['admin'])
        if 'session_timeout' in data:
            kwargs['session_timeout'] = data['session_timeout']
        auth = self._ensure_auth()
        is_first = auth.is_empty
        if not auth.add_user(data['login'], data['password'], **kwargs):
            self._error(client, 'User already exists', 400)
            return
        self._save_auth_config()
        self._log.info("User added: %s", data['login'])
        if is_first:
            token = auth.create_session(data['login'])
            client.respond({'ok': True, 'token': token}, status=201)
        else:
            client.respond({'ok': True}, status=201)

    def _handle_api_users_update(self, client, user, login):
        """Update existing user"""
        if not self._auth:
            self._error(client, 'User not found', 404)
            return
        if not self._require_admin(client, user):
            return
        data = client.data
        if not isinstance(data, dict):
            self._error(client, 'Invalid request', 400)
            return
        error = self._validate_auth_fields(data, {
            'password': 'string', 'session_timeout': 'timeout'})
        if error:
            self._error(client, error, 400)
            return
        kwargs = {}
        if 'password' in data:
            kwargs['password'] = data['password']
        if 'admin' in data:
            kwargs['admin'] = bool(data['admin'])
        if 'session_timeout' in data:
            kwargs['session_timeout'] = data['session_timeout']
        result = self._auth.update_user(login, **kwargs)
        if result is False:
            self._error(client, 'User not found', 404)
            return
        if isinstance(result, str):
            self._error(client, result, 400)
            return
        self._save_auth_config()
        self._log.info("User updated: %s", login)
        client.respond({'ok': True})

    def _handle_api_users_delete(self, client, user, login):
        """Delete user"""
        if not self._auth:
            self._error(client, 'User not found', 404)
            return
        if not self._require_admin(client, user):
            return
        result = self._auth.delete_user(login)
        if result is False:
            self._error(client, 'User not found', 404)
            return
        if isinstance(result, str):
            self._error(client, result, 400)
            return
        self._save_auth_config()
        self._log.info("User deleted: %s", login)
        client.respond({'ok': True})

    def _handle_api_tokens_list(self, client, user):
        """List API tokens"""
        if not self._require_admin(client, user):
            return
        if not self._auth:
            client.respond([])
            return
        client.respond(self._auth.list_tokens())

    def _handle_api_tokens_add(self, client, user):
        """Add new API token"""
        if not self._require_admin(client, user):
            return
        data = client.data
        if not isinstance(data, dict) or 'token' not in data \
                or 'name' not in data:
            self._error(client, 'token and name required', 400)
            return
        error = self._validate_auth_fields(data, {
            'token': 'nonempty', 'name': 'string'})
        if error:
            self._error(client, error, 400)
            return
        auth = self._ensure_auth()
        admin = bool(data.get('admin', False))
        if not auth.add_token(data['token'], data['name'], admin):
            self._error(client, 'Token already exists', 400)
            return
        self._save_auth_config()
        self._log.info("Token added: %s", data['name'])
        client.respond({'ok': True}, status=201)

    def _handle_api_tokens_update(self, client, user, token):
        """Update API token"""
        if not self._auth:
            self._error(client, 'Token not found', 404)
            return
        if not self._require_admin(client, user):
            return
        data = client.data
        if not isinstance(data, dict):
            self._error(client, 'Invalid request', 400)
            return
        error = self._validate_auth_fields(data, {
            'token': 'nonempty', 'name': 'string'})
        if error:
            self._error(client, error, 400)
            return
        kwargs = {}
        if 'token' in data:
            kwargs['token'] = data['token']
        if 'name' in data:
            kwargs['name'] = data['name']
        if 'admin' in data:
            kwargs['admin'] = bool(data['admin'])
        result = self._auth.update_token(token, **kwargs)
        if result is False:
            self._error(client, 'Token not found', 404)
            return
        if isinstance(result, str):
            self._error(client, result, 400)
            return
        self._save_auth_config()
        self._log.info("Token updated: %s", token[:8] + '...')
        client.respond({'ok': True})

    def _handle_api_tokens_delete(self, client, user, token):
        """Delete API token"""
        if not self._auth:
            self._error(client, 'Token not found', 404)
            return
        if not self._require_admin(client, user):
            return
        result = self._auth.delete_token(token)
        if result is False:
            self._error(client, 'Token not found', 404)
            return
        if isinstance(result, str):
            self._error(client, result, 400)
            return
        self._save_auth_config()
        self._log.info("Token deleted: %s", token[:8] + '...')
        client.respond({'ok': True})

    def _handle_api_settings_get(self, client):
        """Return settings (http servers, session_timeout)"""
        settings = {
            # Normalised to a list: the config allows a single object,
            # and a client that got one had to special-case it or
            # conclude there were no servers at all.
            'http': self._http_list(),
            'session_timeout': self._configuration.get('session_timeout'),
        }
        client.respond(settings)

    def _handle_api_settings_update(self, client, user):
        """Update settings"""
        if not self._require_admin(client, user):
            return
        data = client.data
        if not isinstance(data, dict):
            self._error(client, f'Expected JSON object, got {type(data).__name__}', 400)
            return
        if 'session_timeout' in data:
            val = data['session_timeout']
            if val is not None and (not isinstance(val, int) or val < 0):
                self._error(client, 'session_timeout must be positive integer or null', 400)
                return
            if val is None:
                self._configuration.pop('session_timeout', None)
            else:
                self._configuration['session_timeout'] = val
        self._save_config()
        self._log.info("Settings updated")
        client.respond({'ok': True})

    def _validate_http_config(self, data):
        """Validate HTTP server config, return error string or None"""
        if not isinstance(data, dict):
            return f'Expected JSON object, got {type(data).__name__}'
        if 'port' not in data:
            return 'port is required'
        if not self._is_port_number(data['port']):
            return 'port must be 1-65535'
        if 'address' in data and not isinstance(data['address'], str):
            return 'address must be a string'
        if 'name' in data and not isinstance(data['name'], str):
            return 'name must be a string'
        if 'id' in data and not _config_ids.is_valid_id(data['id']):
            return ('id may only contain letters, digits, dot, dash and '
                    'underscore')
        if 'ssl' in data:
            err = self._validate_ssl_config(data['ssl'])
            if err:
                return err
        return None

    def _validate_ssl_config(self, ssl):
        """Validate {"bundle": "...", "require_client_cert": bool} block.
        Checks that the referenced bundle exists and has required files."""
        if not isinstance(ssl, dict):
            return 'ssl must be an object'
        bundle = ssl.get('bundle')
        if not bundle:
            return 'ssl requires bundle name'
        mtls = bool(ssl.get('require_client_cert'))
        try:
            _cert_manager.resolve_bundle_paths(
                self._cert_manager.certs_dir, bundle,
                require_client_cert=mtls)
        except _cert_manager.CertManagerError as err:
            return str(err)
        return None

    def _http_list(self, create=False):
        """The HTTP server entries, as a list.

        The config allows a single object instead of a list; normalise
        it once here, and put the list back so everything downstream -
        including appending to it - works on the same object.
        """
        servers = self._configuration.get('http')
        if isinstance(servers, dict):
            servers = [servers]
            self._configuration['http'] = servers
        elif servers is None:
            servers = []
            if create:
                self._configuration['http'] = servers
        return servers

    @staticmethod
    def _http_entry(data, entry_id):
        """The entry to store for an HTTP server.

        Everything the request carries is kept apart from the id, which
        belongs to the entry rather than to what is in it. Rebuilding
        from a list of known keys instead is how an IP filter used to
        disappear from a server whose name was edited.
        """
        entry = {key: value for key, value in data.items() if key != 'id'}
        entry['id'] = entry_id
        if not entry.get('name'):
            entry.pop('name', None)
        entry.setdefault('address', '0.0.0.0')
        return entry

    def _handle_api_http_add(self, client, user):
        """Add new HTTP server"""
        if not self._require_admin(client, user):
            return
        data = client.data
        error = self._validate_http_config(data)
        if error:
            self._error(client, error, 400)
            return
        http_list = self._http_list(create=True)
        new_id, id_error = self._claim_id(data)
        if id_error:
            self._error(client, id_error, 400)
            return
        srv = self._http_entry(data, new_id)
        # Only write it down once it is actually running.
        try:
            srv_tuple = self._create_http_server(srv)
        except ValueError as e:
            self._error(client, str(e), 400)
            return
        http_list.append(srv)
        self._servers.append(srv_tuple)
        self._save_config()
        self._log.info("HTTP server added")
        client.respond({'ok': True, 'id': srv.get('id')})

    def _handle_api_http_update(self, client, user, index):
        """Update HTTP server"""
        if not self._require_admin(client, user):
            return
        http_list = self._http_list()
        if index < 0 or index >= len(http_list):
            self._error(client, 'HTTP server not found', 404)
            return
        data = client.data
        error = self._validate_http_config(data)
        if error:
            self._error(client, error, 400)
            return
        old = http_list[index]
        new_id, id_error = self._claim_id(data, old.get('id'))
        if id_error:
            self._error(client, id_error, 400)
            return
        srv = self._http_entry(data, new_id)
        needs_restart = (
            old.get('address', '0.0.0.0') != srv.get('address', '0.0.0.0') or
            old.get('port') != srv.get('port') or
            old.get('ssl') != srv.get('ssl') or
            old.get('allow') != srv.get('allow') or
            old.get('deny') != srv.get('deny'))
        if needs_restart and index < len(self._servers):
            self._servers[index][0].close()
            try:
                self._servers[index] = self._create_http_server(srv)
            except ValueError as err:
                # Put back what was running, and leave the file alone:
                # it must not describe a server this process is not.
                try:
                    self._servers[index] = self._create_http_server(old)
                except ValueError as back_err:
                    self._log.error(
                        "HTTP server could not be restored: %s", back_err)
                self._error(client, str(err), 400)
                return
        http_list[index] = srv
        self._save_config()
        self._log.info("HTTP server updated")
        client.respond({'ok': True})

    def _handle_api_http_delete(self, client, user, index):
        """Delete HTTP server"""
        if not self._require_admin(client, user):
            return
        http_list = self._http_list()
        if index < 0 or index >= len(http_list):
            self._error(client, 'HTTP server not found', 404)
            return
        if len(http_list) <= 1:
            self._error(client, 'Cannot delete last HTTP server', 400)
            return
        # Close server before removing from config
        if index < len(self._servers):
            self._servers[index][0].close()
            del self._servers[index]
        del http_list[index]
        self._save_config()
        self._log.info("HTTP server deleted")
        client.respond({'ok': True})

    # ------------------------------------------------------------------
    # Certificate bundles
    # ------------------------------------------------------------------

    def _find_bundle_usage(self, bundle_name):
        """Return list of servers using this bundle. Each entry is a
        dict describing where the bundle is referenced — used for
        "used_by" display in the UI and to block deletion of bundles
        currently in use. `mtls` says whether that server verifies
        client certificates, i.e. whether ca.pem matters to it."""
        usage = []
        # Port SSL servers
        for p_idx, port in enumerate(self._get_ports_config()):
            for s_idx, srv in enumerate(port.get('servers', [])):
                ssl_cfg = srv.get('ssl')
                if not ssl_cfg or ssl_cfg.get('bundle') != bundle_name:
                    continue
                usage.append({
                    'type': 'port',
                    'port_index': p_idx,
                    'port_name': port.get('name'),
                    'server_index': s_idx,
                    'address': srv.get('address'),
                    'server_port': srv.get('port'),
                    'mtls': bool(ssl_cfg.get('require_client_cert')),
                })
        # HTTP servers
        http_list = self._configuration.get('http', [])
        if isinstance(http_list, dict):
            http_list = [http_list]
        for h_idx, srv in enumerate(http_list):
            ssl_cfg = srv.get('ssl')
            if not ssl_cfg or ssl_cfg.get('bundle') != bundle_name:
                continue
            usage.append({
                'type': 'http',
                'index': h_idx,
                'name': srv.get('name'),
                'address': srv.get('address'),
                'server_port': srv.get('port'),
                'mtls': bool(ssl_cfg.get('require_client_cert')),
            })
        return usage

    def _handle_api_certs_list(self, client):
        """List all cert bundles (auth required, not admin)."""
        try:
            bundles = self._cert_manager.list_bundles()
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 400)
            return
        for b in bundles:
            b['used_by'] = self._find_bundle_usage(b['name'])
        client.respond({
            'certs_dir': self._cert_manager.certs_dir,
            'bundles': bundles,
        })

    def _handle_api_certs_create(self, client, user):
        """Create an empty bundle (admin)."""
        if not self._require_admin(client, user):
            return
        data = client.data
        if not isinstance(data, dict) or 'name' not in data:
            self._error(client, 'name is required', 400)
            return
        try:
            self._cert_manager.create_bundle(data['name'])
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 400)
            return
        client.respond({'ok': True}, status=201)

    def _route_api_certs_item(self, client, user):
        """Route /api/certs/<bundle>[/files[/<filename>]]."""
        rest = client.path[len('/api/certs/'):]
        parts = rest.split('/')
        bundle = parts[0]
        if not bundle:
            self._error(client, 'Bundle name required', 400)
            return
        # /api/certs/<bundle>
        if len(parts) == 1:
            if client.method == 'GET':
                self._handle_api_certs_get(client, bundle)
            elif client.method == 'DELETE':
                self._handle_api_certs_delete(client, user, bundle)
            else:
                self._error(client, 'Method not allowed', 405)
            return
        # /api/certs/<bundle>/files
        if len(parts) == 2 and parts[1] == 'files':
            if client.method == 'POST':
                self._handle_api_certs_save_file(client, user, bundle)
            else:
                self._error(client, 'Method not allowed', 405)
            return
        # /api/certs/<bundle>/files/<filename>
        if len(parts) == 3 and parts[1] == 'files':
            filename = parts[2]
            if client.method == 'GET':
                self._handle_api_certs_download_file(client, bundle, filename)
            elif client.method == 'DELETE':
                self._handle_api_certs_delete_file(
                    client, user, bundle, filename)
            else:
                self._error(client, 'Method not allowed', 405)
            return
        # /api/certs/<bundle>/reload
        if len(parts) == 2 and parts[1] == 'reload':
            if client.method == 'POST':
                self._handle_api_certs_reload(client, user, bundle)
            else:
                self._error(client, 'Method not allowed', 405)
            return
        # /api/certs/<bundle>/generate
        if len(parts) == 2 and parts[1] == 'generate':
            if client.method == 'POST':
                self._handle_api_certs_generate(client, user, bundle)
            else:
                self._error(client, 'Method not allowed', 405)
            return
        self._error(client, 'Not found', 404)

    def _handle_api_certs_get(self, client, bundle):
        try:
            info = self._cert_manager.get_bundle(bundle)
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 404)
            return
        info['used_by'] = self._find_bundle_usage(bundle)
        client.respond(info)

    def _handle_api_certs_delete(self, client, user, bundle):
        if not self._require_admin(client, user):
            return
        usage = self._find_bundle_usage(bundle)
        if usage:
            self._error(
                client,
                f"Bundle '{bundle}' is in use by {len(usage)} server(s); "
                "remove SSL references first",
                400)
            return
        try:
            self._cert_manager.delete_bundle(bundle)
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 404)
            return
        client.respond({'ok': True})

    def _handle_api_certs_save_file(self, client, user, bundle):
        """Upload one file ({filename, content}) or a set of them
        ({files: [{filename, content}, ...]}).

        Replacing a certificate means replacing cert.pem and key.pem
        together — sent one at a time they would be rejected as a
        mismatched pair, so the set form exists to send both at once.
        """
        if not self._require_admin(client, user):
            return
        data = client.data
        if not isinstance(data, dict):
            self._error(client, 'Expected JSON object', 400)
            return
        if isinstance(data.get('files'), list):
            entries = data['files']
        elif 'filename' in data and 'content' in data:
            entries = [data]
        else:
            self._error(
                client, 'filename and content (or files) required', 400)
            return
        items = []
        for entry in entries:
            if not isinstance(entry, dict) \
                    or 'filename' not in entry or 'content' not in entry:
                self._error(
                    client, 'each file needs filename and content', 400)
                return
            items.append((entry['filename'], entry['content']))
        if not items:
            self._error(client, 'No files given', 400)
            return
        try:
            self._cert_manager.save_files(bundle, items)
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 400)
            return
        client.respond({'ok': True})

    def _handle_api_certs_delete_file(self, client, user, bundle, filename):
        if not self._require_admin(client, user):
            return
        try:
            self._cert_manager.delete_file(bundle, filename)
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 404)
            return
        client.respond({'ok': True})

    def _parse_generate_params(self, data, require_signer=False):
        """Validate common generate-cert params. Returns dict of normalized
        params or raises ValueError with a user-facing message."""
        if not isinstance(data, dict):
            raise ValueError('Expected JSON object')
        cn = (data.get('cn') or '').strip()
        if not cn:
            raise ValueError('cn is required')
        try:
            days = int(data.get('days', 365))
        except (TypeError, ValueError):
            raise ValueError('days must be integer')
        if days < 1 or days > 36500:
            raise ValueError('days must be 1-36500')
        key_type = data.get('key_type', 'rsa2048')
        if key_type not in _cert_manager.KEY_TYPES:
            raise ValueError(
                f"Unknown key_type (allowed: {', '.join(_cert_manager.KEY_TYPES)})")
        san_dns = data.get('san_dns') or []
        san_ip = data.get('san_ip') or []
        if not isinstance(san_dns, list) or not isinstance(san_ip, list):
            raise ValueError('san_dns and san_ip must be arrays')
        signer_name = data.get('signer_bundle')
        if require_signer and not signer_name:
            raise ValueError('signer_bundle is required')
        return {
            'cn': cn, 'days': days, 'key_type': key_type,
            'san_dns': san_dns, 'san_ip': san_ip,
            'signer_name': signer_name,
        }

    def _load_signer(self, signer_name):
        """Load a signer (cert, key) pair from a bundle. Raises ValueError
        with user-facing message on any problem."""
        try:
            cert_path, key_path, _ca = _cert_manager.resolve_bundle_paths(
                self._cert_manager.certs_dir, signer_name)
        except _cert_manager.CertManagerError as err:
            raise ValueError(str(err)) from err
        with open(cert_path, 'r', encoding='utf-8') as f:
            cert_pem = f.read()
        with open(key_path, 'r', encoding='utf-8') as f:
            key_pem = f.read()
        try:
            return _cert_manager.parse_signer(cert_pem, key_pem), cert_pem
        except _cert_manager.CertManagerError as err:
            raise ValueError(str(err)) from err

    def _handle_api_certs_generate(self, client, user, bundle):
        """Generate a cert+key into <bundle>. Mode controls intent:
          self_signed — standalone server cert
          ca          — root CA cert (basicConstraints CA:TRUE)
          signed_by   — server cert signed by another bundle's CA;
                        also copies signer's cert to bundle/ca.pem so
                        the server can immediately do mTLS
        Bundle is created on demand if it doesn't exist yet.
        Will refuse to overwrite an existing cert.pem in the target."""
        if not self._require_admin(client, user):
            return
        data = client.data or {}
        mode = data.get('mode', 'self_signed')
        if mode not in ('self_signed', 'ca', 'signed_by'):
            self._error(client, f"Unknown mode '{mode}'", 400)
            return
        try:
            params = self._parse_generate_params(
                data, require_signer=(mode == 'signed_by'))
        except ValueError as err:
            self._error(client, str(err), 400)
            return
        # Refuse to clobber an existing cert.pem so a typo doesn't
        # silently overwrite a working cert.
        try:
            existing = self._cert_manager.get_bundle(bundle)
            if existing['files']['cert.pem'].get('present'):
                self._error(
                    client,
                    f"Bundle '{bundle}' already has cert.pem; delete it first",
                    400)
                return
        except _cert_manager.CertManagerError:
            pass  # bundle doesn't exist yet, that's fine
        signer = None
        signer_cert_pem = None
        if mode == 'signed_by':
            try:
                signer, signer_cert_pem = self._load_signer(params['signer_name'])
            except ValueError as err:
                self._error(client, str(err), 400)
                return
        try:
            cert_pem, key_pem = _cert_manager.generate_certificate(
                cn=params['cn'], days=params['days'],
                key_type=params['key_type'],
                is_ca=(mode == 'ca'),
                san_dns=params['san_dns'], san_ip=params['san_ip'],
                signer=signer)
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 400)
            return
        files = [('cert.pem', cert_pem), ('key.pem', key_pem)]
        # Copy signer's cert into ca.pem for one-step mTLS setup
        if mode == 'signed_by' and signer_cert_pem:
            files.append(('ca.pem', signer_cert_pem))
        try:
            self._cert_manager.save_files(bundle, files)
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 400)
            return
        self._log.info(
            "Generated %s cert for bundle '%s' (CN=%s)",
            mode, bundle, params['cn'])
        client.respond({'ok': True}, status=201)

    def _handle_api_certs_generate_client(self, client, user):
        """Generate a client cert signed by a CA bundle. Returns cert+key+ca
        as PEM strings; not stored server-side. The UI packages them as a
        download (zip or .p12) for the human to install on the client."""
        if not self._require_admin(client, user):
            return
        data = client.data or {}
        try:
            params = self._parse_generate_params(data, require_signer=True)
        except ValueError as err:
            self._error(client, str(err), 400)
            return
        try:
            signer, signer_cert_pem = self._load_signer(params['signer_name'])
        except ValueError as err:
            self._error(client, str(err), 400)
            return
        try:
            cert_pem, key_pem = _cert_manager.generate_certificate(
                cn=params['cn'], days=params['days'],
                key_type=params['key_type'],
                is_client=True, signer=signer,
                san_dns=params['san_dns'], san_ip=params['san_ip'])
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 400)
            return
        self._log.info(
            "Generated client cert signed by '%s' (CN=%s)",
            params['signer_name'], params['cn'])
        client.respond({
            'cn': params['cn'],
            'cert_pem': cert_pem,
            'key_pem': key_pem,
            'ca_pem': signer_cert_pem,
        })

    def _reload_bundle_contexts(self, bundle_name):
        """Re-read bundle_name into every live SSLContext that uses it.

        Returns (reloaded, errors): labels of the servers that picked up
        the new files, and user-facing messages for those that refused.
        A server whose reload fails keeps serving its previous cert —
        load_cert_chain() raises before installing anything.
        """
        reloaded = []
        errors = []
        for proxy in self._serial_proxies:
            for srv in proxy.servers:
                ssl_cfg = srv.config.get('ssl') or {}
                if srv.protocol != 'SSL' \
                        or ssl_cfg.get('bundle') != bundle_name:
                    continue
                label = "port %s:%s" % (
                    srv.config.get('address'), srv.config.get('port'))
                try:
                    srv.reload_ssl_context()
                except _cert_manager.CertManagerError as err:
                    errors.append(f"{label}: {err}")
                else:
                    reloaded.append(label)
        for _server, _flt, cfg, ctx in self._servers:
            ssl_cfg = cfg.get('ssl') or {}
            if ctx is None or ssl_cfg.get('bundle') != bundle_name:
                continue
            label = "http %s:%s" % (
                cfg.get('address', '0.0.0.0'), cfg.get('port'))
            try:
                _cert_manager.reload_ssl_context(
                    ctx, ssl_cfg, self._cert_manager.certs_dir)
            except _cert_manager.CertManagerError as err:
                errors.append(f"{label}: {err}")
            else:
                reloaded.append(label)
        return reloaded, errors

    def _handle_api_certs_reload(self, client, user, bundle):
        """Re-read a bundle into the SSLContext of every server using it.

        This is what makes a renewed certificate (Let's Encrypt deploy
        hook, re-upload, generate) take effect without a restart: new
        handshakes get the new cert, established connections are left
        alone. A CA removed from ca.pem still needs a restart — see
        cert_manager.reload_ssl_context().
        """
        if not self._require_admin(client, user):
            return
        try:
            self._cert_manager.get_bundle(bundle)
        except _cert_manager.CertManagerError as err:
            self._error(client, str(err), 404)
            return
        reloaded, errors = self._reload_bundle_contexts(bundle)
        if errors:
            self._error(client, '; '.join(errors), 400)
            return
        self._log.info(
            "Bundle '%s' reloaded into %d server(s)", bundle, len(reloaded))
        client.respond({'ok': True, 'reloaded': reloaded})

    def _handle_api_certs_download_file(self, client, bundle, filename):
        try:
            content = self._cert_manager.read_public_file(bundle, filename)
        except _cert_manager.CertManagerError as err:
            # Distinguish "forbidden" (key.pem) from "not found"
            status = 403 if 'private' in str(err) else 404
            self._error(client, str(err), status)
            return
        # Return as JSON so the UI can show it; downloading as text/plain
        # is also fine but JSON is consistent with other endpoints.
        client.respond({'filename': filename, 'content': content})
