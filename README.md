# Ser2tcp

Simple proxy for connecting over TCP, TELNET, SSL, WebSocket or Unix socket to serial port

https://github.com/cortexm/ser2tcp

## Features

- can serve multiple serial ports using pyserial library
- each serial port can have multiple servers
- server can use TCP, TELNET, SSL, WebSocket or SOCKET protocol
  - TCP protocol just bridge whole RAW serial stream to TCP
  - TELNET protocol will send every character immediately and not wait for ENTER, it is useful to use standard `telnet` as serial terminal
  - SSL protocol provides encrypted TCP connection with optional mutual TLS (mTLS) client certificate verification
  - WebSocket protocol connects through the HTTP server with binary frames for data and JSON text frames for signal control
  - SOCKET protocol uses Unix domain socket for local IPC
- servers accepts multiple connections at one time
  - each connected client can sent to serial port
  - serial port send received data to all connected clients
- non-blocking send with configurable timeout and buffer limit
- serial signal control (RTS, DTR, CTS, DSR, RI, CD) via escape protocol or WebSocket JSON
- IP filtering with allow/deny lists (CIDR notation supported)
- built-in HTTP server with REST API for status monitoring
- web interface for viewing configured ports and connections
- web terminal clients (xterm.js VT100 terminal and raw colored view)
- authentication with session management and API tokens
- SSL certificate manager via web UI (upload, paste, drag-and-drop PEM files)
- light/dark mode web UI (follows system preference)

## Installation

```
pip install ser2tcp
```

or from source:

```
pip install .
```

### Uninstall

```
pip uninstall ser2tcp
```

## Command line options

```
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
  -v, --verbose         Increase verbosity
  -u, --usb             List USB serial devices and exit
  --hash-password PASSWORD
                        Hash password for config file and exit
  -c CONFIG, --config CONFIG
                        configuration in JSON format (default: ~/.config/ser2tcp/config.json)
```

If no config file is specified and default config doesn't exist, creates one with HTTP server on first free port from 20080.

### Verbose

- By default print only ERROR and WARNING messages
- `-v`: will print INFO messages
- `-vv`: print also DEBUG messages

## Configuration file example

```json
{
    "ports": [
        {
            "serial": {
                "port": "/dev/ttyUSB0",
                "baudrate": 115200,
                "parity": "NONE",
                "stopbits": "ONE"
            },
            "servers": [
                {
                    "address": "127.0.0.1",
                    "port": 10001,
                    "protocol": "tcp"
                },
                {
                    "address": "0.0.0.0",
                    "port": 10002,
                    "protocol": "telnet",
                    "send_timeout": 5.0,
                    "buffer_limit": 65536
                }
            ]
        }
    ]
}
```

Legacy format (JSON array at root level) is still supported for backward compatibility.

### Serial configuration

