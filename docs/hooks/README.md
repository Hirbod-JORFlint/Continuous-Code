# Hook System

Hooks are automatic behaviors triggered at specific lifecycle points during agent sessions. They enable powerful features like smart search routing, file conflict prevention, real-time type checking, and multi-session coordination.

This document describes the **harness-neutral** hook model driven from the canonical manifest at
`harness/lifecycle/hooks.toml`. Each targeted harness (opencode, codex, cline) surfaces that manifest
through its own native mechanism via the generators. The legacy Claude-Code-native protocol is
retained below only as a categorized historical appendix (until `.claude/` is removed at Step 11).

## Overview

Hooks run automatically at defined lifecycle events (session start, user prompt, tool use, etc.) and can:
- Inject context into the agent's awareness
- Block/redirect tool calls to more efficient alternatives
- Validate code changes in real-time
- Coordinate across concurrent sessions
- Extract learnings automatically

Hooks are implemented as command-line scripts (Python first, TypeScript/shell for legacy transport
until Step 11) that receive JSON input via stdin and return JSON output via stdout.

## Source of Truth

- **Manifest**: `harness/lifecycle/hooks.toml` — declares the event wiring for every hook.
- **Canonical handlers dir**: `harness/lifecycle/hooks/` — Python handlers + `hook_launcher.py` +
  `_payload.py` adapter.
- **Generator flow**: `opc/scripts/harness/gen_{opencode,codex,cline}.py` translate the manifest into
  each driver's native hook configuration. **Do not edit generated configs directly** — edit the
  manifest, then regenerate.

Neutral install root for re-homed hooks: `~/.opc/hooks` (a junction to `harness/lifecycle/hooks`
today, copied by the Step 9 installer on other machines).

## Lifecycle Events (neutral)

The neutral event taxonomy is the only event model you name in skills/docs/hooks. Each harness maps it
to its native event (see [HARNESS-ADAPTER §3](../../harness/spec/HARNESS-ADAPTER.md)):

| Neutral event | Meaning | opencode | codex | cline |
|---------------|---------|----------|-------|-------|
| `session_start` | Harness session begins | `session.created` | `SessionStart` | `TaskStart` |
| `prompt_submit` | User prompt about to be processed | (no clean equivalent — skipped) | `UserPromptSubmit` | `UserPromptSubmit` |
| `pre_tool_use` | Tool invocation about to run (can block) | `tool.execute.before` | `PreToolUse` | `PreToolUse` |
| `post_tool_use` | Tool invocation finished (non-blocking) | `tool.execute.after` | `PostToolUse` | `PostToolUse` |
| `pre_compact` | Context compaction imminent (may persist state) | `experimental.session.compacting` | `PreCompact` | `PreCompact` |
| `session_stop` | Session ending (must save state/handoff) | `session.idle` (approx) | `SessionEnd` | n/a (`TaskComplete` → `stop`) |
| `status_line` | Footer status text (optional sugar) | `status` | n/a | n/a |

Cline hooks are **real executable scripts** (v3.36+) — JSON in on stdin, JSON out
(`cancel`, `contextModification`). Windows uses `*.ps1`; macOS/Linux use extensionless executables
with `chmod +x`. There is no per-tool matcher on Cline (UI toggle only).

> **Important parity notes:** opencode has no clean `prompt_submit` event (skipped in the bridge) and
> maps `session_stop` to `session.idle` as an approximation. `prompt_submit`/`session_stop` handlers
> therefore do not fire on opencode unless another mechanism is used — see
> `harness/spec/HARNESS-ADAPTER.md`.

### Event payload contract

Hooks receive one JSON object on stdin:

```json
{
  "event": "pre_tool_use",
  "session_id": "…",
  "project_dir": "…",
  "tool_name": "edit",
  "tool_input": {},
  "context_pct": 42
}
```

## Hook Categories

The manifest groups handlers by neutral event. Below is the inventory with what each one does.

### Session Lifecycle (`session_start`, `session_stop`)

| Handler | Event | What It Does |
|---------|-------|--------------|
| `session-register` | `session_start` | Registers session in the coordination database (OPC registry); displays active peer sessions for cross-session conflict warnings |
| `session-start-continuity` | `session_start` (`resume\|compact\|clear`) | Restores continuity ledger context |
| `session-start-tldr-cache` | `session_start` (`startup\|resume`) | Pre-warms the tldr cache |
| `session-symbol-index` | `session_start` | Warms tldr cache and builds the semantic/symbol index |
| `persist-project-dir` | `session_start` | Persists project dir for `OPC_*` env propagation |
| `session-end-cleanup` | `session_stop` | Cleans up session artifacts (7-day agent-cache retention), updates continuity ledger timestamps, triggers background learning extraction |
| `session-outcome` | `session_stop` | Records session outcome (SUCCEEDED / PARTIAL_PLUS / PARTIAL_MINUS / FAILED) |

