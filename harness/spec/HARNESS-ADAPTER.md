# HARNESS-ADAPTER

Neutral harness-adapter contract for **Continuous Code** (formerly Continuous Claude / OPC v3).
This spec replaces the Claude-Code-only hook CONFIG/docs. Everything a harness can do — spawn,
observe, hook, instruct — is expressed through this interface, never through the raw harness schema
in application code.

## 1. Repository layout contract

```
harness/
├── skills/                  canonical SKILL.md library (harness-neutral, one SKILL.md per skill dir)
├── agents/                  canonical agent definitions (neutral frontmatter, see §7)
├── rules/                   global rules injected into every harness
├── mcp/
│   ├── servers/             MCP server implementations (stdio, portable)
│   └── registry.json        master MCP registry (harness-neutral schema, §5)
├── lifecycle/
│   ├── hooks/               neutral python hooks + hook_launcher.py + _payload.py adapter
│   └── hooks.toml           neutral lifecycle-hook manifest (§3); generators emit native configs
└── spec/                    this spec + event taxonomy
```

Generated, committed outputs (written by `opc harness gen`):

| Target | Generated artifact |
|---|---|
| Opencode | `opencode.json`, `.opencode/{agents,commands,skills,plugins}` (hook bridge: `.opencode/plugins/opc-hooks.ts`) |
| Codex | `.codex/config.toml` (MCP `[mcp_servers.*]` + nested `[hooks]` MatcherGroups), `.codex/AGENTS.md`, repo-root `AGENTS.md` |
| Cline | `.clinerules/*.md`, `.clinerules/hooks/*` (real executable hooks, §3), `cline_mcp_settings.json` (install-source template, see below) |

User-level installs (merging, never clobbering): `~/.agents/skills/` (Codex's current location;
`~/.codex/skills/` is deprecated) + optional `~/.codex/config.toml` `[mcp_servers.*]` merge,
`~/.config/opencode/skills/`, and for Cline `~/.cline/skills/` plus CLI MCP `~/.cline/mcp.json`.
Note that Cline does NOT read a project-root `cline_mcp_settings.json` at runtime — the generated
file is an install source; the Step 9 wizard merges it into the real location (VS Code
`globalStorage/*/settings/cline_mcp_settings.json` for the IDE, `~/.cline/data/settings/` or
`~/.cline/mcp.json` for the CLI). Global Cline rules resolve from the OS `Documents/Cline/Rules`
dir. Codex auto-discovers the repo-root `AGENTS.md`; the `.codex/AGENTS.md` twin only loads when
Codex runs with `CODEX_HOME=$(pwd)/.codex`.

Neutral install root for re-homed hooks: `~/.opc/hooks` — a junction to
`harness/lifecycle/hooks` on the canonical machine (created in Step 7 verification), copied by the
Step 9 installer on other machines and registered as the hook launcher's `OPC_HARNESS_DIR`.

## 2. HarnessDriver protocol

Every harness is accessed through a driver implementing this interface. Application code imports
only the driver registry — never `codex`, `opencode`, or `cline` specifics.

```python
class HarnessDriver(Protocol):
    name: str                                  # "opencode" | "codex" | "cline"
    experimental: bool = False                 # True => not certified on this machine

    def installed(self) -> bool: ...

    def session_id(self) -> str: ...           # identity of the current/logged session

    def run_prompt(self, prompt: str, *, cwd: str, json: bool = True,
                   approval: str = "suggest") -> Iterator[OutputEvent]: ...

    def capture_output(self, cmd: list[str], *, cwd: str, timeout: int) -> str: ...
```

Output events are `{"type": "say"|"ask", "text": str, "reasoning": str|None}` — a flat, normalised
stream so the memory/continuity pipeline is driver-agnostic.

Registry: `OPC_DRIVER` env selects a driver. `auto` = first installed from
`["opencode", "codex", "cline"]`.

## 3. Neutral event model

Hooks/plugins are declared against this taxonomy. Harness translates to its native surfacing.

