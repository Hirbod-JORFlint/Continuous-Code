#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""TLDR Read Enforcer Hook - BLOCKING VERSION (DAEMON) (Python port).

Port of hooks/src/tldr-read-enforcer.ts:
  Intercepts Read tool calls for code files and BLOCKS with TLDR context.
  Instead of reading 1000+ line files, returns structured L1 AST context.

Uses TLDR daemon for fast cached responses (50ms vs 500ms CLI).

Result: 95% token savings (50-500 tokens vs 3000-20000 raw)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import daemon_client  # noqa: E402
from _payload import read_stdin, session_id, tool_input, tool_name  # noqa: E402

CONTEXT_DIR = "/tmp/claude-search-context"
CONTEXT_MAX_AGE_MS = 30000  # 30 seconds - context expires after this


def get_search_context(session_id_value: str) -> dict[str, Any] | None:
    try:
        context_path = f"{CONTEXT_DIR}/{session_id_value}.json"
        if not Path(context_path).exists():
            return None

        context = json.loads(Path(context_path).read_text(encoding="utf-8"))

        # Check if context is stale
        if time.time() * 1000 - context.get("timestamp", 0) > CONTEXT_MAX_AGE_MS:
            return None

        return context
    except Exception:
        return None


# Code file extensions that should use TLDR
CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}

# Files that should always be allowed (bypass TLDR)
ALLOWED_PATTERNS = [
    re.compile(r"\.json$"), re.compile(r"\.yaml$"), re.compile(r"\.yml$"),
    re.compile(r"\.toml$"), re.compile(r"\.md$"), re.compile(r"\.txt$"),
    re.compile(r"\.env"), re.compile(r"\.gitignore$"), re.compile(r"Makefile$"),
    re.compile(r"Dockerfile$"),
    re.compile(r"requirements\.txt$"), re.compile(r"package\.json$"),
    re.compile(r"tsconfig\.json$"), re.compile(r"pyproject\.toml$"),
    # Allow test files (need full context for implementation)
    re.compile(r"test_.*\.py$"), re.compile(r".*_test\.py$"),
    re.compile(r".*\.test\.(ts|js)$"), re.compile(r".*\.spec\.(ts|js)$"),
    # Allow hooks/skills (we edit these)
    re.compile(r"\.opc/hooks/"), re.compile(r"\.opc/skills/"),
    re.compile(r"init-db\.sql$"), re.compile(r"migrations/"),
]

ALLOWED_DIRS = ["/tmp/", "node_modules/", ".venv/", "__pycache__/"]


def is_code_file(file_path: str) -> bool:
    return os.path.splitext(file_path)[1] in CODE_EXTENSIONS


def is_allowed_file(file_path: str) -> bool:
    for pattern in ALLOWED_PATTERNS:
        if pattern.search(file_path):
            return True
    for d in ALLOWED_DIRS:
        if d in file_path:
            return True
    return False


def detect_language(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1]
    lang_map = {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript",
        ".go": "go", ".rs": "rust",
    }
    return lang_map.get(ext, "python")


def choose_tldr_mode(target: str | None, layers: list[str], context_source: str) -> dict[str, Any]:
    # Only trust targets from search-router (it actually searched for them)
    # contextSource format: "function: func_name" or "class: ClassName"
    from_search_router = context_source.startswith("function:") or context_source.startswith("class:")
    if target and from_search_router:
        return {"mode": "context", "reason": f"search: {target}"}

    # If advanced layers explicitly requested (cfg, dfg, pdg), use extract
    if any(l in ("cfg", "dfg", "pdg") for l in (layers or [])):
        return {"mode": "extract", "reason": "flow analysis"}

    # Default: structure for navigation (99% savings)
    return {"mode": "structure", "reason": "navigation"}