### User Prompt Processing (`prompt_submit`)

| Handler | What It Does |
|---------|--------------|
| `skill-activation-prompt` | Matches the prompt against `harness/skills/skill-rules.json` and injects the agent with skill suggestions (critical/high/medium/low priority) and agentic-workflow pattern inference |
| `memory-awareness` | Extracts intent, fast-searches archival memory, injects MEMORY MATCH context |
| `premortem-suggest` | Suggests running `/premortem deep <plan>` when implementing from a plan |
| `impact-refactor` | Flags refactor-ready code paths mentioned in the prompt |

### Tool Interception (`pre_tool_use`, `post_tool_use`)

| Handler | Event | What It Does |
|---------|-------|--------------|
| `tldr-read-enforcer` | `pre_tool_use` (`Read`) | Routes reads of indexed files through the tldr cache; 90-95% token savings |
| `smart-search-router` | `pre_tool_use` (`Grep`) | Classifies queries as structural/semantic/literal and routes to the best search tool |
| `tldr-context-inject` | `pre_tool_use` (`Task`) | Adds TLDR code context to subagent prompts |
| `arch-context-inject` | `pre_tool_use` (`Task`) | Adds architecture context to subagent prompts |
| `file-claims` | `pre_tool_use` (`Edit`) | Records file ownership claims; warns on cross-session edit conflicts |
| `edit-context-inject` | `pre_tool_use` (`Edit`) | Injects symbol/file context before edits |
| `signature-helper` | `pre_tool_use` (`Edit`) | Surfaces function signatures for accurate parameters |
| `path-rules` | `pre_tool_use` (`Read\|Edit\|Write`) | Enforces path allow/deny rules before file ops |
| `typescript-preflight` | `post_tool_use` (`Edit\|Write`) | Runs TypeScript typecheck (`tsc` + qlty) after edits; blocks with the error |
| `compiler-in-the-loop` | `post_tool_use` (`Edit\|Write`) | Compiles edited modules and surfaces diagnostics |
| `post-edit-notify` | `post_tool_use` (`Edit\|Write`) | Notifies on edit completion |
| `post-edit-diagnostics` | `post_tool_use` (`Edit\|Write`) | Collects diagnostics after edits |
| `handoff-index` | `post_tool_use` (`Write`) | Indexes handoff docs written this session |
| `import-validator` | `post_tool_use` (`Edit\|Write`) | Validates import statements are correct |
| `import-error-detector` | `post_tool_use` (`Bash`) | Detects import errors in bash output |
| `post-tool-use-tracker` | `post_tool_use` (`Edit\|MultiEdit\|Write\|Bash`) | Tracks edited files + build/test attempts for reasoning VCS |

### Compaction & Termination (`pre_compact`, `stop`)

| Handler | Event | What It Does |
|---------|-------|--------------|
| `pre-compact-continuity` | `pre_compact` | Persists continuity ledger excerpt before context compaction |
| `auto-handoff-stop` | `stop` | Blocks stop when context is too high and suggests a handoff (checks `stop_hook_active` to avoid infinite loops) |
| `compiler-in-the-loop-stop` | `stop` | Final compile sweep before the session stops |

### Braintrust Tracking (all events)

`braintrust-session-start`, `braintrust-user-prompt-submit`, `braintrust-post-tool-use`,
`braintrust-stop`, `braintrust-session-end` route the corresponding events into Braintrust tracing.

## Registration

Add a handler to `harness/lifecycle/hooks/<name>.py`, then register it in the manifest:

```toml
[[hooks.<event>]]
id = "<name>"
description = "What this hook does"
command = "uv run $HOME/.opc/hooks/hook_launcher.py <name>"
timeout = 5
```

Then regenerate the driver configs from `harness/lifecycle/hooks.toml`:

```bash
python opc/scripts/harness/gen_opencode.py
python opc/scripts/harness/gen_codex.py
python opc/scripts/harness/gen_cline.py
```

Hooks that still need the legacy TS/bash transport carry a
`transport = "legacy $HOME/.claude path until dist re-home (Step 11)"` key — extend re-homed hooks via
the Python launcher path instead, and remove the legacy transport once the dist re-home lands (Step 11).

