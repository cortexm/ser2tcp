"""HTTP server integration with uhttp"""

import json as _json
import logging as _logging
import os as _os
import pathlib as _pathlib
import re as _re
import ssl as _ssl
import time as _time

import serial.tools.list_ports as _list_ports

import uhttp.server as _uhttp_server

import ser2tcp.http_auth as _http_auth
import ser2tcp.connection_control as _control
import ser2tcp.ip_filter as _ip_filter
import ser2tcp.serial_proxy as _serial_proxy
import ser2tcp.server as _server
import ser2tcp.server_monitor as _server_monitor

HTML_DIR = _pathlib.Path(__file__).parent / 'html'


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
            server_manager=None):
        self._log = log if log else _logging.getLogger(__name__)
        self._serial_proxies = serial_proxies
        self._server_manager = server_manager
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
        self._ws_clients = {}  # uhttp client -> ServerWebSocket or ServerMonitor
        self._monitor_servers = {}  # port name -> ServerMonitor
        self._servers = []  # list of (HttpServer, IpFilter or None)
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
        for config in configs:
            address = config.get('address', '0.0.0.0')
            port = config.get('port', 8080)
            ssl_context = None
            if 'ssl' in config:
                ssl_config = config['ssl']
                certfile = ssl_config.get('certfile')
                keyfile = ssl_config.get('keyfile')
                if not certfile or not keyfile:
                    self._log.error(
                        "HTTPS server %s:%d: missing certfile or keyfile, skipping",
                        address, port)
                    continue
                if not _os.path.exists(certfile):
                    self._log.error(
                        "HTTPS server %s:%d: certfile not found: %s, skipping",
                        address, port, certfile)
                    continue
                if not _os.path.exists(keyfile):
                    self._log.error(
                        "HTTPS server %s:%d: keyfile not found: %s, skipping",
                        address, port, keyfile)
                    continue
                ssl_context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
                ssl_context.load_cert_chain(certfile, keyfile)
                self._log.info(
                    "HTTPS server: %s:%d", address, port)
            else:
                self._log.info(
                    "HTTP server: %s:%d", address, port)
            ip_flt = _ip_filter.create_filter(config, log=self._log)
            try:
                server = _uhttp_server.HttpServer(
                    address=address, port=port, ssl_context=ssl_context,
                    event_mode=True)
            except OSError as err:
                self._log.error(
                    "HTTP server %s:%d: failed to bind: %s, skipping",
                    address, port, err.strerror or err)
                continue
            self._servers.append((server, ip_flt))

    def _create_http_server(self, config):
        """Create a single HTTP server from config, return (server, ip_flt) or None"""
        address = config.get('address', '0.0.0.0')
        port = config.get('port', 8080)
        ssl_context = None
        if 'ssl' in config:
            ssl_config = config['ssl']
            certfile = ssl_config.get('certfile')
            keyfile = ssl_config.get('keyfile')
            if not certfile or not keyfile:
                raise ValueError(f"HTTPS {address}:{port}: missing certfile or keyfile")
            if not _os.path.exists(certfile):
                raise ValueError(f"HTTPS {address}:{port}: certfile not found: {certfile}")
            if not _os.path.exists(keyfile):
                raise ValueError(f"HTTPS {address}:{port}: keyfile not found: {keyfile}")
            ssl_context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(certfile, keyfile)
            self._log.info("HTTPS server: %s:%d", address, port)
        else:
            self._log.info("HTTP server: %s:%d", address, port)
        ip_flt = _ip_filter.create_filter(config, log=self._log)
        try:
            server = _uhttp_server.HttpServer(
                address=address, port=port, ssl_context=ssl_context,
                event_mode=True)
        except OSError as err:
            raise ValueError(
                f"HTTP {address}:{port}: failed to bind: "
                f"{err.strerror or err}") from err
        return (server, ip_flt)

    def add_http_server(self, config):
        """Add a new HTTP server dynamically"""
        srv_tuple = self._create_http_server(config)
        self._servers.append(srv_tuple)
        return len(self._servers) - 1

    def remove_http_server(self, index):
        """Remove HTTP server by index"""
        if index < 0 or index >= len(self._servers):
            raise ValueError("Invalid server index")
        server, _ = self._servers[index]
        server.close()
        del self._servers[index]

    def reload_http_servers(self):
        """Reload all HTTP servers from current configuration"""
        # Close all existing servers
        for server, _ in self._servers:
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

    def read_sockets(self):
        """Return sockets for reading"""
        sockets = []
        for server, _ in self._servers:
            sockets.extend(server.read_sockets)
        return sockets

    def write_sockets(self):
        """Return sockets for writing"""
        sockets = []
        for server, _ in self._servers:
            sockets.extend(server.write_sockets)
        return sockets

    def process_read(self, read_sockets):
        """Process read events - also handles writes for uhttp"""
        self._process_uhttp(read_sockets, [])

    def process_write(self, write_sockets):
        """Process write events"""
        self._process_uhttp([], write_sockets)

    def _process_uhttp(self, read_sockets, write_sockets):
        """Process uhttp events"""
        for server, ip_flt in self._servers:
            client = server.process_events(read_sockets, write_sockets)
            if client:
                # Check IP filter for new requests
                if ip_flt and client.event in (
                        _uhttp_server.EVENT_REQUEST,
                        _uhttp_server.EVENT_HEADERS,
                        _uhttp_server.EVENT_WS_REQUEST):
                    client_ip = client.addr[0] \
                        if isinstance(client.addr, tuple) else None
                    if client_ip and not ip_flt.is_allowed(client_ip):
                        self._log.info(
                            "HTTP rejected (IP filter): %s", client_ip)
                        client.respond({'error': 'Forbidden'}, status=403)
                        continue
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
                elif client.event == _uhttp_server.EVENT_COMPLETE:
                    self._handle_request(client)
                elif client.event == _uhttp_server.EVENT_REQUEST:
                    self._handle_request(client)


    def process_stale(self):
        """Cleanup expired sessions and handle pending reload"""
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
        for server, _ in self._servers:
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
            try:
                index = int(client.path[len('/api/settings/http/'):])
            except ValueError:
                self._error(client, 'Invalid index', 400)
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
        """
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
                                srv_info['connections'].append(
                                    {'address': '%s:%d' % (addr[0], addr[1])})
                            else:
                                srv_info['connections'].append(
                                    {'address': str(addr)})
                        except Exception:
                            srv_info['connections'].append(
                                {'address': 'unknown'})
                else:
                    srv_info = {
                        'protocol': server.protocol,
                        'address': server.config['address'],
                        'connections': [
                            {'address': con.address_str()}
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
        """Route /api/ports/<index>/... requests"""
        rest = client.path[len('/api/ports/'):]
        parts = rest.split('/')
        try:
            index = int(parts[0])
        except ValueError:
            self._error(client, 'Invalid port index', 400)
            return
        if len(parts) == 1:
            if client.method == 'PUT':
                self._handle_api_ports_update(client, user, index)
            elif client.method == 'DELETE':
                self._handle_api_ports_delete(client, user, index)
            else:
                self._error(client, 'Method not allowed', 405)
        elif len(parts) == 2 and parts[1] == 'signals' \
                and client.method == 'PUT':
            self._handle_api_set_signals(client, user, index)
        elif len(parts) == 4 and parts[1] == 'connections' \
                and client.method == 'DELETE':
            try:
                srv_idx = int(parts[2])
                con_idx = int(parts[3])
            except ValueError:
                self._error(client, 'Invalid index', 400)
                return
            self._handle_api_disconnect(client, user, index,
                srv_idx, con_idx)
        else:
            self._error(client, 'Not found', 404)

    def _validate_port_config(self, data):
        """Validate port configuration, return error string or None"""
        if not isinstance(data, dict):
            return f'Expected JSON object, got {type(data).__name__}'
        if 'serial' not in data:
            return 'serial config required'
        serial = data['serial']
        if not isinstance(serial, dict):
            return 'Invalid serial config'
        if 'port' not in serial and 'match' not in serial:
            return "serial config must have 'port' or 'match'"
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
            proto = srv['protocol'].upper()
            if proto not in ('TCP', 'TELNET', 'SSL', 'SOCKET', 'WEBSOCKET'):
                return f'Unknown protocol: {srv["protocol"]}'
            if not srv.get('data', True) and 'control' not in srv:
                return '"data": false requires "control" config'
            if proto == 'WEBSOCKET':
                if 'endpoint' not in srv:
                    return 'WebSocket endpoint required'
            elif proto == 'SOCKET':
                if 'address' not in srv:
                    return 'Socket path (address) required'
            else:
                if 'port' not in srv:
                    return 'Server port required'
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
        """Create SerialProxy from config"""
        proxy = _serial_proxy.SerialProxy(config, self._log)
        return proxy

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
        self._log.info("Port added: %d", len(self._serial_proxies) - 1)
        client.respond({'ok': True, 'index': len(self._serial_proxies) - 1},
            status=201)

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

    def _handle_api_disconnect(self, client, user, port_idx,
            srv_idx, con_idx):
        """Disconnect a specific client connection"""
        if port_idx < 0 or port_idx >= len(self._serial_proxies):
            self._error(client, 'Port not found', 404)
            return
        proxy = self._serial_proxies[port_idx]
        if srv_idx < 0 or srv_idx >= len(proxy.servers):
            self._error(client, 'Server not found', 404)
            return
        server = proxy.servers[srv_idx]
        if con_idx < 0 or con_idx >= len(server.connections):
            self._error(client, 'Connection not found', 404)
            return
        con = server.connections[con_idx]
        addr = con.address_str()
        server._remove_connection(con)
        self._log.info("Disconnected: %s", addr)
        client.respond({'ok': True})

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
            'http': self._configuration.get('http', []),
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
        if not isinstance(data['port'], int) or data['port'] < 1 or data['port'] > 65535:
            return 'port must be 1-65535'
        if 'ssl' in data:
            ssl = data['ssl']
            if not isinstance(ssl, dict):
                return 'ssl must be an object'
            if not ssl.get('certfile') or not ssl.get('keyfile'):
                return 'ssl requires certfile and keyfile paths'
        return None

    def _handle_api_http_add(self, client, user):
        """Add new HTTP server"""
        if not self._require_admin(client, user):
            return
        data = client.data
        error = self._validate_http_config(data)
        if error:
            self._error(client, error, 400)
            return
        http_list = self._configuration.setdefault('http', [])
        srv = {'address': data.get('address', '0.0.0.0'), 'port': data['port']}
        if data.get('name'):
            srv['name'] = data['name']
        if 'ssl' in data:
            srv['ssl'] = data['ssl']
        # Try to create server before saving config
        try:
            srv_tuple = self._create_http_server(srv)
        except ValueError as e:
            self._error(client, str(e), 400)
            return
        http_list.append(srv)
        self._servers.append(srv_tuple)
        self._save_config()
        self._log.info("HTTP server added")
        client.respond({'ok': True})

    def _handle_api_http_update(self, client, user, index):
        """Update HTTP server"""
        if not self._require_admin(client, user):
            return
        http_list = self._configuration.get('http', [])
        if index < 0 or index >= len(http_list):
            self._error(client, 'HTTP server not found', 404)
            return
        data = client.data
        error = self._validate_http_config(data)
        if error:
            self._error(client, error, 400)
            return
        old = http_list[index]
        srv = {'address': data.get('address', '0.0.0.0'), 'port': data['port']}
        if data.get('name'):
            srv['name'] = data['name']
        if 'ssl' in data:
            srv['ssl'] = data['ssl']
        http_list[index] = srv
        self._save_config()
        # Check if restart needed (address/port/ssl changed)
        needs_restart = (
            old.get('address', '0.0.0.0') != srv.get('address', '0.0.0.0') or
            old.get('port') != srv.get('port') or
            old.get('ssl') != srv.get('ssl'))
        if needs_restart and index < len(self._servers):
            # Restart only this server
            server, _ = self._servers[index]
            server.close()
            try:
                self._servers[index] = self._create_http_server(srv)
            except ValueError as e:
                self._error(client, str(e), 400)
                return
        self._log.info("HTTP server updated")
        client.respond({'ok': True})

    def _handle_api_http_delete(self, client, user, index):
        """Delete HTTP server"""
        if not self._require_admin(client, user):
            return
        http_list = self._configuration.get('http', [])
        if index < 0 or index >= len(http_list):
            self._error(client, 'HTTP server not found', 404)
            return
        if len(http_list) <= 1:
            self._error(client, 'Cannot delete last HTTP server', 400)
            return
        # Close server before removing from config
        if index < len(self._servers):
            server, _ = self._servers[index]
            server.close()
            del self._servers[index]
        del http_list[index]
        self._save_config()
        self._log.info("HTTP server deleted")
        client.respond({'ok': True})