def get_tldr_context(
    file_path: str,
    language: str,
    layers: list[str] | None = None,
    target: str | None = None,
    session_id_value: str | None = None,
    context_source: str = "default",
) -> str | None:
    if layers is None:
        layers = ["ast", "call_graph"]
    project_dir = os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
    file_name = os.path.basename(file_path)
    results: list[str] = []

    # Choose optimal TLDR mode
    mode_info = choose_tldr_mode(target, layers, context_source)
    mode = mode_info["mode"]
    reason = mode_info["reason"]

    try:
        # Header with mode indicator
        results.append(f"# {file_name}")
        results.append(f"Language: {language}")
        results.append(f"Mode: {mode} ({reason})")
        results.append("")

        # MODE: context - focused on target function (87% savings)
        if mode == "context" and target:
            context_resp = daemon_client.query_daemon_sync(
                {"cmd": "context", "entry": target, "language": language, "depth": 2},
                project_dir,
            )
            if context_resp.get("status") == "ok" and context_resp.get("result"):
                results.append("## Focused Context")
                results.append(
                    context_resp["result"] if isinstance(context_resp["result"], str)
                    else json.dumps(context_resp["result"], indent=2)
                )
                results.append("")
                results.append("---")
                results.append("To see more: Read with offset/limit, or ask about specific functions")
                return "\n".join(results)
            # Fall through to extract if context fails

        # MODE: structure - just names (99% savings)
        # Note: use 'extract' for single files (structure is for directories)
        if mode == "structure":
            extract_resp = daemon_client.query_daemon_sync(
                {"cmd": "extract", "file": file_path, "session": session_id_value or None},
                project_dir,
            )
            if extract_resp.get("status") == "ok" and extract_resp.get("result"):
                results.append("## Structure (names only)")
                info = extract_resp["result"]
                functions = info.get("functions")
                if functions and len(functions) > 0:
                    results.append("### Functions")
                    for fn in functions[:30]:
                        fn_params = fn.get("params")
                        if fn_params:
                            params_str = ", ".join(fn_params[:3])
                            if len(fn_params) > 3:
                                params_str += "..."
                            params_disp = f"({params_str})"
                        else:
                            params_disp = "()"
                        line_no = fn.get("line_number") or fn.get("line") or "?"
                        results.append(f"  {fn.get('name')}{params_disp}  [line {line_no}]")
                        # Add first line of docstring for context
                        if fn.get("docstring"):
                            first_line = fn["docstring"].split("\n")[0].strip()[:80]
                            results.append(f"    # {first_line}")
                classes = info.get("classes")
                if classes and len(classes) > 0:
                    results.append("### Classes")
                    for cls in classes[:20]:
                        cls_methods = cls.get("methods") or []
                        methods = ", ".join(m.get("name") for m in cls_methods[:5]) if cls_methods else ""
                        line_no = cls.get("line_number") or cls.get("line") or "?"
                        results.append(f"  {cls.get('name')}  [line {line_no}]")
                        # Add first line of class docstring
                        if cls.get("docstring"):
                            first_line = cls["docstring"].split("\n")[0].strip()[:80]
                            results.append(f"    # {first_line}")
                        if methods:
                            results.append(f"    methods: {methods}{'...' if len(cls_methods) > 5 else ''}")
                results.append("")
                results.append("---")
                results.append("To see full code: Read with limit=100 (or offset=N limit=M for specific lines)")
                return "\n".join(results)
            # Fall through to extract if failed

        # MODE: extract - full AST (26% savings) - fallback and for editing
        # L1/L2: Extract file info (AST + Call Graph) using daemon
        # Pass session ID for token tracking (P7)
        if "ast" in layers or "call_graph" in layers or mode == "extract":
            extract_resp = daemon_client.query_daemon_sync(
                {"cmd": "extract", "file": file_path, "session": session_id_value or None},
                project_dir,
            )

            if extract_resp.get("status") == "ok" and extract_resp.get("result"):
                info = extract_resp["result"]

                # Functions
                functions = info.get("functions")
                if functions and len(functions) > 0:
                    results.append("## Functions")
                    for fn in functions:
                        params = ", ".join(fn.get("params") or [])
                        ret = f" -> {fn.get('return_type')}" if fn.get("return_type") else ""
                        line_no = fn.get("line_number") or fn.get("line")
                        results.append(f"  {fn.get('name')}({params}){ret}  [line {line_no}]")
                        if fn.get("docstring"):
                            doc = fn["docstring"][:100].replace("\n", " ")
                            results.append(f"    # {doc}")

                # Classes
                classes = info.get("classes")
                if classes and len(classes) > 0:
                    results.append("")
                    results.append("## Classes")
                    for cls in classes:
                        line_no = cls.get("line_number") or cls.get("line")
                        results.append(f"  class {cls.get('name')}  [line {line_no}]")
                        if cls.get("methods"):
                            for m in cls["methods"][:10]:
                                results.append(f"    .{m.get('name')}()")

                # Call Graph
                if "call_graph" in layers:
                    call_graph = info.get("call_graph")
                    if call_graph and call_graph.get("calls"):
                        results.append("")
                        results.append("## Call Graph")
                        entries = list(call_graph["calls"].items())[:15]
                        for caller, callees in entries:
                            results.append(f"  {caller} -> {callees}")

        # L3: CFG (Control Flow Graph)
        if "cfg" in layers:
            func_name = target or "main"
            cfg_resp = daemon_client.query_daemon_sync(
                {"cmd": "cfg", "file": file_path, "function": func_name, "language": language},
                project_dir,
            )

            if cfg_resp.get("status") == "ok" and cfg_resp.get("result"):
                cfg = cfg_resp["result"]
                results.append("")
                results.append(f"## CFG: {func_name}")
                results.append(f"  Blocks: {cfg.get('num_blocks') or 'N/A'}, Cyclomatic: {cfg.get('cyclomatic_complexity') or 'N/A'}")
                blocks = cfg.get("blocks")
                if blocks and isinstance(blocks, list):
                    for b in blocks[:8]:
                        results.append(f"    Block {b.get('id')}: lines {b.get('start_line')}-{b.get('end_line')} ({b.get('block_type')})")

        # L4: DFG (Data Flow Graph)
        if "dfg" in layers:
            func_name = target or "main"
            dfg_resp = daemon_client.query_daemon_sync(
                {"cmd": "dfg", "file": file_path, "function": func_name, "language": language},
                project_dir,
            )

            if dfg_resp.get("status") == "ok" and dfg_resp.get("result"):
                dfg = dfg_resp["result"]
                results.append("")
                results.append(f"## DFG: {func_name}")
                definitions = dfg.get("definitions")
                if definitions and len(definitions) > 0:
                    results.append("  Definitions:")
                    for d in definitions[:10]:
                        results.append(f"    {d.get('var_name')} @ line {d.get('line')}")
                uses = dfg.get("uses")
                if uses and len(uses) > 0:
                    results.append("  Uses:")
                    for u in uses[:8]:
                        results.append(f"    {u.get('var_name')} @ line {u.get('line')}")

        # L5: PDG (Program Dependency Graph) via slice
        if "pdg" in layers:
            func_name = target or "main"
            slice_resp = daemon_client.query_daemon_sync(
                {"cmd": "slice", "file": file_path, "function": func_name, "line": 10, "direction": "backward"},
                project_dir,
            )

            if slice_resp.get("status") == "ok" and slice_resp.get("result"):
                slice_info = slice_resp["result"]
                results.append("")
                results.append(f"## PDG: {func_name}")
                slice_lines = slice_info.get("lines")
                if slice_lines and len(slice_lines) > 0:
                    results.append(f"  Slice lines: {len(slice_lines)}")
                variables = slice_info.get("variables")
                if variables and len(variables) > 0:
                    results.append(f"  Variables: {', '.join(variables)}")

        return "\n".join(results) if len(results) > 3 else None  # > 3 means we have more than just header
    except Exception:
        return None