### Handler Template (Python)

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _payload import project_dir  # noqa: E402

def main():
    pdir = Path(project_dir({}))
    # read stdin payload, process, print JSON result

if __name__ == '__main__':
    main()
```

## Exit Code Behavior (legacy transport)

Applies to hooks run through their legacy driver transport until Step 11; the neutral launcher reads a
JSON payload and prints a JSON result.

| Exit Code | Behavior | stdout | stderr |
|-----------|----------|--------|--------|
| **0** | Success | JSON processed | Ignored |
| **2** | Blocking error | **IGNORED** | Error message shown |
| **Other** | Non-blocking error | Ignored | Shown in verbose mode |

## Best Practices

1. **Fail Gracefully**: Always output valid JSON, even on errors. Use `{}` for no-op.
2. **Timeout Awareness**: Keep execution under the configured timeout. Use async spawn for slow tasks.
3. **Silent Failures**: Log errors to stderr, not stdout (stdout is parsed as JSON).
4. **Idempotency**: Hooks may run multiple times. Design for idempotent behavior.
5. **Context Injection**: Use `message` for user-visible output, `additionalContext` for agent-only
   context.
6. **Token Efficiency**: Keep injected context concise. TLDR hooks save ~90-95% tokens vs raw files.

## Behavior Examples

### TLDR Read Enforcement

When the agent tries to read a code file:

```
Read → tldr-read-enforcer hook intercepts (pre_tool_use)
     → Analyzes search context (from smart-search-router)
     → Returns structured context (L1:AST + L2:CallGraph)
     → The agent receives function signatures + call graph (~500 tokens)
     → vs raw file read (~5000 tokens)
     → ~90% token savings
```

### Smart Search Routing

When the agent tries to grep:

```
Grep "process_data" → smart-search-router hook intercepts (pre_tool_use)
                    → Classifies as "literal" query
                    → Extracts target: "process_data" (function)
                    → Stores search context for tldr-read-enforcer
                    → Blocks Grep, suggests the best search tool instead
```

### Multi-Session Coordination

When the agent tries to edit a file:

```
Edit file.py → file-claims hook intercepts (pre_tool_use)
            → Checks coordination DB for a file claim
            → Session A already editing file.py
            → Warns: "File conflict: Session A is editing file.py"
            → The agent can coordinate or edit a different file
```

### Type Checking

When the agent edits a TypeScript file:

```
Edit hook.ts → typescript-preflight hook runs (post_tool_use)
            → Executes: tsc --noEmit hook.ts
            → Finds: "Type 'string' not assignable to 'number'"
            → Blocks with the error message
            → The agent sees the error immediately and fixes it next turn
```

### Skill Activation

When the user submits a prompt:

```
Prompt: "refactor this code" → skill-activation-prompt hook runs (prompt_submit)
                            → Matches "refactor" keyword
                            → Suggests: refactor skill (high priority)
                            → The agent: /refactor before responding
```

## Advanced Features

### Symbol Indexing

The `session-symbol-index` hook builds a symbol index at session start:

```json
{
  "process_data": { "type": "function", "location": "/path/to/file.py:42" },
  "DataProcessor": { "type": "class", "location": "/path/to/file.py:10" }
}
```

Used by `smart-search-router` and `signature-helper` for accurate code understanding.

### Search Context Chaining

`smart-search-router` stores search context that `tldr-read-enforcer` consumes:

```json
{
  "timestamp": 1704067200000,
  "queryType": "literal",
  "pattern": "process_data",
  "target": "process_data",
  "targetType": "function",
  "suggestedLayers": ["ast", "call_graph", "cfg"],
  "callers": ["file1.py:10", "file2.py:25"]
}
```

This enables multi-layer context enrichment without repeated tool calls.

### Learning Extraction

`session-end-cleanup` spawns a background process to extract learnings:

```bash
uv run python opc/scripts/braintrust_analyze.py --learn --session-id <id>
```

Uses LLM-as-judge to extract:
- What worked
- What failed
- Decisions made
- Patterns discovered

Stored in `archival_memory` for future semantic recall.

## Debugging Hooks

### Test a Handler Manually

Pipe an event payload to the launcher:

```bash
echo '{"project_dir": "/path/to/project", "matcher": "resume"}' | \
  uv run $HOME/.opc/hooks/hook_launcher.py <handler-name>
