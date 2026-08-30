from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

try:
    import tomllib as _toml
except ImportError:  # Python < 3.11
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ImportError:
        _toml = None

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[2]
HARNESS_DIR = REPO_ROOT / "harness"
RULES_DIR = HARNESS_DIR / "rules"
AGENTS_SRC_DIR = HARNESS_DIR / "agents"
SKILLS_SRC_DIR = HARNESS_DIR / "skills"
LIFECYCLE_DIR = HARNESS_DIR / "lifecycle"
HOOKS_MANIFEST = LIFECYCLE_DIR / "hooks.toml"
REGISTRY_PATH = HARNESS_DIR / "mcp" / "registry.json"
CODEX_DIR = REPO_ROOT / ".codex"
CONFIG_PATH = CODEX_DIR / "config.toml"
AGENTS_MD_PATH = CODEX_DIR / "AGENTS.md"

_NEUTRAL_TO_CODEX_EVENT = {
    "session_start": "SessionStart",
    "prompt_submit": "UserPromptSubmit",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "pre_compact": "PreCompact",
    "post_compact": "PostCompact",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "session_stop": "SessionEnd",
    "stop": "Stop",
}


def _toml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(
            f"{k} = {_toml_scalar(v)}" for k, v in sorted(value.items())
        )
        return "{" + inner + "}"
    return json.dumps(str(value), ensure_ascii=False)


def _dump_toml(data: dict[str, object]) -> str:
    lines: list[str] = []

    def walk(path: list[str], mapping: dict[str, object]) -> None:
        has_scalars = any(not isinstance(v, dict) for v in mapping.values())
        if path and has_scalars:
            lines.append(f"[{'.'.join(path)}]")
        items = sorted(mapping.items(), key=lambda item: (isinstance(item[1], dict), item[0]))
        for key, value in items:
            if isinstance(value, dict):
                walk(path + [key], value)
            else:
                lines.append(f"{key} = {_toml_scalar(value)}")
        if path and has_scalars:
            lines.append("")

    walk([], data)
    return "\n".join(lines).rstrip() + "\n"


def _load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _translate_server(cfg: object) -> dict[str, object] | None:
    if not isinstance(cfg, dict):
        return None
    enabled = not bool(cfg.get("disabled"))
    kind = cfg.get("type", "stdio")
    if kind == "http":
        url = cfg.get("url")
        if not isinstance(url, str) or not url:
            return None
        entry: dict[str, object] = {"url": url, "enabled": enabled}
        headers = cfg.get("headers")
        if isinstance(headers, dict):
            entry["http_headers"] = {str(k): str(v) for k, v in headers.items()}
        return entry
    command = cfg.get("command")
    if not isinstance(command, str) or not command:
        return None
    entry: dict[str, object] = {"command": command, "enabled": enabled}
    args = cfg.get("args")
    if isinstance(args, list) and args:
        entry["args"] = [str(x) for x in args]
    env = cfg.get("env")
    if isinstance(env, dict):
        entry["env"] = {str(k): str(v) for k, v in env.items()}
    return entry


def _load_hooks() -> dict[str, object]:
    if _toml is None:
        return {}
    if not HOOKS_MANIFEST.exists():
        return {}
    try:
        data = _toml.loads(HOOKS_MANIFEST.read_text(encoding="utf-8-sig"))
    except (_toml.TOMLDecodeError, OSError):
        return {}
    hooks = data.get("hooks")
    return hooks if isinstance(hooks, dict) else {}


def _emit_hooks() -> dict[str, object]:
    hooks = _load_hooks()
    out: dict[str, object] = {}
    for event, handlers in sorted(hooks.items()):
        codex_event = _NEUTRAL_TO_CODEX_EVENT.get(event, event)
        entries = []
        values = handlers if isinstance(handlers, list) else [handlers]
        for handler in values:
            if not isinstance(handler, dict):
                continue
            if bool(handler.get("disabled")):
                continue
            entry: dict[str, object] = {
                "type": "command",
                "command": str(handler.get("command", "")),
            }
            if not entry["command"]:
                continue
            for key in ("matcher", "statusMessage", "timeout", "async", "additionalContextLimit"):
                if key in handler and handler[key] is not None:
                    if key == "statusMessage":
                        entry["statusMessage"] = str(handler[key])
                    else:
                        entry[key] = handler[key]
            entries.append(entry)
        if entries:
            out[codex_event] = entries
    return out


