---
name: hooks
description: Hook Development Rules
user-invocable: false
---

# Hook Development Rules

When working with lifecycle hooks:

## Source of Truth

- **Manifest**: `harness/lifecycle/hooks.toml` — declares event wiring per hook.
- **Canonical handlers dir**: `harness/lifecycle/hooks/` — Python handlers + launcher.
- **Generator flow**: `opc/scripts/harness/gen_{opencode,codex,cline}.py` translate the
  manifest into each driver's hook configuration; `gen_claude` emits the live
  (legacy) `settings.json` wiring. Do not edit generated configs directly.

## Pattern

Re-homed Python handlers run through the canonical launcher:

```bash
uv run $HOME/.opc/hooks/hook_launcher.py <handler-name>
```

(`~/.opc/hooks` is a junction → `harness/lifecycle/hooks` today; copied by the
Step 9 installer on other machines.)

## Handler Template (Python)

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

## Hook Events

- **session_start** - On session start/resume/compact
- **prompt_submit** - Before processing user prompt
- **pre_tool_use** - Before tool execution (can block)
- **post_tool_use** - After tool execution
- **pre_compact** - Before context compaction
- **stop** - When the agent finishes
- **session_stop** - When the session ends

## Testing

Run a handler directly by piping an event payload to the launcher:

```bash
echo '{"project_dir": "/path/to/project", "matcher": "resume"}' | \
  uv run $HOME/.opc/hooks/hook_launcher.py <handler-name>
```

## Registration

Add a handler to `harness/lifecycle/hooks/<name>.py`, then register it in
`harness/lifecycle/hooks.toml`:

```toml
[[hooks.<event>]]
id = "<name>"
description = "What this hook does"
command = "uv run $HOME/.opc/hooks/hook_launcher.py <name>"
timeout = 5
```

Then regenerate driver configs:

```bash
python opc/scripts/harness/gen_opencode.py
python opc/scripts/harness/gen_codex.py
python opc/scripts/harness/gen_cline.py
```

All hooks are dispatched through the Python launcher via `~/.opc/hooks/hook_launcher.py`; no legacy TS/bash transport is used. Extend hooks via the launcher path.