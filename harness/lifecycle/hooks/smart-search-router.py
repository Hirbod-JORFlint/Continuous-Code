#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""PreToolUse Hook: Smart Search Router (Python port).

Routes Grep calls to the most token-efficient tool:
1. AST-grep - structural code queries (most efficient)
2. LEANN - semantic/conceptual queries
3. Grep - literal patterns (fallback)

Uses TLDR daemon for fast symbol lookups when available.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import daemon_client  # noqa: E402
from _payload import read_stdin, session_id, tool_input, tool_name  # noqa: E402

QueryType = str  # 'structural' | 'semantic' | 'literal'
TargetType = str  # 'function' | 'class' | 'variable' | 'import' | 'decorator' | 'unknown'


def storeSearchContext(sessionId: str, context: dict[str, Any]) -> None:
    try:
        context_dir = Path(tempfile.gettempdir()) / "opc-search-context"
        context_dir.mkdir(parents=True, exist_ok=True)
        (context_dir / f"{sessionId}.json").write_text(
            json.dumps(context, indent=2), encoding="utf-8"
        )
    except Exception:
        # Ignore errors - context storage is best-effort
        pass


def ripgrepFallback(pattern: str, projectDir: str) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["rg", pattern, projectDir, "--type", "py", "--line-number", "--max-count", "10"],
            capture_output=True,
            text=True,
            timeout=3000,
        )
        # Parse ripgrep output: file:line:content
        lines = [l for l in result.stdout.strip().split("\n") if l][:10]
        return_ = []
        for line in lines:
            match = re.match(r"^([^:]+):(\d+):(.*)$", line)
            if match:
                return_.append({"file": match.group(1), "line": int(match.group(2)), "content": match.group(3)})
            else:
                return_.append({"file": line, "line": 0, "content": ""})
        return return_
    except Exception:
        return []


def tldrSearch(pattern: str, projectDir: str = ".") -> list[dict[str, Any]]:
    try:
        # Try daemon first
        response = daemon_client.query_daemon_sync({"cmd": "search", "pattern": pattern}, projectDir)

        # If daemon is indexing or unavailable, fall back to ripgrep
        if response.get("indexing") or response.get("status") == "unavailable":
            return ripgrepFallback(pattern, projectDir)

        # Parse successful daemon response
        if response.get("status") == "ok" and response.get("results"):
            return response["results"]

        return []
    except Exception:
        # Fall back to ripgrep on any error
        return ripgrepFallback(pattern, projectDir)


def checkSemanticIndexExists(projectDir: str) -> bool:
    indexPath = Path(projectDir) / ".tldr" / "cache" / "semantic" / "index.faiss"
    return indexPath.exists()


def tldrSemantic(query: str, projectDir: str = ".") -> dict[str, Any]:
    # First check if index exists
    if not checkSemanticIndexExists(projectDir):
        return {"results": [], "status": "no_index"}

    try:
        response = daemon_client.query_daemon_sync({"cmd": "semantic", "query": query, "k": 5}, projectDir)

        if response.get("indexing"):
            return {"results": [], "status": "indexing"}

        if response.get("status") == "unavailable":
            return {"results": [], "status": "daemon_unavailable"}

        if response.get("status") == "ok" and response.get("results"):
            return {"results": response["results"], "status": "ok"}

        return {"results": [], "status": "ok"}  # Empty results but working
    except Exception:
        return {"results": [], "status": "error"}


def tldrImpact(funcName: str, projectDir: str = ".") -> list[str]:
    try:
        response = daemon_client.query_daemon_sync({"cmd": "impact", "func": funcName}, projectDir)

        # Skip if indexing or unavailable
        if response.get("indexing") or response.get("status") == "unavailable":
            return []

        # Parse callers from response
        if response.get("status") == "ok" and response.get("callers"):
            return [f"{c.get('file')}:{c.get('line')}" for c in response["callers"]]

        return []
    except Exception:
        return []


def lookupCallers(pattern: str) -> list[str]:
    projectDir = os.environ.get("OPC_PROJECT_DIR") or "."
    return tldrImpact(pattern, projectDir)[:20]


