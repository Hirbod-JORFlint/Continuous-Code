"""Harness-neutral agent spawning and orchestration.

Spawns headless agents through the harness driver registry (opencode / codex /
cline). Application code must never construct harness command lines directly;
all headless orchestration goes through this module plus the registry
(see `harness/spec/HARNESS-ADAPTER.md` §9).

Every agent runs in its own daemon thread that calls the resolved driver's
`run_prompt()` (blocking subprocess under the hood). Orchestration metadata that
legacy spawned agents received via process environment is now inlined into the
prompt as a neutral context block, since drivers receive no `env` knob.

Environment:
    OPC_DRIVER       driver selector: "auto" | "opencode" | "codex" | "cline"
                     (default "auto" = first installed in that order)
    OPC_PROJECT_DIR  working project dir passed to the driver as `cwd`
    OPC_RUNTIME_DIR  scratch/output root (default <temp>/opc-agents)
    OPC_SESSION_ID   session identity for agent lineage (fallback SESSION_ID)
    OPC_HARNESS_DIR  neutral assets root (default repo `harness/`)
    DEPTH_LEVEL      current nesting depth (0 = orchestrator)
    AGENT_ID         parent agent identity (default "orchestrator")
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psutil
import yaml

# Bootstrap: make the harness package importable when this script is invoked
# directly (`uv run python scripts/core/spawn.py ...`) rather than through the
# packaged runtime.
_OPC_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_OPC_SRC) in sys.path:
    sys.path.remove(str(_OPC_SRC))
sys.path.insert(0, str(_OPC_SRC))

from harness.base import (  # noqa: E402
    DriverRegistry,
    HarnessDriver,
    HarnessOutput,
    register_drivers,
)
from runtime import coordination as _coordination  # noqa: E402

register_drivers()

logger = logging.getLogger(__name__)


# =============================================================================
# Concurrent Agent Limits
# =============================================================================

SOFT_AGENT_LIMIT = 50
HARD_AGENT_LIMIT = 100


class AgentLimitExceededError(Exception):
    """Raised when agent spawn would exceed hard limit."""

    def __init__(self, current: int, limit: int):
        self.current = current
        self.limit = limit
        super().__init__(f"Agent limit exceeded: {current} agents running, limit is {limit}")


class DepthLimitExceededError(Exception):
    """Raised when spawn depth exceeds maximum allowed."""


MAX_DEPTH = 3

# Orchestration state (thread-safe)
_lock = threading.Lock()
_agents: dict[str, _AgentEntry] = {}


@dataclass
class _AgentEntry:
    result: _AgentResult
    driver: str
    depth_level: int


class _AgentResult:
    """Holds a spawned agent's outcome for `wait_agent`."""

    def __init__(self) -> None:
        self._done = threading.Event()
        self.output: HarnessOutput | None = None
        self.error: BaseException | None = None

    def set(self, output: HarnessOutput | None = None, error: BaseException | None = None) -> None:
        self.output = output
        self.error = error
        self._done.set()

    def is_done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: float | None = None) -> HarnessOutput | None:
        self._done.wait(timeout)
        return self.output


@dataclass
class SpawnedAgent:
    """Record of a spawned agent."""

    pid: int
    agent_id: str
    output_file: str
    depth_level: int
    pattern: str | None = None
    premise: str | None = None
    driver: str | None = None


@dataclass
class AgentProfile:
    """Neutral agent profile from `harness/agents/{name}.md` frontmatter."""

    name: str
    description: str
    prompt: str
    tools: list[str] = field(default_factory=list)
    model: str | None = None


# =============================================================================
# Runtime paths
# =============================================================================

HARNESS_ROOT = Path(
    os.environ.get("OPC_HARNESS_DIR") or Path(__file__).resolve().parents[3] / "harness"
)
NEUTRAL_AGENTS_DIR = HARNESS_ROOT / "agents"


def _runtime_dir() -> Path:
    return Path(os.environ.get("OPC_RUNTIME_DIR") or (Path(tempfile.gettempdir()) / "opc-agents"))


def get_agent_count() -> int:
    """Number of currently running (not-yet-finished) agents."""
    with _lock:
        _prune_finished()
        return sum(1 for entry in _agents.values() if not entry.result.is_done())


def _prune_finished() -> None:
    for agent_id in [a for a, e in _agents.items() if e.result.is_done()]:
        _agents.pop(agent_id, None)


# =============================================================================
# Driver selection
# =============================================================================

AUTO_DRIVER_ORDER: tuple[str, ...] = ("opencode", "codex", "cline")