def _config() -> dict[str, object]:
    cfg: dict[str, object] = {}
    servers = {}
    registry = _load_registry()
    raw = registry.get("mcpServers", {})
    if isinstance(raw, dict):
        for name, server in sorted(raw.items()):
            entry = _translate_server(server)
            if entry is not None:
                servers[name] = entry
    if servers:
        cfg["mcp_servers"] = servers
    hooks = _emit_hooks()
    if hooks:
        cfg["hooks"] = hooks
    return cfg


def _agenda() -> str:
    lines = [
        "# Continuous Code - Codex Agent Guide",
        "",
        "This file is generated by `opc/scripts/harness/gen_codex.py` from the canonical",
        "tree under `harness/`. Edit the canonical sources, not this file.",
        "",
        "## Rules",
        "",
        "Global rules injected into every harness. Canonical copies:",
        "",
    ]
    for path in sorted(RULES_DIR.glob("*.md")):
        title = _rule_title(path)
        lines.append(f"- `harness/rules/{path.name}` - {title}")
    lines += [
        "",
        "## Agents",
        "",
        "Subagent definitions live in `harness/agents/`. Register them for a project layer",
        "with `agents.<name>.config_file` (user-level install merges into `~/.codex/agents/`):",
        "",
    ]
    for path in sorted(AGENTS_SRC_DIR.glob("*.md")):
        data, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        name = data.get("name")
        description = data.get("description")
        if isinstance(name, str) and isinstance(description, str):
            lines.append(f"- `harness/agents/{path.name}` - {name}: {description}")
    lines += [
        "",
        "## Sources",
        "",
        "- Rules: `harness/rules/`",
        "- Agents: `harness/agents/`",
        "- Skills: `harness/skills/` (user-level install merges into `~/.codex/skills/`)",
        "- MCP: `harness/mcp/registry.json` "
        "(emitted into `.codex/config.toml` as `[mcp_servers.*]`)",
        "",
    ]
    return "\n".join(lines)


def _rule_title(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, parts[2].lstrip("\n")


def _validate(toml_text: str) -> int:
    if _toml is None:
        print("no tomllib/tomli available; skipping TOML parse validation")
        return 0
    try:
        parsed = _toml.loads(toml_text)
    except _toml.TOMLDecodeError as exc:
        print(f"generated config.toml is not valid TOML: {exc}")
        return 1
    servers = parsed.get("mcp_servers", {})
    if isinstance(servers, dict):
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            if not (entry.get("command") or entry.get("url")):
                print(f"mcp_servers.{name} is malformed: {entry!r}")
                return 1
    hooks = parsed.get("hooks", {})
    print(
        f"config.toml parses; mcp_servers={len(servers)} hooks_events={len(hooks)} "
        f"rules={len(list(RULES_DIR.glob('*.md')))} agents={len(list(AGENTS_SRC_DIR.glob('*.md')))}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.harness.gen_codex",
        description=(
            "Generate the project-scoped Codex integration config from the canonical harness tree"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the generated files without writing"
    )
    args = parser.parse_args(argv)
    cfg = _config()
    toml_text = _dump_toml(cfg)
    agenda = _agenda()
    if args.dry_run:
        print(toml_text)
        print("--- AGENTS.md ---")
        print(agenda)
        return 0
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(toml_text, encoding="utf-8")
    AGENTS_MD_PATH.write_text(agenda, encoding="utf-8")
    print(f"wrote {CONFIG_PATH.relative_to(REPO_ROOT)}")
    print(f"wrote {AGENTS_MD_PATH.relative_to(REPO_ROOT)}")
    return _validate(toml_text)


if __name__ == "__main__":
    sys.exit(main())
