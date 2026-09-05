from __future__ import annotations

import argparse
import json
import re
import shutil
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
CODEX_SKILLS_DIR = CODEX_DIR / "skills"
ROOT_AGENTS_MD_PATH = REPO_ROOT / "AGENTS.md"

# User-level (global) config root for Codex CLI: ~/.codex
USER_CONFIG_DIR = Path.home() / ".codex"
# Reusable user-level skills moved to the current recommended location;
# ~/.codex/skills is deprecated.
AGENTS_SKILLS_DIR = Path.home() / ".agents" / "skills"
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MODEL_ID = ""
MODEL_PROVIDER = ""

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
        entry: dict[str, object] = {"url": _codex_expand(url), "enabled": enabled}
        headers = cfg.get("headers")
        if isinstance(headers, dict):
            entry["http_headers"] = {
                str(k): _codex_expand(str(v)) for k, v in headers.items()
            }
        return entry
    command = cfg.get("command")
    if not isinstance(command, str) or not command:
        return None
    entry: dict[str, object] = {"command": _codex_expand(command), "enabled": enabled}
    args = cfg.get("args")
    if isinstance(args, list) and args:
        entry["args"] = [_codex_expand(str(x)) for x in args]
    env = cfg.get("env")
    if isinstance(env, dict):
        entry["env"] = {
            str(k): _codex_expand(str(v)) for k, v in env.items()
        }
    return entry


def _codex_expand(value: str) -> str:
    """Resolve $HOME so MCP servers spawn despite Codex spawning without a shell.

    Codex passes config values to the process env/argv literally (no shell
    expansion), so a bare ``$HOME`` would be sent as a literal string and the
    server would fail to start. Env references of the form ``${VAR}`` are left
    verbatim: Codex emits them into the spawned server's environment itself.
    """
    return value.replace("$HOME", str(Path.home()))


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
    """Emit hooks in Codex's nested MatcherGroup format.

    Codex expects each event to deserialize into ``Vec<MatcherGroup>`` where a
    group is ``{ matcher?: string, hooks: [ {type, command, timeout, async} ] }``.
    A flat ``When = [{command, matcher, type}]`` file deserializes as groups with
    ``hooks = []`` and every hook silently never runs, so handlers are grouped by
    matcher and their parameters moved onto the inner ``hooks`` entries.
    """
    hooks = _load_hooks()
    out: dict[str, object] = {}
    for event, handlers in sorted(hooks.items()):
        codex_event = _NEUTRAL_TO_CODEX_EVENT.get(event, event)
        values = handlers if isinstance(handlers, list) else [handlers]
        groups: dict[str, list[dict[str, object]]] = {}
        for handler in values:
            if not isinstance(handler, dict) or bool(handler.get("disabled")):
                continue
            command = handler.get("command")
            if not isinstance(command, str) or not command:
                continue
            matcher = handler.get("matcher")
            group_key = str(matcher) if isinstance(matcher, str) and matcher else ""
            hook_entry: dict[str, object] = {"type": "command", "command": command}
            for key in ("timeout", "async", "additionalContextLimit"):
                if key in handler and handler[key] is not None:
                    hook_entry[key] = handler[key]
            if "statusMessage" in handler and handler["statusMessage"] is not None:
                hook_entry["statusMessage"] = str(handler["statusMessage"])
            groups.setdefault(group_key, []).append(hook_entry)
        entries: list[dict[str, object]] = []
        for group_key in sorted(groups):
            group: dict[str, object] = {"hooks": groups[group_key]}
            if group_key:
                group["matcher"] = group_key
            entries.append(group)
        if entries:
            out[codex_event] = entries
    return out


