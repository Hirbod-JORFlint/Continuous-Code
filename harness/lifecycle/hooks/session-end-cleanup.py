#!/usr/bin/env python3
"""SessionEnd hook: Clean up session artifacts and trigger extraction.

Neutral Python port of session-end-cleanup.ts (Step 11, Phase B).
Re-homes the extractor lock and global braintrust script to .opc:

  ~/.claude/braintrust-extractor.lock      -> ~/.opc/braintrust-extractor.lock
  ~/.claude/scripts/braintrust_analyze.py  -> ~/.opc/scripts/braintrust_analyze.py
  scripts/braintrust_analyze.py (project)  -> opc/scripts/braintrust_analyze.py
"""

from __future__ import annotations

import ctypes
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from _payload import read_stdin, project_dir, session_id

LOCK_MAX_AGE_MS = 5 * 60 * 1000  # 5 minutes - consider stale after this


def _extractor_lock() -> Path:
    home = Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())
    return home / ".opc" / "braintrust-extractor.lock"


def _process_exists(pid: int) -> bool:
    if sys.platform == "win32":
        # SIGTERM still reports "process exists" even for our own process
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                if ok and exit_code.value == 259:  # STILL_ACTIVE
                    return True
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
            return False
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def is_extractor_running() -> bool:
    lock = _extractor_lock()
    if not lock.exists():
        return False
    try:
        lock_content = lock.read_text(encoding="utf-8").strip()
        pid_str, timestamp_str = lock_content.split(":", 1)
        pid = int(pid_str)
        timestamp = int(timestamp_str)
        if int(time.time() * 1000) - timestamp > LOCK_MAX_AGE_MS:
            lock.unlink(missing_ok=True)
            return False
        if _process_exists(pid):
            return True
        lock.unlink(missing_ok=True)
        return False
    except Exception:
        try:
            lock.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def create_extractor_lock(pid: int) -> None:
    try:
        lock = _extractor_lock()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(f"{pid}:{int(time.time() * 1000)}", encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    payload = read_stdin()
    project_root = Path(project_dir(payload, default=str(Path.cwd())))
    sid = session_id(payload)

    try:
        ledger_dir = project_root / "thoughts" / "ledgers"
        if ledger_dir.is_dir():
            ledger_files = [f for f in ledger_dir.iterdir()
                            if f.name.startswith("CONTINUITY-") and f.name.endswith(".md")]
            if ledger_files:
                most_recent = max(ledger_files, key=lambda f: f.stat().st_mtime)
                try:
                    content = most_recent.read_text(encoding="utf-8")
                    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    content = re.sub(r"Updated: .*", f"Updated: {timestamp}", content)
                    most_recent.write_text(content, encoding="utf-8")
                except OSError:
                    pass

        agent_cache_dir = project_root / ".opc" / "cache" / "agents"
        if agent_cache_dir.is_dir():
            now_ms = int(time.time() * 1000)
            max_age = 7 * 24 * 60 * 60 * 1000  # 7 days
            for agent_dir in agent_cache_dir.iterdir():
                if agent_dir.is_dir():
                    output_file = agent_dir / "latest-output.md"
                    if output_file.exists():
                        if now_ms - int(output_file.stat().st_mtime * 1000) > max_age:
                            try:
                                output_file.unlink()
                            except OSError:
                                pass

        if not os.environ.get("BRAINTRUST_API_KEY"):
            print(json.dumps({"result": "continue"}))
            return

        project_script = project_root / "opc" / "scripts" / "braintrust_analyze.py"
        global_script = Path(
            os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
        ) / ".opc" / "scripts" / "braintrust_analyze.py"
        is_global = not project_script.exists() and global_script.exists()
        script_path = project_script if project_script.exists() else global_script

        if script_path.exists():
            if is_extractor_running():
                print(json.dumps({"result": "continue"}))
                return

            args = (
                ["run", "--with", "braintrust", "--with", "openai", "--with", "aiohttp",
                 "python", str(script_path), "--learn", "--session-id", sid]
                if is_global
                else ["run", "python", str(script_path), "--learn", "--session-id", sid]
            )
            try:
                child = subprocess.Popen(
                    ["uv"] + args,
                    cwd=str(project_root),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=(
                        subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                        if sys.platform == "win32" else 0
                    ),
                )
                if child.pid:
                    create_extractor_lock(child.pid)
            except Exception:
                pass

        print(json.dumps({"result": "continue"}))
    except Exception:
        print(json.dumps({"result": "continue"}))


if __name__ == "__main__":
    main()
