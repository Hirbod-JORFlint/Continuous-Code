from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[2]
HARNESS_DIR = REPO_ROOT / "harness"
RULES_DIR = HARNESS_DIR / "rules"
AGENTS_SRC_DIR = HARNESS_DIR / "agents"
REGISTRY_PATH = HARNESS_DIR / "mcp" / "registry.json"
CLINERULES_DIR = REPO_ROOT / ".clinerules"
MCP_JSON_PATH = REPO_ROOT / "cline_mcp_settings.json"


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
    disabled = bool(cfg.get("disabled"))
    kind = cfg.get("type", "stdio")
    if kind == "http":
        url = cfg.get("url")
        if not isinstance(url, str) or not url:
            return None
        entry: dict[str, object] = {
            "type": "streamableHttp",
            "url": url,
            "disabled": disabled,
            "autoApprove": [],
        }
        headers = cfg.get("headers")
        if isinstance(headers, dict):
            entry["headers"] = {str(k): str(v) for k, v in headers.items()}
        return entry
    command = cfg.get("command")
    if not isinstance(command, str) or not command:
        return None
    entry: dict[str, object] = {
        "command": command,
        "disabled": disabled,
        "autoApprove": [],
    }
    args = cfg.get("args")
    if isinstance(args, list) and args:
        entry["args"] = [str(x) for x in args]
    env = cfg.get("env")
    if isinstance(env, dict):
        entry["env"] = {str(k): str(v) for k, v in env.items()}
    return entry


def _mcp_config() -> dict[str, object]:
    out: dict[str, object] = {}
    servers: dict[str, object] = {}
    registry = _load_registry()
    raw = registry.get("mcpServers", {})
    if isinstance(raw, dict):
        for name, server in sorted(raw.items()):
            entry = _translate_server(server)
            if entry is not None:
                servers[name] = entry
    if servers:
        out["mcpServers"] = servers
    return out


def _emit_rules() -> list[str]:
    CLINERULES_DIR.mkdir(parents=True, exist_ok=True)
    emitted: list[str] = []
    for path in sorted(RULES_DIR.glob("*.md")):
        name = path.name
        (CLINERULES_DIR / name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        emitted.append(name)
    return emitted


def _validate(mcp_text: str) -> int:
    try:
        parsed = json.loads(mcp_text)
    except json.JSONDecodeError as exc:
        print(f"generated cline_mcp_settings.json is not valid JSON: {exc}")
        return 1
    servers = parsed.get("mcpServers", {})
    if isinstance(servers, dict):
        for name, entry in servers.items():
            if not isinstance(entry, dict):
                continue
            if not (entry.get("command") or entry.get("url")):
                print(f"mcpServers.{name} is malformed: {entry!r}")
                return 1
    print(
        f"cline_mcp_settings.json parses; mcpServers={len(servers)} "
        f"rules={len(list(CLINERULES_DIR.glob('*.md'))) if CLINERULES_DIR.is_dir() else 0} "
        f"agents={len(list(AGENTS_SRC_DIR.glob('*.md')))}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.harness.gen_cline",
        description=(
            "Generate the project-scoped Cline integration config from the canonical harness tree"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the generated files without writing"
    )
    args = parser.parse_args(argv)
    cfg = _mcp_config()
    mcp_text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    if args.dry_run:
        print(mcp_text)
        print("--- .clinerules/ ---")
        for path in sorted(RULES_DIR.glob("*.md")):
            print(path.name)
        return 0
    rules = _emit_rules()
    MCP_JSON_PATH.write_text(mcp_text, encoding="utf-8")
    print(f"wrote {MCP_JSON_PATH.relative_to(REPO_ROOT)}")
    print(f"wrote {len(rules)} rules to {CLINERULES_DIR.relative_to(REPO_ROOT)}")
    return _validate(mcp_text)


if __name__ == "__main__":
    sys.exit(main())
