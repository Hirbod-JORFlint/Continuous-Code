#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""SessionStart Hook: TLDR Cache Warming (Async) (Python port).

Port of .claude/hooks/src/session-start-tldr-cache.ts:
On session startup, triggers cache warming in a detached background process.
Returns immediately to avoid blocking startup.

The daemon will warm the cache asynchronously. First TLDR command
will use the warmed cache or trigger on-demand indexing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _payload import project_dir, read_stdin  # noqa: E402


def get_cache_age(project_dir_str: str) -> int | None:
    meta_path = Path(project_dir_str) / ".opc" / "cache" / "tldr" / "meta.json"
    if not meta_path.exists():
        return None

    try:
        meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        cached_at = meta.get("cached_at", "")
        parsed = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
        now = datetime.now().astimezone(parsed.tzinfo) if parsed.tzinfo else datetime.now()
        return int(round((now - parsed).total_seconds() / (60 * 60)))
    except Exception:
        return None


def is_cache_stale(project_dir_str: str) -> bool:
    cache_dir = Path(project_dir_str) / ".opc" / "cache" / "tldr"
    if not cache_dir.exists():
        return True

    age = get_cache_age(project_dir_str)
    return age is None or age > 24  # Stale if >24h old or missing


def main() -> None:
    payload = read_stdin(timeout=2.0)

    # Only run on startup/resume (not clear/compact)
    if payload.get("source") not in ("startup", "resume"):
        print("{}")
        return

    project_dir_str = os.environ.get("OPC_PROJECT_DIR") or project_dir(payload)

    # Warm cache in detached background process if stale
    # Uses spawn with detached:true so process exits immediately
    if is_cache_stale(project_dir_str):
        # Cross-platform: use tldr daemon warm command
        try:
            subprocess.Popen(
                ["tldr", "daemon", "warm", "--project", project_dir_str],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=os.name == "nt",  # Shell needed on Windows
                close_fds=True,
            )
        except Exception:
            pass  # tldr binary unavailable - warm fails silently

    # Return immediately - don't block startup
    print("{}")


if __name__ == "__main__":
    main()