def lookupSymbol(pattern: str):
    projectDir = os.environ.get("OPC_PROJECT_DIR") or "."

    # Try function first (most common)
    funcResults = tldrSearch(f"def {pattern}", projectDir)
    if len(funcResults) > 0:
        return {
            "type": "function",
            "location": f"{funcResults[0]['file']}:{funcResults[0]['line']}",
        }

    # Try class
    classResults = tldrSearch(f"class {pattern}", projectDir)
    if len(classResults) > 0:
        return {
            "type": "class",
            "location": f"{classResults[0]['file']}:{classResults[0]['line']}",
        }

    # Try variable (SCREAMING_CASE assignment)
    if re.match(r"^[A-Z][A-Z0-9_]+$", pattern):
        varResults = tldrSearch(f"{pattern} =", projectDir)
        if len(varResults) > 0:
            return {
                "type": "variable",
                "location": f"{varResults[0]['file']}:{varResults[0]['line']}",
            }

    return None


# Verb prefixes AND standalone verbs that indicate a function (not a variable)
# Prefixes: get_, set_, is_, has_, etc.
# Standalone: poll, call, exec, sync, etc. (common method names that are single verbs)
FUNCTION_VERB_PREFIXES = re.compile(r"^(get|set|is|has|do|can|create|update|delete|fetch|load|save|read|write|parse|build|make|init|setup|run|start|stop|handle|process|validate|check|find|search|filter|sort|map|reduce|transform|convert|format|render|display|show|hide|enable|disable|add|remove|insert|append|push|pop|clear|reset|close|open|connect|disconnect|send|receive|emit|on_|async_|_get|_set|_is|_has|_do|_create|_update|_delete|_fetch|_load|_save|_read|_write|_parse|_build|_make|_init|_setup|_run|_handle|_process|_validate|_check|_find|poll|call|exec|execute|invoke|apply|bind|dispatch|trigger|fire|notify|broadcast|publish|subscribe|unsubscribe|listen|watch|observe|register|unregister|mount|unmount|attach|detach|flush|dump|log|warn|error|debug|trace|print|throw|raise|assert|test|mock|stub|spy|wait|sleep|delay|retry|abort|cancel|pause|resume|refresh|reload|rerun|revert|rollback|commit|merge|split|join|clone|copy|move|swap|toggle|flip|increment|decrement|next|prev|first|last|peek|drain|consume|produce|yield|spawn|fork|kill|terminate|shutdown|cleanup|destroy|dispose|release|acquire|lock|unlock|enter|exit|begin|end|finalize)(_|$)")


