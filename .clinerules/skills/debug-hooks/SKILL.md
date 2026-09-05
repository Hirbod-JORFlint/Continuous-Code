---
name: debug-hooks
description: Systematic hook debugging workflow. Use when hooks aren't firing, producing wrong output, or behaving unexpectedly.
allowed-tools: [Bash, Read, Grep]
---

# Debug Hooks

Systematic workflow for debugging lifecycle hooks.

## When to Use

- "Hook isn't firing"
- "Hook produces wrong output"
- "SessionEnd not working"
- "PostToolUse hook not triggering"
- "Why didn't my hook run?"

## Workflow

### 1. Check Outputs First (Observe Before Editing)

```bash
# Check project cache
ls -la $OPC_PROJECT_DIR/.opc/cache/

# Check specific outputs
ls -la $OPC_PROJECT_DIR/.opc/cache/learnings/

# Check for debug logs
tail $OPC_PROJECT_DIR/.opc/cache/*.log 2>/dev/null

# Also check global state (user-global, under ~/.opc)
ls -la ~/.opc/cache/ 2>/dev/null
```

### 2. Verify Hook Registration

The source of truth is the neutral manifest `harness/lifecycle/hooks.toml`:

```bash
# Which hooks are declared per event?
grep -A 8 "\[\[hooks.post_tool_use\]\]" $OPC_PROJECT_DIR/harness/lifecycle/hooks.toml

# List every declared handler id
grep -E '^id = "|hook_launcher.py ' $OPC_PROJECT_DIR/harness/lifecycle/hooks.toml
```

### 3. Check Hook Files Exist

```bash
# Re-homed Python handlers
ls -la $OPC_PROJECT_DIR/harness/lifecycle/hooks/*.py
```

### 4. Test Hook Manually

```bash
# Re-homed handler through the canonical launcher
echo '{"project_dir": "'"$OPC_PROJECT_DIR"'", "session_id": "test-123"}' | \
  uv run $HOME/.opc/hooks/hook_launcher.py post-tool-use-tracker
```

### 5. Check for Silent Failures

If using detached spawn with `stdio: 'ignore'`:

```typescript
// This pattern hides errors!
spawn(cmd, args, { detached: true, stdio: 'ignore' })
```

**Fix:** Add temporary logging:

```typescript
const logFile = fs.openSync('.opc/cache/debug.log', 'a');
spawn(cmd, args, {
  detached: true,
  stdio: ['ignore', logFile, logFile]  // capture stdout/stderr
});
```

### 6. Rebuild After Edits

Re-homed Python handlers run directly via the launcher — no build step is required. Edit the handler in
`harness/lifecycle/hooks/<name>.py` and regenerate driver configs only if you changed the manifest.

## Common Issues

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Hook never runs | Not registered in `harness/lifecycle/hooks.toml` | Add a `[[hooks.<event>]]` entry, regenerate driver configs |
| Handler not found | Launcher id ≠ handler filename | `hook_launcher.py <name>` requires `harness/lifecycle/hooks/<name>.py` |
| Hook runs but no output | Detached spawn hiding errors | Add logging, check manually |
| Wrong session ID | Using "most recent" query | Pass ID explicitly |
| Works locally, not in CI | Missing deps (uv run / node) | Check uv/npx availability |
| Runs twice | Registered in both manifest and driver config | Remove duplicate / regenerate driver config |

## Debug Checklist

- [ ] Outputs exist? (`ls -la .opc/cache/`)
- [ ] Registered? (`grep -A8 '\[\[hooks.<event>\]\]' harness/lifecycle/hooks.toml`)
- [ ] Handler exists? (`ls harness/lifecycle/hooks/*.py`)
- [ ] Launcher resolves? (`uv run $HOME/.opc/hooks/hook_launcher.py <name>`)
- [ ] Manual test works? (`echo '{}' | uv run $HOME/.opc/hooks/hook_launcher.py <name>`)
- [ ] Regenerated driver config? (run the three `gen_*.py` after manifest edits)
- [ ] No silent failures? (check for `stdio: 'ignore'`)

## Source Sessions

Derived from 10 sessions (83% of all learnings):
- a541f08a, 1c21e6c8, 6a9f2d7a, a8bd5cea, 2ca1a178, 657ce0b2, 3998f3a2, 2a829f12, 0b46cfd7, 862f6e2c