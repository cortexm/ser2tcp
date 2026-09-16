"""Tests for SerialProxy config parsing"""

import unittest
from unittest.mock import patch, MagicMock

import serial

from ser2tcp.serial_proxy import SerialProxy, _format_signals


def _mock_init(self, config=None, log=None):
    """Mock init that sets required attributes for __del__"""
    self._servers = []
    self._serial = None
    self._selector = None
    self._serial_source = None
    self._reader_thread = None
    self._reader_sock_r = None
    self._reader_sock_w = None
    self._reader_running = False


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

    def test_write_failure_drops_clients_and_closes_the_port(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        proxy._serial.write.side_effect = OSError('device gone')
        server = MagicMock()
        proxy._servers = [server]
        proxy.disconnect = MagicMock()
        proxy.send(b'data')  # must not raise
        server.close_connections.assert_called_once()
        proxy.disconnect.assert_called_once()

    def test_successful_write_notifies_monitors(self):
        proxy = self._make_proxy()
        proxy._serial = MagicMock()
        seen = []
        proxy._monitors = [lambda direction, data: seen.append((direction, data))]
        proxy.send(b'data')
        self.assertEqual(seen, [(1, b'data')])


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