def extractTarget(pattern: str) -> dict[str, Any]:
    # 1. Try AST-based symbol index first (100% accurate if indexed)
    indexed = lookupSymbol(pattern)
    if indexed:
        return {"target": pattern, "targetType": indexed["type"]}

    # 2. Fall back to heuristics for unindexed patterns
    # Explicit keywords first
    classMatch = re.match(r"^class\s+(\w+)", pattern)
    if classMatch:
        return {"target": classMatch.group(1), "targetType": "class"}

    defMatch = re.match(r"^(?:async\s+)?def\s+(\w+)", pattern)
    if defMatch:
        return {"target": defMatch.group(1), "targetType": "function"}

    functionMatch = re.match(r"^(?:async\s+)?function\s+(\w+)", pattern)
    if functionMatch:
        return {"target": functionMatch.group(1), "targetType": "function"}

    decoratorMatch = re.match(r"^@(\w+)", pattern)
    if decoratorMatch:
        return {"target": decoratorMatch.group(1), "targetType": "decorator"}

    importMatch = re.match(r"^(?:import|from)\s+(\w+)", pattern)
    if importMatch:
        return {"target": importMatch.group(1), "targetType": "import"}

    # Self/this attribute access (handle escaped dots too: self\._data)
    attrMatch = re.search(r"(?:self|this)(?:\.|\\\.|\\\.\s*)(\w+)", pattern)
    if attrMatch:
        attr = attrMatch.group(1)
        # Check if it looks like a method (verb prefix) or variable
        if FUNCTION_VERB_PREFIXES.search(attr):
            return {"target": attr, "targetType": "function"}
        return {"target": attr, "targetType": "variable"}

    # Python dunder handling
    if re.match(r"^__[a-z][a-z0-9_]*__$", pattern):
        # Module-level dunder VARIABLES (not methods)
        moduleVars = ['__all__', '__version__', '__author__', '__doc__', '__file__', '__name__', '__package__', '__path__', '__cached__', '__loader__', '__spec__', '__builtins__', '__dict__', '__module__', '__slots__', '__annotations__']
        if pattern in moduleVars:
            return {"target": pattern, "targetType": "variable"}
        # All other dunders are methods (e.g., __init__, __str__, __repr__, __eq__)
        return {"target": pattern, "targetType": "function"}

    # SCREAMING_CASE = constant (variable)
    if re.match(r"^[A-Z][A-Z0-9_]+$", pattern):
        return {"target": pattern, "targetType": "variable"}

    # PascalCase = class
    if re.match(r"^[A-Z][a-zA-Z0-9]+$", pattern):
        return {"target": pattern, "targetType": "class"}

    # snake_case with verb prefix = function
    if re.match(r"^_?[a-z][a-z0-9_]*$", pattern) and FUNCTION_VERB_PREFIXES.search(pattern):
        return {"target": pattern, "targetType": "function"}

    # snake_case WITHOUT verb prefix = variable (e.g., _pool, cpu_percent, data_source)
    if re.match(r"^_?[a-z][a-z0-9_]*$", pattern):
        return {"target": pattern, "targetType": "variable"}

    # camelCase with verb prefix = function (e.g., handleClick, useState, fetchData)
    camelCaseVerbPattern = re.compile(r"^(get|set|is|has|do|can|use|create|update|delete|fetch|load|save|read|write|parse|build|make|init|setup|run|start|stop|handle|process|validate|check|find|search|filter|sort|map|reduce|transform|convert|format|render|display|show|hide|enable|disable|add|remove|insert|append|push|pop|clear|reset|close|open|connect|disconnect|send|receive|emit|on|async|poll|call|exec|execute|invoke|apply|bind|dispatch|trigger|fire|notify|broadcast|publish|subscribe|watch|observe|register|mount|attach|flush|dump|log|warn|error|debug|print|throw|assert|test|mock|wait|sleep|retry|abort|cancel|pause|resume|refresh|reload|revert|commit|merge|clone|copy|move|toggle|spawn|fork|kill|terminate|shutdown|cleanup|destroy|dispose|release|acquire|lock|unlock|enter|exit|begin|end)[A-Z]")
    if camelCaseVerbPattern.search(pattern):
        return {"target": pattern, "targetType": "function"}

    # camelCase WITHOUT verb prefix = variable (e.g., sessionId, userData, configOptions)
    if re.match(r"^[a-z][a-zA-Z0-9]*$", pattern) and re.search(r"[A-Z]", pattern):
        return {"target": pattern, "targetType": "variable"}

    # Fallback: extract any identifier
    identMatch = re.search(r"\b([a-zA-Z_][a-zA-Z0-9_]{2,})\b", pattern)
    if identMatch:
        return {"target": identMatch.group(1), "targetType": "unknown"}

    return {"target": None, "targetType": "unknown"}


def suggestLayers(targetType: str, queryType: str) -> list[str]:
    if targetType == "function":
        return ["ast", "call_graph", "cfg"]
    elif targetType == "class":
        return ["ast", "call_graph"]
    elif targetType == "variable":
        return ["ast", "dfg"]
    elif targetType == "import":
        return ["ast"]
    elif targetType == "decorator":
        return ["ast", "call_graph"]
    else:
        return ["ast", "call_graph", "cfg"] if queryType == "semantic" else ["ast", "call_graph"]


