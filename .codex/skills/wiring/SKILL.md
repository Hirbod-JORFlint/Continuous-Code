---
name: wiring
description: Wiring Verification
user-invocable: false
---

# Wiring Verification

When building infrastructure components, ensure they're actually invoked in the execution path.

## Pattern

Every module needs a clear entry point. Dead code is worse than no code - it creates maintenance burden and false confidence.

## The Four-Step Wiring Check

Before marking infrastructure "done", verify:

1. **Entry Point Exists**: How does user action trigger this code?
2. **Call Graph Traced**: Can you follow the path from entry to execution?
3. **Integration Tested**: Does an end-to-end test exercise this path?
4. **No Dead Code**: Is every built component actually reachable?

## DO

### Verify Entry Points

```bash
# Hook registered in manifest?
grep -r "orchestration" harness/lifecycle/hooks.toml

# Skill activated?
grep -r "skill-name" harness/skills/skill-rules.json

# Script executable?
ls -la scripts/orchestrate.py

# Module imported?
grep -r "from orchestration_layer import" .
```

### Trace Call Graphs

```python
# Entry point (hook) — declared in manifest
harness/lifecycle/hooks.toml
  ↓
# Re-homed Python handler via canonical launcher
uv run $HOME/.opc/hooks/hook_launcher.py <name>
  ↓
# Handler calls Python script
spawn('scripts/orchestrate.py')
  ↓
# Script imports module
from orchestration_layer import dispatch
  ↓
# Module executes
dispatch(agent_type, task)
```

### Test End-to-End

```bash
# Don't just unit test the module
pytest tests/unit/orchestration_layer_test.py  # NOT ENOUGH

# Test the full invocation path (payload → handler → script)
echo '{"project_dir": "/path/to/project", "matcher": "Task"}' | \
  uv run $HOME/.opc/hooks/hook_launcher.py pre-tool-use  # VERIFY THIS WORKS
```

### Document Wiring

```markdown
## Wiring

- **Entry Point**: pre_tool_use hook on Task tool
- **Registration**: `harness/lifecycle/hooks.toml` (pre_tool_use section)
- **Call Path**: manifest → hook_launcher.py → scripts/orchestrate.py → orchestration_layer.py
- **Test**: `tests/integration/task_orchestration_test.py`
```

## DON'T

### Build Without Wiring

```python
# BAD: Created orchestration_layer.py with 500 lines
# But nothing imports it or calls it
# Result: Dead code, wasted effort

# GOOD: Start with minimal wiring, then expand
# 1. Create hook (10 lines)
# 2. Test hook fires
# 3. Add script (20 lines)
# 4. Test script executes
# 5. Add module logic (iterate)
```

### Create Parallel Routing

```python
# BAD: Agent router has dispatch logic
# AND skill-rules.json has agent selection logic
# AND hooks have agent filtering logic
# Result: Three places to update, routing conflicts

# GOOD: Single source of truth for routing
# skill-rules.json activates skill → skill calls router → router dispatches
```

### Assume Imports Work

```python
# BAD: Assume because you wrote the code, it's imported
from orchestration_layer import dispatch  # Does this path exist?

# GOOD: Verify imports at integration test time
uv run python -c "from orchestration_layer import dispatch; print('OK')"
```

### Skip Integration Tests

```bash
# BAD: Only unit test
pytest tests/unit/  # All pass, but nothing works end-to-end

# GOOD: Integration test the wiring
pytest tests/integration/  # Verify full call path
```

## Common Wiring Gaps

### Hook Not Registered

```toml
# harness/lifecycle/hooks.toml - handler exists but no [hooks.*] entry references it
[[hooks.pre_tool_use]]
# ...orchestration entry MISSING - your handler never fires
```

**Fix**: Add the hook to the manifest:
```toml
[[hooks.pre_tool_use]]
id = "orchestration"
description = "Dispatch Task-tool work to the agent router"
command = "uv run $HOME/.opc/hooks/hook_launcher.py orchestration"
matcher = "Task"
```
Then regenerate driver configs (`gen_opencode.py`, `gen_codex.py`, `gen_cline.py`).

