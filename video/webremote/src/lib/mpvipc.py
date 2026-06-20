"""Minimal JSON IPC client for the persistent mpv (`--input-ipc-server`).

One request/response per connection: send {"command": [...]} and read lines
until the reply with our request_id arrives (skipping async event lines). Small
and dependency-free; the socket is a local AF_UNIX path.
"""
import json
import os
import socket


class MpvError(Exception):
    pass


class MpvNotRunning(Exception):
    pass


def is_running(sock_path: str, timeout: float = 0.5) -> bool:
    if not os.path.exists(sock_path):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock_path)
        s.close()
        return True
    except OSError:
        return False


def command(sock_path: str, args: list, timeout: float = 5.0):
    """Send one mpv command and return its `data` (or None)."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock_path)
    except OSError as e:
        raise MpvNotRunning(str(e))

    rid = 1
    try:
        s.sendall((json.dumps({"command": args, "request_id": rid}) + "\n").encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                raise MpvError("connection closed before reply")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("request_id") == rid:
                    if msg.get("error") != "success":
                        raise MpvError(msg.get("error", "unknown error"))
                    return msg.get("data")
    finally:
        s.close()


def get_property(sock_path: str, name: str, default=None):
    try:
        return command(sock_path, ["get_property", name])
    except (MpvError, MpvNotRunning):
        return default


def set_property(sock_path: str, name: str, value):
    return command(sock_path, ["set_property", name, value])
