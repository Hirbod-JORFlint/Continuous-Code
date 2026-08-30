from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence

from .base import DriverRegistry, HarnessOutput


class ClineDriver:
    """Harness driver for the Cline CLI (headless `cline --json`).

    Cline has no per-run `--agent` flag, no config-defined subagents to list
    (its "subagents" are built-in read-only research agents driven by the
    `use_subagents` tool), and no `resume`-style session continuation in the
    CLI. `run_prompt(agent=...)` and `run_prompt(session_id=...)` are accepted
    for interface parity and ignored.
    """

    name = "cline"
    experimental = True

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable

    def _binary(self) -> str | None:
        if self._executable is not None:
            return self._executable
        return shutil.which("cline")

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
            raise RuntimeError("cline executable not found on PATH")
        command = [binary, "--json", "-y"]
        if cwd:
            command.extend(["-c", cwd])
        if model:
            command.extend(["-m", model])
        if timeout:
            command.extend(["-t", str(int(timeout))])
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
            if not isinstance(event, dict):
                continue
            events.append(event)
            event_text = event.get("text")
            if event.get("type") in ("say", "agent_event") and isinstance(event_text, str):
                if not event.get("partial"):
                    text.append(event_text)
        joined = "\n".join(part for part in text if part).strip()
        return HarnessOutput(
            text=joined or process.stdout.strip(),
            exit_code=process.returncode,
            raw=process.stdout,
            events=tuple(events),
        )

    def session_id(self, cwd: str | None = None) -> str | None:
        # Cline keeps session data in `~/.cline/` SQLite directories rather
        # than rollout-style log files; no reliable filesystem session id
        # source exists for the CLI. Deferred to Step 13 live validation.
        return None

    def list_agents(self, cwd: str | None = None) -> Sequence[str]:
        return ()


def register() -> None:
    DriverRegistry.register(ClineDriver())
