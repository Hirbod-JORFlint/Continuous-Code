from __future__ import annotations

import dataclasses
from typing import Dict, Optional, Protocol, Sequence, Type


@dataclasses.dataclass
class HarnessOutput:
    text: str
    exit_code: int
    raw: str = ""
    events: Sequence[Dict] = dataclasses.field(default_factory=tuple)


class HarnessDriver(Protocol):
    name: str

    def installed(self) -> bool: ...
    def start(self, cwd: Optional[str] = None) -> None: ...
    def run_prompt(
        self,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> HarnessOutput: ...
    def capture_output(self, process: object) -> HarnessOutput: ...
    def session_id(self, cwd: Optional[str] = None) -> Optional[str]: ...
    def list_agents(self, cwd: Optional[str] = None) -> Sequence[str]: ...


class DriverRegistry:
    _drivers: Dict[str, HarnessDriver] = {}

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
    from harness.opencode import OpencodeDriver

    DriverRegistry.register(OpencodeDriver())