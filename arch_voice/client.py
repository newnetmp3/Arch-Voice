"""Client for the Arch Voice daemon; starts it on demand."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time

from .state import socket_path

SERVICE = "arch-voice.service"


class DaemonError(RuntimeError):
    pass


def _connect(timeout: float = 30.0) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(socket_path()))
    return s


def is_running() -> bool:
    try:
        _connect(1.0).close()
        return True
    except OSError:
        return False


def _systemd_unit_available() -> bool:
    if not shutil.which("systemctl"):
        return False
    res = subprocess.run(["systemctl", "--user", "cat", SERVICE],
                         capture_output=True, text=True)
    return res.returncode == 0


def start_daemon(wait: float = 15.0) -> None:
    if is_running():
        return
    if _systemd_unit_available():
        subprocess.run(["systemctl", "--user", "start", SERVICE], capture_output=True)
    else:
        subprocess.Popen([sys.executable, "-m", "arch_voice", "daemon"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if is_running():
            return
        time.sleep(0.2)
    raise DaemonError("the arch-voice daemon did not start; run 'arch-voice daemon' "
                      "in a terminal to see why")


def call(cmd: str, autostart: bool = True, **args):
    if autostart:
        start_daemon()
    try:
        with _connect() as s:
            s.sendall((json.dumps({"cmd": cmd, **args}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError as e:
        raise DaemonError(f"cannot talk to the arch-voice daemon: {e}") from e
    if not data:
        raise DaemonError("daemon closed the connection")
    reply = json.loads(data)
    if not reply.get("ok"):
        raise DaemonError(reply.get("error", "unknown error"))
    return reply.get("result")
