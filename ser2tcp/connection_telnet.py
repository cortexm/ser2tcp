"""Connection Telnet"""

import ser2tcp.connection as _connection


class ConnectionTelnet(_connection.Connection):
    """Telnet connection"""

    # RFC 854 : https://datatracker.ietf.org/doc/html/rfc854.html
    TELNET_SE = 0xf0  # End of subnegotiation parameters.
    TELNET_NOP = 0xf1  # No operation.
    TELNET_DATA_MARK = 0xf2  # The data stream portion of a Synchronization This should always be accompanied by a TCP Urgent notification.
    TELNET_BREAK = 0xf3  # NVT character BRK.
    TELNET_INTERRUPT_PROCESS = 0xf4  # The function IP.
    TELNET_ABORT_OUTPUT = 0xf5  # The function AO.
    TELNET_ARE_YOU_THERE = 0xf6  # The function AYT.
    TELNET_ERASE_CHARACTER = 0xf7  # The function EC.
    TELNET_ERASE_LINE = 0xf8  # The function EL.
    TELNET_GO_AHEAD = 0xf9  # The GA signal.
    TELNET_SB = 0xfa  # Indicates that what follows is subnegotiation of the indicated option.
    TELNET_WILL = 0xfb  # Indicates the desire to begin performing, or confirmation that you are now performing, the indicated option.
    TELNET_WONT = 0xfc  # Indicates the refusal to perform, or continue performing, the indicated option.
    TELNET_DO = 0xfd  # Indicates the request that the other party perform, or confirmation that you are expecting the other party to perform, the indicated option.
    TELNET_DONT = 0xfe  # Indicates the demand that the other party stop performing, or confirmation that you are no longer expecting the other party to perform, the indicated option.
    TELNET_IAC = 0xff  # Data Byte 255.

    TELNET_OPTION_CODES = {
        TELNET_WILL: 'WILL',
        TELNET_WONT: 'WONT',
        TELNET_DO: 'DO',
        TELNET_DONT: 'DONT',
    }

    def __init__(
            self, connection, ser, send_timeout=None, buffer_limit=None,
            log=None):
        super().__init__(connection, send_timeout, buffer_limit, log)
        self._serial = ser
        # Send initial telnet negotiation
        self.send(bytes((self.TELNET_IAC, self.TELNET_DO, 0x22)))
        self.send(bytes((self.TELNET_IAC, self.TELNET_WILL, 0x01)))
        self._telnet_iac = False
        self._telnet_state = None
        self._subnegotiation_frame = None
        self._log.info("Client connected: %s:%d TELNET", *self._addr)

    def send(self, data):
        """Send data to client, escape IAC bytes"""
        return super().send(data.replace(b'\xff', b'\xff\xff'))

    def _telnet_subnegotiation(self, subnegotiation):
        """Process subnegotiation frame"""
        self._log.debug(
            "(%s:%d) received TELNET SUBNEGOTIATION: %s",
            *self._addr, ' '.join(['%02x' % i for i in subnegotiation]))

    def _telnet_command(self, command, value):
        """Process telnet command"""
        self._log.debug(
            "(%s:%d) received TELNET COMMAND: %s 0x%02x",
            *self._addr, self.TELNET_OPTION_CODES[command], value)

    def _send_data(self, data):
        if self._telnet_state is None:
            self._serial.send(data)
        elif self._telnet_state == self.TELNET_SB:
            self._subnegotiation_frame.extend(data)

    def _process_iac(self, state):
        if state in self.TELNET_OPTION_CODES:
            self._telnet_state = state
        elif state == self.TELNET_SB:
            self._telnet_state = state
            self._subnegotiation_frame = bytearray()
        elif state == self.TELNET_SE:
            # Clearing _telnet_state is what lets the data after the
            # subnegotiation reach the device again: _send_data() routes
            # into the frame buffer while SB is set, and that buffer is
            # gone by then. Assigning to `state` here only touched the
            # local name, so the next byte raised AttributeError - and
            # a client answering the LINEMODE request this server opens
            # with gets there on its very first reply.
            if self._subnegotiation_frame is not None:
                self._telnet_subnegotiation(self._subnegotiation_frame)
                self._subnegotiation_frame = None
            else:
                # SE with no SB before it: a broken peer, not our problem
                self._log.warning(
                    "(%s:%d) received TELNET SE without SB", *self._addr)
            self._telnet_state = None
        elif state == self.TELNET_IAC:
            self._send_data(bytes((state, )))
        else:
            self._log.warning(
                "(%s:%d) received unexpected TELNET COMMAND: 0x%02x",
                *self._addr, state)

    def on_received(self, data):
        """Received data from client"""
        data = bytearray(data)
        while data:
            if self._telnet_iac:
                # IAC byte was received
                self._telnet_iac = False
                self._process_iac(data.pop(0))
                continue
            if self._telnet_state in self.TELNET_OPTION_CODES:
                self._telnet_command(self._telnet_state, data.pop(0))
                self._telnet_state = None
                continue
            # NO state
            if self.TELNET_IAC in data:
                # find index of IAC
                index = data.index(self.TELNET_IAC)
                if index > 0:
                    # send data before IAC
                    self._send_data(data[:index])
                # remove data + IAC
                del data[:index + 1]
                self._telnet_iac = True
            else:
                self._send_data(data)
                break