| Neutral event | Meaning | Parity |
|---|---|---|
| `session_start` | harness session begins | codex `SessionStart`; opencode plugin `session.created`; cline `TaskStart` |
| `prompt_submit` | user prompt is about to be processed | codex `UserPromptSubmit`; opencode NO clean equivalent (unmapped, skipped in the bridge); cline `UserPromptSubmit` |
| `pre_tool_use` | tool invocation about to run (can block) | codex `PreToolUse` (tool-filtered); opencode plugin `tool.execute.before`; cline `PreToolUse` |
| `post_tool_use` | tool invocation finished (non-blocking) | codex `PostToolUse`; opencode plugin `tool.execute.after`; cline `PostToolUse` |
| `pre_compact` | context compaction imminent (may persist state) | codex `PreCompact`; opencode plugin `experimental.session.compacting`; cline `PreCompact` |
| `session_stop` | session ending (must save state/handoff) | codex `SessionEnd`; opencode plugin `event` hook routing `session.idle` (approximation — there is no `session.closed`/stop event; fire-once-per-session debounce); cline n/a (`TaskComplete` maps to `stop`, not `session_stop`) |
| `status_line` | footer status text (harness-specific sugar, optional) | codex n/a, opencode plugin `status`, cline n/a |

Cline hooks are **real executable scripts** (since v3.36+), not model-executed markdown: each hook
reads one JSON object on stdin and returns JSON controlling the session (`cancel`,
`contextModification`). They live in `.clinerules/hooks/` (project, committed) or
`~/Documents/Cline/Hooks` (global). Windows: `HookType.ps1` is the only supported name; hook files
have no per-tool matcher (enable/disable is a UI toggle only). macOS/Linux: extensionless executable
named exactly `HookType` + `chmod +x`. The `stop` event maps to `TaskComplete`; `session_stop` has no
Cline equivalent and is not emitted there.

Event payload contract (JSON on stdin to the hook/plugin body):

```json
{
  "event": "pre_tool_use",
  "session_id": "…",
  "project_dir": "…",
  "tool_name": "edit",
  "tool_input": { },
  "context_pct": 42
}
```

## 4. Environment naming standard (neutral, no aliases)

| Old (Claude) | New | Scope |
|---|---|---|
| `CLAUDE_OPC_DIR` | `OPC_ROOT` | install root |
| `CLAUDE_SESSION_ID` | `OPC_SESSION_ID` | session identity |
| `CLAUDE_PROJECT_DIR` | `OPC_PROJECT_DIR` | working project |
| `CLAUDE_ENV_FILE` | `OPC_ENV_FILE` | env file |
| `CLAUDE_PPID` | `OPC_PPID` | parent process |
| `CLAUDE_CONFIG_DIR` | `OPC_CONFIG_DIR` | config/state root (`~/.opc`) |
| `CONTINUOUS_CLAUDE_DB_URL` | `DATABASE_URL` | postgres |
| n/a | `OPC_DRIVER` | driver selector (`auto\|opencode\|codex\|cline`) |