def select_driver() -> HarnessDriver:
    """Resolve the driver for this spawn.

    `OPC_DRIVER` selects a named driver; `auto` (default) picks the first
    installed from ``AUTO_DRIVER_ORDER``.
    """
    name = os.environ.get("OPC_DRIVER", "auto").strip().lower() or "auto"
    if name == "auto":
        return DriverRegistry.auto(AUTO_DRIVER_ORDER)
    try:
        return DriverRegistry.get(name)
    except KeyError as exc:
        raise RuntimeError(
            f"OPC_DRIVER '{name}' is not a registered driver; choose from "
            f"{', '.join(DriverRegistry.available()) or 'none'}"
        ) from exc


# =============================================================================
# Limits and routing (harness-neutral)
# =============================================================================


def _current_depth() -> int:
    try:
        return int(os.environ.get("DEPTH_LEVEL", "0"))
    except ValueError:
        return 0


def check_spawn_allowed() -> bool:
    """True if spawning is allowed based on depth."""
    return _current_depth() < MAX_DEPTH


def _check_depth_limit(depth_level: int) -> None:
    if depth_level >= MAX_DEPTH:
        raise DepthLimitExceededError(f"Max nesting depth ({MAX_DEPTH}) reached")


def _check_agent_limits() -> None:
    with _lock:
        _prune_finished()
        current = sum(1 for entry in _agents.values() if not entry.result.is_done())
    if current >= HARD_AGENT_LIMIT:
        raise AgentLimitExceededError(current=current, limit=HARD_AGENT_LIMIT)
    if current >= SOFT_AGENT_LIMIT:
        logger.warning(
            f"Soft agent limit reached: {current} agents running "
            f"(soft limit: {SOFT_AGENT_LIMIT}, hard limit: {HARD_AGENT_LIMIT})"
        )


def resources_available() -> bool:
    """True if system resources allow spawning (CPU < 80%, memory < 85%)."""
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem_percent = psutil.virtual_memory().percent
        return cpu_percent < 80 and mem_percent < 85
    except Exception:
        return True


# Agent routing table: keyword patterns -> agent name
AGENT_ROUTING = {
    # Orchestrate (check first - high priority)
    r"\b(coordinate|orchestrate|multi-agent)\b": "maestro",
    # Implement
    r"\b(implement|build|create)\b.*\b(feature|system|module)\b": "kraken",
    r"\b(fix|tweak|update|small)\b": "spark",
    # Document
    r"\b(document|handoff|summarize|ledger)\b": "scribe",
    # Debug
    r"\b(security|vulnerability|CVE|audit)\b": "aegis",
    r"\b(performance|memory|slow|race|profile)\b": "profiler",
    r"\b(debug|investigate|error|bug|crash)\b": "sleuth",
    # Validate
    r"\b(e2e|end-to-end|acceptance)\b": "atlas",
    r"\b(test|validate|verify|unit|integration)\b": "arbiter",
    # Research
    r"\b(research|best practices|NIA|learn)\b": "oracle",
    r"\b(analyze repo|external repo|github)\b": "pathfinder",
    r"\b(find|locate|where|codebase)\b": "scout",
    # Plan (check refactor first, then general plan)
    r"\b(refactor|migration|cleanup)\b": "phoenix",
    r"\b(plan|design)\b.*\b(feature|dashboard|new|api|endpoint)\b": "architect",
    # Review
    r"\b(review)\b.*\b(refactor|migration)\b": "warden",
    r"\b(review)\b": "sentinel",
    # Session/History
    r"\b(session|precedent|history|context)\b": "chronicler",
    # Deploy
    r"\b(deploy|release|version|changelog)\b": "herald",
}


def route_to_agent(prompt: str) -> str:
    """Route a prompt to the appropriate specialist using keyword matching."""
    prompt_lower = prompt.lower()
    for pattern, agent in AGENT_ROUTING.items():
        if re.search(pattern, prompt_lower, re.IGNORECASE):
            return agent
    return "spark"


# Lead agents that can spawn other agents
LEAD_AGENTS = {"kraken", "architect", "phoenix", "herald", "maestro"}

# Worker agents (leaf nodes, no spawning)
WORKER_AGENTS = {
    "spark",
    "scribe",
    "sleuth",
    "aegis",
    "profiler",
    "arbiter",
    "atlas",
    "oracle",
    "scout",
    "pathfinder",
    "sentinel",
    "warden",
    "chronicler",
}


def is_lead_agent(agent_name: str) -> bool:
    return agent_name.lower() in LEAD_AGENTS


