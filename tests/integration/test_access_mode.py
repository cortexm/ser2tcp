"""Integration tests for a server's access mode.

Gating a direction is only worth anything if the bytes really do not
move, so this drives a pty as the device and watches both ways at once:
one server that may only read, one that may only write, and one with no
restriction to prove the wiring is not simply broken.
"""

import os
import unittest

from tests.integration import base
from tests.integration.test_serial import (
    SerialPtyTestCase, read_client, read_device)

# A gate is proved by nothing arriving, and nothing arriving cannot be
# hurried. Kept short so the negative cases do not dominate the suite;
# the positive ones use the helpers' own generous default.
SILENCE = 1.0


class AccessModeTestCase(SerialPtyTestCase):
    """One pty, three TCP servers on it with different modes."""

    ro_port = None
    wo_port = None

    @classmethod
    def port_servers(cls):
        cls.ro_port = base.free_port()
        cls.wo_port = base.free_port()
        return [
            {'protocol': 'tcp', 'address': '127.0.0.1', 'port': cls.tcp_port},
            {'protocol': 'tcp', 'address': '127.0.0.1', 'port': cls.ro_port,
             'access': 'ro'},
            {'protocol': 'tcp', 'address': '127.0.0.1', 'port': cls.wo_port,
             'access': 'wo'},
        ]

class TestReadOnly(AccessModeTestCase):

    def test_it_receives_what_the_device_says(self):
        sock = self.connect(self.ro_port)
        self.wait_for_serial()
        os.write(self.master_fd, b'from device')
        self.assertEqual(read_client(sock, 11), b'from device')

    def test_what_it_sends_never_reaches_the_device(self):
        sock = self.connect(self.ro_port)
        self.wait_for_serial()
        self.drain_device()
        sock.sendall(b'should not arrive')
        self.assertEqual(
            read_device(self.master_fd, 17, timeout=SILENCE), b'')

    def test_and_does_not_reach_the_other_clients_either(self):
        """It is dropped, not quietly broadcast somewhere else"""
        listener = self.connect(self.tcp_port)
        writer = self.connect(self.ro_port)
        self.wait_for_serial()
        self.drain_device()
        writer.sendall(b'nope')
        self.assertEqual(read_client(listener, 4, timeout=SILENCE), b'')


class TestWriteOnly(AccessModeTestCase):

    def test_what_it_sends_reaches_the_device(self):
        sock = self.connect(self.wo_port)
        self.wait_for_serial()
        self.drain_device()
        sock.sendall(b'to device')
        self.assertEqual(read_device(self.master_fd, 9), b'to device')

    def test_it_never_hears_the_device(self):
        sock = self.connect(self.wo_port)
        self.wait_for_serial()
        os.write(self.master_fd, b'from device')
        self.assertEqual(read_client(sock, 11, timeout=SILENCE), b'')

    def test_while_an_unrestricted_client_does(self):
        """Proves the device really did speak, so the silence is the mode"""
        deaf = self.connect(self.wo_port)
        hearing = self.connect(self.tcp_port)
        self.wait_for_serial()
        os.write(self.master_fd, b'from device')
        self.assertEqual(read_client(hearing, 11), b'from device')
        self.assertEqual(read_client(deaf, 11, timeout=SILENCE), b'')


class TestTheDefaultIsUnchanged(AccessModeTestCase):

    def test_both_directions_work(self):
        sock = self.connect(self.tcp_port)
        self.wait_for_serial()
        self.drain_device()
        sock.sendall(b'up')
        self.assertEqual(read_device(self.master_fd, 2), b'up')
        os.write(self.master_fd, b'down')
        self.assertEqual(read_client(sock, 4), b'down')


class TestABadModeIsRefused(AccessModeTestCase):
    """The API must not accept what would stop the port from starting."""

    def test_the_api_says_no(self):
        config = self.build_config()['ports'][0]
        config['servers'][0]['access'] = 'readonly'
        status, body = self.put(
            '/api/ports/' + self.port_id(), config)
        self.assertEqual(status, 400, body)
        self.assertIn('readonly', body['error'])

    def test_data_and_access_disagreeing_is_refused(self):
        config = self.build_config()['ports'][0]
        config['servers'][0]['data'] = False
        config['servers'][0]['access'] = 'rw'
        status, body = self.put('/api/ports/' + self.port_id(), config)
        self.assertEqual(status, 400, body)

    def test_zz_the_port_is_still_running_afterwards(self):
        sock = self.connect(self.tcp_port)
        self.wait_for_serial()
        os.write(self.master_fd, b'alive')
        self.assertEqual(read_client(sock, 5), b'alive')


if __name__ == '__main__':
    unittest.main()
