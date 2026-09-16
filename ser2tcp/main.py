"""Ser2tcp
Simple proxy for connecting over TCP or telnet to serial port
"""

import argparse as _argparse
import importlib.metadata as _metadata
import json as _json
import logging as _logging
import os as _os
import signal as _signal
import socket as _socket

import serial.tools.list_ports as _list_ports

import ser2tcp.serial_proxy as _serial_proxy
import ser2tcp.server_manager as _server_manager

try:
    _about = _metadata.metadata("ser2tcp")
    VERSION_STR = "%s %s (%s)" % (
        _about["Name"], _about["Version"], _about["Author-email"])
except _metadata.PackageNotFoundError:
    VERSION_STR = "ser2tcp (not installed)"

DESCRIPTION_STR = VERSION_STR + """
(c) 2016-2026 by pavel.revak@gmail.com
https://github.com/cortexm/ser2tcp
"""

DEFAULT_CONFIG_DIR = _os.path.expanduser("~/.config/ser2tcp")
DEFAULT_CONFIG_PATH = _os.path.join(DEFAULT_CONFIG_DIR, "config.json")


def find_free_port(start_port=20080, max_attempts=100):
    """Find first available port starting from start_port"""
    for port in range(start_port, start_port + max_attempts):
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    return None


def create_default_config(config_path, log):
    """Create default config with HTTP server on free port"""
    port = find_free_port()
    if port is None:
        raise SystemExit("Cannot find free port for HTTP server")

    config = {
        "ports": [],
        "http": [{"name": "main", "address": "127.0.0.1", "port": port}]
    }

    config_dir = _os.path.dirname(config_path)
    if not _os.path.exists(config_dir):
        _os.makedirs(config_dir)

    with open(config_path, 'w', encoding='utf-8') as f:
        _json.dump(config, f, indent=2)

    log.info(f"Created default config: {config_path}")
    log.info(f"HTTP server will start on port {port}")
    return config


def list_usb_devices():
    """List USB serial devices with match attributes"""
    devices = []
    for port in _list_ports.comports():
        if port.vid is not None:
            devices.append(port)
    if not devices:
        print("No USB serial devices found")
        return
    for port in devices:
        print(f"{port.device}")
        print(f"  vid: 0x{port.vid:04X}")
        print(f"  pid: 0x{port.pid:04X}")
        if port.serial_number:
            print(f"  serial_number: {port.serial_number}")
        if port.manufacturer:
            print(f"  manufacturer: {port.manufacturer}")
        if port.product:
            print(f"  product: {port.product}")
        if port.location:
            print(f"  location: {port.location}")
        print()


def main():
    """Main"""
    parser = _argparse.ArgumentParser(description=DESCRIPTION_STR)
    parser.add_argument('-V', '--version', action='version', version=VERSION_STR)
    log_group = parser.add_mutually_exclusive_group()
    log_group.add_argument(
        '-v', '--verbose', action='count', default=0,
        help="Verbose output (-v: debug)")
    log_group.add_argument(
        '-q', '--quiet', action='store_true',
        help="No output")
    parser.add_argument(
        '-u', '--usb', action='store_true',
        help="List USB serial devices and exit")
    parser.add_argument(
        '--hash-password', metavar='PASSWORD',
        help="Hash password for config file and exit")
    parser.add_argument(
        '-c', '--config', default=DEFAULT_CONFIG_PATH,
        help=f"configuration in JSON format (default: {DEFAULT_CONFIG_PATH})")
    args = parser.parse_args()

    if args.hash_password:
        import ser2tcp.http_auth as _http_auth
        print(_http_auth.hash_password(args.hash_password))
        return

    if args.usb:
        list_usb_devices()
        return

    _logging.basicConfig(format='%(levelname).1s: %(message)s (%(filename)s:%(lineno)s)')
    log = _logging.getLogger('ser2tcp')
    if args.quiet:
        log.setLevel(_logging.CRITICAL)
    elif args.verbose:
        log.setLevel(_logging.DEBUG)
    else:
        log.setLevel(_logging.INFO)

    config_path = args.config
    if _os.path.exists(config_path):
        with open(config_path, "r", encoding='utf-8') as config_file:
            configuration = _json.load(config_file)
    else:
        if config_path == DEFAULT_CONFIG_PATH:
            configuration = create_default_config(config_path, log)
        else:
            raise SystemExit(f"Config file not found: {config_path}")

    if isinstance(configuration, list):
        ports = configuration
    elif isinstance(configuration, dict):
        ports = configuration.get('ports', [])
    else:
        raise SystemExit("Invalid configuration format")

    http_config = configuration.get('http') if isinstance(configuration, dict) else None
    if not ports and not http_config:
        raise SystemExit("No ports or HTTP server configured")

    # Cert bundles live next to config.json. Pass to SerialProxy so SSL
    # servers can resolve bundle references.
    certs_dir = _os.path.join(_os.path.dirname(config_path) or '.', 'certs')

    servers_manager = _server_manager.ServersManager(log=log)
    serial_proxies = []
    for config in ports:
        try:
            proxy = _serial_proxy.SerialProxy(
                config, log, certs_dir=certs_dir,
                selector=servers_manager.selector)
        except Exception as err:
            log.error("Failed to create port: %s", err)
            continue
        serial_proxies.append(proxy)
        servers_manager.add_server(proxy)

    if isinstance(configuration, dict) and 'http' in configuration:
        import ser2tcp.http_server as _http_server
        http_server = _http_server.HttpServerWrapper(
            configuration['http'], serial_proxies, log,
            config_path=args.config, configuration=configuration,
            server_manager=servers_manager,
            selector=servers_manager.selector)
        servers_manager.add_server(http_server)
        # uhttp connections come out of the loop as return values, not
        # through a server the manager knows about.
        servers_manager.set_client_handler(http_server.handle_client)

    _signal.signal(_signal.SIGTERM, servers_manager.stop)
    _signal.signal(_signal.SIGINT, servers_manager.stop)

    servers_manager.run()
    log.info("Exiting..")