`serial` structure pass all parameters to [serial.Serial](https://pythonhosted.org/pyserial/pyserial_api.html#classes) constructor from pyserial library, this allows full control of the serial port.

#### USB device matching

Instead of specifying `port` directly, you can use `match` to find device by USB attributes:

```json
{
    "serial": {
        "match": {
            "vid": "0x303A",
            "pid": "0x4001",
            "serial_number": "dcda0c2004bc0000"
        },
        "baudrate": 115200
    }
}
```

Use `ser2tcp --usb` to list available USB devices with their attributes:

```
$ ser2tcp --usb
/dev/cu.usbmodem1101
  vid: 0x303A
  pid: 0x4001
  serial_number: dcda0c2004bc0000
  manufacturer: Espressif Systems
  product: Espressif Device
  location: 1-1
```

Match attributes: `vid`, `pid`, `serial_number`, `manufacturer`, `product`, `location`, `description`, `hwid`

- Wildcard `*` supported (e.g. `"product": "CP210*"`)
- Matching is case-insensitive
- Error if multiple devices match the criteria
- Device is resolved when client connects, not at startup (device does not need to exist at startup)
- `baudrate` is optional (default 9600, CDC devices ignore it)

### Server configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `address` | Bind address (IP for tcp/telnet/ssl, path for socket) | required* |
| `port` | TCP port (not used for socket/websocket) | required* |
| `protocol` | `tcp`, `telnet`, `ssl`, `websocket` or `socket` | required |
| `endpoint` | WebSocket URL path (websocket only), must be unique | required* |
| `token` | Per-server auth token (websocket only) | - |
| `ssl` | SSL configuration (required for `ssl` protocol) | - |
| `data` | Forward serial data (default true), `false` = control-only | true |
| `control` | Signal control configuration | - |
| `send_timeout` | Disconnect client if data cannot be sent within this time (seconds) | 5.0 |
| `buffer_limit` | Maximum send buffer size per client (bytes), `null` for unlimited | null |
| `max_connections` | Maximum clients per server (0 = unlimited) | 0 |

\* `address`/`port` required for tcp/telnet/ssl; `address` for socket; `endpoint` for websocket

#### Port-level connection limit

You can also limit total connections across all servers on a port:

```json
{
    "ports": [{
        "max_connections": 10,
        "serial": {"port": "/dev/ttyUSB0"},
        "servers": [
            {"protocol": "tcp", "address": "0.0.0.0", "port": 10001, "max_connections": 5},
            {"protocol": "websocket", "endpoint": "device"}
        ]
    }]
}
```

- Port-level `max_connections`: limits total clients across all servers (default 0 = unlimited)
- Server-level `max_connections`: limits clients on that specific server (default 0 = unlimited)
- Both limits are checked — if either is reached, new connections are rejected

#### WebSocket configuration

WebSocket connections go through the HTTP server — no separate listening port needed:

```json
{
    "protocol": "websocket",
    "endpoint": "my-device",
    "control": {
        "rts": true,
        "signals": ["rts", "dtr", "cts", "dsr"]
    }
}
```

- Accessible at `ws://host:port/ws/my-device` (or `wss://` for HTTPS)
- Available on all configured HTTP servers
- Binary frames carry raw serial data (bidirectional)
- Text frames carry JSON control messages: `{"rts": true}`, `{"signals": {...}}`
- Signal state sent automatically on connect, then only on change
- Auth: per-server `token`, global user session, or both accepted
- Web terminals available at `/xterm/<endpoint>` (VT100) and `/raw/<endpoint>` (colored hex)

#### Socket configuration

For `socket` protocol, `address` is the path to the Unix domain socket:

```json
{
    "address": "/tmp/ser2tcp.sock",
    "protocol": "socket"
}
```

- Socket file is created on startup and removed on shutdown
- If socket file already exists, it is replaced
- Connect with: `socat - UNIX-CONNECT:/tmp/ser2tcp.sock`
- Not available on Windows

#### SSL configuration

For `ssl` protocol, reference a certificate bundle managed by the
Certificate Manager (see [below](#managing-certificates-via-web-ui)):

```json
{
    "address": "0.0.0.0",
    "port": 10003,
    "protocol": "ssl",
    "ssl": {
        "bundle": "main",
        "require_client_cert": false
    }
}
```

| Parameter | Description | Required |
|-----------|-------------|----------|
| `bundle` | Name of bundle in `{config_dir}/certs/<bundle>/` (must contain `cert.pem` + `key.pem`) | yes |
| `require_client_cert` | Enable mTLS — requires `ca.pem` in the bundle | no (default false) |

When `require_client_cert: true`, clients must provide a valid certificate signed by `ca.pem` from the bundle.

#### IP filtering

Restrict client connections by IP address using `allow` and/or `deny` lists:

```json
{
    "address": "0.0.0.0",
    "port": 10001,
    "protocol": "tcp",
    "allow": ["192.168.1.0/24", "10.0.0.5"],
    "deny": ["192.168.1.100"]
}
```

| Parameter | Description |
|-----------|-------------|
| `allow` | List of allowed IP addresses/networks (CIDR notation supported) |
| `deny` | List of denied IP addresses/networks (CIDR notation supported) |

Filter logic:
- **No config**: all IPs allowed
- **Only `deny`**: all IPs allowed except those in deny list
- **Only `allow`**: only IPs in allow list are allowed
- **Both**: deny takes precedence, then allow list is checked

Works on TCP, TELNET, SSL, WebSocket and HTTP servers. Not applicable to Unix socket (no IP addresses). Rejected connections are logged.

##### Managing certificates via web UI

The web UI has a **Certificates** tab (admin only) for managing SSL
certificate bundles without shell access. A bundle is a directory under
`{config_dir}/certs/{bundle_name}/` containing a fixed set of PEM files:

```
~/.config/ser2tcp/certs/
  main/
    cert.pem      # server certificate (or full chain)
    key.pem       # private key (file mode 0600, never served via API)
    ca.pem        # optional — CA cert(s) for mTLS client verification
```

- Bundle names: letters, digits, dot, underscore, dash (no leading dot)
- Directory mode `0700`, key file `0600`
- PEM format validated on upload (BEGIN/END markers must match the file type)
- `key.pem` is never downloadable via API — only filesystem access
- Bundles are referenced from SSL config via `"bundle": "<name>"` (both
  port SSL servers and HTTPS servers); a bundle cannot be deleted while
  any server still references it

**Web UI operations** (Certificates tab):
- Create / delete bundles
- Upload PEM file via file picker
- Paste PEM content into a textarea
- Drag-and-drop PEM file onto the file row
- Download public files (cert.pem / ca.pem)
- Delete individual files within a bundle

**Let's Encrypt** — point a bundle at LE's `live/` directory using
symlinks (no native LE handling in code):

```bash
mkdir -p ~/.config/ser2tcp/certs/elhome.sk
ln -s /etc/letsencrypt/live/elhome.sk/fullchain.pem \
    ~/.config/ser2tcp/certs/elhome.sk/cert.pem
ln -s /etc/letsencrypt/live/elhome.sk/privkey.pem \
    ~/.config/ser2tcp/certs/elhome.sk/key.pem
```

Note: `privkey.pem` in `/etc/letsencrypt/live/` is owned by `root:root`
with mode `0600`, so ser2tcp needs to either run as root or use an LE
`--deploy-hook` to copy files into the bundle dir with appropriate
ownership/permissions on renewal.

##### Creating self-signed certificates

Generate CA and server certificate for testing:

```bash
# Create CA key and certificate
openssl genrsa -out ca.key 2048
openssl req -new -x509 -days 365 -key ca.key -out ca.crt -subj "/CN=ser2tcp CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"

# Create server key and certificate signing request
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr -subj "/CN=localhost"

# Sign server certificate with CA
openssl x509 -req -days 365 -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt

# For certificate bound to specific domain/IP (SAN - Subject Alternative Name):
openssl req -new -key server.key -out server.csr -subj "/CN=myserver.example.com" -addext "subjectAltName=DNS:myserver.example.com,DNS:localhost,IP:192.168.1.100"
openssl x509 -req -days 365 -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -copy_extensions copy

# Clean up CSR
rm server.csr
```

For mTLS (mutual TLS with client certificates):

```bash
# Create client key and certificate
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr -subj "/CN=client"
openssl x509 -req -days 365 -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt
rm client.csr
```

Testing SSL connection:

```bash
# Without client certificate
openssl s_client -connect localhost:10003

# With client certificate (mTLS)
openssl s_client -connect localhost:10003 -cert client.crt -key client.key
```

### HTTP server and API

Optional HTTP server for monitoring and management:

```json
{
    "http": [
        {"name": "main", "address": "0.0.0.0", "port": 8080}
    ]
}
```

- `name`: optional label for the server (displayed in web UI Settings tab)
- HTTP servers can be added/removed/modified via web UI without restart

With authentication (configured at root level, shared across all HTTP servers):

```json
{
    "http": [
        {"address": "0.0.0.0", "port": 8080}
    ],
    "users": [
        {"login": "admin", "password": "sha256:...", "admin": true}
    ],
    "tokens": [
        {"token": "my-api-key", "name": "monitoring", "admin": false}
    ],
    "session_timeout": 3600
}
```

- `users`: login credentials with optional `admin` flag and per-user `session_timeout`
- `tokens`: permanent API tokens for automation (no expiration)
- `session_timeout`: global default session timeout in seconds
- First user added (via CLI or web UI) is automatically admin
- Cannot delete last admin (user or token) — at least one admin must exist

Generate password hash:

```bash
ser2tcp --hash-password mysecretpassword
```

HTTPS with SSL — uses the same bundle-based config as port SSL servers:

```json
{
    "http": [
        {"address": "0.0.0.0", "port": 8080},
        {"address": "0.0.0.0", "port": 8443, "ssl": {
            "bundle": "main", "require_client_cert": false
        }}
    ]
}
```

With IP filtering:

```json
{
    "http": [{
        "address": "0.0.0.0",
        "port": 8080,
        "allow": ["192.168.0.0/16"],
        "deny": ["192.168.1.100"]
    }]
}
```

#### API endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/login` | no | Authenticate, returns session token |
| POST | `/api/logout` | no | Invalidate session |
| GET | `/api/status` | yes | Runtime status (serial ports, servers, connections) |
| GET | `/api/detect` | yes | Available serial ports with USB/device attributes |
| GET | `/api/signals` | yes | Signal states for all ports |
| GET | `/api/settings` | yes | Get settings (http servers, session_timeout) |
| DELETE | `/api/ports/<p>/connections/<s>/<c>` | yes | Disconnect client |
| POST | `/api/ports` | admin | Add new port configuration |
| PUT | `/api/ports/<index>` | admin | Update port configuration |
| DELETE | `/api/ports/<index>` | admin | Delete port configuration |
| PUT | `/api/ports/<index>/signals` | admin | Set RTS/DTR signals |
| GET | `/api/users` | admin | List users |
| POST | `/api/users` | admin | Add user |
| PUT | `/api/users/<login>` | admin | Update user |
| DELETE | `/api/users/<login>` | admin | Delete user |
| GET | `/api/tokens` | admin | List API tokens |
| POST | `/api/tokens` | admin | Add API token |
| PUT | `/api/tokens/<token>` | admin | Update API token |
| DELETE | `/api/tokens/<token>` | admin | Delete API token |
| PUT | `/api/settings` | admin | Update session_timeout |
| POST | `/api/settings/http` | admin | Add HTTP server |
| PUT | `/api/settings/http/<index>` | admin | Update HTTP server |
| DELETE | `/api/settings/http/<index>` | admin | Delete HTTP server |
| GET | `/api/certs` | yes | List certificate bundles |
| POST | `/api/certs` | admin | Create empty bundle |
| GET | `/api/certs/<bundle>` | yes | Bundle detail (files, mtime, symlink target) |
| DELETE | `/api/certs/<bundle>` | admin | Delete bundle and all its files |
| POST | `/api/certs/<bundle>/files` | admin | Upload/paste a file into bundle |
| GET | `/api/certs/<bundle>/files/<filename>` | yes | Download public file (cert.pem / ca.pem) |
| DELETE | `/api/certs/<bundle>/files/<filename>` | admin | Delete single file from bundle |
| GET | `/xterm/<endpoint>` | no | WebSocket VT100 terminal |
| GET | `/raw/<endpoint>` | no | WebSocket raw terminal |

Auth levels: `no` = public, `yes` = any authenticated user, `admin` = admin user/token only.

Authentication: `Authorization: Bearer <token>` header or `?token=<token>` query parameter. Without users/tokens configured, all endpoints are accessible without authentication.

## Usage examples

```
ser2tcp -c ser2tcp.conf
```

Direct running from repository:

```
python run.py -c ser2tcp.conf
```

### Connecting using telnet

```
telnet localhost 10002
```

(to exit telnet press `CTRL + ]` and type `quit`)

## Installation as service

### Linux - systemd user service

1. Copy service file:
    ```
    cp ser2tcp.service ~/.config/systemd/user/
    ```
2. Configuration file will be created automatically at `~/.config/ser2tcp/config.json` on first run
3. Reload user systemd services:
    ```
    systemctl --user daemon-reload
    ```
4. Start and enable service:
    ```
    systemctl --user enable --now ser2tcp
    ```
5. To allow user services running after boot you need to enable linger (if this is not configured, then service will start after user login and stop after logout):
    ```
    sudo loginctl enable-linger $USER
    ```

### Linux - systemd system service

1. Create system user:
    ```
    sudo useradd -r -s /usr/sbin/nologin -G dialout ser2tcp
    ```
2. Copy service file:
    ```
    sudo cp ser2tcp-system.service /etc/systemd/system/ser2tcp.service
    ```
3. Create configuration file `/etc/ser2tcp.conf`
4. Reload systemd and start service:
    ```
    sudo systemctl daemon-reload
    sudo systemctl enable --now ser2tcp
    ```

### Useful commands

```bash
# Check status
systemctl --user status ser2tcp

# View logs
journalctl --user-unit ser2tcp -e

# Restart
systemctl --user restart ser2tcp

# Stop
systemctl --user stop ser2tcp
```

For system service, use `sudo systemctl` instead of `systemctl --user`.

## Requirements

- Python 3.8+
- pyserial 3.0+
- uhttp-server 2.3.2+ (for HTTP/API and WebSocket)

### Running on

- Linux
- macOS
- Windows

## Credits

(c) 2016-2026 by Pavel Revak

### Support

- Basic support is free over GitHub issues.
- Professional support is available over email: [Pavel Revak](mailto:pavel.revak@gmail.com?subject=[GitHub]%20ser2tcp).