def classifyQuery(pattern: str) -> QueryType:
    # STRUCTURAL: Code patterns that AST-grep handles best
    structuralPatterns = [
        re.compile(r"^(class|function|def|async def|const|let|var|interface|type|export)\s+\w+"),
        re.compile(r"^(import|from|require)\s"),
        re.compile(r"^\w+\s*\([^)]*\)"),  # function calls
        re.compile(r"^async\s+(function|def)"),
        re.compile(r"\$\w+"),  # AST-grep metavariables
        re.compile(r"^@\w+"),  # decorators
    ]

    if any(p.search(pattern) for p in structuralPatterns):
        return "structural"

    # LITERAL: Exact identifiers, regex, file paths
    # Regex patterns
    if "\\" in pattern or "[" in pattern or re.search(r"\([^)]*\|", pattern):
        return "literal"

    # Exact identifier patterns (CamelCase, snake_case, SCREAMING_CASE)
    if re.match(r"^[A-Z][a-zA-Z0-9]*$", pattern) or re.match(r"^[a-z_][a-z0-9_]*$", pattern) or re.match(r"^[A-Z_][A-Z0-9_]*$", pattern):
        return "literal"

    # File paths
    if "/" in pattern or re.search(r"\.(ts|py|js|go|rs|md)", pattern):
        return "literal"

    # Short patterns (1-2 words, no question words) are likely literal
    words = [w for w in re.split(r"\s+", pattern) if len(w) > 0]
    if len(words) <= 2 and not re.match(r"^(how|what|where|why|when|find|show|list)", pattern, re.IGNORECASE):
        return "literal"

    # SEMANTIC: Natural language, questions, conceptual
    semanticPatterns = [
        re.compile(r"^(how|what|where|why|when|which)\s", re.IGNORECASE),
        re.compile(r"\?$"),
        re.compile(r"^(find|show|list|get|explain)\s+(all|the|every|any)", re.IGNORECASE),
        re.compile(r"works?$", re.IGNORECASE),
        re.compile(r"^.*\s+(implementation|architecture|flow|pattern|logic|system)$", re.IGNORECASE),
    ]

    if any(p.search(pattern) for p in semanticPatterns):
        return "semantic"

    # 3+ words without code indicators → likely semantic
    if len(words) >= 3:
        return "semantic"

    return "literal"


def getAstGrepSuggestion(pattern: str, lang: str = "python") -> str:
    # Convert natural language to AST-grep pattern hints
    suggestions = {
        "function": "def $FUNC($$$):",
        "async": "async def $FUNC($$$):",
        "class": "class $NAME:",
        "import": "import $MODULE",
        "decorator": "@$DECORATOR",
    }

    for keyword, astPattern in suggestions.items():
        if keyword in pattern.lower():
            return astPattern
    return "$PATTERN($$$)"