def is_worker_agent(agent_name: str) -> bool:
    return agent_name.lower() in WORKER_AGENTS


# =============================================================================
# Neutral agent profiles
# =============================================================================


def load_neutral_profile(agent_name: str) -> AgentProfile | None:
    """Load `{name}`.md from the neutral agents directory (YAML frontmatter)."""
    path = NEUTRAL_AGENTS_DIR / f"{agent_name}.md"
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Failed to read agent profile {path}: {exc}")
        return None
    if not content.startswith("---"):
        return None
    try:
        parts = content.split("---", 2)
        front, body = parts[1], parts[2]
    except (ValueError, IndexError):
        return None
    try:
        data = yaml.safe_load(front) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"Failed to parse agent profile {path}: {exc}")
        return None
    tools = data.get("tools")
    return AgentProfile(
        name=str(data.get("name") or agent_name),
        description=str(data.get("description", "")),
        prompt=body.strip(),
        tools=[str(t) for t in tools] if isinstance(tools, list) else [],
        model=str(data["model"]) if data.get("model") else None,
    )


def _neutral_worker_summary(agent_name: str) -> dict | None:
    profile = load_neutral_profile(agent_name)
    if profile is None:
        logger.warning(f"Worker profile not found: {agent_name}")
        return None
    return {
        "name": profile.name,
        "description": profile.description,
        "tools": profile.tools,
        "model": profile.model,
    }


# =============================================================================
# Prompt composition
# =============================================================================


def _orchestration_context(
    agent_id: str,
    depth_level: int,
    pattern: str | None,
    driver: str,
) -> str:
    parent = os.environ.get("AGENT_ID") or "orchestrator"
    session = os.environ.get("OPC_SESSION_ID") or os.environ.get("SESSION_ID") or "default"
    pattern_type = pattern or "none"
    return (
        "Orchestration context "
        f"(AGENT_ID={agent_id}; DEPTH_LEVEL={depth_level}; PARENT_AGENT_ID={parent}; "
        f"SESSION_ID={session}; PATTERN_TYPE={pattern_type}; DRIVER={driver})"
    )


def _build_agent_prompt(
    prompt: str,
    perspective: str,
    profile: AgentProfile | None,
    agent_id: str,
    depth_level: int,
    pattern: str | None,
    driver: str,
) -> str:
    parts: list[str] = []
    if profile is not None and profile.prompt:
        parts.append(profile.prompt)
    elif perspective:
        parts.append(f"{perspective}: {prompt}")
    parts.append(_orchestration_context(agent_id, depth_level, pattern, driver))
    parts.append(f"Task: {prompt}")
    return "\n\n".join(p for p in parts if p)


def _build_lead_prompt(
    task: str,
    workers: list[str] | None,
    context: str | None,
    agent_id: str,
    depth_level: int,
    pattern: str | None,
    driver: str,
) -> str:
    parts: list[str] = []
    worker_summaries: list[dict] = []
    for name in workers or []:
        summary = _neutral_worker_summary(name)
        if summary is not None:
            worker_summaries.append(summary)
    if worker_summaries:
        lines = ["AVAILABLE WORKER AGENTS:"]
        for summary in worker_summaries:
            tools = ", ".join(summary["tools"]) if summary["tools"] else "default"
            model = summary["model"] or "default"
            lines.append(
                f"- {summary['name']}: {summary['description']} (tools: {tools}, model: {model})"
            )
        parts.append("\n".join(lines))
    parts.append(_orchestration_context(agent_id, depth_level, pattern, driver))
    parts.append(task)
    if context:
        parts.append(context)
    return "\n\n".join(parts)


# =============================================================================
# Worker execution
# =============================================================================


