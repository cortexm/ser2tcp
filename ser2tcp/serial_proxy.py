"""Serial proxy - serial port management and USB device matching"""

import fnmatch as _fnmatch
import logging as _logging
import os as _os
import selectors as _selectors
import socket as _socket
import threading as _threading
import time as _time

import serial as _serial
import serial.tools.list_ports as _list_ports

import ser2tcp.connection_control as _control
import ser2tcp.server as _server
import ser2tcp.server_websocket as _server_websocket


def _format_signals(bitmask):
    """Render a signal bitmask as 'RTS=1 DTR=0 CTS=1 ...' for the log."""
    return ' '.join(
        '%s=%d' % (name.upper(), bool(bitmask & (1 << bit)))
        for bit, name in enumerate(_control.SIGNAL_NAMES))


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

    # A device drains at its baud rate - 256 KB at 9600 is minutes - so
    # what clients hand over is buffered and written as the port takes
    # it. Past the high water mark their sockets stop being read, which
    # closes the TCP window and makes the sender wait; reading starts
    # again below the low mark, so a busy port does not flap on every
    # byte. The hard limit is the last line of defence, for clients that
    # cannot be paused (uhttp owns the WebSocket sockets) or a device
    # that has stopped draining altogether.
    WRITE_HIGH_WATER = 64 * 1024
    WRITE_LOW_WATER = 16 * 1024
    WRITE_BUFFER_LIMIT = 1024 * 1024
    WRITE_WARN_INTERVAL = 10.0
    # A device that accepts nothing at all for this long is not slow,
    # it is stuck - and while it is, clients paused for backpressure
    # are not being watched, so nothing would ever reap them.
    WRITE_STALL_TIMEOUT = 30.0

    def __init__(self, config, log=None, certs_dir=None, selector=None):
        self._log = log if log else _logging.Logger(self.__class__.__name__)
        self._serial = None
        self._out_buffer = bytearray()
        self._read_paused = False
        self._last_drop_warning = 0
        self._write_progress_at = _time.time()
        self._certs_dir = certs_dir
        self._selector = selector
        # What the loop watches for incoming serial data: the port
        # itself, or the socketpair a reader thread feeds for ports with
        # no usable fileno(). None while disconnected.
        self._serial_source = None
        self._serial_interest = None
        self._reader_thread = None
        self._reader_sock_r = None
        self._reader_sock_w = None
        self._reader_running = False
        self._servers = []
        self._monitors = []
        self._last_signals = None
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
                        certs_dir=self._certs_dir,
                        selector=self._selector))
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
        # Non-blocking writes, whatever the config says: pyserial then
        # returns how much it took instead of waiting for the device,
        # and the rest is driven by write interest from the loop.
        config['write_timeout'] = 0
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
            self._register_serial()
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
            # Must come first: the selector cannot unregister a source
            # whose fileno() has already gone away.
            self._unregister_serial()
            self._stop_reader_thread()
            # Whatever is still queued belongs to a port that is gone.
            self._out_buffer.clear()
            if self._read_paused:
                self._set_read_paused(False)
            self._last_signals = None
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

    def _register_serial(self):
        """Watch the open port (or its reader socketpair) for input"""
        if self._selector is None or self._serial_source is not None:
            return
        source = self._reader_sock_r or self._serial
        try:
            self._selector.register(source, _selectors.EVENT_READ, self)
        except (KeyError, ValueError, OSError) as err:
            self._log.warning("Cannot watch serial port: %s", err)
            return
        self._serial_source = source
        self._serial_interest = _selectors.EVENT_READ

    def _unregister_serial(self):
        """Stop watching the port before it is closed"""
        if self._serial_source is None:
            return
        try:
            self._selector.unregister(self._serial_source)
        except (KeyError, ValueError, OSError):
            pass
        self._serial_source = None
        self._serial_interest = None

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
            self._serial_failed()

    def _serial_failed(self):
        """Drop every client and close the port after an I/O error.

        The clients are told by the disconnect; holding them open on a
        port that is gone would only feed them silence.
        """
        for server in self._servers:
            server.close_connections()
        self.disconnect()

    def handle_event(self, fileobj, mask):
        """Owner dispatch for the serial source"""
        if self._serial is None or fileobj is not self._serial_source:
            # Disconnected between the select() and this event.
            return None
        if mask & _selectors.EVENT_WRITE:
            self.flush_serial()
            if self._serial is None:
                return None
        if mask & _selectors.EVENT_READ:
            self._process_serial_data()
        return None

    def process_stale(self):
        """Remove stale connections"""
        for server in self._servers:
            server.process_stale()
        if self._out_buffer and self._serial_source is not self._serial:
            # A port with no usable fileno() is read through a reader
            # thread's socketpair, which says nothing about whether the
            # device will take a write - so retry here instead.
            self.flush_serial()
        self.process_signals()

    def set_rts(self, value):
        """Set RTS signal and broadcast report to all clients.

        Plenty of devices have no modem control lines — a pty or a CDC
        gadget answers the ioctl with ENOTTY — and the request arrives
        from a client, so a refusal is logged rather than raised. Left
        unhandled it would take the whole process down.
        """
        if not self._serial:
            return
        try:
            self._serial.rts = value
        except (OSError, _serial.SerialException) as err:
            self._log.warning("Cannot set RTS on %s: %s",
                self._serial_config.get('port'), err)
            return
        self._log.debug(
            "(%s): set RTS=%d", self._serial_config.get('port'), bool(value))
        self._broadcast_signals()

    def set_dtr(self, value):
        """Set DTR signal and broadcast report to all clients.

        Same caveat as set_rts(): unsupported by many devices, asked for
        by clients, so a failure is logged and dropped.
        """
        if not self._serial:
            return
        try:
            self._serial.dtr = value
        except (OSError, _serial.SerialException) as err:
            self._log.warning("Cannot set DTR on %s: %s",
                self._serial_config.get('port'), err)
            return
        self._log.debug(
            "(%s): set DTR=%d", self._serial_config.get('port'), bool(value))
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
        self._log_signal_change(bitmask)
        for server in self._servers:
            server.send_signal_report(bitmask)
        self._last_signals = bitmask

    def _log_signal_change(self, bitmask):
        """Debug-log a signal transition; silent when nothing moved.

        Input signals are sampled every poll interval, so logging each
        sample would bury everything else — only the edges are worth a
        line. Call before _last_signals is updated.
        """
        if bitmask == self._last_signals:
            return
        self._log.debug(
            "(%s): signals %s%s", self._serial_config.get('port'),
            _format_signals(bitmask),
            ' (initial)' if self._last_signals is None else '')

    def process_signals(self):
        """Poll serial signals, broadcast changes and log transitions.

        Polling normally earns its ioctls only when some server has
        control enabled and wants the reports. Debug logging is the
        other reason to look: without this the input signals (CTS, DSR,
        RI, CD) never change as far as the log is concerned, because
        nothing was sampling them.
        """
        if not self._serial:
            return
        if not self._has_control_servers \
                and not self._log.isEnabledFor(_logging.DEBUG):
            return
        now = _time.time()
        if now - self._last_signal_poll < self._signal_poll_interval:
            return
        self._last_signal_poll = now
        bitmask = self.get_signals()
        if bitmask != self._last_signals:
            self._log_signal_change(bitmask)
            self._last_signals = bitmask
            # Sampling for the log alone stays observational: a port
            # with no control server has nobody to report to.
            if self._has_control_servers:
                for server in self._servers:
                    server.send_signal_report(bitmask)

    def send(self, data):
        """Queue data for the serial port and write what it will take"""
        if not self._serial or not data:
            return
        room = self.WRITE_BUFFER_LIMIT - len(self._out_buffer)
        if len(data) > room:
            # Nothing else to do: the device is not keeping up and the
            # client could not be slowed down. Say so rather than
            # dropping in silence, but not on every write.
            self._warn_dropped(len(data) - max(room, 0))
            data = data[:max(room, 0)]
            if not data:
                return
        if not self._out_buffer:
            self._write_progress_at = _time.time()
        self._out_buffer.extend(data)
        # Monitors see what was accepted for the device, in order.
        self._notify_monitors(1, bytes(data))  # TX
        self.flush_serial()

    def _warn_dropped(self, count):
        """Report dropped output, at most once every few seconds"""
        now = _time.time()
        if now - self._last_drop_warning < self.WRITE_WARN_INTERVAL:
            return
        self._last_drop_warning = now
        self._log.warning(
            "(%s): write buffer full, dropped %d bytes - the device is "
            "not keeping up", self._serial_config.get('port'), count)

    def flush_serial(self):
        """Write as much of the buffer as the device will take"""
        if not self._serial or not self._out_buffer:
            return
        try:
            sent = self._write_device(self._out_buffer)
        except (OSError, _serial.SerialException) as err:
            # A device unplugged mid-write reaches us here rather than
            # on the read side; same response either way.
            self._log.warning(err)
            self._serial_failed()
            return
        if sent:
            del self._out_buffer[:sent]
            self._write_progress_at = _time.time()
        elif self._stalled():
            return
        self._update_serial_interest()
        self._update_backpressure()

    def _write_device(self, data):
        """Hand bytes to the device, returning how many it took.

        Writes through the file descriptor rather than Serial.write():
        with write_timeout=0 pyserial swallows the EAGAIN from a full
        device and loops on it, so a device that can take nothing spins
        a core inside a call that never comes back. Ports with no usable
        descriptor - socket:// and friends, the same ones that need a
        reader thread - still go through pyserial, which uses a blocking
        socket there and returns on its own.
        """
        try:
            fileno = self._serial.fileno()
        except (OSError, AttributeError, NotImplementedError):
            fileno = None
        if fileno is None:
            return self._serial.write(bytes(data)) or 0
        try:
            return _os.write(fileno, data)
        except BlockingIOError:
            return 0

    def _stalled(self):
        """True once the device has taken nothing for far too long.

        Treated as a device failure, which drops the clients: a port
        that accepts nothing is no more use than one that is unplugged,
        and leaving it be would strand clients paused for backpressure
        with nothing watching them.
        """
        if _time.time() - self._write_progress_at < self.WRITE_STALL_TIMEOUT:
            return False
        self._log.warning(
            "(%s): device accepted nothing for %.0fs with %d bytes queued, "
            "giving up on it", self._serial_config.get('port'),
            self.WRITE_STALL_TIMEOUT, len(self._out_buffer))
        self._serial_failed()
        return True

    def _update_serial_interest(self):
        """Arm EVENT_WRITE only while there is something to write.

        Same rule the client connections follow: left armed on an empty
        buffer the loop spins on an always-writable device, never armed
        the backlog never leaves.
        """
        if self._selector is None or self._serial_source is None:
            return
        want = _selectors.EVENT_READ
        if self._out_buffer:
            want |= _selectors.EVENT_WRITE
        if want == self._serial_interest:
            return
        try:
            self._selector.modify(self._serial_source, want, self)
        except (KeyError, ValueError, OSError) as err:
            self._log.warning("Cannot watch serial port: %s", err)
            return
        self._serial_interest = want

    def _update_backpressure(self):
        """Stop or resume reading clients based on the backlog.

        Hysteresis on purpose: pausing at the high mark and resuming at
        the low one keeps a steadily busy port from toggling interest on
        every single write.
        """
        if self._read_paused:
            if len(self._out_buffer) > self.WRITE_LOW_WATER:
                return
            self._set_read_paused(False)
        else:
            if len(self._out_buffer) <= self.WRITE_HIGH_WATER:
                return
            self._set_read_paused(True)

    def _set_read_paused(self, paused):
        self._read_paused = paused
        self._log.debug(
            "(%s): %s reading clients (%d bytes queued)",
            self._serial_config.get('port'),
            'pausing' if paused else 'resuming', len(self._out_buffer))
        for server in self._servers:
            server.set_read_paused(paused)

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
