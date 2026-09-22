"""Tests for SerialProxy config parsing"""

import selectors
import socket
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

import serial

from ser2tcp.serial_proxy import SerialProxy, _format_signals


def _mock_init(self, config=None, log=None):
    """Mock init that sets required attributes for __del__"""
    self._servers = []
    self._monitors = []
    self._serial = None
    self._selector = None
    self._serial_source = None
    self._serial_interest = None
    self._reader_thread = None
    self._reader_sock_r = None
    self._reader_sock_w = None
    self._reader_running = False
    self._out_buffer = bytearray()
    self._read_paused = False
    self._last_drop_warning = 0


def _make_port_info(device, vid=None, pid=None, serial_number=None,
        manufacturer=None, product=None, location=None):
    """Create mock ListPortInfo"""
    info = MagicMock()
    info.device = device
    info.vid = vid
    info.pid = pid
    info.serial_number = serial_number
    info.manufacturer = manufacturer
    info.product = product
    info.location = location
    return info


class TestFixSerialConfig(unittest.TestCase):
    """Test serial config normalization"""

    def _make_proxy(self):
        """Helper to create SerialProxy with mocked __init__"""
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        return proxy

    def test_parity_none(self):
        proxy = self._make_proxy()
        config = {'port': '/dev/ttyUSB0', 'parity': 'NONE'}
        result = proxy._init_serial_config(config)
        self.assertEqual(result['parity'], serial.PARITY_NONE)

    def test_parity_even(self):
        proxy = self._make_proxy()
        config = {'port': '/dev/ttyUSB0', 'parity': 'EVEN'}
        result = proxy._init_serial_config(config)
        self.assertEqual(result['parity'], serial.PARITY_EVEN)

    def test_parity_odd(self):
        proxy = self._make_proxy()
        config = {'port': '/dev/ttyUSB0', 'parity': 'ODD'}
        result = proxy._init_serial_config(config)
        self.assertEqual(result['parity'], serial.PARITY_ODD)

    def test_stopbits_one(self):
        proxy = self._make_proxy()
        config = {'port': '/dev/ttyUSB0', 'stopbits': 'ONE'}
        result = proxy._init_serial_config(config)
        self.assertEqual(result['stopbits'], serial.STOPBITS_ONE)

    def test_stopbits_two(self):
        proxy = self._make_proxy()
        config = {'port': '/dev/ttyUSB0', 'stopbits': 'TWO'}
        result = proxy._init_serial_config(config)
        self.assertEqual(result['stopbits'], serial.STOPBITS_TWO)

    def test_bytesize_eightbits(self):
        proxy = self._make_proxy()
        config = {'port': '/dev/ttyUSB0', 'bytesize': 'EIGHTBITS'}
        result = proxy._init_serial_config(config)
        self.assertEqual(result['bytesize'], serial.EIGHTBITS)

    def test_bytesize_sevenbits(self):
        proxy = self._make_proxy()
        config = {'port': '/dev/ttyUSB0', 'bytesize': 'SEVENBITS'}
        result = proxy._init_serial_config(config)
        self.assertEqual(result['bytesize'], serial.SEVENBITS)

    def test_config_without_parity(self):
        """Config without parity should remain unchanged"""
        proxy = self._make_proxy()
        config = {'port': '/dev/ttyUSB0', 'baudrate': 115200}
        result = proxy._init_serial_config(config)
        self.assertNotIn('parity', result)
        self.assertEqual(result['baudrate'], 115200)

    def test_unknown_parity_unchanged(self):
        """Unknown parity value should remain unchanged"""
        proxy = self._make_proxy()
        config = {'port': '/dev/ttyUSB0', 'parity': 'UNKNOWN'}
        result = proxy._init_serial_config(config)
        self.assertEqual(result['parity'], 'UNKNOWN')


class TestSerialProxyConfigMaps(unittest.TestCase):
    """Test config mapping dictionaries"""

    def test_parity_config_keys(self):
        expected = {'NONE', 'EVEN', 'ODD', 'MARK', 'SPACE'}
        self.assertEqual(set(SerialProxy.PARITY_CONFIG.keys()), expected)

    def test_stopbits_config_keys(self):
        expected = {'ONE', 'ONE_POINT_FIVE', 'TWO'}
        self.assertEqual(set(SerialProxy.STOPBITS_CONFIG.keys()), expected)

    def test_bytesize_config_keys(self):
        expected = {'FIVEBITS', 'SIXBITS', 'SEVENBITS', 'EIGHTBITS'}
        self.assertEqual(set(SerialProxy.BYTESIZE_CONFIG.keys()), expected)