def _config() -> dict[str, object]:
    cfg: dict[str, object] = {}
    if MODEL_ID:
        cfg["model"] = MODEL_ID
    if MODEL_PROVIDER:
        cfg["model_provider"] = MODEL_PROVIDER
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
        "## Loading",
        "",
        "Codex auto-discovers a repository-root `AGENTS.md`, so the root copy of this",
        "guide is always read. The `.codex/AGENTS.md` twin is additionally loaded when",
        "Codex runs with `CODEX_HOME=$(pwd)/.codex`.",
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
        "- Skills: `harness/skills/` (user-level install merges into `~/.agents/skills/`)",
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
    if isinstance(hooks, dict):
        for event, groups in hooks.items():
            if not isinstance(groups, list) or not groups:
                print(f"hooks.{event} is not a non-empty MatcherGroup array")
                return 1
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    print(f"hooks.{event} group is missing its nested `hooks` array: {group!r}")
                    return 1
    skills_count = (
        len(list(CODEX_SKILLS_DIR.glob("*/SKILL.md")))
        if CODEX_SKILLS_DIR.is_dir() else 0
    )
    rules_count = len(list(RULES_DIR.glob("*.md")))
    agents_count = len(list(AGENTS_SRC_DIR.glob("*.md")))
    print(
        f"config.toml parses; mcp_servers={len(servers)} "
        f"hooks_events={len(hooks)} rules={rules_count} "
        f"agents={agents_count} skills={skills_count}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.harness.gen_codex",
        description=(
            "Generate the Codex integration config from the canonical harness tree "
            "(project scope by default, or --user-level for the global ~/.codex root)"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the generated files without writing"
    )
    parser.add_argument(
        "--user-level",
        action="store_true",
        help="emit to the user-global root (~/.codex) instead of the project",
    )
    parser.add_argument(
        "--model",
        default="",
        help="pin a model id (top-level \"model\" in config.toml)",
    )
    parser.add_argument(
        "--model-provider",
        default="",
        help="set the model provider (top-level \"model_provider\" in config.toml)",
    )
    args = parser.parse_args(argv)
    if args.user_level:
        _repoint(USER_CONFIG_DIR)
    globals().update({"MODEL_ID": args.model, "MODEL_PROVIDER": args.model_provider})
    if MODEL_PROVIDER and not args.user_level:
        print(
            "warning: model_provider is ignored in a project-local config.toml; "
            "re-run with --user-level or set it in ~/.codex/config.toml"
        )
    cfg = _config()
    toml_text = _dump_toml(cfg)
    agenda = _agenda()
    if args.dry_run:
        print(toml_text)
        print("--- AGENTS.md ---")
        print(agenda)
        print("--- AGENTS.md (repo root) ---")
        print("same content as .codex/AGENTS.md (auto-discovered by Codex)")
        print("--- .codex/skills/ ---")
        copied, _ = _copy_skills_to(CODEX_SKILLS_DIR, dry_run=True)
        print(f"{len(copied)} skills (project scope)")
        if args.user_level:
            print("--- ~/.agents/skills/ ---")
            copied2, _ = _copy_user_skills(AGENTS_SKILLS_DIR, dry_run=True)
            print(f"{len(copied2)} skills (user scope)")
        return 0
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(toml_text, encoding="utf-8")
    AGENTS_MD_PATH.write_text(agenda, encoding="utf-8")
    print(f"wrote {CONFIG_PATH.relative_to(REPO_ROOT)}")
    print(f"wrote {AGENTS_MD_PATH.relative_to(REPO_ROOT)}")
    if not args.user_level:
        ROOT_AGENTS_MD_PATH.write_text(agenda, encoding="utf-8")
        print(f"wrote {ROOT_AGENTS_MD_PATH.relative_to(REPO_ROOT)}")
    if MODEL_ID or MODEL_PROVIDER:
        print(f"  model={MODEL_ID} model_provider={MODEL_PROVIDER}")
    if not args.user_level:
        copied, skipped = _copy_project_skills()
        if copied:
            print(f"copied {len(copied)} skills to {CODEX_SKILLS_DIR.relative_to(REPO_ROOT)}")
        if skipped:
            print(f"skipped skills: {', '.join(skipped)}")
    if args.user_level:
        copied, skipped = _copy_user_skills(AGENTS_SKILLS_DIR)
        if copied:
            print(f"copied {len(copied)} skills to {AGENTS_SKILLS_DIR}")
        if skipped:
            print(f"skipped skills: {', '.join(skipped)}")
    return _validate(toml_text)


def _repoint(root: Path) -> None:
    """Rebind the destination constants to a non-project config root."""
    globals().update(
        {
            "REPO_ROOT": root,
            "CODEX_DIR": root,
            "CONFIG_PATH": root / "config.toml",
            "AGENTS_MD_PATH": root / "AGENTS.md",
        }
    )


def _copy_user_skills(dst_dir: Path, dry_run: bool = False) -> tuple[list[str], list[str]]:
    """Copy canonical skills into a user-level (global) skills directory.

    Codex keeps reusable skills at the user level (~/.agents/skills, the
    current location; ~/.codex/skills is deprecated) rather than per project.
    Mirror the harness/skills layout, pruning stale dirs and skipping entries
    whose frontmatter name is missing or invalid.
    """
    return _copy_skills_to(dst_dir, dry_run)


def _copy_project_skills() -> tuple[list[str], list[str]]:
    """Copy canonical skills into the project-level .codex/skills/ directory."""
    return _copy_skills_to(CODEX_SKILLS_DIR, dry_run=False)


def _copy_skills_to(dst_dir: Path, dry_run: bool = False) -> tuple[list[str], list[str]]:
    copied: list[str] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for skill_dir in sorted(SKILLS_SRC_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        md = skill_dir / "SKILL.md"
        if not md.exists():
            continue
        data, _ = _parse_frontmatter(md.read_text(encoding="utf-8"))
        name = data.get("name")
        if not (isinstance(name, str) and _SKILL_NAME_RE.match(name)):
            skipped.append(f"{skill_dir.name} (invalid name {name!r})")
            continue
        if name in seen:
            skipped.append(f"{skill_dir.name} (duplicate name {name!r})")
            continue
        seen.add(name)
        if dry_run:
            copied.append(name)
            continue
        dst = dst_dir / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(
            skill_dir,
            dst,
            ignore=shutil.ignore_patterns("*.v6.md", "*.bak", "*.backup"),
        )
        copied.append(name)
    if not dry_run:
        dst_dir.mkdir(parents=True, exist_ok=True)
        for stale_dir in dst_dir.iterdir():
            if stale_dir.is_dir() and stale_dir.name not in seen:
                shutil.rmtree(stale_dir)
                print(f"pruned stale skill dir {dst_dir.name}/{stale_dir.name}")
    return copied, skipped


if __name__ == "__main__":
    sys.exit(main())
