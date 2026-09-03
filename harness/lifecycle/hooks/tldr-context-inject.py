#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""TLDR Context Injection Hook - Intent-Aware Version (DAEMON) (Python port).

Routes to different TLDR layers based on detected intent:
- "debug/investigate X" → Call Graph + CFG (what it calls, complexity)
- "where does Y come from" → DFG (data flow)
- "what affects line Z" → PDG (program slicing)
- "show structure" → AST only
- Default → Call Graph (navigation)

Uses TLDR daemon for fast cached responses (50ms vs 500ms CLI).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import daemon_client  # noqa: E402
from _payload import read_stdin, session_id, tool_input, tool_name  # noqa: E402

TldrLayer = str  # 'call_graph' | 'cfg' | 'dfg' | 'pdg' | 'ast'

INTENT_PATTERNS = [
    {
        # Data flow questions
        "patterns": [
            re.compile(r"where\s+does?\s+(\w+)\s+come\s+from", re.IGNORECASE),
            re.compile(r"what\s+sets?\s+(\w+)", re.IGNORECASE),
            re.compile(r"who\s+assigns?\s+(\w+)", re.IGNORECASE),
            re.compile(r"track\s+(?:the\s+)?(?:variable\s+)?(\w+)", re.IGNORECASE),
            re.compile(r"data\s+flow", re.IGNORECASE),
            re.compile(r"variable\s+(?:origin|source)", re.IGNORECASE),
        ],
        "layers": ["dfg"],
        "description": "data flow analysis"
    },
    {
        # Program slicing / dependency questions
        "patterns": [
            re.compile(r"what\s+affects?\s+(?:line\s+)?(\d+)", re.IGNORECASE),
            re.compile(r"what\s+depends?\s+on", re.IGNORECASE),
            re.compile(r"slice\s+(?:at|from)", re.IGNORECASE),
            re.compile(r"dependencies?\s+(?:of|for)", re.IGNORECASE),
            re.compile(r"impact\s+(?:of|analysis)", re.IGNORECASE),
        ],
        "layers": ["pdg"],
        "description": "program slicing"
    },
    {
        # Complexity / control flow questions
        "patterns": [
            re.compile(r"how\s+complex", re.IGNORECASE),
            re.compile(r"complexity\s+(?:of|for)", re.IGNORECASE),
            re.compile(r"control\s+flow", re.IGNORECASE),
            re.compile(r"branch(?:es|ing)", re.IGNORECASE),
            re.compile(r"cyclomatic", re.IGNORECASE),
            re.compile(r"paths?\s+through", re.IGNORECASE),
        ],
        "layers": ["cfg"],
        "description": "control flow analysis"
    },
    {
        # Structure only
        "patterns": [
            re.compile(r"list\s+(?:all\s+)?(?:functions?|methods?|classes?)", re.IGNORECASE),
            re.compile(r"show\s+structure", re.IGNORECASE),
            re.compile(r"what\s+(?:functions?|methods?)\s+(?:are\s+)?in", re.IGNORECASE),
            re.compile(r"overview\s+of", re.IGNORECASE),
        ],
        "layers": ["ast"],
        "description": "structure overview"
    },
    {
        # Debug / investigate (default rich context)
        "patterns": [
            re.compile(r"debug", re.IGNORECASE),
            re.compile(r"investigate", re.IGNORECASE),
            re.compile(r"fix\s+(?:the\s+)?(?:bug|issue|error)", re.IGNORECASE),
            re.compile(r"understand", re.IGNORECASE),
            re.compile(r"how\s+does?\s+(\w+)\s+work", re.IGNORECASE),
            re.compile(r"explain", re.IGNORECASE),
        ],
        "layers": ["call_graph", "cfg"],
        "description": "debugging context"
    }
]

