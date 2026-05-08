"""ser2tcp-install — install/uninstall ser2tcp as a system service"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

DEFAULT_CONFIG_DIR = os.path.expanduser("~/.config/ser2tcp")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")

# --- Linux / systemd ---

SYSTEMD_DIR = os.path.expanduser("~/.config/systemd/user")
SYSTEMD_SERVICE = "ser2tcp.service"

SYSTEMD_TEMPLATE = """\
[Unit]
Description=ser2tcp serial port proxy
After=network.target

[Service]
ExecStart={exec_path} --config {config_path}
Restart=on-failure
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

# --- macOS / launchd ---

LAUNCHD_DIR = os.path.expanduser("~/Library/LaunchAgents")
LAUNCHD_PLIST = "com.ser2tcp.plist"

LAUNCHD_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ser2tcp</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exec_path}</string>
        <string>--config</string>
        <string>{config_path}</string>
    </array>
    <key>RunAtLoad</key>
    <false/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/ser2tcp.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/ser2tcp.err</string>
</dict>
</plist>
"""


def _detect_exec():
    path = shutil.which("ser2tcp")
    if path:
        return path
    # fallback: same venv as this install script
    venv_bin = os.path.join(os.path.dirname(sys.executable), "ser2tcp")
    if os.path.exists(venv_bin):
        return venv_bin
    return None


def _write_file(path, content, label):
    existing = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    if content == existing:
        print(f"  unchanged: {path}")
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  {'updated' if existing else 'created'}: {path} ({label})")
    return True


def _ensure_config(config_path):
    if os.path.exists(config_path):
        print(f"  exists:    {config_path}")
        return
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    config = {"ports": [], "http": [{"address": "127.0.0.1", "port": 20080}]}
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    os.chmod(config_path, 0o600)
    print(f"  created:   {config_path} (default config, edit before starting)")


# ---- Linux ----------------------------------------------------------------

def _install_linux(exec_path, config_path):
    service_path = os.path.join(SYSTEMD_DIR, SYSTEMD_SERVICE)
    content = SYSTEMD_TEMPLATE.format(
        exec_path=exec_path, config_path=config_path)
    changed = _write_file(service_path, content, "systemd unit")
    if changed:
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], check=True)
    _ensure_config(config_path)
    svc = SYSTEMD_SERVICE
    print(f"""
Commands:
  systemctl --user enable --now {svc}   # enable and start
  systemctl --user start {svc}          # start
  systemctl --user stop {svc}           # stop
  systemctl --user restart {svc}        # restart
  systemctl --user status {svc}         # status
  journalctl --user -u {svc} -f         # logs
""")


def _uninstall_linux(remove_config, config_path):
    service_path = os.path.join(SYSTEMD_DIR, SYSTEMD_SERVICE)
    if os.path.exists(service_path):
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", SYSTEMD_SERVICE],
            check=False)
        os.remove(service_path)
        print(f"  removed: {service_path}")
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"], check=True)
    else:
        print(f"  not found: {service_path}")
    if remove_config:
        _remove_config(config_path)


# ---- macOS ----------------------------------------------------------------

def _install_macos(exec_path, config_path):
    log_dir = os.path.expanduser("~/Library/Logs/ser2tcp")
    os.makedirs(log_dir, exist_ok=True)
    plist_path = os.path.join(LAUNCHD_DIR, LAUNCHD_PLIST)
    content = LAUNCHD_TEMPLATE.format(
        exec_path=exec_path, config_path=config_path, log_dir=log_dir)
    changed = _write_file(plist_path, content, "launchd plist")
    if changed:
        # unload old version first if loaded
        subprocess.run(
            ["launchctl", "unload", plist_path],
            check=False, capture_output=True)
        subprocess.run(["launchctl", "load", plist_path], check=True)
    _ensure_config(config_path)
    print(f"""
Commands:
  launchctl start com.ser2tcp     # start
  launchctl stop com.ser2tcp      # stop
  tail -f {log_dir}/ser2tcp.log   # logs
  tail -f {log_dir}/ser2tcp.err   # errors
""")


def _uninstall_macos(remove_config, config_path):
    plist_path = os.path.join(LAUNCHD_DIR, LAUNCHD_PLIST)
    if os.path.exists(plist_path):
        subprocess.run(
            ["launchctl", "unload", plist_path],
            check=False, capture_output=True)
        os.remove(plist_path)
        print(f"  removed: {plist_path}")
    else:
        print(f"  not found: {plist_path}")
    if remove_config:
        _remove_config(config_path)


# ---- shared ---------------------------------------------------------------

def _remove_config(config_path):
    if os.path.exists(config_path):
        os.remove(config_path)
        print(f"  removed: {config_path}")
        config_dir = os.path.dirname(config_path)
        if not os.listdir(config_dir):
            os.rmdir(config_dir)
            print(f"  removed: {config_dir}/")
    else:
        print(f"  not found: {config_path}")


def _reset_users(config_path):
    if not os.path.exists(config_path):
        print(f"Config not found: {config_path}")
        return
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    removed_users = len(config.pop("users", []))
    removed_tokens = len(config.pop("tokens", []))
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    print(
        f"  removed {removed_users} user(s) and {removed_tokens} token(s) "
        f"from {config_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Install or uninstall ser2tcp as a system service")
    parser.add_argument(
        "--install", action="store_true",
        help="Install and enable service (default action if no flag given)")
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Stop, disable and remove service unit file")
    parser.add_argument(
        "--uninstall-all", action="store_true",
        help="Uninstall service and remove config files")
    parser.add_argument(
        "--reset-users", action="store_true",
        help="Remove all users and API tokens from config (reset auth)")
    parser.add_argument(
        "--exec", metavar="PATH",
        help="Path to ser2tcp executable (default: auto-detect)")
    parser.add_argument(
        "--config", metavar="PATH", default=DEFAULT_CONFIG_PATH,
        help=f"Config file path (default: {DEFAULT_CONFIG_PATH})")
    args = parser.parse_args()

    system = platform.system()
    if system not in ("Linux", "Darwin"):
        print(
            f"Unsupported platform: {system}\n"
            "Manual installation required.")
        sys.exit(1)

    if system == "Linux" and os.geteuid() == 0:
        print(
            "Running as root is not supported.\n"
            "For a system-wide installation, create the service manually:\n"
            "  /etc/systemd/system/ser2tcp.service")
        sys.exit(1)

    # default action: --install
    action_given = args.install or args.uninstall or args.uninstall_all \
        or args.reset_users
    if not action_given:
        args.install = True

    if args.reset_users:
        print("Resetting users:")
        _reset_users(args.config)
        return

    if args.uninstall or args.uninstall_all:
        remove_config = args.uninstall_all
        print("Uninstalling ser2tcp service:")
        if system == "Linux":
            _uninstall_linux(remove_config, args.config)
        else:
            _uninstall_macos(remove_config, args.config)
        return

    # install
    exec_path = args.exec or _detect_exec()
    if not exec_path:
        print(
            "Cannot find ser2tcp executable.\n"
            "Use --exec /path/to/ser2tcp to specify it explicitly.")
        sys.exit(1)

    print(f"Installing ser2tcp service ({system}):")
    print(f"  executable: {exec_path}")
    print(f"  config:     {args.config}")
    if system == "Linux":
        _install_linux(exec_path, args.config)
    else:
        _install_macos(exec_path, args.config)