def _write_output(
    output_file: str,
    agent_id: str,
    subtype: str,
    output: HarnessOutput | None,
    error: str | None = None,
) -> None:
    record = {
        "agent_id": agent_id,
        "subtype": subtype,  # "success" | "error" (legacy monitor contract)
        "result": (output.text[:500] if output else (error or "")[:500]),
        "text": output.text if output else "",
        "exit_code": output.exit_code if output else None,
        "error": error,
        "driver": os.environ.get("OPC_DRIVER", "auto"),
        "completed_at": datetime.now(UTC).isoformat(),
    }
    try:
        Path(output_file).write_text(json.dumps(record, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning(f"Failed to write output for {agent_id}: {exc}")


def _run_agent(
    driver: HarnessDriver,
    prompt: str,
    agent_id: str,
    output_file: str,
    *,
    model: str | None,
    cwd: str | None,
    timeout: float | None,
    result: _AgentResult,
    depth_level: int,
) -> None:
    status = "failed"
    summary = None
    try:
        output = driver.run_prompt(
            prompt,
            cwd=cwd,
            model=model,
            session_id=agent_id,
            timeout=timeout,
        )
        status = "completed" if output.exit_code == 0 else "failed"
        summary = output.text[:500]
        _write_output(
            output_file,
            agent_id,
            "success" if status == "completed" else "error",
            output,
        )
        result.set(output)
    except Exception as exc:
        logger.warning(f"Agent {agent_id} failed during run: {exc}")
        _write_output(output_file, agent_id, "error", None, error=str(exc))
        result.set(error=exc)
        status = "failed"
    update_agent_status(agent_id, status, summary)
    logger.debug(f"Agent {agent_id} finished with status {status} (depth {depth_level})")


# =============================================================================
# Public spawn APIs
# =============================================================================


def spawn_agent(
    prompt: str,
    perspective: str = "",
    depth_level: int | None = None,
    pattern: str | None = None,
    agent_id: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> SpawnedAgent:
    """Spawn a headless agent via the harness driver registry.

    Args:
        prompt: The task prompt for the agent.
        perspective: Optional perspective prefix or neutral agent name
            (a matching ``harness/agents/{name}.md`` profile is inlined).
        depth_level: Override depth level (defaults to ``DEPTH_LEVEL`` env + 1).
        pattern: Coordination pattern (swarm, hierarchical, etc.).
        agent_id: Override agent ID (defaults to UUID).
        model: Model override (drivers map per harness/provider).
        timeout: Seconds to bound the driver run.

    Returns:
        SpawnedAgent with tracking/result metadata.

    Raises:
        DepthLimitExceededError: If spawn would exceed max depth.
        AgentLimitExceededError: If spawn would exceed hard agent limit.
        RuntimeError: If no harness driver is resolvable.
    """
    if depth_level is None:
        depth_level = _current_depth()
    _check_depth_limit(depth_level)
    _check_agent_limits()

    driver = select_driver()
    if agent_id is None:
        agent_id = str(uuid.uuid4())

    profile = load_neutral_profile(perspective) if perspective else None
    effective_model = model or (profile.model if profile else None)
    child_depth = depth_level + 1
    full_prompt = _build_agent_prompt(
        prompt, perspective, profile, agent_id, child_depth, pattern, driver.name
    )

    runtime_dir = _runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    output_file = str(runtime_dir / f"{agent_id}.json")

    session = os.environ.get("OPC_SESSION_ID") or os.environ.get("SESSION_ID") or "default"
    parent = os.environ.get("AGENT_ID") or "orchestrator"
    cwd = os.environ.get("OPC_PROJECT_DIR") or None
    result = _AgentResult()

    thread = threading.Thread(
        target=_run_agent,
        args=(
            driver,
            full_prompt,
            agent_id,
            output_file,
        ),
        kwargs={
            "model": effective_model,
            "cwd": cwd,
            "timeout": timeout,
            "result": result,
            "depth_level": child_depth,
        },
        name=f"agent-{agent_id[:8]}",
        daemon=True,
    )

    with _lock:
        thread.start()
        pid = thread.ident or 0
        _agents[agent_id] = _AgentEntry(result=result, driver=driver.name, depth_level=child_depth)

    register_agent_in_db(
        agent_id=agent_id,
        session_id=session,
        pid=pid,
        parent=parent,
        pattern=pattern,
        premise=perspective or None,
        depth_level=child_depth,
    )

    return SpawnedAgent(
        pid=pid,
        agent_id=agent_id,
        output_file=output_file,
        depth_level=child_depth,
        pattern=pattern,
        premise=perspective,
        driver=driver.name,
    )


def spawn_lead(
    task: str,
    workers: list[str] | None = None,
    context: str = "",
    model: str = "sonnet",
    pattern: str | None = None,
    timeout: float | None = None,
) -> SpawnedAgent:
    """Spawn a Lead agent (kraken, architect, ...) that may coordinate workers.

    Worker profiles from ``harness/agents/*.md`` are inlined into the prompt so
    the lead knows what specialists are available (harness-neutral; drivers have
    no ``--agents`` flag).

    Args:
        task: The task prompt for the Lead agent.
        workers: List of worker agent names to make available.
        context: Additional system context to append.
        model: Model to use. Defaults to "sonnet".
        pattern: Optional pattern type for routing/tracking.
        timeout: Seconds to bound the driver run.

    Returns:
        SpawnedAgent with process info and tracking IDs.
    """
    depth_level = _current_depth()
    _check_depth_limit(depth_level)
    _check_agent_limits()

    driver = select_driver()
    agent_id = str(uuid.uuid4())
    child_depth = depth_level + 1
    full_prompt = _build_lead_prompt(
        task, workers, context or None, agent_id, child_depth, pattern, driver.name
    )

    runtime_dir = _runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    output_file = str(runtime_dir / f"{agent_id}.json")

    session = os.environ.get("OPC_SESSION_ID") or os.environ.get("SESSION_ID") or "default"
    parent = os.environ.get("AGENT_ID") or "orchestrator"
    cwd = os.environ.get("OPC_PROJECT_DIR") or None
    result = _AgentResult()

    thread = threading.Thread(
        target=_run_agent,
        args=(
            driver,
            full_prompt,
            agent_id,
            output_file,
        ),
        kwargs={
            "model": model,
            "cwd": cwd,
            "timeout": timeout,
            "result": result,
            "depth_level": child_depth,
        },
        name=f"lead-{agent_id[:8]}",
        daemon=True,
    )

    with _lock:
        thread.start()
        pid = thread.ident or 0
        _agents[agent_id] = _AgentEntry(result=result, driver=driver.name, depth_level=child_depth)

    register_agent_in_db(
        agent_id=agent_id,
        session_id=session,
        pid=pid,
        parent=parent,
        pattern=pattern,
        premise="lead",
        depth_level=child_depth,
    )

    return SpawnedAgent(
        pid=pid,
        agent_id=agent_id,
        output_file=output_file,
        depth_level=child_depth,
        pattern=pattern,
        premise="lead",
        driver=driver.name,
    )


def wait_agent(agent: SpawnedAgent, timeout: float | None = None) -> HarnessOutput | None:
    """Block until the agent's thread finishes and return its output.

    Returns None on timeout. The spawned agent also writes its output JSON to
    `output_file` when it completes.
    """
    with _lock:
        entry = _agents.get(agent.agent_id)
    if entry is None:
        return None
    return entry.result.wait(timeout)


# =============================================================================
# CoordinationDB registration
# =============================================================================


def register_agent_in_db(
    agent_id: str,
    session_id: str,
    pid: int,
    parent: str,
    pattern: str | None = None,
    premise: str | None = None,
    depth_level: int = 1,
    swarm_id: str | None = None,
) -> None:
    """Register agent in the coordination database.

    Tries PostgreSQL first; falls back to an append-only JSONL registry under
    the OPC runtime dir (neither harness-specific).
    """
    effective_swarm_id = swarm_id if swarm_id is not None else session_id
    record = {
        "event": "register",
        "agent_id": agent_id,
        "session_id": session_id,
        "pid": pid,
        "parent_agent_id": parent,
        "pattern": pattern,
        "premise": premise,
        "depth_level": depth_level,
        "swarm_id": effective_swarm_id,
        "driver": os.environ.get("OPC_DRIVER", "auto"),
        "spawned_at": datetime.now(UTC).isoformat(),
        "status": "running",
    }

    # Try PostgreSQL first (CoordnDB via runtime.coordination); JSONL fallback below.
    if _coordination.register_agent(
        agent_id=agent_id,
        session_id=session_id,
        pid=pid,
        parent_agent_id=parent if parent != "orchestrator" else None,
        pattern=pattern,
        premise=premise,
        depth_level=depth_level,
        swarm_id=effective_swarm_id,
        driver=record["driver"],
    ):
        record["db"] = "postgres"
    else:
        logger.warning(
            f"CoordinationDB (PostgreSQL) unreachable; agent {agent_id} tracked via JSONL registry"
        )

    runtime_dir = _runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    tracking_file = runtime_dir / "registry.jsonl"
    with _lock:
        with open(tracking_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def update_agent_status(
    agent_id: str,
    status: str,
    result_summary: str | None = None,
) -> None:
    """Record an agent's completion status (fire-and-forget)."""
    record = {
        "event": "completion",
        "agent_id": agent_id,
        "status": status,
        "result_summary": result_summary,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if _coordination.update_agent_status(
        agent_id=agent_id, status=status, result_summary=result_summary
    ):
        record["db"] = "postgres"
    else:
        logger.debug(f"CoordinationDB unreachable for {agent_id}; JSONL status fallback")

    runtime_dir = _runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    tracking_file = runtime_dir / "registry.jsonl"
    with _lock:
        with open(tracking_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
