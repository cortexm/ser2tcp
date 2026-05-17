"""Serial proxy - serial port management and USB device matching"""

import fnmatch as _fnmatch
import logging as _logging
import socket as _socket
import threading as _threading
import time as _time

import serial as _serial
import serial.tools.list_ports as _list_ports

import ser2tcp.connection_control as _control
import ser2tcp.server as _server
import ser2tcp.server_websocket as _server_websocket


class SerialProxy():
    """Serial connection manager"""
    PARITY_CONFIG = {
        'NONE': _serial.PARITY_NONE,
        'EVEN': _serial.PARITY_EVEN,
        'ODD': _serial.PARITY_ODD,
        'MARK': _serial.PARITY_MARK,
        'SPACE': _serial.PARITY_SPACE,
    }
    STOPBITS_CONFIG = {
        'ONE': _serial.STOPBITS_ONE,
        'ONE_POINT_FIVE': _serial.STOPBITS_ONE_POINT_FIVE,
        'TWO': _serial.STOPBITS_TWO,
    }
    BYTESIZE_CONFIG = {
        'FIVEBITS': _serial.FIVEBITS,
        'SIXBITS': _serial.SIXBITS,
        'SEVENBITS': _serial.SEVENBITS,
        'EIGHTBITS': _serial.EIGHTBITS,
    }
    MATCH_ATTRIBUTES = ('vid', 'pid', 'serial_number', 'manufacturer',
        'product', 'location', 'description', 'hwid')

    def __init__(self, config, log=None, certs_dir=None):
        self._log = log if log else _logging.Logger(self.__class__.__name__)
        self._serial = None
        self._certs_dir = certs_dir
        self._reader_thread = None
        self._reader_sock_r = None
        self._reader_sock_w = None
        self._reader_running = False
        self._servers = []
        self._monitors = []
        self._last_signals = 0
        self._last_signal_poll = 0
        self._signal_poll_interval = 0.1
        self._has_control_servers = False
        self._name = config.get('name', '')
        self._max_connections = config.get('max_connections', 0)
        self._match = config['serial'].get('match')
        self._serial_config = self._init_serial_config(config['serial'])
        port = self._serial_config.get('port')
        baudrate = self._serial_config.get('baudrate')
        name = self._name or port or f"match:{self._match}"
        if baudrate:
            self._log.info("Serial: %s %d", name, baudrate)
        else:
            self._log.info("Serial: %s", name)
        for server_config in config['servers']:
            proto = server_config.get('protocol', '').upper()
            if proto == 'WEBSOCKET':
                self._servers.append(
                    _server_websocket.ServerWebSocket(
                        server_config, self, log))
            else:
                self._servers.append(
                    _server.Server(
                        server_config, self, log,
                        certs_dir=self._certs_dir))
        # Detect control-enabled servers and set poll interval
        for server in self._servers:
            if server.control:
                self._has_control_servers = True
                interval = server.control.get('poll_interval')
                if interval is not None:
                    self._signal_poll_interval = interval

    def _init_serial_config(self, config):
        """Initialize serial configuration - validate and convert enum values"""
        if 'port' not in config and 'match' not in config:
            raise ValueError("Serial config must have 'port' or 'match'")
        config = {k: v for k, v in config.items() if k != 'match'}
        if 'parity' in config:
            for key, val in self.PARITY_CONFIG.items():
                if config['parity'] == key:
                    config['parity'] = val
        if 'stopbits' in config:
            for key, val in self.STOPBITS_CONFIG.items():
                if config['stopbits'] == key:
                    config['stopbits'] = val
        if 'bytesize' in config:
            for key, val in self.BYTESIZE_CONFIG.items():
                if config['bytesize'] == key:
                    config['bytesize'] = val
        return config

    def find_port_by_match(self, match):
        """Find serial port by matching USB device attributes"""
        if not match:
            raise ValueError("Match criteria cannot be empty")
        for key in match:
            if key not in self.MATCH_ATTRIBUTES:
                raise ValueError(f"Unknown match attribute: {key}")
        matched_ports = []
        for port_info in _list_ports.comports():
            if self._port_matches(port_info, match):
                matched_ports.append(port_info.device)
        if not matched_ports:
            raise ValueError(f"No device found matching: {match}")
        if len(matched_ports) > 1:
            raise ValueError(
                f"Multiple devices match {match}: {matched_ports}")
        return matched_ports[0]

    def _port_matches(self, port_info, match):
        """Check if port_info matches all criteria"""
        for attr, pattern in match.items():
            value = getattr(port_info, attr, None)
            if value is None:
                return False
            # Convert vid/pid to hex string for comparison
            if attr in ('vid', 'pid') and isinstance(value, int):
                value = f"0x{value:04X}"
            else:
                value = str(value)
            # Case-insensitive wildcard matching
            if not _fnmatch.fnmatch(value.upper(), str(pattern).upper()):
                return False
        return True

    def __del__(self):
        self.close()

    def _start_reader_thread_if_needed(self):
        """Start reader thread if serial port doesn't support fileno()"""
        try:
            self._serial.fileno()
        except OSError:
            self._start_reader_thread()

    def _serial_reader_run(self):
        """Reader thread: read from serial, forward to socketpair"""
        while self._reader_running:
            try:
                data = self._serial.read(size=max(1, self._serial.in_waiting))
                if data:
                    self._reader_sock_w.sendall(data)
            except (OSError, _serial.SerialException):
                break

    def _start_reader_thread(self):
        """Start reader thread with socketpair for select() compatibility"""
        self._reader_sock_r, self._reader_sock_w = _socket.socketpair()
        self._reader_running = True
        self._reader_thread = _threading.Thread(
            target=self._serial_reader_run, daemon=True)
        self._reader_thread.start()
        self._log.debug("Serial reader thread started")

    def _stop_reader_thread(self):
        """Stop reader thread and close socketpair"""
        if self._reader_thread is None:
            return
        self._reader_running = False
        self._reader_thread.join(timeout=2)
        self._reader_sock_r.close()
        self._reader_sock_w.close()
        self._reader_thread = None
        self._reader_sock_r = None
        self._reader_sock_w = None

    @property
    def name(self):
        """Return port name"""
        return self._name

    @property
    def serial_config(self):
        """Return serial configuration"""
        return self._serial_config

    @property
    def match(self):
        """Return match criteria"""
        return self._match

    @property
    def is_connected(self):
        """Return True if serial port is connected"""
        return self._serial is not None

    @property
    def servers(self):
        """Return list of servers"""
        return self._servers

    @property
    def max_connections(self):
        """Return max connections limit (0 = unlimited)"""
        return self._max_connections

    def connect(self):
        """Connect to serial port"""
        if not self._serial:
            if self._match:
                try:
                    self._serial_config['port'] = self.find_port_by_match(
                        self._match)
                except ValueError as err:
                    self._log.warning(err)
                    return False
            try:
                self._serial = _serial.Serial(**self._serial_config)
            except (_serial.SerialException, OSError) as err:
                self._log.warning(err)
                return False
            self._log.info(
                "Serial %s connected", self._serial_config['port'])
            self._start_reader_thread_if_needed()
        return True

    def has_connections(self):
        """Check if there are any active connections"""
        for server in self._servers:
            if server.has_connections():
                return True
        return False

    def total_connections(self):
        """Return total number of connections across all servers"""
        return sum(len(server.connections) for server in self._servers)

    def can_add_connection(self):
        """Check if new connection can be added (port-level limit)"""
        if self._max_connections > 0:
            return self.total_connections() < self._max_connections
        return True

    def disconnect(self):
        """Disconnect serial port, but if there are no active connections"""
        if self._serial and not self.has_connections():
            self._stop_reader_thread()
            self._serial.close()
            self._serial = None
            self._log.info(
                "Serial %s disconnected", self._serial_config['port'])
            if getattr(self, '_match', None):
                del self._serial_config['port']

    def close(self):
        """Close socket and all connections"""
        while self._servers:
            self._servers.pop().close()
        self.disconnect()

    def read_sockets(self):
        """Return all sockets for reading"""
        sockets = []
        for server in self._servers:
            sockets += server.read_sockets()
        if self._serial:
            if self._reader_sock_r:
                sockets.append(self._reader_sock_r)
            else:
                sockets.append(self._serial)
        return sockets

    def write_sockets(self):
        """Return all sockets for writing (with pending data)"""
        sockets = []
        for server in self._servers:
            sockets += server.write_sockets()
        return sockets

    def send_to_connections(self, data):
        """Send data to all connections"""
        for server in self._servers:
            server.send(data)

    def _process_serial_data(self):
        """Read and forward serial data to connections"""
        try:
            if self._reader_sock_r:
                data = self._reader_sock_r.recv(4096)
            else:
                data = self._serial.read(size=self._serial.in_waiting)
            if data:
                self._log.debug("(%s): %s", self._serial_config['port'], data)
                self.send_to_connections(data)
                self._notify_monitors(2, data)  # RX
            else:
                raise OSError("Serial reader closed")
        except (OSError, _serial.SerialException) as err:
            self._log.warning(err)
            for server in self._servers:
                server.close_connections()
            self.disconnect()

    def process_read(self, read_sockets):
        """Process sockets with read event"""
        for server in self._servers:
            server.process_read(read_sockets)
        serial_sock = self._reader_sock_r or self._serial
        if self._serial and serial_sock in read_sockets:
            self._process_serial_data()

    def process_write(self, write_sockets):
        """Process sockets with write event"""
        for server in self._servers:
            server.process_write(write_sockets)

    def process_stale(self):
        """Remove stale connections"""
        for server in self._servers:
            server.process_stale()
        self.process_signals()

    def set_rts(self, value):
        """Set RTS signal and broadcast report to all clients"""
        if self._serial:
            self._serial.rts = value
            self._broadcast_signals()

    def set_dtr(self, value):
        """Set DTR signal and broadcast report to all clients"""
        if self._serial:
            self._serial.dtr = value
            self._broadcast_signals()

    def get_signals(self):
        """Get current signal states as bitmask"""
        if not self._serial:
            return 0
        bitmask = 0
        try:
            if self._serial.rts:
                bitmask |= (1 << _control.SIGNAL_BITS['rts'])
            if self._serial.dtr:
                bitmask |= (1 << _control.SIGNAL_BITS['dtr'])
            if self._serial.cts:
                bitmask |= (1 << _control.SIGNAL_BITS['cts'])
            if self._serial.dsr:
                bitmask |= (1 << _control.SIGNAL_BITS['dsr'])
            if self._serial.ri:
                bitmask |= (1 << _control.SIGNAL_BITS['ri'])
            if self._serial.cd:
                bitmask |= (1 << _control.SIGNAL_BITS['cd'])
        except OSError:
            pass
        return bitmask

    def _broadcast_signals(self):
        """Broadcast signal report to all control-enabled servers"""
        bitmask = self.get_signals()
        for server in self._servers:
            server.send_signal_report(bitmask)
        self._last_signals = bitmask

    def process_signals(self):
        """Poll serial signals and broadcast changes"""
        if not self._serial or not self._has_control_servers:
            return
        now = _time.time()
        if now - self._last_signal_poll < self._signal_poll_interval:
            return
        self._last_signal_poll = now
        bitmask = self.get_signals()
        if bitmask != self._last_signals:
            self._last_signals = bitmask
            for server in self._servers:
                server.send_signal_report(bitmask)

    def send(self, data):
        """Send data to serial port"""
        if self._serial:
            self._serial.write(data)
            self._notify_monitors(1, data)  # TX

    def add_monitor(self, callback):
        """Register monitor callback - receives (direction, data)"""
        if callback not in self._monitors:
            self._monitors.append(callback)

    def remove_monitor(self, callback):
        """Unregister monitor callback"""
        if callback in self._monitors:
            self._monitors.remove(callback)

    def _notify_monitors(self, direction, data):
        """Notify all monitors - direction: 1=TX, 2=RX"""
        if self._monitors:
            self._log.debug(
                "Monitor notify: dir=%d len=%d monitors=%d",
                direction, len(data), len(self._monitors))
        for callback in list(self._monitors):
            try:
                callback(direction, data)
            except Exception as e:
                self._log.warning("Monitor callback error: %s", e)
