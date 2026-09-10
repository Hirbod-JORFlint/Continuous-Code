from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Sequence

from .base import DriverRegistry, HarnessOutput


class OpencodeDriver:
    name = "opencode"

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable

    def _binary(self) -> str | None:
        if self._executable is not None:
            return self._executable
        return shutil.which("opencode") or shutil.which("op")

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
            raise RuntimeError("opencode executable not found on PATH")
        command = [binary, "run", "--format", "json"]
        if session_id:
            command.extend(["--session", session_id])
        if agent:
            command.extend(["--agent", agent])
        if model:
            command.extend(["--model", model])
        command.append("--auto")
        command.append(prompt)
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
            if isinstance(event, dict):
                events.append(event)
                if event.get("type") == "text" and event.get("text"):
                    text.append(str(event["text"]))
        joined = "\n".join(part for part in text if part).strip()
        return HarnessOutput(
            text=joined or process.stdout.strip(),
            exit_code=process.returncode,
            raw=process.stdout,
            events=tuple(events),
        )

    def session_id(self, cwd: str | None = None) -> str | None:
        binary = self._binary()
        if binary is None:
            return None
        process = subprocess.run(
            [binary, "session", "list", "--format", "json"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            data = json.loads(process.stdout or "[]")
        except json.JSONDecodeError:
            data = []
        if isinstance(data, dict):
            data = data.get("sessions") or data.get("data") or []
        if not isinstance(data, list) or not data:
            return None
        entries = [e for e in data if isinstance(e, dict)]
        if not entries:
            return None
        last: dict = max(entries, key=lambda e: e.get("updated") or 0)
        return str(last.get("id") or last.get("session_id") or "")

    def list_agents(self, cwd: str | None = None) -> Sequence[str]:
        binary = self._binary()
        if binary is None:
            return ()
        process = subprocess.run(
            [binary, "agent", "list"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        name_re = re.compile(r"^[\w.-]+\s*\((?:primary|subagent)\)?$")
        return tuple(
            line.strip()
            for line in process.stdout.splitlines()
            if name_re.match(line.strip())
        )


def register() -> None:
    DriverRegistry.register(OpencodeDriver())
