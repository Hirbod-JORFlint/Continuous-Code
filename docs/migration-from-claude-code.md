# Migration from Claude Code

> Harness-neutral "Continuous Code" engine. **Decision log** for the migration from a
> Claude-Code-only setup to one that targets opencode, codex, cline (and legacy claude until
> step 11), driven from the canonical `harness/` tree.
>
> Legend: 🏗 = structural/planning, 🧭 = decision, ✅ = done, ⏳ = pending.

## Objective

Keep every capability. Drop nothing. Replace the Claude Code-only coupling with a
harness-neutral contract, and produce per-harness configs (opencode / codex / cline) from a
single canonical source of truth under `harness/`.

The plan is tracked step-by-step in `instructions.md` (gitignored, working log). This document
records the *why* behind the notable decisions.

## 🏗 Core architecture decisions

- **Single neutral tree drives every harness.** All skills, agents, rules, MCP servers, hook
  manifest and harness specs live under `harness/`. Each driver (`opc/scripts/harness/gen_*`)
  renders that tree into a target harness's native config. Change once in `harness/`, re-run the
  generator, all harnesses stay in sync.
- **`~/.opc` is the harness-neutral config/state root** (was `~/.claude`). Runtime state
  (memory daemon, embedded postgres, recall cache, personalization) re-homes to `~/.opc`,
  honoring `$OPC_CONFIG_DIR`. The `.claude` legacy root is kept only as a shim until step 11.
- **Env contract is neutral.** `CLAUDE_*` variable names were renamed to `OPC_*`; the
  coordination database URL became `DATABASE_URL`. Canonical accessor module:
  `opc/src/runtime/env.py`.

## 🧭 Notable decisions

- **Skills: migrate, don't delete.** 108 live skills moved to `harness/skills`; the generator
  only emits directories that carry a `SKILL.md` with a valid slug frontmatter name. Archived
  and scratch skills are not emitted. Skill `name:` frontmatter + `cat` paths were translated to
  the canonical `harness/skills/<name>/SKILL.md` location (step 8).
- **Hook lifecycle is a neutral manifest.** `harness/lifecycle/hooks.toml` declares the wiring;
  each harness surfaces it through its own mechanism (opencode plugin, codex config, cline
  executable hooks). The Claude-Code-native registration (`.claude/settings.json` +
  `SessionStart`/`UserPromptSubmit`/`PreToolUse`/... event names) is superseded and is slated
  for removal at step 11.
- **Hook install root is `~/.opc/hooks`** (junction to `harness/lifecycle/hooks`), not
  `~/.claude/hooks`.
- **Model / auth are pinned at generation time, not embedded in the shared tree.** The wizard
  (step 9) prompts once for a target model and injects it per-harness; codex trust level and
  login are separate user steps.
- **MCP config is written from the registry.** `harness/mcp/registry.json` is the one source of
  truth for MCP servers. Each harness gets its own config; the runtime also gets a
  harness-neutral global config at `~/.opc/mcp_config.json` (step 9,
  `opc/scripts/harness/gen_opc.py`).
- **Cline MCP: follow the real resolver.** The docs in the Cline repo say `~/.cline/mcp.json`,
  but the authoritative CLI/SDK reads `$CLINE_MCP_SETTINGS_PATH` else
  `<CLINE_DATA_DIR|~/.cline>/data/settings/cline_mcp_settings.json`. We conform to the real
  resolver (cline/cline#11671, #7249).

## ✅ Done (stable milestones)

- Steps 1–9 (harness skeleton → installer wizard: re-home, model/auth pinning, user-level merges,
  neutral MCP CLI config). See `instructions.md` for the per-step commit log.
- Steps 10–14 (brand scrub → delete `.claude/` → parity audit → conformance → introspection
  adapters). See `instructions.md` Step 11–14 notes + commit log.
- Step 15: live driver smoke — opencode 1.18.30 on this machine. Found + fixed 3 driver/extractor
  conformance bugs (`session list` newest-first, headless-unsafe `agent list` dump, current Codex
  `response_item/reasoning` rollouts); codex/cline graceful absence verified; git identity
  re-authored to `Hirbod-JORFlint <hirbod_99@gmx.com>`. See `instructions.md` Step 15 Notes.
  (opencode transcripts are SQLite-backed in `~/.local/share/opencode/opencode.db`, not JSONL.)

## ⏳ Pending

- Step 16: knowledge-graph integration verification.

## Protection list (do not edit before their step)

- Step 11: `.claude/hooks/` live bridge, `.claude/backup/` snapshots, real model IDs,
  `harness/skills/agentica-claude-proxy/SKILL.md`, `harness/skills/archive/`, this doc's own
  brand-scrub scope.
- Legacy `Path.home()/".claude"` refs left intentionally: `opc/scripts/setup/update.py` L55,
  `opc/scripts/setup/claude_integration.py` L258, `harness/mcp/registry.json` qlty entry.