class TestFindPortByMatch(unittest.TestCase):
    """Test USB device matching"""

    def _make_proxy(self):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        return proxy

    @patch('ser2tcp.serial_proxy._list_ports.comports')
    def test_match_by_vid_pid(self, mock_comports):
        mock_comports.return_value = [
            _make_port_info('/dev/ttyUSB0', vid=0x303A, pid=0x4001),
        ]
        proxy = self._make_proxy()
        result = proxy.find_port_by_match({'vid': '0x303A', 'pid': '0x4001'})
        self.assertEqual(result, '/dev/ttyUSB0')

    @patch('ser2tcp.serial_proxy._list_ports.comports')
    def test_match_by_serial_number(self, mock_comports):
        mock_comports.return_value = [
            _make_port_info('/dev/ttyUSB0', vid=0x303A, serial_number='ABC123'),
        ]
        proxy = self._make_proxy()
        result = proxy.find_port_by_match({'serial_number': 'ABC123'})
        self.assertEqual(result, '/dev/ttyUSB0')

    @patch('ser2tcp.serial_proxy._list_ports.comports')
    def test_match_wildcard(self, mock_comports):
        mock_comports.return_value = [
            _make_port_info(
                '/dev/ttyUSB0', vid=0x303A, manufacturer='Espressif Systems'),
        ]
        proxy = self._make_proxy()
        result = proxy.find_port_by_match({'manufacturer': 'Espressif*'})
        self.assertEqual(result, '/dev/ttyUSB0')

    @patch('ser2tcp.serial_proxy._list_ports.comports')
    def test_match_case_insensitive(self, mock_comports):
        mock_comports.return_value = [
            _make_port_info('/dev/ttyUSB0', vid=0x303A, product='USB Device'),
        ]
        proxy = self._make_proxy()
        result = proxy.find_port_by_match({'product': 'usb*'})
        self.assertEqual(result, '/dev/ttyUSB0')

    @patch('ser2tcp.serial_proxy._list_ports.comports')
    def test_match_no_device_found(self, mock_comports):
        mock_comports.return_value = [
            _make_port_info('/dev/ttyUSB0', vid=0x1234, pid=0x5678),
        ]
        proxy = self._make_proxy()
        with self.assertRaises(ValueError) as ctx:
            proxy.find_port_by_match({'vid': '0x303A'})
        self.assertIn('No device found', str(ctx.exception))

    @patch('ser2tcp.serial_proxy._list_ports.comports')
    def test_match_multiple_devices(self, mock_comports):
        mock_comports.return_value = [
            _make_port_info('/dev/ttyUSB0', vid=0x303A, pid=0x4001),
            _make_port_info('/dev/ttyUSB1', vid=0x303A, pid=0x4001),
        ]
        proxy = self._make_proxy()
        with self.assertRaises(ValueError) as ctx:
            proxy.find_port_by_match({'vid': '0x303A'})
        self.assertIn('Multiple devices', str(ctx.exception))

    def test_match_empty_criteria(self):
        proxy = self._make_proxy()
        with self.assertRaises(ValueError) as ctx:
            proxy.find_port_by_match({})
        self.assertIn('cannot be empty', str(ctx.exception))

    def test_match_unknown_attribute(self):
        proxy = self._make_proxy()
        with self.assertRaises(ValueError) as ctx:
            proxy.find_port_by_match({'unknown': 'value'})
        self.assertIn('Unknown match attribute', str(ctx.exception))

    @patch('ser2tcp.serial_proxy._list_ports.comports')
    def test_match_filters_none_values(self, mock_comports):
        """Devices with None for matched attribute should not match"""
        mock_comports.return_value = [
            _make_port_info('/dev/ttyUSB0', vid=None, pid=None),
            _make_port_info('/dev/ttyUSB1', vid=0x303A, pid=0x4001),
        ]
        proxy = self._make_proxy()
        result = proxy.find_port_by_match({'vid': '0x303A'})
        self.assertEqual(result, '/dev/ttyUSB1')


class TestInitSerialConfigMatch(unittest.TestCase):
    """Test _init_serial_config with match"""

    def _make_proxy(self):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        return proxy

    def test_config_requires_port_or_match(self):
        proxy = self._make_proxy()
        with self.assertRaises(ValueError) as ctx:
            proxy._init_serial_config({})
        self.assertIn("'port' or 'match'", str(ctx.exception))

    def test_config_with_match_filtered(self):
        proxy = self._make_proxy()
        config = {'match': {'vid': '0x303A'}, 'baudrate': 115200}
        result = proxy._init_serial_config(config)
        self.assertNotIn('match', result)
        self.assertEqual(result['baudrate'], 115200)