# Function name extraction patterns
FUNCTION_PATTERNS = [
    re.compile(r"(?:function|method|def|fn)\s+[`\"']?(\w+)[`\"']?", re.IGNORECASE),
    re.compile(r"the\s+[`\"']?(\w+)[`\"']?\s+(?:function|method)", re.IGNORECASE),
    re.compile(r"(?:fix|debug|investigate|look at|check|analyze)\s+[`\"']?(\w+(?:\.\w+)?)[`\"']?", re.IGNORECASE),
    re.compile(r"[`\"']?(\w+\.\w+)[`\"']?"),
    re.compile(r"[`\"']?([a-z][a-z0-9_]{2,})[`\"']?"),
]

EXCLUDE_WORDS = {
    'the', 'and', 'for', 'with', 'from', 'this', 'that', 'what', 'how',
    'can', 'you', 'fix', 'debug', 'investigate', 'look', 'check', 'analyze',
    'function', 'method', 'class', 'file', 'code', 'error', 'bug', 'issue',
    'please', 'help', 'need', 'want', 'should', 'could', 'would', 'make',
    'add', 'remove', 'update', 'change', 'modify', 'create', 'delete',
    'test', 'tests', 'run', 'build', 'install', 'start', 'stop',
    'where', 'does', 'come', 'from', 'affects', 'line', 'variable',
}


def detectIntent(prompt: str) -> dict[str, Any]:
    for intent in INTENT_PATTERNS:
        for pattern in intent["patterns"]:
            if pattern.search(prompt):
                return {"layers": intent["layers"], "description": intent["description"]}
    # Default: call graph for navigation
    return {"layers": ["call_graph"], "description": "code navigation"}


def detectLanguage(projectPath: str) -> str:
    indicators = {
        "python": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
        "typescript": ["tsconfig.json", "package.json"],
        "rust": ["Cargo.toml"],
        "go": ["go.mod", "go.sum"],
    }

    for lang, files in indicators.items():
        for file in files:
            if (Path(projectPath) / file).exists():
                return lang
    return "python"


def extractEntryPoints(prompt: str) -> list[str]:
    candidates: set[str] = set()

    for pattern in FUNCTION_PATTERNS:
        for match in pattern.finditer(prompt):
            candidate = match.group(1)
            if candidate and len(candidate) > 2 and candidate.lower() not in EXCLUDE_WORDS:
                candidates.add(candidate)

    return sorted(candidates, key=lambda c: (
        0 if "." in c else 1,  # dot-containing first
        -len(c),  # then longer first
    ))


def extractLineNumber(prompt: str):
    match = re.search(r"line\s+(\d+)", prompt, re.IGNORECASE)
    return int(match.group(1)) if match else None


