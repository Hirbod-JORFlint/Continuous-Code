from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Protocol


@dataclasses.dataclass
class HarnessOutput:
    text: str
    exit_code: int
    raw: str = ""
    events: Sequence[dict] = dataclasses.field(default_factory=tuple)


class HarnessDriver(Protocol):
    name: str

    def installed(self) -> bool: ...
    def start(self, cwd: str | None = None) -> None: ...
    def run_prompt(
        self,
        prompt: str,
        *,
        cwd: str | None = None,
        agent: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> HarnessOutput: ...
    def capture_output(self, process: object) -> HarnessOutput: ...
    def session_id(self, cwd: str | None = None) -> str | None: ...
    def list_agents(self, cwd: str | None = None) -> Sequence[str]: ...


class DriverRegistry:
    _drivers: dict[str, HarnessDriver] = {}

    @classmethod
    def register(cls, driver: HarnessDriver) -> None:
        cls._drivers[driver.name] = driver

    @classmethod
    def get(cls, name: str) -> HarnessDriver:
        return cls._drivers[name]

    @classmethod
    def available(cls) -> Sequence[str]:
        return tuple(cls._drivers)

    @classmethod
    def auto(cls, preferred: Sequence[str] = ("opencode", "codex", "cline")) -> HarnessDriver:
        for name in preferred:
            driver = cls._drivers.get(name)
            if driver is not None and driver.installed():
                return driver
        for driver in cls._drivers.values():
            if driver.installed():
                return driver
        raise RuntimeError("no supported harness CLI found on PATH")


def register_drivers() -> None:
    from harness.cline import ClineDriver
    from harness.codex import CodexDriver
    from harness.opencode import OpencodeDriver

    DriverRegistry.register(OpencodeDriver())
    DriverRegistry.register(CodexDriver())
    DriverRegistry.register(ClineDriver())
