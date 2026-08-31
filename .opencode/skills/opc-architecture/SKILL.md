---
name: opc-architecture
description: OPC Architecture Understanding
user-invocable: false
---

# OPC Architecture Understanding

OPC (Orchestrated Parallel Claude) is a harness-neutral orchestration layer. It
runs through whichever driver CLI is configured (opencode, codex, or cline via
the generated configs; the legacy Claude Code CLI via the live bridge).

## Core Concept

The driver CLI is the execution engine. OPC adds orchestration via:
- **Hooks** - Intercept driver events (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, etc.)
- **Skills** - Load prompts into the driver
- **Scripts** - Called by hooks/skills for coordination
- **Database** - Store state between driver instances

## Canonical Locations

- **Manifest**: `harness/lifecycle/hooks.toml` — event wiring source of truth
- **Hook handlers**: `harness/lifecycle/hooks/` — re-homed Python + launcher
- **Rules**: `harness/rules/*.md` — auto-injected into the driver context
- **Skills**: `harness/skills/*/SKILL.md` — copied by generators into driver skill dirs
- **Scripts**: `opc/scripts/` — Python called by hooks/skills
- **State**: `.opc/cache/` — session artifacts, agent outputs
- **Coordination DB**: PostgreSQL (`opc/docker-compose.yml`, schema in `opc/init-db.sql`)

## How Driver Configs Are Produced

```
harness/lifecycle/hooks.toml ──┐
harness/skills/*/SKILL.md ─────┼── gen_opencode.py → .opencode/ plugins + skills
harness/agents/*.md ──────────┤── gen_codex.py     → .codex/ agents
harness/rules/*.md ───────────┘── gen_cline.py     → .clinerules/
                              └── gen_claude (legacy bridge, Step 8/11)
```

Re-homed Python hooks run through the canonical launcher:
`uv run $HOME/.opc/hooks/hook_launcher.py <name>`.

## How Agents Work

When an agent is spawned via the Task tool:

1. Main driver instance (your terminal) runs a hook on the Task tool
2. Hook triggers the sub-agent runner (driver-native spawn, or legacy `claude -p`)
3. A NEW driver instance spawns as a child process
4. Child runs independently, reads/writes to the coordination DB
5. Parent tracks child via PID in the DB

```
$ <driver>                          ← Main instance (your terminal)
    ↓ Task tool triggers hook
    ↓ spawn sub-agent
        ├── research agent    ← Child agent 1
        ├── implement agent   ← Child agent 2
        └── test agent        ← Child agent 3
```

## What OPC IS NOT

- OPC is NOT a separate application
- OPC does NOT run without a driver CLI (opencode/codex/cline/legacy Claude)
- OPC does NOT intercept model API calls directly
- OPC does NOT modify a driver's internal behavior

## What OPC IS

- OPC IS hooks wired from `harness/lifecycle/hooks.toml` (generated per driver)
- OPC IS skills in `harness/skills/` (copied by generators)
- OPC IS scripts in `opc/scripts/` that hooks/skills call for coordination
- OPC IS a database backend for state across driver instances

## Key Files

```
harness/
├── lifecycle/hooks.toml   ← Neutral hook registration manifest (source of truth)
├── lifecycle/hooks/       ← Re-homed Python hook handlers + launcher
├── agents/                ← Agent definitions for generated driver configs
├── skills/                ← SKILL.md prompts (copied by generators)
└── rules/                 ← Always-on rules injected into driver context

opc/
├── scripts/               ← Python scripts called by hooks/skills
├── docker-compose.yml     ← PostgreSQL, Redis, PgBouncer
└── init-db.sql            ← Database schema
```

## Coordination Flow

1. User runs the driver CLI in a terminal
2. Driver loads hooks (generated config; Claude Code's settings.json is produced by gen_claude)
3. User asks to spawn a sub-agent
4. Agent uses the Task tool
5. PreToolUse hook fires, checks resources
6. Hook spawns the sub-agent runner as a child process
7. Hook stores PID in PostgreSQL
8. Child agent runs, writes output to `.opc/cache/agents/<id>/`
9. Child completes, broadcasts "done" to PostgreSQL
10. Parent checks DB, reads child's output file

## Remember

- Every "agent" is just another driver CLI invocation
- Hooks intercept events, they don't create new functionality
- All coordination happens via files and PostgreSQL
- The driver CLI is always the execution engine; harness files are the source of truth