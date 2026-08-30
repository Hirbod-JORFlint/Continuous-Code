# Harness Skills

**Purpose:** Canonical, harness-neutral skill library for Continuous Code. Each skill is emitted to every harness (opencode / codex / cline) at generation time.

---

## Structure

This directory is the source of truth for skills:

```
harness/skills/
├── <skill-name>/
│   └── SKILL.md        # YAML frontmatter (name, description) + markdown instructions
├── archive/            # Archived skills, preserved but not emitted
├── math/               # Math category tree (solver knowledge), not emitted
└── _sandbox/           # Scratch space
```

## SKILL.md Format

- YAML frontmatter with `name` and `description`
- `name` must be kebab-case (`[a-z0-9-]+`) — the emitter uses it as the skill's id
- Markdown body with step-by-step instructions for the model to follow
- Optional `triggers`, `allowed-tools`, and metadata keys are preserved as-is

## How Skills Are Emitted

The generators (`opc/scripts/harness/gen_codex.py`, `gen_opencode.py`, `gen_cline.py`) copy every `harness/skills/*/<SKILL.md>` into the harness output:

- `.codex/skills/<name>/SKILL.md` (codex prompt skill)
- `.opencode/skills/<name>/SKILL.md` (opencode skill / subagent)
- `.clinerules/skills/<name>/SKILL.md` (cline prompt skill)

Directories without a `SKILL.md` at their root (`archive/`, `math/`, `_sandbox/`) are skipped.

## Skills vs Agents

- **Skills** (`harness/skills/`) — reusable procedure/domain knowledge, ask-before-use.
- **Agents** (`harness/agents/`) — long-running specialists with their own prompt and working agreements.

## Creating a Skill

1. Create `harness/skills/<kebab-name>/SKILL.md`
2. Frontmatter: `name: <kebab-name>` + `description: <one-line>`
3. Body: concise procedural instructions
4. Re-run the generators to emit the skill into each harness

## Naming Rules

- `name` and directory must match: lowercase letters, numbers, hyphens only
- No underscores, spaces, or uppercase

## Validation

- Developer-facing skills are validated during generation (invalid names are skipped and reported)
- Generator output is validated against each harness's schema (opencode config, cline rules)