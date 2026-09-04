# Contributing to Continuous Claude

Thank you for your interest in contributing! This guide covers how to add skills, agents, hooks, and extend TLDR.

## Table of Contents

- [Development Setup](#development-setup)
- [Adding Skills](#adding-skills)
- [Creating Agents](#creating-agents)
- [Developing Hooks](#developing-hooks)
- [Extending TLDR](#extending-tldr)
- [Code Style](#code-style)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)

---

## Development Setup

```bash
# Clone (your fork) of the repository
cd Continuous-Code/opc

# Install Python dependencies
uv sync

# Verify installation
# For opencode:
opencode
# For codex:
codex login
# For cline:
cline --version
```

---

## Adding Skills

Skills are modular capabilities defined in `harness/skills/<skill-name>/SKILL.md`.

### Skill Structure

```
harness/skills/my-skill/
├── SKILL.md          # Main skill definition (required)
└── templates/        # Optional templates
```

### SKILL.md Format

```markdown
# my-skill

Short description of what this skill does.

## When to Use

- Trigger condition 1
- Trigger condition 2

## Workflow

1. Step one
2. Step two
3. Step three

## Commands

| Command | Description |
|---------|-------------|
| `/my-skill` | Main invocation |
| `/my-skill --option` | With options |

## Example

User: "do my-skill thing"
Claude: [executes skill workflow]
```

### Registering Skill Triggers

Add to `harness/skills/skill-rules.json`:

```json
{
  "skill": "my-skill",
  "keywords": ["my-skill", "specific trigger"],
  "intentPatterns": ["do.*my.*skill"],
  "priority": 50
}
```

### Quick Skill Creation

```
> /skill-developer
```

This interactive skill walks you through creating new skills.

---

## Creating Agents

Agents are specialized AI workers defined in `harness/agents/<agent-name>.md`.

### Agent Structure

```markdown
# Frontmatter
---
name: <name of agent>
description: <one line description of agent>
model: <preferred model: opus | sonnet | haiku>
tools: <list tools agent needs (optional): Read | Grep | Glob | etc...>
---

## Prompt

<system>
You are the agent-name agent for Continuous Claude.

Your job is to:
1. Specific task
2. Another task

## Constraints

- Constraint 1
- Constraint 2

## Output Format

Return results as:
[format description]
</system>
```

### Agent Types

| Type | Model | Use Case |
|------|-------|----------|
| **Orchestrator** | Opus | Coordinate multi-agent workflows |
| **Researcher** | Sonnet/Opus | Explore codebase or external sources |
| **Implementer** | Opus | Write/modify code |
| **Reviewer** | Opus | Analyze and critique |
| **Specialist** | Varies | Domain-specific tasks |

### Spawning Agents

Agents are spawned via the Task tool:

```typescript
Task({
  subagent_type: "my-agent",
  prompt: "Do the thing",
  description: "Doing the thing"
})
```

---

## Developing Hooks

Hooks intercept Claude Code at lifecycle points. Canonical manifest: `harness/lifecycle/hooks.toml`; implementations in `harness/lifecycle/hooks/`.

### Hook Types

| Lifecycle | When it runs |
|-----------|--------------|
| `SessionStart` | Session begins |
| `SessionEnd` | Session ends |
| `PreToolUse` | Before tool execution |
| `PostToolUse` | After tool execution |
| `UserPromptSubmit` | User sends a message |
| `PreCompact` | Before context compaction |
| `SubagentStart` | Subagent spawned |
| `SubagentStop` | Subagent completes |
| `Stop` | LLM generation stops |

### Shell Hook Template

```bash
#!/bin/bash
# harness/lifecycle/hooks/my-hook.sh

# Read input from stdin (JSON)
INPUT=$(cat)

# Parse relevant fields
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
CONTENT=$(echo "$INPUT" | jq -r '.tool_input.content // empty')

# Your logic here
if [[ "$TOOL_NAME" == "Read" ]]; then
    # Do something
    echo '{"result": "modified content"}'
else
    # Pass through
    echo "$INPUT"
fi
```

### Python Hook Template

```python
# harness/lifecycle/hooks/my-hook.py
"""My custom hook (Python, launched via hook_launcher.py)."""
import json
import sys


def main() -> None:
    """Read payload from stdin, process, write JSON to stdout."""
    payload = json.loads(sys.stdin.read())
    # Your logic here
    print(json.dumps({"result": "success"}))


if __name__ == "__main__":
    main()
```

### Registering Hooks

Add a handler to `harness/lifecycle/hooks.toml`:

```toml
[[hooks.pre_tool_use]]
id = "my-hook"
description = "My custom hook"
command = "bash $HOME/.opc/hooks/hook_launcher.py my-hook"
```

The three generators then emit the driver-native config: Claude Code `settings.json`, Codex `config.toml`, and opencode's auto-loaded plugin.

### Hook Development Workflow

```
> /hook-developer
```

Or debug existing hooks:

```
> /debug-hooks
```

---

## Extending TLDR

TLDR is the 5-layer code analysis system in `opc/packages/tldr-code/`.

### Architecture

```
tldr/
├── api.py              # Unified API (all layers)
├── ast_extractor.py    # L1: AST extraction
├── hybrid_extractor.py # Multi-language support
├── cross_file_calls.py # L2: Call graph
├── cfg_extractor.py    # L3: Control flow
├── dfg_extractor.py    # L4: Data flow
└── pdg_extractor.py    # L5: Program dependence
```

### Adding Language Support

1. Add tree-sitter grammar:
```bash
uv pip install tree-sitter-<language>
```

2. Update `hybrid_extractor.py`:
```python
LANGUAGE_CONFIGS = {
    'mylang': LanguageConfig(
        extension='.ml',
        tree_sitter_lang='mylang',
        function_patterns=['function_definition'],
        class_patterns=['class_definition']
    )
}
```

3. Add tests in `tests/test_mylang.py`

### Running TLDR Tests

```bash
cd opc/packages/tldr-code
source .venv/bin/activate
python -m pytest tests/ -v
```

---

## Code Style

### Python

- Use type hints
- Format with `ruff`
- Follow PEP 8

### TypeScript

- Use strict mode
- Format with Prettier
- Follow existing patterns

### Markdown

- Use CommonMark
- Keep lines under 100 chars
- Use tables for structured data

---

## Testing

### Running All Tests

```bash
# TLDR tests
cd opc/packages/tldr-code && pytest tests/ -v

# Hook tests (Python hooks in harness/lifecycle/hooks/)
cd ../harness/lifecycle/hooks && python -m py_compile *.py && python -m py_compile _lib/*.py
```

### Testing Skills

```bash
# Manual test in Claude
> /my-skill

# Check skill-rules.json triggers
> "trigger phrase that should activate my-skill"
```

### Testing Hooks

```bash
# Test hook directly
echo '{"tool_name": "Read", "tool_input": {"file_path": "test.py"}}' | harness/lifecycle/hooks/my-hook.sh
```

---

## Pull Request Process

1. **Fork** the repository
2. **Create branch**: `git checkout -b feature/my-feature`
3. **Make changes** following the guidelines above
4. **Test** your changes
5. **Commit** with clear message
6. **Push** to your fork
7. **Open PR** with:
   - Description of changes
   - Testing done
   - Any breaking changes

### PR Checklist

- [ ] Tests pass
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
- [ ] Follows code style

---

## Getting Help

- Open an issue for bugs or feature requests
- Use `/help` in Claude for usage questions
- Check existing skills/agents for patterns

Thank you for contributing!