### Script Not Executable

```bash
# Script exists but can't execute
-rw-r--r-- scripts/orchestrate.py

# Fix: Make executable
chmod +x scripts/orchestrate.py
```

### Module Not Importable

```python
# Script tries to import but path is wrong
from orchestration_layer import dispatch
# ModuleNotFoundError

# Fix: Add to Python path or use proper package structure
sys.path.insert(0, str(Path(__file__).parent.parent))
```

### Router Has No Dispatch Path

```python
# BAD: Router has beautiful mapping
AGENT_MAP = {
    "implement": ImplementAgent,
    "research": ResearchAgent,
    # ... 18 agent types
}

# But no dispatch function uses the map
def route(task):
    return "general-purpose"  # Hardcoded! Map is dead code

# GOOD: Dispatch actually uses the map
def route(task):
    agent_type = classify(task)
    return AGENT_MAP[agent_type]
```

## Wiring Checklist

Before marking infrastructure "complete":

- [ ] Entry point identified and tested (hook/skill/CLI)
- [ ] Call graph documented (entry → module execution)
- [ ] Integration test exercises full path
- [ ] No orphaned modules (everything imported/called)
- [ ] Registration complete (manifest: `harness/lifecycle/hooks.toml` / skill-rules.json)
- [ ] Permissions correct (scripts executable)
- [ ] Import paths verified (manual import test passes)

## Real-World Examples

### Example 1: DAG Orchestration (This Session)

**What was built:**
- `opc/orchestration/orchestration_layer.py` (500+ lines)
- `opc/orchestration/dag/` (DAG builder, validator, executor)
- 18 agent type definitions
- Sophisticated routing logic

**Wiring gap:**
- No hook calls orchestration_layer.py
- No script imports the DAG modules
- Agent routing returns hardcoded "general-purpose"
- Result: 100% dead code

**Fix:**
1. Create PreToolUse hook for Task tool
2. Hook calls `scripts/orchestrate.py`
3. Script imports and calls `orchestration_layer.dispatch()`
4. Dispatch uses AGENT_MAP to route to actual agents
5. Integration test: Submit Task → verify correct agent type used

### Example 2: Artifact Index (Previous Session)

**What was built:**
- SQLite database schema
- Indexing logic
- Query functions

**Wiring gap:**
- No hook triggered indexing
- Files created but never indexed

**Fix:**
1. PostToolUse hook on Write tool
2. Hook calls indexing script immediately
3. Integration test: Write file → verify indexed

## Detection Strategy

### Grep for Orphans

```bash
# Find Python modules
find . -name "*.py" -type f

# Check if each is imported
for file in $(find . -name "*.py"); do
  module=$(basename $file .py)
  grep -r "from.*$module import\|import.*$module" . || echo "ORPHAN: $file"
done
```

### Check Hook Registration

```bash
# List all hooks declared in the manifest
grep -oE '^\[\[hooks\.[a-z_]+\]\]' harness/lifecycle/hooks.toml | sort -u

# Check handlers exist for each declared id
for id in $(grep -oE '^id = "[a-z0-9-]+"' harness/lifecycle/hooks.toml | cut -d'"' -f2); do
  handler=$(grep -A5 "id = \"$id\"" harness/lifecycle/hooks.toml | grep -oE 'hook_launcher.py [a-z0-9-]+' | awk '{print $2}')
  if [ -n "$handler" ] && [ ! -f "harness/lifecycle/hooks/$handler.py" ]; then
    echo "MISSING HANDLER: $id -> $handler.py"
  fi
done
```

### Verify Script Execution

```bash
# Find all Python scripts
find scripts/ -name "*.py"

# Test each can be imported
for script in $(find scripts/ -name "*.py"); do
  uv run python -c "import sys; sys.path.insert(0, 'scripts'); import $(basename $script .py)" 2>/dev/null || echo "IMPORT FAIL: $script"
done
```

## Source

- This session: DAG orchestration wiring gap - 500+ lines of dead code discovered
- Previous sessions: Artifact Index, LMStudio integration - wiring added after initial build
