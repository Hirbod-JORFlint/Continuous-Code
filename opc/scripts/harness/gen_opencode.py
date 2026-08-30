from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

_SCHEMA_URL = "https://opencode.ai/config.json"
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[2]
HARNESS_DIR = REPO_ROOT / "harness"
RULES_DIR = HARNESS_DIR / "rules"
AGENTS_SRC_DIR = HARNESS_DIR / "agents"
SKILLS_SRC_DIR = HARNESS_DIR / "skills"
REGISTRY_PATH = HARNESS_DIR / "mcp" / "registry.json"
CONFIG_PATH = REPO_ROOT / "opencode.json"
OPCODE_DIR = REPO_ROOT / ".opencode"
AGENTS_DST_DIR = OPCODE_DIR / "agents"
SKILLS_DST_DIR = OPCODE_DIR / "skills"
SCHEMA_PATH = _HERE / "schemas" / "opencode-config.json"

_TOOL_PERMISSION_MAP = {
    "Read": "read",
    "Grep": "grep",
    "Glob": "glob",
    "Bash": "bash",
    "List": "list",
    "Edit": "edit",
    "Write": "edit",
    "ApplyPatch": "edit",
    "TodoWrite": "todowrite",
    "Task": "task",
    "Agent": "task",
    "WebFetch": "webfetch",
    "WebSearch": "websearch",
}


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
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


def _permission_from_tools(tools: object) -> Optional[Dict[str, str]]:
    if not isinstance(tools, list):
        return None
    allowed = sorted(
        {
            _TOOL_PERMISSION_MAP[name]
            for name in tools
            if isinstance(name, str) and name in _TOOL_PERMISSION_MAP
        }
    )
    if not allowed:
        return None
    return {**{"*": "deny"}, **{tool: "allow" for tool in allowed}}


def _load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _translate_server(cfg: object) -> Optional[Dict[str, object]]:
    if not isinstance(cfg, dict):
        return None
    enabled = not bool(cfg.get("disabled"))
    kind = cfg.get("type", "stdio")
    if kind == "http":
        url = cfg.get("url")
        if not isinstance(url, str) or not url:
            return None
        entry: Dict[str, object] = {"type": "remote", "url": url, "enabled": enabled}
        headers = cfg.get("headers")
        if isinstance(headers, dict):
            entry["headers"] = headers
        return entry
    command = cfg.get("command")
    args = cfg.get("args")
    if not isinstance(command, str) or not command:
        return None
    exec_args = [command] + (list(args) if isinstance(args, list) else [])
    entry = {
        "type": "local",
        "command": [str(x) for x in exec_args],
        "enabled": enabled,
    }
    env = cfg.get("env")
    if isinstance(env, dict):
        entry["environment"] = {str(k): str(v) for k, v in env.items()}
    return entry


def _mcp_config() -> Dict[str, object]:
    registry = _load_registry()
    servers = registry.get("mcpServers", {})
    out: Dict[str, object] = {}
    if not isinstance(servers, dict):
        return out
    for name, cfg in sorted(servers.items()):
        entry = _translate_server(cfg)
        if entry is not None:
            out[name] = entry
    return out


def _instructions() -> List[str]:
    return [f"harness/rules/{path.name}" for path in sorted(RULES_DIR.glob("*.md"))]


def _config() -> Dict[str, object]:
    cfg: Dict[str, object] = {"$schema": "https://opencode.ai/config.json"}
    instructions = _instructions()
    if instructions:
        cfg["instructions"] = instructions
    mcp = _mcp_config()
    if mcp:
        cfg["mcp"] = mcp
    return cfg


def _emit_agents() -> List[str]:
    AGENTS_DST_DIR.mkdir(parents=True, exist_ok=True)
    emitted: List[str] = []
    for path in sorted(AGENTS_SRC_DIR.glob("*.md")):
        data, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        name = data.get("name")
        description = data.get("description")
        if not isinstance(name, str) or not isinstance(description, str) or not body:
            continue
        frontmatter: Dict[str, object] = {"description": description, "mode": "subagent"}
        permission = _permission_from_tools(data.get("tools"))
        if permission is not None:
            frontmatter["permission"] = permission
        header = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
        (AGENTS_DST_DIR / f"{name}.md").write_text(f"---\n{header}\n---\n\n{body}", encoding="utf-8")
        emitted.append(name)
    return emitted


def _emit_skills() -> Tuple[List[str], List[str]]:
    SKILLS_DST_DIR.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    skipped: List[str] = []
    seen = set()
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
        shutil.copytree(skill_dir, SKILLS_DST_DIR / name, dirs_exist_ok=True)
        copied.append(name)
    return copied, skipped


def _validate(cfg: Dict[str, object]) -> int:
    if not SCHEMA_PATH.exists():
        print(f"schema not cached at {SCHEMA_PATH}; fetch from {_SCHEMA_URL} to enable validation")
        return 0
    try:
        import jsonschema
    except ImportError:
        print("jsonschema not installed; skipping config validation")
        return 0
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(cfg, schema)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "$"
        print(f"config validation failed at {location}: {exc.message}")
        return 1
    except jsonschema.SchemaError as exc:
        print(f"schema error: {exc.message}")
        return 1
    print("config validates against the opencode schema")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.harness.gen_opencode",
        description="Generate the project-scoped OpenCode integration config from the canonical harness tree",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the opencode.json that would be written")
    args = parser.parse_args(argv)
    cfg = _config()
    if args.dry_run:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        return 0
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    agents = _emit_agents()
    copied, skipped = _emit_skills()
    print(f"wrote {CONFIG_PATH.name} with {len(cfg.get('mcp', {}))} mcp servers and {len(cfg.get('instructions', []))} rules")
    print(f"emitted {len(agents)} agents to {AGENTS_DST_DIR.relative_to(REPO_ROOT)}")
    print(f"copied {len(copied)} skills to {SKILLS_DST_DIR.relative_to(REPO_ROOT)}; skipped {len(skipped)}: {', '.join(skipped)}")
    return _validate(cfg)


if __name__ == "__main__":
    sys.exit(main())