`OPC_ROOT` (or the machine's equivalent) hosts runtime state and points at the installed
`semver` manifest. No `CLAUDE_*` names may appear in code, hooks, skills, or docs.

## 5. MCP registry schema (`harness/mcp/registry.json`)

Harness-neutral; generators emit native formats (opencode `mcp`, codex `[mcp_servers.*]`,
cline `mcpServers`). Environment references in registry values are rewritten per driver at
generation: opencode `{env:VAR}`, cline `${env:VAR}`, codex `${VAR}` verbatim; `$HOME` is always
resolved to an absolute path (bare `$HOME` is not expanded by any of the three spawners).

```json
{
  "version": 1,
  "servers": {
    "git": {
      "command": "uvx",
      "args": ["mcp-server-git", "--include-root"],
      "env": [],
      "transport": "stdio",
      "location": "harness/mcp/servers/git"
    }
  }
}
```

## 6. Skill format

Canonical = one directory per skill with `SKILL.md` (YAML frontmatter: `name`, `description`,
optional `when`). This parses natively in Codex (`~/.codex/skills/`, project `agents/skills/`)
and Opencode (`.opencode/skills/`, global `~/.config/opencode/skills/`). Cline consumes skills
indirectly (see §8).

## 7. Agent format (neutral frontmatter)

Canonical agents use the intersection schema; the generator maps per harness.

```yaml
name: researcher          # Codex/Opencode use description+filename id
description: ...          # kept everywhere
tools: [read, edit, bash] # opencode == tools? permission changes shape only
model: auto               # per-harness model mapping (opencode provider-agnostic)
mode: normal              # opencode mode; mapped only there
```

## 8. Parity tiers

- **Opencode** — parity except `prompt_submit`/`session_stop` (no native events; the bridge maps
  `session_stop`→`session.idle` via the generic `event` hook — opencode has no top-level
  `session.created`/`session.idle` hook keys — with a per-session debounce, and skips
  `prompt_submit`): plugins (TS) covering all neutral events, skills/agents/commands natively,
  provider-agnostic models. The only real re-platform (Python+TS hooks -> TS plugin wrappers,
  keeping heavy logic in neutral Python invoked by thin plugin wrappers under
  `.opencode/plugins/opc-hooks.ts`).
- **Codex** — full parity where its schema allows: inline `[hooks]` in `.codex/config.toml` covers the
  neutral set except where noted; hook handlers are emitted as nested `MatcherGroup` entries
  (`{matcher, hooks:[{type,command,...}]}` — a flat entry deserializes to `hooks: []` and never
  runs); skills root `AGENTS.md` native (repo-root `AGENTS.md` plus a `.codex/AGENTS.md` twin);
  subagents not emitted (documented mechanism is `agents.<name>.config_file` / user
  `~/.codex/agents/`; wired at smoke in Step 13).
- **Cline** — **thin client + real hooks** (experimental): surface = `.clinerules/*.md` (every
  `.md`/`.txt` loaded, numeric prefixes fine), real executable hooks in `.clinerules/hooks/*` (§3),
  `cline_mcp_settings.json` (project MCP), and repo-root `AGENTS.md` (read natively). No config-file
  agents — Cline's "subagents" are built-in read-only research agents driven by the `use_subagents`
  tool. Continuity/memory run through the experimental Cline CLI driver (`cline --json -y`). Not
  certified until Cline is installed on the box.

## 9. Spawn layer

All headless orchestration goes through `opc/scripts/core/spawn.py` + the driver registry.
Adapters for thinking-block extraction, continuity, and event observation consume
`run_prompt(json=True)` and never construct harness command lines in application code.

## 10. Introspection & coordination

Continuity/event-observation adapters discover the current session, its transcript, and running
agents through driver-agnostic modules in the packaged runtime — never by constructing
driver-specific commands or reading driver paths ad hoc.

| Module | Role |
|---|---|
| `runtime.introspect` | `resolve_driver()` (OPC_DRIVER or auto), `current_session()` → `{driver, session_id, transcript_path}`, `resolve_transcript()` (per-driver path resolution), `running_agents()`, `available_agents()` (driver `list_agents()`), `list_sessions()`, `observe()` |
| `runtime.coordination` | Agent coordination store: PostgreSQL (`DATABASE_URL`, `continuous_claude`, `agents` table — same DB the hooks' `_lib/db_utils_pg.py` manages for `sessions`/`file_claims`) with append-only JSONL fallback (`registry.jsonl` under `OPC_RUNTIME_DIR`). Sync helpers `register_agent()` / `update_agent_status()` / `running_agents()` / `list_sessions()` return `False`/`[]` on DB unavailability — callers never need `try/except` |

**Transcript resolution by driver:**

| Driver | Session id | Transcript |
|---|---|---|
| opencode | `session list --format json` (last session) | best-effort probe of opencode storage roots for the session id (None on miss) |
| codex | newest `$CODEX_HOME/sessions/rollout-*.jsonl` (matches regex) | the rollout JSONL itself |
| cline | None (no reliable CLI source) | None (documented limitation) |

**Consumers (wired in Step 14):**

- `opc/scripts/observe_agents.py` — observer CLI: `--what session|agents|sessions|memory|blackboard|tasks|outputs|all`, memory queried via `archival_memory` on `DATABASE_URL` (sqlite `~/.opc/cache/memory.db` fallback).
- `opc/scripts/core/memory_daemon.py` — falls back to `introspect.resolve_transcript()` when no
  `~/.opc/projects/*/*.jsonl` transcript exists for the session.
- `opc/scripts/core/extract_thinking_blocks.py` — `--format anthropic|codex|opencode|auto`
  (auto = anthropic shape first, generic `thinking`/`reasoning` walker fallback).
- `opc/scripts/core/spawn.py` — registers spawned agents via `runtime.coordination.register_agent`
  (SQLite/legacy `scripts.agentica_patterns` superseded).