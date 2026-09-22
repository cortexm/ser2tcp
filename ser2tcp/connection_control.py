"""Connection control protocol - serial signal control via 0xFF escape"""

# Escape protocol:
# FF FF  = literal 0xFF byte
# FF 00  = RTS low
# FF 01  = RTS high
# FF 10  = DTR low
# FF 11  = DTR high
# FF C0  = GET signals request
# FF 8x  = signal report (x = 6-bit bitmask)
#   bit 0: RTS, bit 1: DTR, bit 2: CTS, bit 3: DSR, bit 4: RI, bit 5: CD

ESCAPE = 0xFF
CMD_RTS_LOW = 0x00
CMD_RTS_HIGH = 0x01
CMD_DTR_LOW = 0x10
CMD_DTR_HIGH = 0x11
CMD_GET_SIGNALS = 0xC0
REPORT_BASE = 0x80
REPORT_MASK = 0x3F

SIGNAL_NAMES = ('rts', 'dtr', 'cts', 'dsr', 'ri', 'cd')
SIGNAL_BITS = {name: i for i, name in enumerate(SIGNAL_NAMES)}


def wrap_control(connection_class, control_config, can_read=True,
        can_write=True):
    """Wrap a connection class with control protocol handling.

    Returns a new class that escapes 0xFF in outgoing data and parses
    escape sequences in incoming data for signal control commands.

    The two directions are separate: `can_read` decides whether the
    device's output is escaped and forwarded, `can_write` whether data
    bytes from the client reach the device. Control commands are not
    data and work in either case - a read-only server may still be
    allowed to toggle RTS, and that is `control`'s business, not this
    one's.
    """
    signals = control_config.get('signals', [])
    signal_set = set(s.lower() for s in signals)
    rts_enabled = bool(control_config.get('rts'))
    dtr_enabled = bool(control_config.get('dtr'))
    forward_data = can_read
    accept_data = can_write

    class ControlConnection(connection_class):

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._ctl_escape = False
            self._ctl_signals = signal_set
            self._ctl_rts = rts_enabled
            self._ctl_dtr = dtr_enabled
            self._ctl_data = forward_data
            self._ctl_accept = accept_data

        def send(self, data):
            """Send data with 0xFF escaped (skipped when data disabled)"""
            if not self._ctl_data:
                return 0
            return super().send(data.replace(b'\xff', b'\xff\xff'))

        def send_signal_report(self, bitmask):
            """Send signal report FF 8x"""
            # Filter bitmask to only configured signals
            filtered = 0
            for name in self._ctl_signals:
                bit = SIGNAL_BITS.get(name)
                if bit is not None and bitmask & (1 << bit):
                    filtered |= (1 << bit)
            return super().send(
                bytes((ESCAPE, REPORT_BASE | (filtered & REPORT_MASK))))

        def on_received(self, data):
            """Parse escape sequences, forward clean data to serial"""
            data = bytearray(data)
            clean = bytearray()
            while data:
                if self._ctl_escape:
                    self._ctl_escape = False
                    cmd = data.pop(0)
                    self._process_control_cmd(cmd, clean)
                    continue
                if ESCAPE in data:
                    index = data.index(ESCAPE)
                    if self._ctl_accept and index > 0:
                        clean.extend(data[:index])
                    del data[:index + 1]
                    self._ctl_escape = True
                else:
                    if self._ctl_accept:
                        clean.extend(data)
                    break
            self.to_serial(bytes(clean))

        def _process_control_cmd(self, cmd, clean):
            """Process a control command byte after 0xFF escape"""
            if cmd == ESCAPE:
                # FF FF = literal 0xFF
                clean.append(ESCAPE)
            elif cmd == CMD_RTS_LOW:
                if self._ctl_rts:
                    self._serial.set_rts(False)
            elif cmd == CMD_RTS_HIGH:
                if self._ctl_rts:
                    self._serial.set_rts(True)
            elif cmd == CMD_DTR_LOW:
                if self._ctl_dtr:
                    self._serial.set_dtr(False)
            elif cmd == CMD_DTR_HIGH:
                if self._ctl_dtr:
                    self._serial.set_dtr(True)
            elif cmd == CMD_GET_SIGNALS:
                bitmask = self._serial.get_signals()
                self.send_signal_report(bitmask)
            else:
                self._log.warning(
                    "(%s): unknown control command: 0x%02x",
                    self.address_str(), cmd)

    ControlConnection.__name__ = 'Control' + connection_class.__name__
    ControlConnection.__qualname__ = 'Control' + connection_class.__qualname__
    return ControlConnection
