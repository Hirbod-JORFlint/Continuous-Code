from __future__ import annotations

import json
import shutil
import subprocess
from typing import Dict, Optional, Sequence

from .base import DriverRegistry, HarnessOutput


class OpencodeDriver:
    name = "opencode"

    def __init__(self, executable: Optional[str] = None) -> None:
        self._executable = executable

    def _binary(self) -> Optional[str]:
        if self._executable is not None:
            return self._executable
        return shutil.which("opencode") or shutil.which("op")

    def installed(self) -> bool:
        return self._binary() is not None

    def start(self, cwd: Optional[str] = None) -> None:
        return None

    def run_prompt(
        self,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout: Optional[float] = None,
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
        events: list[Dict] = []
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

    def session_id(self, cwd: Optional[str] = None) -> Optional[str]:
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
        last = data[-1]
        if isinstance(last, dict):
            return str(last.get("id") or last.get("session_id") or "")
        return None

    def list_agents(self, cwd: Optional[str] = None) -> Sequence[str]:
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
        return tuple(line.strip() for line in process.stdout.splitlines() if line.strip())


def register() -> None:
    DriverRegistry.register(OpencodeDriver())