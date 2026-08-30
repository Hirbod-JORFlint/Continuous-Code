from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from .base import DriverRegistry, HarnessOutput

_SESSION_FILE_RE = re.compile(
    r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-([0-9a-f-]{36})\.jsonl"
)


class CodexDriver:
    """Harness driver for the Codex CLI (headless `codex exec`).

    Codex has no per-run `--agent` flag and no `agent list` subcommand;
    `run_prompt(agent=...)` is accepted for interface parity and ignored.
    """

    name = "codex"

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable

    def _binary(self) -> str | None:
        if self._executable is not None:
            return self._executable
        found = shutil.which("codex")
        if found is not None:
            return found
        home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
        for candidate in (
            Path(home) / "bin" / "codex",
            Path(home) / "bin" / "codex.exe",
            Path.home() / ".local" / "bin" / "codex",
        ):
            if candidate.is_file():
                return str(candidate)
        return None

    def installed(self) -> bool:
        return self._binary() is not None

    def start(self, cwd: str | None = None) -> None:
        return None

    def run_prompt(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        agent: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> HarnessOutput:
        binary = self._binary()
        if binary is None:
            raise RuntimeError("codex executable not found on PATH")
        if session_id:
            command = [binary, "exec", "resume"]
            if model:
                command.extend(["-m", model])
            command += ["--json", "--skip-git-repo-check", session_id, prompt]
        else:
            command = [binary, "exec", "--json"]
            if cwd:
                command.extend(["-C", cwd])
            if model:
                command.extend(["-m", model])
            command += ["--skip-git-repo-check", "-s", "workspace-write", "-a", "never", prompt]
        process = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return self.capture_output(process)

    def capture_output(self, process: object) -> HarnessOutput:
        events: list[dict] = []
        text: list[str] = []
        for line in process.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                text.append(line)
                continue
            if not isinstance(event, dict):
                continue
            events.append(event)
            event_type = event.get("type")
            payload = event.get("payload")
            message = payload.get("message") if isinstance(payload, dict) else None
            if event_type == "agent_message" and isinstance(message, str):
                text.append(message)
            elif event_type in ("log", "error") and isinstance(message, str):
                text.append(message)
        joined = "\n".join(part for part in text if part).strip()
        return HarnessOutput(
            text=joined or process.stdout.strip(),
            exit_code=process.returncode,
            raw=process.stdout,
            events=tuple(events),
        )

    def session_id(self, cwd: str | None = None) -> str | None:
        home = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
        sessions_dir = home / "sessions"
        if not sessions_dir.is_dir():
            return None
        session_files: list[Path] = []
        for path in sessions_dir.rglob("rollout-*.jsonl"):
            if _SESSION_FILE_RE.match(path.name):
                session_files.append(path)
        if not session_files:
            return None
        newest = max(session_files, key=lambda p: p.stat().st_mtime)
        match = _SESSION_FILE_RE.match(newest.name)
        if match:
            return match.group(1)
        return None

    def list_agents(self, cwd: str | None = None) -> Sequence[str]:
        return ()


def register() -> None:
    DriverRegistry.register(CodexDriver())
