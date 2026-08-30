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
├── lifecycle/               hooks/plugins source (re-homed from .claude/hooks in later steps)
└── spec/                    this spec + event taxonomy
```

Generated, committed outputs (written by `opc harness gen`):

| Target | Generated artifact |
|---|---|
| Opencode | `opencode.json`, `.opencode/{agents,commands,skills,plugins}` |
| Codex | `.codex/config.toml`, `.codex/hooks.toml`, `.codex/AGENTS.md`, skills manifest |
| Cline | `.clinerules/*.md`, `cline-custom-instructions.md`, `cline_mcp_settings.json` |

User-level installs (merging, never clobbering): `~/.codex/skills/`, `~/.config/opencode/skills/`,
`~/.clinerules/`, optional `~/.codex/config.toml` `[mcp_servers.*]` merge.

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
| `session_start` | harness session begins | codex `session_start`, opencode plugin `session.created`, cline custom-instructions (read-only) |
| `prompt_submit` | user prompt is about to be processed | codex `userpromptsubmit`, opencode plugin `event`/`chat`-submit, cline n/a |
| `pre_tool_use` | tool invocation about to run (can block) | codex `pre_tool_use` (tool-filtered), opencode plugin `tool.execute.before`, cline n/a |
| `post_tool_use` | tool invocation finished (non-blocking) | codex `post_tool_use`, opencode plugin `tool.execute.after`, cline n/a |
| `pre_compact` | context compaction imminent (may persist state) | codex `before_compact`-style, opencode plugin `session.compacted`, cline n/a |
| `session_stop` | session ending (must save state/handoff) | codex `session_stop`, opencode plugin `session.closed`, cline n/a (custom instructions run at start) |
| `status_line` | footer status text (harness-specific sugar, optional) | codex `/statusline`, opencode plugin `status`, cline n/a |

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
cline `mcpServers`).

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

- **Opencode** — full parity: plugins (JS/TS) covering all neutral events, skills/agents/commands
  natively, provider-agnostic models. The only real re-platform (Python+TS hooks -> JS/TS plugins,
  keeping heavy logic in neutral Python invoked by thin plugin wrappers).
- **Codex** — full parity where its schema allows: `hooks.toml` covers the neutral set except where
  noted; skills/AGENTS.md native; subagents via `multi_agent`.
- **Cline** — **thin client**: no native hooks/subagents. Surface = `.clinerules/`, custom
  instructions, memory-bank bootstrap, MCP. Continuity/memory run through `cmdline`-driven
  `npx cline --json` (experimental driver). Not certified until Cline is installed on the box.

## 9. Spawn layer

All headless orchestration goes through `opc/scripts/core/spawn.py` + the driver registry.
Adapters for thinking-block extraction, continuity, and event observation consume
`run_prompt(json=True)` and never construct harness command lines in application code.