def main() -> None:
    try:
        payload = read_stdin(timeout=2.0)

        if tool_name(payload) != "Read":
            print("{}")
            return

        params = tool_input(payload)
        file_path = params.get("file_path") or ""

        # Allow non-code files
        if not is_code_file(file_path):
            print("{}")
            return

        # Allow explicitly permitted files
        if is_allowed_file(file_path):
            print("{}")
            return

        # If requesting specific lines (offset/limit), allow - they know what they want
        offset = params.get("offset")
        limit = params.get("limit")
        if offset or (limit and limit < 100):
            print("{}")
            return

        # Small files: TLDR overhead not worth it, just read directly
        try:
            stats = os.stat(file_path)
            if stats.st_size < 3000:  # ~100 lines
                print("{}")
                return
        except Exception:
            # File doesn't exist or can't stat, let Read handle the error
            print("{}")
            return

        # Get TLDR context instead of raw file
        language = detect_language(file_path)

        # Try to detect intent from multiple sources (in priority order)
        layers = ["ast", "call_graph"]  # Default layers
        target = None
        context_source = "default"

        # Check for search context from smart-search-router
        search_context = get_search_context(session_id(payload))
        if search_context:
            layers = search_context.get("suggestedLayers")
            target = search_context.get("target")
            context_source = f"{search_context.get('targetType')}: {search_context.get('target')}"

        tldr_context = get_tldr_context(file_path, language, layers, target, session_id(payload), context_source)

        if not tldr_context:
            # TLDR failed, allow normal read
            print("{}")
            return

        # Format layer names for display
        layer_names = " + ".join(
            {
                "ast": "L1:AST",
                "call_graph": "L2:CallGraph",
                "cfg": "L3:CFG",
                "dfg": "L4:DFG",
                "pdg": "L5:PDG",
            }.get(l, l)
            for l in (layers or [])
        )

        # Format cross-file usage (L6) if available
        cross_file_section = ""
        callers = (search_context or {}).get("callers") if search_context else None
        if callers and len(callers) > 0:
            caller_lines = []
            for c in callers[:10]:
                # Format: /full/path/file.py:123 → file.py:123
                parts = c.split("/")
                file_and_line = parts[-1]
                d = parts[-2] if len(parts) > 2 else ""
                caller_lines.append(f"  {d + '/' if d else ''}{file_and_line}")
            extra = f"\n  ... and {len(callers) - 10} more" if len(callers) > 10 else ""
            cross_file_section = f"\n## Cross-File Usage ({len(callers)} refs)\n{'\n'.join(caller_lines)}{extra}\n"

        # Add definition location if different from current file
        definition_section = ""
        definition_location = (search_context or {}).get("definitionLocation") if search_context else None
        if definition_location and definition_location not in os.path.basename(file_path):
            definition_section = f"\n📍 Defined at: {definition_location}\n"

        # Track hook activity (P8)
        project_dir = os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
        daemon_client.track_hook_activity_sync(
            "tldr-read-enforcer", project_dir, True,
            {"reads_intercepted": 1, "layers_returned": len(layers or [])},
        )

        # BLOCK the read and return TLDR context
        context_line = f"🔗 Context: {context_source}" if search_context else ""
        output: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"✅ TLDR Enhanced Read ({layer_names}) - 95% token savings:\n"
                    f"{context_line}{definition_section}\n"
                    f"\n"
                    f"{tldr_context}{cross_file_section}\n"
                    f"---\n"
                    f"To read specific lines, use: Read with offset/limit\n"
                    f"To read full file anyway, use: Read {os.path.basename(file_path)} (test files bypass this)"
                ),
            }
        }

        print(json.dumps(output))
    except Exception:
        # TLDR enforcer error - allow normal read
        print("{}")


if __name__ == "__main__":
    main()
