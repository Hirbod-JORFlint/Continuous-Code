#!/usr/bin/env python3
"""TLDR daemon client for harness lifecycle hooks (Python port).

Port of the daemon-query subset of .claude/hooks/src/daemon-client.ts that the
re-homed hooks need. Currently used by post-edit-notify.py:
  - query_daemon_sync({cmd:'notify', file}, project_dir) -> notify + reindex
  - track_hook_activity_sync('post-edit-notify', project_dir, success, metrics)

Connection info (socket path / TCP port) mirrors the Python daemon exactly —
reusing the platform logic also present in session-start-continuity.py.
Gracefully degrades to a no-op whenever the daemon is unreachable.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sys
from typing import Any


def _get_connection_info(project_dir: str) -> tuple[str, int | None]:
    """Return (address, port) — port is None for Unix sockets.

    On Windows, uses TCP on localhost with a deterministic port.
    On Unix (Linux/macOS), uses a Unix domain socket.
    Mirrors the daemon.py / TS getConnectionInfo() logic.
    """
    hash_val = hashlib.md5(project_dir.encode("utf-8")).hexdigest()[:8]
    if sys.platform == "win32":
        port = 49152 + (int(hash_val, 16) % 10000)
        return ("127.0.0.1", port)
    return (f"/tmp/tldr-{hash_val}.sock", None)


def query_daemon_sync(query: dict[str, Any], project_dir: str) -> dict[str, Any]:
    """Send a query to the TLDR daemon and return its JSON response.

    Returns a dict; on any failure returns {"status": "unavailable"} so the
    caller can degrade silently.
    """
    addr, port = _get_connection_info(project_dir)
    payload = (json.dumps(query) + "\n").encode("utf-8")
    sock: socket.socket | None = None
    try:
        if port is not None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect((addr, port))
        else:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect(addr)
        sock.sendall(payload)
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\n" in response:
                break
        try:
            return json.loads(response.decode("utf-8").strip())
        except (ValueError, UnicodeDecodeError):
            return {"status": "error", "error": "Invalid JSON response from daemon"}
    except (OSError, socket.timeout):
        return {"status": "unavailable", "error": "Daemon not running"}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def track_hook_activity_sync(
    hook_name: str,
    project_dir: str,
    success: bool = True,
    metrics: dict[str, int] | None = None,
) -> None:
    """Track hook activity via the daemon (fire-and-forget).

    Errors are silently ignored so stats tracking never fails the hook.
    """
    try:
        query_daemon_sync(
            {"cmd": "track", "hook": hook_name, "success": success, "metrics": metrics or {}},
            project_dir,
        )
    except Exception:
        pass