class TestSerialProxyName(unittest.TestCase):
    """Test port name property"""

    @patch('ser2tcp.serial_proxy._server.Server')
    def test_name_from_config(self, _mock_server):
        proxy = SerialProxy(
            {'name': 'gate2a', 'serial': {'port': '/dev/ttyUSB0'},
             'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                 'port': 10001}]})
        self.assertEqual(proxy.name, 'gate2a')

    @patch('ser2tcp.serial_proxy._server.Server')
    def test_name_default_empty(self, _mock_server):
        proxy = SerialProxy(
            {'serial': {'port': '/dev/ttyUSB0'},
             'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                 'port': 10001}]})
        self.assertEqual(proxy.name, '')

    @patch('ser2tcp.serial_proxy._server.Server')
    def test_name_used_in_log(self, _mock_server):
        from unittest.mock import Mock
        log = Mock()
        proxy = SerialProxy(
            {'name': 'mydev', 'serial': {'port': '/dev/ttyUSB0'},
             'servers': [{'protocol': 'tcp', 'address': '0.0.0.0',
                 'port': 10001}]},
            log=log)
        log.info.assert_any_call("Serial: %s", 'mydev')


class TestSignalControl(unittest.TestCase):
    """Test serial signal control methods"""

    def _make_proxy(self):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._serial_config = {'port': '/dev/ttyUSB0'}
        proxy._last_signals = 0
        proxy._last_signal_poll = 0
        proxy._signal_poll_interval = 0.1
        proxy._has_control_servers = False
        proxy._name = ''
        proxy._match = None
        return proxy

    def test_get_signals_returns_bitmask(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.rts = True
        proxy._serial.dtr = False
        proxy._serial.cts = True
        proxy._serial.dsr = False
        proxy._serial.ri = False
        proxy._serial.cd = True
        bitmask = proxy.get_signals()
        # rts=bit0, cts=bit2, cd=bit5
        self.assertEqual(bitmask, 0b100101)

    def test_get_signals_not_connected(self):
        proxy = self._make_proxy()
        self.assertEqual(proxy.get_signals(), 0)

    def test_set_rts_broadcasts(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.rts = True
        proxy._serial.dtr = False
        proxy._serial.cts = False
        proxy._serial.dsr = False
        proxy._serial.ri = False
        proxy._serial.cd = False
        mock_server = MagicMock()
        proxy._servers = [mock_server]
        proxy.set_rts(True)
        self.assertTrue(proxy._serial.rts)
        mock_server.send_signal_report.assert_called_once()

    def test_set_dtr_broadcasts(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.rts = False
        proxy._serial.dtr = True
        proxy._serial.cts = False
        proxy._serial.dsr = False
        proxy._serial.ri = False
        proxy._serial.cd = False
        mock_server = MagicMock()
        proxy._servers = [mock_server]
        proxy.set_dtr(False)
        mock_server.send_signal_report.assert_called_once()

    def test_process_signals_detects_change(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.rts = True
        proxy._serial.dtr = False
        proxy._serial.cts = False
        proxy._serial.dsr = False
        proxy._serial.ri = False
        proxy._serial.cd = False
        proxy._has_control_servers = True
        proxy._signal_poll_interval = 0
        mock_server = MagicMock()
        proxy._servers = [mock_server]
        proxy._last_signals = 0  # different from current
        proxy.process_signals()
        mock_server.send_signal_report.assert_called_once()

    def test_process_signals_no_change(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.rts = False
        proxy._serial.dtr = False
        proxy._serial.cts = False
        proxy._serial.dsr = False
        proxy._serial.ri = False
        proxy._serial.cd = False
        proxy._has_control_servers = True
        proxy._signal_poll_interval = 0
        mock_server = MagicMock()
        proxy._servers = [mock_server]
        proxy._last_signals = 0  # same as current
        proxy.process_signals()
        mock_server.send_signal_report.assert_not_called()

    def test_process_signals_skipped_without_control(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._has_control_servers = False
        mock_server = MagicMock()
        proxy._servers = [mock_server]
        proxy.process_signals()
        mock_server.send_signal_report.assert_not_called()


class TestSerialReaderThread(unittest.TestCase):
    """Test reader thread for platforms without fileno() support"""

    def _make_proxy(self):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._serial_config = {'port': '/dev/ttyUSB0'}
        return proxy

    def test_fileno_supported_no_thread(self):
        """No reader thread when fileno() works"""
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.fileno.return_value = 3
        proxy._start_reader_thread_if_needed()
        self.assertIsNone(proxy._reader_thread)

    def test_fileno_not_supported_starts_thread(self):
        """Reader thread started when fileno() raises OSError"""
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.in_waiting = 0
        proxy._serial.fileno.side_effect = OSError("fileno")
        proxy._serial.read.side_effect = OSError("closed")
        proxy._start_reader_thread_if_needed()
        self.assertIsNotNone(proxy._reader_thread)
        proxy._stop_reader_thread()

    def test_start_stop_reader_thread(self):
        """Reader thread starts and stops cleanly"""
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.in_waiting = 0
        proxy._serial.read.side_effect = OSError("closed")
        proxy._start_reader_thread()
        self.assertIsNotNone(proxy._reader_thread)
        self.assertIsNotNone(proxy._reader_sock_r)
        self.assertIsNotNone(proxy._reader_sock_w)
        self.assertTrue(proxy._reader_running)
        proxy._stop_reader_thread()
        self.assertIsNone(proxy._reader_thread)
        self.assertIsNone(proxy._reader_sock_r)
        self.assertIsNone(proxy._reader_sock_w)
        self.assertFalse(proxy._reader_running)

    def test_reader_thread_forwards_data(self):
        """Reader thread forwards serial data through socketpair"""
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.in_waiting = 5
        proxy._serial.read.side_effect = [b'hello', OSError("closed")]
        proxy._start_reader_thread()
        proxy._reader_thread.join(timeout=2)
        data = proxy._reader_sock_r.recv(4096)
        self.assertEqual(data, b'hello')
        proxy._stop_reader_thread()

    def test_registers_socketpair_when_thread_active(self):
        """The loop watches the socketpair, not the port, when a reader
        thread is feeding it"""
        proxy = self._make_proxy()
        proxy._selector = MagicMock()
        proxy._serial = MagicMock()
        proxy._serial.in_waiting = 0
        proxy._serial.read.side_effect = OSError("closed")
        proxy._start_reader_thread()
        proxy._register_serial()
        self.assertIs(proxy._serial_source, proxy._reader_sock_r)
        registered = proxy._selector.register.call_args[0][0]
        self.assertIs(registered, proxy._reader_sock_r)
        proxy._unregister_serial()
        proxy._stop_reader_thread()

    def test_registers_serial_directly(self):
        """Without a reader thread the port itself is watched"""
        proxy = self._make_proxy()
        proxy._selector = MagicMock()
        proxy._serial = MagicMock()
        proxy._register_serial()
        self.assertIs(proxy._serial_source, proxy._serial)
        proxy._selector.register.assert_called_once()

    def test_register_is_idempotent(self):
        """connect() runs per client; the source is registered once"""
        proxy = self._make_proxy()
        proxy._selector = MagicMock()
        proxy._serial = MagicMock()
        proxy._register_serial()
        proxy._register_serial()
        proxy._selector.register.assert_called_once()

    def test_unregister_clears_source(self):
        proxy = self._make_proxy()
        proxy._selector = MagicMock()
        proxy._serial = MagicMock()
        proxy._register_serial()
        proxy._unregister_serial()
        self.assertIsNone(proxy._serial_source)
        proxy._selector.unregister.assert_called_once()

    def test_register_survives_a_selector_refusal(self):
        """A port the selector cannot watch is logged, not raised"""
        proxy = self._make_proxy()
        proxy._selector = MagicMock()
        proxy._selector.register.side_effect = ValueError('bad fd')
        proxy._serial = MagicMock()
        proxy._register_serial()
        self.assertIsNone(proxy._serial_source)

    def test_handle_event_ignores_a_stale_source(self):
        """An event for a source already disconnected does nothing"""
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._process_serial_data = MagicMock()
        proxy.handle_event(object(), 1)
        proxy._process_serial_data.assert_not_called()


class TestSerialErrorsAreContained(unittest.TestCase):
    """A device that refuses an operation must not kill the loop.

    set_rts/set_dtr are driven by clients and write() by whatever they
    send, so any of them can be aimed at a port that has no modem
    control lines or has just been unplugged.
    """

    def _make_proxy(self):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._serial_config = {'port': '/dev/ttyUSB0'}
        proxy._monitors = []
        proxy._last_signals = 0
        return proxy

    def test_set_rts_survives_an_unsupported_device(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        type(proxy._serial).rts = property(
            lambda self: True,
            lambda self, value: (_ for _ in ()).throw(
                OSError(25, 'Inappropriate ioctl for device')))
        proxy.set_rts(False)  # must not raise
        proxy._log.warning.assert_called_once()

    def test_set_dtr_survives_an_unsupported_device(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        type(proxy._serial).dtr = property(
            lambda self: True,
            lambda self, value: (_ for _ in ()).throw(
                OSError(25, 'Inappropriate ioctl for device')))
        proxy.set_dtr(False)
        proxy._log.warning.assert_called_once()

    def test_setters_are_noops_without_a_port(self):
        proxy = self._make_proxy()
        proxy._serial = None
        proxy.set_rts(True)
        proxy.set_dtr(True)
        proxy._log.warning.assert_not_called()

    def test_write_failure_tells_the_servers_and_closes_the_port(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.fileno.side_effect = OSError('no fileno')
        proxy._serial.write.side_effect = OSError('device gone')
        server = MagicMock()
        proxy._servers = [server]
        proxy._close_device = MagicMock()
        proxy.send(b'data')  # must not raise
        # What that means for the clients is the server's to decide.
        server.on_serial_lost.assert_called_once()
        proxy._close_device.assert_called_once()

    def test_successful_write_notifies_monitors(self):
        """And names whoever asked for it, so a monitor can say who."""
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        writer = object()
        monitor = MagicMock()
        proxy._monitors = [monitor]
        proxy.send(b'data', writer)
        monitor.on_data.assert_called_once_with(writer, b'data')

    def test_and_the_device_is_named_by_nobody(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.read.return_value = b'from the device'
        proxy._serial.in_waiting = 15
        monitor = MagicMock()
        proxy._monitors = [monitor]
        proxy._process_serial_data()
        monitor.on_data.assert_called_once_with(None, b'from the device')


class TestSignalLogging(unittest.TestCase):
    """Signal transitions have to be visible with -v.

    Without this the log shows serial data but says nothing about RTS,
    DTR or the input lines, which is exactly what you want to see when
    a device is not responding.
    """

    def _make_proxy(self, debug=True, control=False):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._log.isEnabledFor.return_value = debug
        proxy._serial_config = {'port': '/dev/ttyUSB0'}
        proxy._monitors = []
        proxy._last_signals = None
        proxy._last_signal_poll = 0
        proxy._signal_poll_interval = 0
        proxy._has_control_servers = control
        proxy._serial = MagicMock()
        return proxy

    def _debug_lines(self, proxy):
        return [call[0][0] % call[0][1:]
            for call in proxy._log.debug.call_args_list]

    def test_format_covers_every_signal(self):
        self.assertEqual(
            _format_signals(0), 'RTS=0 DTR=0 CTS=0 DSR=0 RI=0 CD=0')
        self.assertEqual(
            _format_signals(0b111111), 'RTS=1 DTR=1 CTS=1 DSR=1 RI=1 CD=1')

    def test_set_rts_is_logged(self):
        proxy = self._make_proxy()
        proxy.get_signals = MagicMock(return_value=1)
        proxy.set_rts(True)
        self.assertIn(
            '(/dev/ttyUSB0): set RTS=1', self._debug_lines(proxy))

    def test_set_dtr_is_logged(self):
        proxy = self._make_proxy()
        proxy.get_signals = MagicMock(return_value=2)
        proxy.set_dtr(False)
        self.assertIn(
            '(/dev/ttyUSB0): set DTR=0', self._debug_lines(proxy))

    def test_first_reading_is_logged_even_when_all_low(self):
        proxy = self._make_proxy()
        proxy.get_signals = MagicMock(return_value=0)
        proxy.process_signals()
        self.assertIn(
            '(/dev/ttyUSB0): signals RTS=0 DTR=0 CTS=0 DSR=0 RI=0 CD=0 '
            '(initial)', self._debug_lines(proxy))

    def test_input_signal_change_is_logged(self):
        """CTS moving is the case that had nothing sampling it."""
        proxy = self._make_proxy()
        proxy.get_signals = MagicMock(return_value=0)
        proxy.process_signals()
        proxy.get_signals.return_value = 1 << 2  # CTS high
        proxy.process_signals()
        self.assertIn(
            '(/dev/ttyUSB0): signals RTS=0 DTR=0 CTS=1 DSR=0 RI=0 CD=0',
            self._debug_lines(proxy))

    def test_steady_signals_are_not_logged_again(self):
        proxy = self._make_proxy()
        proxy.get_signals = MagicMock(return_value=0b101)
        for _ in range(5):
            proxy.process_signals()
        lines = [l for l in self._debug_lines(proxy) if 'signals' in l]
        self.assertEqual(len(lines), 1)

    def test_polling_is_skipped_without_debug_or_control(self):
        """Sampling costs an ioctl per line; nobody asked for it here."""
        proxy = self._make_proxy(debug=False, control=False)
        proxy.get_signals = MagicMock(return_value=0)
        proxy.process_signals()
        proxy.get_signals.assert_not_called()

    def test_control_servers_poll_without_debug(self):
        proxy = self._make_proxy(debug=False, control=True)
        proxy.get_signals = MagicMock(return_value=0)
        proxy.process_signals()
        proxy.get_signals.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class TestSerialWriteBuffer(unittest.TestCase):
    """Writing to the device must never stall the loop.

    One selectors loop serves every port and the HTTP API, and a serial
    port drains at its baud rate: 256 KB at 9600 is minutes. Writes are
    buffered and driven by write interest, exactly as client sockets
    already are.
    """

    def _make_proxy(self, accept=None):
        """A proxy whose device accepts `accept` bytes per write"""
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._serial_config = {'port': '/dev/null'}
        proxy._monitors = []
        device = MagicMock()
        # No usable descriptor, so writes go through pyserial - which is
        # what device.write mocks here.
        device.fileno.side_effect = OSError('no fileno')
        if accept is None:
            device.write.side_effect = lambda data: len(data)
        else:
            device.write.side_effect = lambda data: min(accept, len(data))
        proxy._serial = device
        proxy._serial_source = device
        return proxy, device

    def test_a_write_the_device_takes_whole_leaves_nothing_behind(self):
        proxy, device = self._make_proxy()
        proxy.send(b'hello')
        device.write.assert_called_once()
        self.assertEqual(bytes(proxy._out_buffer), b'')

    def test_what_the_device_would_not_take_is_kept(self):
        proxy, _ = self._make_proxy(accept=2)
        proxy.send(b'hello')
        self.assertEqual(bytes(proxy._out_buffer), b'llo')

    def test_the_rest_goes_out_as_the_device_accepts_it(self):
        proxy, device = self._make_proxy(accept=2)
        proxy.send(b'hello')
        device.write.side_effect = lambda data: len(data)
        proxy.flush_serial()
        self.assertEqual(bytes(proxy._out_buffer), b'')

    def test_order_is_preserved_across_partial_writes(self):
        proxy, device = self._make_proxy(accept=3)
        written = bytearray()
        device.write.side_effect = lambda data: (
            written.extend(data[:3]) or min(3, len(data)))
        proxy.send(b'abcdefghij')
        for _ in range(5):
            proxy.flush_serial()
        self.assertEqual(bytes(written), b'abcdefghij')

    def test_a_device_error_drops_the_clients(self):
        proxy, device = self._make_proxy()
        device.write.side_effect = OSError('device gone')
        proxy._serial_failed = MagicMock()
        proxy.send(b'x')
        proxy._serial_failed.assert_called_once()

    def test_nothing_is_written_while_disconnected(self):
        proxy, _ = self._make_proxy()
        proxy._serial = None
        proxy.send(b'x')
        self.assertEqual(bytes(proxy._out_buffer), b'')


class TestSerialWriteInterest(unittest.TestCase):
    """EVENT_WRITE is armed only while there is something to write"""

    def _make_proxy(self, accept=0):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._serial_config = {'port': '/dev/null'}
        proxy._monitors = []
        device = MagicMock()
        device.fileno.side_effect = OSError('no fileno')
        device.write.side_effect = lambda data: min(accept, len(data))
        proxy._serial = device
        proxy._serial_source = device
        proxy._selector = MagicMock()
        # A registered port is already being watched for reads.
        proxy._serial_interest = selectors.EVENT_READ
        return proxy, device

    def test_a_backed_up_write_arms_write_interest(self):
        proxy, _ = self._make_proxy(accept=0)
        proxy.send(b'stuck')
        proxy._selector.modify.assert_called_once()
        mask = proxy._selector.modify.call_args.args[1]
        self.assertTrue(mask & selectors.EVENT_WRITE)
        self.assertTrue(mask & selectors.EVENT_READ)

    def test_a_drained_buffer_disarms_write_interest(self):
        proxy, device = self._make_proxy(accept=0)
        proxy.send(b'stuck')
        proxy._selector.modify.reset_mock()
        device.write.side_effect = lambda data: len(data)
        proxy.flush_serial()
        mask = proxy._selector.modify.call_args.args[1]
        self.assertEqual(mask, selectors.EVENT_READ)

    def test_a_write_that_goes_straight_out_never_arms_it(self):
        proxy, _ = self._make_proxy(accept=1000)
        proxy.send(b'quick')
        proxy._selector.modify.assert_not_called()


class TestSerialBackpressure(unittest.TestCase):
    """Stop reading clients rather than buffering without end.

    A client can hand over data far faster than the device drains it.
    Dropping is a last resort; the honest answer is to stop reading,
    which closes the TCP window and makes the sender wait.
    """

    def _make_proxy(self):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._serial_config = {'port': '/dev/null'}
        proxy._monitors = []
        device = MagicMock()
        device.fileno.side_effect = OSError('no fileno')
        device.write.side_effect = lambda data: 0
        proxy._serial = device
        proxy._serial_source = device
        server = MagicMock()
        proxy._servers = [server]
        return proxy, device, server

    def test_a_small_backlog_does_not_pause_anyone(self):
        proxy, _, server = self._make_proxy()
        proxy.send(b'x' * 1024)
        server.set_read_paused.assert_not_called()

    def test_passing_the_high_water_mark_pauses_reading(self):
        proxy, _, server = self._make_proxy()
        proxy.send(b'x' * (SerialProxy.WRITE_HIGH_WATER + 1))
        server.set_read_paused.assert_called_once_with(True)

    def test_dropping_below_the_low_water_mark_resumes_reading(self):
        proxy, device, server = self._make_proxy()
        proxy.send(b'x' * (SerialProxy.WRITE_HIGH_WATER + 1))
        server.set_read_paused.reset_mock()
        # The device takes all but a little.
        keep = SerialProxy.WRITE_LOW_WATER - 1
        device.write.side_effect = lambda data: len(data) - keep
        proxy.flush_serial()
        server.set_read_paused.assert_called_once_with(False)

    def test_between_the_marks_nothing_changes(self):
        proxy, device, server = self._make_proxy()
        proxy.send(b'x' * (SerialProxy.WRITE_HIGH_WATER + 1))
        server.set_read_paused.reset_mock()
        # Still above the low water mark: no flapping on every byte.
        device.write.side_effect = lambda data: 1
        proxy.flush_serial()
        server.set_read_paused.assert_not_called()

    def test_the_hard_limit_drops_and_says_so(self):
        """WebSocket clients cannot be paused, so a cap still matters"""
        proxy, _, _ = self._make_proxy()
        proxy.send(b'x' * (SerialProxy.WRITE_BUFFER_LIMIT + 4096))
        self.assertLessEqual(
            len(proxy._out_buffer), SerialProxy.WRITE_BUFFER_LIMIT)
        self.assertTrue(proxy._log.warning.called)

    def test_disconnecting_clears_the_backlog_and_the_pause(self):
        proxy, _, server = self._make_proxy()
        proxy.send(b'x' * (SerialProxy.WRITE_HIGH_WATER + 1))
        server.set_read_paused.reset_mock()
        proxy.has_connections = MagicMock(return_value=False)
        proxy._unregister_serial = MagicMock()
        proxy._stop_reader_thread = MagicMock()
        proxy.disconnect()
        self.assertEqual(bytes(proxy._out_buffer), b'')
        server.set_read_paused.assert_called_once_with(False)


class TestWriteTimeoutIsForced(unittest.TestCase):
    """A blocking write has no place in the loop, whatever the config"""

    def _config(self, **serial_cfg):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        cfg = {'port': '/dev/ttyUSB0'}
        cfg.update(serial_cfg)
        return proxy._init_serial_config(cfg)

    def test_write_timeout_defaults_to_non_blocking(self):
        self.assertEqual(self._config()['write_timeout'], 0)

    def test_a_configured_write_timeout_is_overridden(self):
        self.assertEqual(self._config(write_timeout=30)['write_timeout'], 0)


class TestDeviceWritePath(unittest.TestCase):
    """How bytes actually reach the device.

    pyserial's write() swallows the EAGAIN from a completely full
    device and loops on it, so with write_timeout=0 a stuck port spins
    a core inside a call that never returns. Ports with a real
    descriptor are written through it instead.
    """

    def _proxy(self):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._serial_config = {'port': '/dev/null'}
        proxy._monitors = []
        return proxy

    def test_a_port_with_a_descriptor_is_written_through_it(self):
        proxy = self._proxy()
        device = MagicMock()
        device.fileno.return_value = 42
        proxy._serial = device
        with patch('ser2tcp.serial_proxy._os.write',
                   return_value=3) as os_write:
            self.assertEqual(proxy._write_device(bytearray(b'abc')), 3)
        os_write.assert_called_once()
        device.write.assert_not_called()

    def test_a_full_device_reports_nothing_written(self):
        """EAGAIN means "not now", not an error and not a reason to loop"""
        proxy = self._proxy()
        device = MagicMock()
        device.fileno.return_value = 42
        proxy._serial = device
        with patch('ser2tcp.serial_proxy._os.write',
                   side_effect=BlockingIOError()):
            self.assertEqual(proxy._write_device(bytearray(b'abc')), 0)

    def test_a_port_without_a_descriptor_falls_back_to_pyserial(self):
        proxy = self._proxy()
        device = MagicMock()
        device.fileno.side_effect = OSError('no fileno')
        device.write.return_value = 3
        proxy._serial = device
        self.assertEqual(proxy._write_device(bytearray(b'abc')), 3)
        device.write.assert_called_once_with(b'abc')


class TestStalledDevice(unittest.TestCase):
    """A device that takes nothing at all is stuck, not slow"""

    def _proxy(self):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._serial_config = {'port': '/dev/null'}
        proxy._monitors = []
        device = MagicMock()
        device.fileno.side_effect = OSError('no fileno')
        device.write.side_effect = lambda data: 0
        proxy._serial = device
        proxy._serial_source = device
        proxy._serial_failed = MagicMock()
        return proxy

    def test_a_slow_device_is_left_alone(self):
        proxy = self._proxy()
        proxy.send(b'queued')
        proxy._serial_failed.assert_not_called()

    def test_a_device_that_never_takes_anything_is_given_up_on(self):
        proxy = self._proxy()
        proxy.send(b'queued')
        proxy._write_progress_at = time.time() - (
            SerialProxy.WRITE_STALL_TIMEOUT + 1)
        proxy.flush_serial()
        proxy._serial_failed.assert_called_once()
        self.assertTrue(proxy._log.warning.called)

    def test_progress_resets_the_clock(self):
        proxy = self._proxy()
        proxy.send(b'queued')
        proxy._write_progress_at = time.time() - (
            SerialProxy.WRITE_STALL_TIMEOUT + 1)
        proxy._serial.write.side_effect = lambda data: len(data)
        proxy.flush_serial()
        proxy._serial_failed.assert_not_called()


class TestReaderThreadShutdown(unittest.TestCase):
    """Stopping the reader thread must not stall the loop.

    The thread exists for ports with no usable descriptor, which cannot
    be selected on. Waiting two seconds for it inside disconnect() -
    which runs in the one loop serving everything else - was two seconds
    of the whole process standing still, every time the last client on
    such a port left.
    """

    def _proxy(self):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        proxy._serial_config = {'port': 'socket://host:1234'}
        proxy._monitors = []
        proxy._serial = MagicMock()
        proxy._serial.in_waiting = 0
        return proxy

    def test_stopping_an_idle_proxy_does_nothing(self):
        proxy = self._proxy()
        proxy._stop_reader_thread()  # no thread: must not raise

    def test_a_responsive_thread_is_waited_for_briefly(self):
        proxy = self._proxy()
        proxy._serial.read.return_value = b''
        proxy._start_reader_thread()
        started = time.time()
        proxy._stop_reader_thread()
        self.assertLess(time.time() - started, 1.0)
        self.assertIsNone(proxy._reader_thread)

    def test_the_port_is_asked_to_cancel_the_read(self):
        """Waking it at once beats waiting out a read timeout"""
        proxy = self._proxy()
        proxy._serial.read.return_value = b''
        proxy._start_reader_thread()
        proxy._stop_reader_thread()
        proxy._serial.cancel_read.assert_called_once()

    def test_a_backend_without_cancel_read_still_stops(self):
        proxy = self._proxy()
        proxy._serial.read.return_value = b''
        proxy._serial.cancel_read.side_effect = NotImplementedError()
        proxy._start_reader_thread()
        proxy._stop_reader_thread()
        self.assertIsNone(proxy._reader_thread)

    def test_a_thread_that_will_not_stop_is_reported(self):
        """Better a warning than a loop frozen without explanation"""
        proxy = self._proxy()
        release = threading.Event()
        proxy._serial.read.side_effect = lambda **kw: (
            release.wait(5) or b'')
        proxy._serial.cancel_read.side_effect = NotImplementedError()
        proxy._start_reader_thread()
        time.sleep(0.05)
        started = time.time()
        try:
            proxy._stop_reader_thread()
            self.assertLess(time.time() - started, 1.5)
            self.assertTrue(proxy._log.warning.called)
        finally:
            release.set()

    def test_the_thread_closes_its_own_end_of_the_pair(self):
        """Neither side closes a descriptor the other might still use.

        Closing the writer from here while the thread is mid-send is a
        descriptor that can be reused underneath it - the kind of bug
        that shows up somewhere else entirely.
        """
        proxy = self._proxy()
        proxy._serial.read.return_value = b''
        proxy._start_reader_thread()
        writer = proxy._reader_sock_w
        proxy._stop_reader_thread()
        deadline = time.time() + 2
        while time.time() < deadline and writer.fileno() != -1:
            time.sleep(0.02)
        self.assertEqual(writer.fileno(), -1)

    def test_the_reader_forwards_what_it_reads(self):
        proxy = self._proxy()
        chunks = [b'from the device', b'']
        proxy._serial.read.side_effect = lambda **kw: (
            chunks.pop(0) if chunks else b'')
        proxy._start_reader_thread()
        try:
            proxy._reader_sock_r.settimeout(2)
            self.assertEqual(proxy._reader_sock_r.recv(64), b'from the device')
        finally:
            proxy._stop_reader_thread()


class TestReadTimeoutIsForced(unittest.TestCase):
    """The reader thread has to come up for air to notice it should stop"""

    def _config(self, **serial_cfg):
        proxy = SerialProxy.__new__(SerialProxy)
        _mock_init(proxy)
        proxy._log = MagicMock()
        cfg = {'port': '/dev/ttyUSB0'}
        cfg.update(serial_cfg)
        return proxy._init_serial_config(cfg)

    def test_a_read_timeout_is_set(self):
        self.assertEqual(
            self._config()['timeout'], SerialProxy.READ_TIMEOUT)

    def test_a_configured_read_timeout_is_overridden(self):
        self.assertEqual(
            self._config(timeout=None)['timeout'], SerialProxy.READ_TIMEOUT)


class TestHalfBuiltProxyLeavesNothingBehind(unittest.TestCase):
    """A port whose second server fails must not leave the first running.

    __init__ raises, so the caller never gets the object and has nothing
    to close - but the servers already built are listening and
    registered in the selector. The result answers connections, opens
    the device a second time, and appears nowhere in the status.
    """

    def _config(self, servers):
        return {
            'name': 'dev',
            'serial': {'port': '/dev/ttyUSB-nowhere'},
            'servers': servers,
        }

    def _free_port(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]

    def test_a_good_config_still_builds(self):
        port = self._free_port()
        proxy = SerialProxy(self._config([
            {'protocol': 'tcp', 'address': '127.0.0.1', 'port': port}]),
            log=MagicMock())
        try:
            self.assertEqual(len(proxy.servers), 1)
        finally:
            proxy.close()

    def test_the_first_server_is_closed_when_a_later_one_fails(self):
        first, blocked = self._free_port(), self._free_port()
        keep = socket.socket()
        keep.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        keep.bind(('127.0.0.1', blocked))
        keep.listen(1)
        try:
            with self.assertRaises(Exception):
                SerialProxy(self._config([
                    {'protocol': 'tcp', 'address': '127.0.0.1',
                     'port': first},
                    {'protocol': 'tcp', 'address': '127.0.0.1',
                     'port': blocked},
                ]), log=MagicMock())
            # The first port must be free again: nothing is listening.
            probe = socket.socket()
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', first))
            finally:
                probe.close()
        finally:
            keep.close()

    def test_the_selector_is_left_clean(self):
        first, blocked = self._free_port(), self._free_port()
        keep = socket.socket()
        keep.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        keep.bind(('127.0.0.1', blocked))
        keep.listen(1)
        selector = selectors.DefaultSelector()
        try:
            with self.assertRaises(Exception):
                SerialProxy(self._config([
                    {'protocol': 'tcp', 'address': '127.0.0.1',
                     'port': first},
                    {'protocol': 'tcp', 'address': '127.0.0.1',
                     'port': blocked},
                ]), log=MagicMock(), selector=selector)
            self.assertEqual(len(selector.get_map()), 0)
        finally:
            selector.close()
            keep.close()