def main() -> None:
    payload = read_stdin(timeout=2.0)

    # Only intercept Grep tool
    if tool_name(payload) != "Grep":
        print("{}")
        return

    params = tool_input(payload)

    # Validate tool_input exists and has required fields
    if not params or not isinstance(params.get("pattern"), str):
        print("{}")
        return

    pattern = params["pattern"]
    queryType = classifyQuery(pattern)
    sessionId = session_id(payload)

    # Extract target and store context for downstream hooks (tldr-read-enforcer)
    extracted = extractTarget(pattern)
    target = extracted["target"]
    targetType = extracted["targetType"]
    layers = suggestLayers(targetType, queryType)

    # Look up cross-file info from indexes
    symbolInfo = lookupSymbol(target) if target else None
    callers = lookupCallers(target) if target else []

    storeSearchContext(sessionId, {
        "timestamp": int(time.time() * 1000),
        "queryType": queryType,
        "pattern": pattern,
        "target": target,
        "targetType": targetType,
        "suggestedLayers": layers,
        "definitionLocation": symbolInfo.get("location") if symbolInfo else None,
        "callers": callers[:20],  # Limit to 20 callers for token efficiency
    })

    # Track hook activity (P8) - get project dir early for tracking
    projectDir = os.environ.get("OPC_PROJECT_DIR") or "."

    # LITERAL: Suggest TLDR search (finds + enriches in one call)
    if queryType == "literal":
        daemon_client.track_hook_activity_sync("smart-search-router", projectDir, True, {
            "queries_routed": 1, "literal_queries": 1,
        })

        reason = f"🔍 Use TLDR search for code exploration (95% token savings):\n\n**Option 1 - TLDR Skill:**\n/tldr-search {pattern}\n\n**Option 2 - Direct CLI:**\n```bash\ntldr search \"{pattern}\" .\n```\n\n**Option 3 - Read specific file (TLDR auto-enriches):**\nRead the file containing \"{pattern}\" - the tldr-read-enforcer will return structured context.\n\nTLDR finds location + provides call graph + docstrings in one call."

        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason
            }
        }
        print(json.dumps(output))
        return

    # STRUCTURAL: Suggest AST-grep (most token-efficient for patterns)
    if queryType == "structural":
        daemon_client.track_hook_activity_sync("smart-search-router", projectDir, True, {
            "queries_routed": 1, "structural_queries": 1,
        })

        astPattern = getAstGrepSuggestion(pattern)
        reason = f"🎯 Structural query - Use AST-grep OR TLDR:\n\n**Option 1 - AST-grep (pattern matching):**\nast-grep --pattern \"{astPattern}\" --lang python\n\n**Option 2 - TLDR (richer context):**\n/tldr-search {target or pattern}\n\nAST-grep: precise pattern match, file:line only\nTLDR: finds + call graph + docstrings + complexity"

        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason
            }
        }
        print(json.dumps(output))
        return

    # SEMANTIC: Actually run semantic search via daemon
    daemon_client.track_hook_activity_sync("smart-search-router", projectDir, True, {
        "queries_routed": 1, "semantic_queries": 1,
    })

    semanticSearch = tldrSemantic(pattern, projectDir)

    if semanticSearch["status"] == "ok" and len(semanticSearch["results"]) > 0:
        # We have results - provide them directly
        resultsStr = "\n".join(
            f"  - {r.get('file')}:{r.get('function') or 'module'}"
            + (f" ({int(r['score'] * 100)}%)" if r.get("score") else "")
            for r in semanticSearch["results"]
        )

        reason = f"🧠 **Semantic Search Results** (via TLDR daemon):\n\n{resultsStr}\n\n**Next steps:**\n1. Read the most relevant file: `Read {semanticSearch['results'][0]['file']}`\n2. For deeper analysis: `/tldr-search {target or pattern} --layer all`\n\nThe results above are semantically similar to \"{pattern}\"."
    elif semanticSearch["status"] == "no_index":
        # Semantic index doesn't exist - offer to set it up
        reason = f"🧠 **Semantic Search Not Set Up**\n\nNo semantic index found. To enable AI-powered code search:\n\n```bash\ntldr semantic index . --lang all\n```\n\nThis creates embeddings for your codebase (one-time, ~30s).\nAfter indexing, natural language queries like \"{pattern}\" will find relevant code.\n\n**For now, use:**\n- `/tldr-search {target or pattern}` - structured search\n- `Task(subagent_type=\"Explore\", prompt=\"{pattern}\")` - agent exploration"
    elif semanticSearch["status"] == "daemon_unavailable":
        # Daemon not running
        reason = f"🧠 **TLDR Daemon Not Running**\n\nStart the daemon for semantic search:\n```bash\ntldr daemon start\n```\n\nThen retry your query. The daemon provides fast, in-memory semantic search.\n\n**For now, use:**\n- `/tldr-search {target or pattern}` - structured search (no daemon needed)"
    elif semanticSearch["status"] == "indexing":
        # Daemon is indexing
        reason = f"🧠 **Semantic Index Building...**\n\nThe daemon is currently building the semantic index. This takes ~30s.\nRetry in a moment, or use structured search for now:\n\n`/tldr-search {target or pattern}`"
    else:
        # No results but system is working - genuinely no matches
        reason = f"🧠 **No Semantic Matches**\n\nNo code semantically similar to \"{pattern}\" found in the index.\n\n**Try:**\n1. Rephrase the query with different keywords\n2. Use structured search: `/tldr-search {target or pattern}`\n3. Explore with agent: `Task(subagent_type=\"Explore\", prompt=\"{pattern}\")`"

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never crash - degrade to no-op
        print("{}")
