"""Generate the harness-neutral global MCP config (\"user-level MCP CLI config\") from the
canonical harness registry.

Every harness generator (opencode/codex/cline) turns harness/mcp/registry.json into its
own per-harness config file. The runtime (opc/src/runtime/mcp_client.py and
generate_wrappers.py) additionally reads a harness-neutral global config at
get_global_mcp_config_path() -> ~/.opc/mcp_config.json (honors $OPC_CONFIG_DIR). Nothing
previously wrote that file; this generator closes the gap so the runtime's global-fallback
tools (standalone wrappers, config-less MCP client) resolve the same neutral servers.

The emitted shape is the standard MCP config: {\"mcpServers\": {...}} with ServerConfig
fields (type/command/args/env/url/headers/disabled). Disabled servers are dropped.

Usage:
    python -m scripts.harness.gen_opc [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[2]
HARNESS_DIR = REPO_ROOT / "harness"
REGISTRY_PATH = HARNESS_DIR / "mcp" / "registry.json"

_DEFAULT_CONFIG_DIR = Path.home() / ".opc"


def get_config_dir() -> Path:
    """Harness-neutral config root; honors OPC_CONFIG_DIR (see runtime.env.get_config_dir)."""
    return Path(os.environ.get("OPC_CONFIG_DIR", str(_DEFAULT_CONFIG_DIR)))


def _load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _translate_server(cfg: object) -> Optional[Dict[str, object]]:
    """Map a registry entry to the runtime ServerConfig JSON shape (net of `disabled`)."""
    if not isinstance(cfg, dict):
        return None
    if bool(cfg.get("disabled")):
        return None
    kind = cfg.get("type", "stdio")
    if kind in ("sse", "http"):
        url = cfg.get("url")
        if not isinstance(url, str) or not url:
            return None
        entry: Dict[str, object] = {"type": kind, "url": url}
        headers = cfg.get("headers")
        if isinstance(headers, dict) and headers:
            entry["headers"] = {str(k): str(v) for k, v in headers.items()}
        return entry
    command = cfg.get("command")
    if not isinstance(command, str) or not command:
        return None
    entry = {"type": "stdio", "command": command, "args": []}
    args = cfg.get("args")
    if isinstance(args, list):
        entry["args"] = [str(x) for x in args]
    env = cfg.get("env")
    if isinstance(env, dict):
        entry["env"] = {str(k): str(v) for k, v in env.items()}
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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.harness.gen_opc",
        description=(
            "Generate the harness-neutral global MCP config (~/.opc/mcp_config.json)"
            " from harness/mcp/registry.json (user-level MCP CLI config)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the config that would be written to the global path",
    )
    args = parser.parse_args(argv)

    mcp = _mcp_config()
    payload = {"mcpServers": mcp}
    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"--- target: {get_config_dir() / 'mcp_config.json'} ---")
        return 0

    dst = get_config_dir() / "mcp_config.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dst} with {len(mcp)} mcp servers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