```

### Check Hook Execution

Hooks log to stderr (not stdout, which is parsed as JSON):

```python
import sys
print('[my-hook] Processing input:', file=sys.stderr)
```

### Check Hook Timeout

If a hook exceeds its timeout it is killed and the agent continues. Increase the `timeout` in the
manifest if needed.

## Performance Considerations

- **TLDR hooks**: ~90-95% token savings (50-500 tokens vs 3000-20000 raw)
- **Search routing**: Prevents inefficient Grep scans
- **Signature injection**: Avoids reading definition files (saves 1000+ tokens per function)
- **Symbol indexing**: One-time ~5s cost at session start, saves 100+ tool calls
- **Learning extraction**: Background process, doesn't block session end

## Security Considerations

Hooks run with the same permissions as the harness driver. They can:
- Execute arbitrary commands
- Read/write files in the project
- Access environment variables
- Make network requests

**Recommendations:**
- Review hook source code before enabling
- Use `$OPC_PROJECT_DIR` / `$HOME/.opc` paths to scope to the current project/harness
- Set reasonable timeouts to prevent hanging
- Validate hook inputs (untrusted user prompts)
- Use readonly operations when possible (`pre_tool_use` hooks)

## See Also

- [HARNESS-ADAPTER](../../harness/spec/HARNESS-ADAPTER.md) — neutral harness-adapter contract + event taxonomy
- [ARCHITECTURE.md](../ARCHITECTURE.md) — overall system design
- [QUICKSTART.md](../QUICKSTART.md) — getting started
- [hooks skill](../../harness/skills/hooks/SKILL.md) — hook development rules
- `harness/lifecycle/hooks/` — canonical Python hooks + launcher + `_payload.py`
- `harness/lifecycle/hooks.toml` — neutral hook manifest

---

## Appendix: Legacy Claude-Code-native protocol (until Step 11)

> Categorized historical record of the superseded Claude-Code-native hook protocol. `.claude/` is
> slated for removal at Step 11. **New work must target the neutral model above**, not anything here.
> This section is preserved so legacy transport (still live today) can be debugged and its content
> migrated rather than lost.

### Native lifecycle events (superseded)

Claude Code registered hooks against PascalCase native events:

- `SessionStart` (source: `startup`, `resume`, `clear`, `compact`)
- `UserPromptSubmit`
- `PreToolUse` — can block/allow/modify (`hookSpecificOutput.permissionDecision`)
- `PostToolUse` — after a tool executes
- `PreCompact` (source: `manual`, `auto`)
- `SubagentStart` — spawn of a Task subagent (cannot block, inject only)
- `SubagentStop` — subagent completes
- `Stop` — agent produces a stop sequence (check `stop_hook_active` to prevent loops)
- `SessionEnd` — session terminates (reason)
- `PermissionRequest` — permission dialog suppression (requires matcher)
- `Notification` — engine notification (requires matcher)

### Native registration (superseded)

Hooks were registered in `.claude/settings.json` (generated from the manifest today):

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [{ "type": "command", "command": "$OPC_PROJECT_DIR/.claude/hooks/session-register.sh", "timeout": 10 }] }
    ],
    "PreToolUse": [
      { "matcher": "Read", "hooks": [{ "type": "command", "command": "$OPC_PROJECT_DIR/.claude/hooks/tldr-read-enforcer.sh", "timeout": 20 }] }
    ]
  }
}
```

### Native matcher pattern syntax (superseded)

| Pattern | Example |
|---------|---------|
| `Bash` | Exact match |
| `Edit\|Write` | OR operator |
| `Read.*` | Regex |
| `mcp__.*__write.*` | MCP tools |
| `*` | Wildcard |

Case-sensitive. Matcher-requiring events: `PreToolUse`, `PostToolUse`, `PermissionRequest`,
`Notification` (optional), `SessionStart` (optional), `PreCompact` (optional). Ordering: hooks run in
listed order; for `PreToolUse`, a `deny` skips subsequent hooks.

### Native hook types (superseded)

- **Command hooks** (`"type": "command"`): determininstic scripts.
- **Prompt hooks** (`"type": "prompt"`): LLM (Haiku) context-aware decisions — `approve`/`block`
  decision with `reason`, `continue`, `stopReason`, `systemMessage`.

### Working with MCP tools (still applicable)

MCP tools follow `mcp__<server>__<tool>` naming — the matcher conventions remain useful for
tool-pattern hooks under the neutral model.

### Native input/output schemas (superseded)

Input schema varied by event (`SessionStartInput`, `PreToolUseInput`, `UserPromptSubmitInput`, …);
output used `HookOutput { result, message, hookSpecificOutput }`. See the neutral payload contract in
the main body above for the current shape.
