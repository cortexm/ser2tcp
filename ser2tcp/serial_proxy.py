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


def port_info(proxy):
    """What is on the other end, as a WebSocket client is told it.

    One reader for the endpoint's greeting and the monitor's, so the
    two describe the same port the same way. Always complete: the
    `port` topic is re-sent whole when any of it changes, which saves
    a client from having to merge a partial object into one it holds.
    """
    config = proxy.serial_config or {}
    return {
        'name': proxy.name,
        # Resolved at connect time when the port is found by USB
        # match, so it can still be unknown here.
        'device': config.get('port'),
        'baudrate': config.get('baudrate'),
        'state': proxy.state,
    }


def _format_signals(bitmask):
    """Render a signal bitmask as 'RTS=1 DTR=0 CTS=1 ...' for the log."""
    return ' '.join(
        '%s=%d' % (name.upper(), bool(bitmask & (1 << bit)))
        for bit, name in enumerate(_control.SIGNAL_NAMES))


class FailedProxy():
    """Stands in the list for a port that could not be started.

    Ports are addressed by their position in the configuration, so a
    port left out of the runtime list renumbers every port after it:
    an edit meant for one rewrites another, and a delete removes the
    wrong entry. It also gives the port somewhere to appear, with the
    reason it failed, rather than vanishing from the UI as though it
    had never been configured - which is the state you most want to
    see, since it is the one you have to fix.

    Read-only and inert: it serves nothing and owns nothing.
    """

    def __init__(self, config, error):
        self._config = config if isinstance(config, dict) else {}
        serial = self._config.get('serial')
        self._serial_config = serial if isinstance(serial, dict) else {}
        self._error = str(error)

    @property
    def id(self):
        """Stable identifier from the configuration"""
        return self._config.get('id')

    @property
    def name(self):
        """Return port name"""
        return self._config.get('name', '')

    @property
    def serial_config(self):
        """Return the configured serial settings"""
        return self._serial_config

    @property
    def match(self):
        """Return match criteria"""
        return self._serial_config.get('match')

    @property
    def info(self):
        """What the configuration says is on the other end"""
        return port_info(self)

    @property
    def state(self):
        """Nothing was built, so there is nothing to be wrong with"""
        return 'error'

    def set_state(self, state):
        """Nothing to record: this one is only ever in error"""

    @property
    def is_connected(self):
        """Never: there is no port to be connected to"""
        return False

    @property
    def servers(self):
        """Nothing was built, so nothing is served"""
        return []

    @property
    def max_connections(self):
        """Return max connections limit (0 = unlimited)"""
        return self._config.get('max_connections', 0)

    @property
    def error(self):
        """Why this port could not be started"""
        return self._error

    def get_signals(self):
        """No port, no signals"""
        return 0

    def can_add_connection(self):
        """Nothing can connect to a port that does not exist"""
        return False

    def has_connections(self):
        """Never"""
        return False

    def total_connections(self):
        """Always none"""
        return 0

    def connect(self):
        """Cannot be opened; the config has to be fixed first"""
        return False

    def disconnect(self):
        """Nothing to disconnect"""

    def process_stale(self):
        """Nothing ages here"""

    def close(self):
        """Nothing to close"""


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
    # byte. That covers WebSocket clients too, through uhttp's
    # pause_reading(). The hard limit is the last line of defence, for
    # a burst that lands before the pause takes effect, or a device
    # that has stopped draining altogether.
    WRITE_HIGH_WATER = 64 * 1024
    WRITE_LOW_WATER = 16 * 1024
    WRITE_BUFFER_LIMIT = 1024 * 1024
    WRITE_WARN_INTERVAL = 10.0
    # A device that accepts nothing at all for this long is not slow,
    # it is stuck - and while it is, clients paused for backpressure
    # are not being watched, so nothing would ever reap them.
    WRITE_STALL_TIMEOUT = 30.0
    # The reader thread blocks in read(), so it only notices it should
    # stop when that returns. A read timeout bounds how long that takes
    # for backends whose cancel_read() does nothing.
    READ_TIMEOUT = 0.2
    # How long disconnect() waits for the thread. It runs in the one
    # loop that serves everything else, so this is a ceiling on how long
    # the whole process can stand still; with the read timeout above it
    # normally returns at once.
    READER_JOIN_TIMEOUT = 0.5
    # How often to try a device that somebody is waiting for. The loop
    # turns over many times a second and a port that is unplugged will
    # not be back by the next pass - for a USB device this is roughly
    # how long re-enumeration takes anyway.
    REOPEN_INTERVAL = 2.0

    def __init__(self, config, log=None, certs_dir=None, selector=None):
        self._log = log if log else _logging.Logger(self.__class__.__name__)
        self._serial = None
        self._out_buffer = bytearray()
        self._read_paused = False
        self._last_drop_warning = 0
        self._last_reopen = 0
        # Whether the last failed open has already been reported.
        self._open_warned = False
        # The last device state the clients were told about; nothing is
        # open yet, so that is what they would say if asked.
        self._announced_connected = False
        # Nothing is open, and nobody has looked at the system yet.
        self._state = 'offline'
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
        self._id = config.get('id')
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
        try:
            self._build_servers(config['servers'], log)
        except Exception:
            # __init__ is about to raise, so the caller never sees this
            # object and has nothing to close - but the servers built so
            # far are already listening and registered in the selector.
            # Left there they answer connections, open the device a
            # second time, and appear in no status anywhere.
            self.close()
            raise

    def _build_servers(self, server_configs, log):
        """Create every server this port serves"""
        for server_config in server_configs:
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

    def _init_serial_config(self, config):
        """Initialize serial configuration - validate and convert enum values"""
        if 'port' not in config and 'match' not in config:
            raise ValueError("Serial config must have 'port' or 'match'")
        config = {k: v for k, v in config.items() if k != 'match'}
        # Non-blocking writes, whatever the config says: pyserial then
        # returns how much it took instead of waiting for the device,
        # and the rest is driven by write interest from the loop.
        config['write_timeout'] = 0
        # A bounded read, so the reader thread cannot sit in read()
        # ignoring a request to stop. Reads here always pass an explicit
        # size taken from in_waiting, so this only caps the waiting.
        config['timeout'] = self.READ_TIMEOUT
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

    def _serial_reader_run(self, sock_w):
        """Reader thread: read from serial, forward to socketpair.

        Owns `sock_w` for its whole life and closes it on the way out.
        Letting the main thread close it instead is a descriptor being
        closed under a thread that may be mid-send, and a descriptor
        number that can then be reused by something else entirely.
        """
        try:
            while self._reader_running:
                try:
                    data = self._serial.read(
                        size=max(1, self._serial.in_waiting))
                    if data:
                        sock_w.sendall(data)
                except (OSError, _serial.SerialException):
                    break
        finally:
            try:
                sock_w.close()
            except OSError:
                pass

    def _start_reader_thread(self):
        """Start reader thread with socketpair for select() compatibility"""
        self._reader_sock_r, self._reader_sock_w = _socket.socketpair()
        self._reader_running = True
        self._reader_thread = _threading.Thread(
            target=self._serial_reader_run, args=(self._reader_sock_w,),
            daemon=True)
        self._reader_thread.start()
        self._log.debug("Serial reader thread started")

    def _stop_reader_thread(self):
        """Stop the reader thread and let go of the socketpair.

        Waits, but briefly: the thread reads the port we are about to
        close, so letting it run on would risk a read against a
        descriptor that has been closed and perhaps reused. cancel_read()
        wakes it at once where the backend has one, and the read timeout
        covers the rest, so this normally returns without waiting.
        """
        if self._reader_thread is None:
            return
        self._reader_running = False
        try:
            self._serial.cancel_read()
        except Exception:  # pylint: disable=W0703
            # Not every backend has one, and the read timeout covers it.
            pass
        self._reader_thread.join(timeout=self.READER_JOIN_TIMEOUT)
        if self._reader_thread.is_alive():
            self._log.warning(
                "(%s): serial reader thread did not stop within %.1fs",
                self._serial_config.get('port'), self.READER_JOIN_TIMEOUT)
        try:
            self._reader_sock_r.close()
        except OSError:
            pass
        # The thread closes its own end, whenever it gets there.
        self._reader_thread = None
        self._reader_sock_r = None
        self._reader_sock_w = None

    @property
    def id(self):
        """Stable identifier from the configuration.

        Positions renumber; this does not. The API addresses ports by
        it, so the UI has to be told what it is.
        """
        return self._id

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
    def info(self):
        """What is on the other end, as a WebSocket client is told it"""
        return port_info(self)

    @property
    def state(self):
        """How the port is doing, for colouring it: online, offline or
        error.

        Worked out by HttpServerWrapper and handed back here, because
        telling "the device is unplugged" from "the device is there and
        nobody has opened it" needs the USB enumeration it caches. Held
        here so everything that describes this port reads one answer.
        """
        return self._state

    def set_state(self, state):
        """Record the state, and tell the clients when it moved"""
        if state == self._state:
            return
        self._state = state
        info = self.info
        for server in self._servers:
            server.on_port_changed(info)
        for monitor in list(self._monitors):
            try:
                monitor.on_port_changed(info)
            except Exception as e:
                self._log.warning("Monitor callback error: %s", e)

    @property
    def is_connected(self):
        """Return True if serial port is connected"""
        return self._serial is not None

    @property
    def error(self):
        """Why this port could not be started, or None if it did.

        Always None here - a SerialProxy that exists started. Its
        counterpart FailedProxy answers with the reason, and callers
        ask both the same question.
        """
        return None

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
                    return self._cannot_open(err)
            try:
                self._serial = _serial.Serial(**self._serial_config)
            except (_serial.SerialException, OSError) as err:
                return self._cannot_open(err)
            self._log.info(
                "Serial %s connected", self._serial_config['port'])
            # Said once per absence, so the next one is news again.
            self._open_warned = False
            self._start_reader_thread_if_needed()
            self._register_serial()
            self._announce_serial(True)
        return True

    def _announce_serial(self, connected, reason=None):
        """Tell everyone the device appeared or went away.

        Every transition, not every call: the port opens whenever
        somebody attaches and closes when the last of them lets go, so
        a client watching this port - a monitor, or one that detached
        but stayed - learns about it either way. Announcing what was
        already announced would just repeat itself.
        """
        if connected == self._announced_connected:
            return
        self._announced_connected = connected
        for server in self._servers:
            if connected:
                server.on_serial_found()
            else:
                server.on_serial_lost(reason)
        self._notify_monitor_serial(connected, reason)

    def _cannot_open(self, err):
        """Report a failed open, once per absence.

        A client waiting for a device that is unplugged has the port
        retried for as long as it waits, which can be hours. Saying the
        same thing every couple of seconds buries everything else.
        """
        if self._open_warned:
            self._log.debug(err)
        else:
            self._log.warning(err)
            self._open_warned = True
        return False

    def has_connections(self):
        """Check if there are any active connections"""
        for server in self._servers:
            if server.has_connections():
                return True
        return False

    def total_connections(self):
        """How many clients are using the device, across all servers.

        Counts the *attached* ones: the port-level limit is about how
        many share the device, and a WebSocket that has detached is not
        one of them even though its socket is still open.
        """
        return sum(len(server.attached) for server in self._servers)

    def can_add_connection(self):
        """Check if new connection can be added (port-level limit)"""
        if self._max_connections > 0:
            return self.total_connections() < self._max_connections
        return True

    def disconnect(self):
        """Close the port once nobody is holding it open"""
        if self._serial and not self.has_connections():
            self._close_device()

    def _close_device(self, reason=None):
        """Let go of the device, whoever is still waiting for it.

        disconnect() asks first; after an I/O error there is nothing
        left to ask about - the port is gone either way, and the
        clients that want it back are what drives reopening it.
        """
        if not self._serial:
            return
        # Before the port goes: a socket server answers this by closing
        # its clients, which is tidier while everything is still up.
        self._announce_serial(False, reason)
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
        """Stop watching the port before it is closed.

        The same guard its counterpart has: _register_serial() will not
        register without a selector, so this cannot happen in a running
        process - but the asymmetry raised AttributeError out of
        __del__ for anything built without one, which is a trap and
        was burying real failures in test output.
        """
        if self._serial_source is None or self._selector is None:
            self._serial_source = None
            self._serial_interest = None
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
                self._notify_monitors(None, data)   # the device
            else:
                raise OSError("Serial reader closed")
        except (OSError, _serial.SerialException) as err:
            self._log.warning(err)
            self._serial_failed()

    def _serial_failed(self, reason='device disappeared'):
        """Close the port after an I/O error and say why.

        Each server decides what that means for its clients. A socket
        protocol drops them, because holding one open on a port that
        is gone only feeds it silence; a WebSocket client has a channel
        to be told on, so it keeps its socket and waits.
        """
        self._close_device(reason)

    def _reopen(self):
        """Try the device again while somebody is waiting for it.

        Reopening used to need no code of its own: losing the port
        dropped every client, and the port came back when they
        reconnected. A WebSocket client that stays is the thing that
        has to be waited for instead. connect() does the announcing.
        """
        now = _time.time()
        if now - self._last_reopen < self.REOPEN_INTERVAL:
            return
        self._last_reopen = now
        self.connect()

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
        if self._serial is None and self.has_connections():
            self._reopen()
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
        self._notify_monitor_signals(bitmask)
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

        The rate comes from whoever is actually waiting to hear: the
        shortest interval among the servers that have clients on them
        and report some line. A server nobody is connected to asks for
        nothing, and a server with a longer interval never gets a turn
        of its own - by the time it is due, the faster one has sampled
        and everybody has been told.

        Debug logging is the other reason to look: without it the input
        signals (CTS, DSR, RI, CD) never change as far as the log is
        concerned, because nothing was sampling them.
        """
        if not self._serial:
            return
        watchers = self._signal_watchers()
        if not watchers and not self._log.isEnabledFor(_logging.DEBUG):
            return
        interval = min(
            (_control.poll_interval(server.control) for server in watchers),
            default=_control.DEFAULT_POLL_INTERVAL)
        now = _time.time()
        if now - self._last_signal_poll < interval:
            return
        self._last_signal_poll = now
        bitmask = self.get_signals()
        if bitmask != self._last_signals:
            self._log_signal_change(bitmask)
            self._last_signals = bitmask
            # Sampling for the log alone stays observational: with
            # nobody waiting there is nobody to report to.
            if watchers:
                for server in self._servers:
                    server.send_signal_report(bitmask)
                self._notify_monitor_signals(bitmask)

    def _signal_watchers(self):
        """The servers waiting to hear about the lines.

        Both halves matter. A server with no `control` is not watching
        anything; a control server with nobody connected to it is an
        empty room, and sampling for it is ioctls spent on no one.
        """
        return [
            server for server in self._servers
            if server.connections and _control.reported_signals(server.control)
        ]

    def send(self, data, source=None):
        """Queue data for the serial port and write what it will take.

        `source` is the connection that asked for it, carried only so a
        monitor can say which client wrote what. None means us.
        """
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
        self._notify_monitors(source, bytes(data))
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

    def add_monitor(self, monitor):
        """Register a monitor - on_data(source, data), on_signals(mask)"""
        if monitor not in self._monitors:
            self._monitors.append(monitor)

    def remove_monitor(self, monitor):
        """Unregister a monitor"""
        if monitor in self._monitors:
            self._monitors.remove(monitor)

    def _notify_monitors(self, source, data):
        """Pass traffic on, saying who produced it (None = the device)"""
        for monitor in list(self._monitors):
            try:
                monitor.on_data(source, data)
            except Exception as e:
                self._log.warning("Monitor callback error: %s", e)

    def _notify_monitor_serial(self, connected, reason=None):
        """Tell the monitors the device went away, or came back"""
        for monitor in list(self._monitors):
            try:
                monitor.on_serial(connected, reason)
            except Exception as e:
                self._log.warning("Monitor callback error: %s", e)

    def _notify_monitor_signals(self, bitmask):
        """Pass a signal reading on to the monitors.

        Monitors are not in _servers - they watch a port rather than
        serve it - so the broadcast that reaches every server has to
        reach them separately.
        """
        for monitor in list(self._monitors):
            try:
                monitor.on_signals(bitmask)
            except Exception as e:
                self._log.warning("Monitor callback error: %s", e)
