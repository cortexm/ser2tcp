"""WebSocket monitor server - read-only serial communication monitoring"""

import json as _json
import logging as _logging

import ser2tcp.connection as _connection
import ser2tcp.connection_control as _control


class ServerMonitor():
    """Watches one serial port, read-only, for any number of clients.

    Every binary frame starts with one byte naming **who produced it**:
    0 is the device, 1-254 a client by its slot, 255 a client that
    arrived when no slot was left. The byte used to say the direction
    instead (1=TX, 2=RX), which could not tell three writers apart -
    and telling them apart is the reason to watch a shared line.

    Text frames follow the same format as a serial endpoint: one JSON
    object whose top-level keys are topics.
    """

    DEVICE = 0
    FIRST_SLOT = 1
    # 255 is "no slot left", not a slot: without reserving it the
    # 255th client and an overflow would be indistinguishable.
    LAST_SLOT = 254
    OVERFLOW = 255

    def __init__(self, serial_proxy, log=None):
        self._log = log if log else _logging.Logger(self.__class__.__name__)
        self._serial = serial_proxy
        self._connections = []
        # Client connection -> slot, and the description last reported
        # for it. The description is kept so a departing peer can still
        # be named after it has gone from the port's own lists.
        self._slots = {}
        self._known = {}
        # What the watchers have been told about the signal lines.
        self._reported = None

    @property
    def connections(self):
        """Return list of connections"""
        return self._connections

    def add_connection(self, client):
        """Add WebSocket connection and register as monitor"""
        # Before the newcomer is on the list: anyone already watching
        # hears about peers this walk discovers, and the newcomer gets
        # the whole list in its greeting instead.
        self._refresh_peers()
        self._connections.append(client)
        if len(self._connections) == 1:
            self._serial.add_monitor(self)
            self._log.debug(
                "Monitor callback registered for %s", self._serial.name)
        addr = self._client_addr(client)
        self._log.info(
            "Monitor connected: %s /ws/monitor/%s",
            addr, self._serial.name)
        self._send_hello(client)

    def remove_connection(self, client):
        """Remove WebSocket connection"""
        if client in self._connections:
            addr = self._client_addr(client)
            self._connections.remove(client)
            self._log.info("Monitor disconnected: %s", addr)
            if not self._connections:
                self._serial.remove_monitor(self)
                self._forget_peers()

    def process_message(self, client):
        """Ignore incoming messages - monitor is read-only"""

    def on_data(self, source, data):
        """Traffic on the line, tagged with whoever produced it"""
        if source is not None and source not in self._slots:
            # A client can write before the next walk notices it. Its
            # slot has to mean something by the time its bytes arrive,
            # so announce it first.
            self._refresh_peers()
        slot = self.DEVICE if source is None \
            else self._slots.get(source, self.OVERFLOW)
        self._broadcast(bytes([slot]) + data)

    def on_signals(self, bitmask):
        """Report the lines that moved, like an endpoint does"""
        names = self._reported_signals()
        if not names:
            return
        signals = _control.signals_dict(bitmask, names)
        changed = signals
        if self._reported is not None:
            changed = dict(
                (name, value) for name, value in signals.items()
                if self._reported.get(name) != value)
        self._reported = signals
        if changed:
            self._broadcast_json({'signals': changed})

    def process_stale(self):
        """Remove closed connections and notice who came and went"""
        for client in list(self._connections):
            if not client.is_websocket or client.socket is None:
                self.remove_connection(client)
        if not self._connections:
            # Called every loop pass for every port that has ever been
            # watched; with nobody watching there is nothing to walk
            # the port for.
            return
        self._refresh_peers()

    def close(self):
        """Close all connections"""
        while self._connections:
            client = self._connections.pop()
            try:
                client.ws_close(1001, 'Server shutting down')
            except OSError:
                pass
        self._serial.remove_monitor(self)
        self._forget_peers()

    # --- peers ------------------------------------------------------

    def _refresh_peers(self):
        """Notice who joined or left the port, and say so.

        Servers do not report to a monitor, so there is no event to
        ride on - this walks the port instead, once per loop pass and
        only while somebody is watching.

        Departures are reported before their slots are released, so a
        number is never handed on while its holder is still on the
        list a client last saw.

        The first watcher is not on the list yet when this runs, so
        the broadcast reaches nobody and the slots it hands out are
        what its greeting carries.
        """
        present = []
        for server in self._serial.servers:
            for con in server.connections:
                present.append((server, con))
        here = [con for _, con in present]
        for con in [con for con in self._slots if con not in here]:
            entry = self._known.pop(con, {'slot': self._slots[con]})
            del self._slots[con]
            self._broadcast_json({
                'peer_disconnected': entry,
                'peers': self._peer_list(present),
            })
        for server, con in present:
            if con in self._slots:
                continue
            self._slots[con] = self._free_slot()
            self._known[con] = self._describe(server, con)
            self._broadcast_json({
                'peer_connected': self._known[con],
                'peers': self._peer_list(present),
            })

    def _forget_peers(self):
        """Nobody is watching, so nothing is being kept track of"""
        self._slots.clear()
        self._known.clear()
        self._reported = None

    def _peer_list(self, present=None):
        """Everyone on this port, across every server it has"""
        if present is None:
            present = [(server, con)
                       for server in self._serial.servers
                       for con in server.connections]
        return [self._describe(server, con) for server, con in present]

    def _free_slot(self):
        """The lowest free number, so slots stay short and readable"""
        used = set(self._slots.values())
        for slot in range(self.FIRST_SLOT, self.LAST_SLOT + 1):
            if slot not in used:
                return slot
        return self.OVERFLOW

    def _describe(self, server, con):
        """One peer, as a watcher is told about it"""
        entry = {
            'slot': self._slots.get(con, self.OVERFLOW),
            # The same id /api/status reports, so the two listings can
            # be matched up. Unlike a slot it is never reused.
            'id': _connection.connection_id(con),
            'protocol': str(server.protocol).lower(),
        }
        entry.update(self._describe_address(con))
        return entry

    @staticmethod
    def _describe_address(con):
        """Where a peer is, as well as it can be known.

        Behind a reverse proxy the socket belongs to the proxy, so its
        port says nothing about the client and is left out rather than
        reported as if it were the client's.
        """
        addr = None
        if hasattr(con, 'get_address'):
            addr = con.get_address()
        if addr is None:
            addr = getattr(con, 'addr', None)
        where = {}
        if isinstance(addr, tuple) and len(addr) >= 2:
            where['address'] = addr[0]
            where['port'] = addr[1]
        elif addr:
            where['address'] = str(addr)
        remote = getattr(con, 'remote_address', None)
        if isinstance(remote, str) and remote and remote != where.get('address'):
            # uhttp resolved a trusted X-Forwarded-For chain: that is
            # the client, and what connected is the proxy.
            where['address'] = remote
            where.pop('port', None)
            socket_address = getattr(con, 'socket_address', None)
            if isinstance(socket_address, str) and socket_address:
                where['socket'] = socket_address
        forwarded = getattr(con, 'remote_addresses', None)
        if isinstance(forwarded, (list, tuple)) and len(forwarded) > 1:
            where['forwarded'] = list(forwarded)
        return where

    # --- frames -----------------------------------------------------

    def _send_hello(self, client):
        """Everything a watcher needs, in one frame"""
        msg = {
            'port': self._serial.info,
            'can': {
                'read': True,
                # A monitor watches; it neither writes nor holds the
                # port open, and saying so keeps a generic client from
                # offering either.
                'write': False,
                'signals': [],
                'attach': False,
            },
            'serial': {'connected': bool(self._serial.is_connected)},
            'peers': self._peer_list(),
        }
        names = self._reported_signals()
        if names:
            signals = _control.signals_dict(
                self._serial.get_signals(), names)
            msg['signals'] = signals
            if self._reported is None:
                self._reported = signals
        self._send_json(client, msg)

    def _reported_signals(self):
        """The lines any server on this port is set up to report.

        A monitor has no control configuration of its own. Showing
        every line regardless would mean six indicators on a port
        nobody asked to watch the signals of - and a row of dead ones
        says less than no row at all.
        """
        names = set()
        for server in self._serial.servers:
            control = server.control or {}
            for name in control.get('signals', ()):
                names.add(str(name).lower())
        return tuple(
            name for name in _control.SIGNAL_NAMES if name in names)

    def _broadcast(self, frame):
        """Send one binary frame to every watcher"""
        for client in list(self._connections):
            try:
                client.ws_send(frame)
            except OSError:
                self.remove_connection(client)

    def _broadcast_json(self, msg):
        """Send one text frame to every watcher"""
        self._broadcast(_json.dumps(msg))

    def _send_json(self, client, msg):
        """Send one text frame, reaping a client that has gone"""
        try:
            client.ws_send(_json.dumps(msg))
        except OSError:
            self.remove_connection(client)

    def _client_addr(self, client):
        """Return formatted client address string"""
        try:
            addr = client.addr
            if isinstance(addr, tuple) and len(addr) >= 2:
                return "%s:%d" % (addr[0], addr[1])
            return str(addr)
        except Exception:
            return 'unknown'