def extractVariableName(prompt: str):
    patterns = [
        re.compile(r"where\s+does?\s+[`\"']?(\w+)[`\"']?\s+come\s+from", re.IGNORECASE),
        re.compile(r"what\s+sets?\s+[`\"']?(\w+)[`\"']?", re.IGNORECASE),
        re.compile(r"track\s+(?:the\s+)?(?:variable\s+)?[`\"']?(\w+)[`\"']?", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(prompt)
        if match:
            return match.group(1)
    return None


def getTldrContext(
    projectPath: str,
    entryPoint: str,
    language: str,
    layers: list[str],
    lineNumber=None,
    varName=None,
):
    results: list[str] = []

    try:
        for layer in layers:
            if layer == "call_graph":
                # Use daemon context command
                response = daemon_client.query_daemon_sync(
                    {"cmd": "context", "entry": entryPoint, "language": language, "depth": 2},
                    projectPath
                )
                if response.get("status") == "ok" and response.get("result"):
                    ctx = response["result"]
                    lines = [f"## Context: {entryPoint}"]
                    if ctx.get("entry_point"):
                        lines.append(f"📍 {ctx['entry_point']['file']}:{ctx['entry_point']['line']}")
                        if ctx["entry_point"].get("signature"):
                            lines.append(f"  {ctx['entry_point']['signature']}")
                    if ctx.get("callees") and len(ctx["callees"]) > 0:
                        lines.append("\nCalls:")
                        for c in ctx["callees"][:10]:
                            lines.append(f"  → {c.get('function')} ({c.get('file')}:{c.get('line')})")
                    if ctx.get("callers") and len(ctx["callers"]) > 0:
                        lines.append("\nCalled by:")
                        for c in ctx["callers"][:10]:
                            lines.append(f"  ← {c.get('function')} ({c.get('file')}:{c.get('line')})")
                    results.append("\n".join(lines))

            elif layer == "cfg":
                # Use daemon cfg command - need to find file first
                searchResp = daemon_client.query_daemon_sync(
                    {"cmd": "search", "pattern": f"def {entryPoint}"},
                    projectPath
                )
                if searchResp.get("results") and len(searchResp["results"]) > 0:
                    file_ = searchResp["results"][0]["file"]
                    cfgResp = daemon_client.query_daemon_sync(
                        {"cmd": "cfg", "file": file_, "function": entryPoint, "language": language},
                        projectPath
                    )
                    if cfgResp.get("status") == "ok" and cfgResp.get("result"):
                        cfg = cfgResp["result"]
                        lines = [f"## CFG: {entryPoint}"]
                        lines.append(f"Blocks: {cfg.get('num_blocks') or 'N/A'}")
                        lines.append(f"Cyclomatic: {cfg.get('cyclomatic_complexity') or 'N/A'}")
                        if cfg.get("blocks") and isinstance(cfg["blocks"], list):
                            for b in cfg["blocks"][:8]:
                                lines.append(f"  Block {b.get('id')}: lines {b.get('start_line')}-{b.get('end_line')} ({b.get('block_type')})")
                        results.append("\n".join(lines))

            elif layer == "dfg":
                # Use daemon dfg command
                funcForDfg = entryPoint.split(".").pop() or entryPoint
                searchResp = daemon_client.query_daemon_sync(
                    {"cmd": "search", "pattern": f"def {funcForDfg}"},
                    projectPath
                )
                if searchResp.get("results") and len(searchResp["results"]) > 0:
                    file_ = searchResp["results"][0]["file"]
                    dfgResp = daemon_client.query_daemon_sync(
                        {"cmd": "dfg", "file": file_, "function": funcForDfg, "language": language},
                        projectPath
                    )
                    if dfgResp.get("status") == "ok" and dfgResp.get("result"):
                        dfg = dfgResp["result"]
                        varTarget = varName or entryPoint
                        lines = [f"## DFG: {varTarget} in {funcForDfg}"]
                        if dfg.get("definitions") and isinstance(dfg["definitions"], list):
                            lines.append("Definitions:")
                            for d in dfg["definitions"][:10]:
                                lines.append(f"  {d.get('var_name')} @ line {d.get('line')}")
                        if dfg.get("uses") and isinstance(dfg["uses"], list):
                            lines.append("Uses:")
                            for u in dfg["uses"][:8]:
                                lines.append(f"  {u.get('var_name')} @ line {u.get('line')}")
                        results.append("\n".join(lines))

            elif layer == "pdg":
                # Use daemon slice command for PDG
                targetLine = lineNumber or 10  # Default to line 10 if no line specified
                searchResp = daemon_client.query_daemon_sync(
                    {"cmd": "search", "pattern": f"def {entryPoint}"},
                    projectPath
                )
                if searchResp.get("results") and len(searchResp["results"]) > 0:
                    file_ = searchResp["results"][0]["file"]
                    sliceResp = daemon_client.query_daemon_sync(
                        {"cmd": "slice", "file": file_, "function": entryPoint, "line": targetLine, "direction": "backward"},
                        projectPath
                    )
                    if sliceResp.get("status") == "ok" and sliceResp.get("result"):
                        slice_ = sliceResp["result"]
                        lines = [f"## PDG Slice: {entryPoint} @ line {targetLine}"]
                        if slice_.get("lines") and isinstance(slice_["lines"], list):
                            lines.append(f"Slice lines: {len(slice_['lines'])}")
                            for ln in slice_["lines"][:15]:
                                lines.append(f"  Line {ln}")
                        if slice_.get("variables") and isinstance(slice_["variables"], list):
                            lines.append(f"Variables: {', '.join(slice_['variables'])}")
                        results.append("\n".join(lines))

            elif layer == "ast":
                # Use daemon structure command
                structResp = daemon_client.query_daemon_sync(
                    {"cmd": "structure", "language": language, "max_results": 20},
                    projectPath
                )
                if structResp.get("status") == "ok" and structResp.get("result"):
                    struct_ = structResp["result"]
                    lines = ["## Structure Overview"]
                    if struct_.get("files") and isinstance(struct_["files"], list):
                        for file in struct_["files"][:10]:
                            lines.append(f"\n### {file.get('path') or file.get('file')}")
                            if file.get("functions") and isinstance(file["functions"], list):
                                for fn in file["functions"][:8]:
                                    lines.append(f"  fn {fn.get('name')}:{fn.get('line')}")
                            if file.get("classes") and isinstance(file["classes"], list):
                                for cls in file["classes"][:5]:
                                    lines.append(f"  class {cls.get('name')}:{cls.get('line')}")
                    results.append("\n".join(lines))

        return "\n\n".join(results) if len(results) > 0 else None
    except Exception:
        return None


def findProjectRoot(startPath: str) -> str:
    current = Path(startPath)
    markers = ['.git', 'pyproject.toml', 'package.json', 'Cargo.toml', 'go.mod']

    while True:
        for marker in markers:
            if (current / marker).exists():
                return str(current)
        parent = current.parent
        if parent == current:
            return startPath
        current = parent


def main() -> None:
    payload = read_stdin(timeout=2.0)

    if tool_name(payload) != "Task":
        print("{}")
        return

    params = tool_input(payload)
    prompt = params.get("prompt") or ""
    description = params.get("description") or ""
    fullText = f"{prompt} {description}"

    # Skip if already has TLDR context
    if "## Code Context:" in prompt or "## CFG:" in prompt or "## DFG:" in prompt:
        print("{}")
        return

    # Detect intent → choose layers
    intentResult = detectIntent(fullText)
    layers = intentResult["layers"]
    intentDesc = intentResult["description"]

    # Extract targets
    entryPoints = extractEntryPoints(fullText)
    lineNumber = extractLineNumber(fullText)
    varName = extractVariableName(fullText)

    if len(entryPoints) == 0 and not varName and not lineNumber:
        print("{}")
        return

    # Find project and language
    cwd = payload.get("cwd") or os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
    projectRoot = findProjectRoot(cwd)
    language = detectLanguage(projectRoot)

    # Get TLDR context for the appropriate layers
    tldrContext = None
    usedTarget = varName or (entryPoints[0] if entryPoints else f"line {lineNumber}")

    for entryPoint in entryPoints[:3]:
        tldrContext = getTldrContext(projectRoot, entryPoint, language, layers, lineNumber, varName)
        if tldrContext:
            usedTarget = entryPoint
            break

    # Fallback: try with varName if we have it
    if not tldrContext and varName:
        tldrContext = getTldrContext(projectRoot, varName, language, layers, lineNumber, varName)

    if not tldrContext:
        print("{}")
        return

    # Inject context
    enhancedPrompt = f"## TLDR Context ({intentDesc}: {'+'.join(layers)})\n\n{tldrContext}\n\n---\nORIGINAL TASK:\n{prompt}"

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": f"Injected {'+'.join(layers)} context for: {usedTarget}",
            "updatedInput": {
                **params,
                "prompt": enhancedPrompt,
            }
        }
    }

    # Track hook activity for flush threshold
    daemon_client.track_hook_activity_sync("tldr-context-inject", projectRoot, True, {
        "context_injected": 1,
        "layers_used": len(layers),
    })

    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("{}")
