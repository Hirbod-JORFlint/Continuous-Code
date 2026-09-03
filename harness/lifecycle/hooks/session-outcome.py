#!/usr/bin/env python3
"""SessionEnd hook: Surface latest handoff and outcome-marking guidance.

Neutral Python port of session-outcome.ts (Step 11, Phase B).
Re-homes the user-facing artifact_mark guidance path to the harness tree
(cd <project> && uv run python opc/scripts/core/artifact_mark.py).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from _payload import read_stdin, project_dir


def main() -> None:
    payload = read_stdin()
    project_root = Path(project_dir(payload, default=str(Path.cwd())))

    if payload.get("reason") == "other":
        print(json.dumps({"result": "continue"}))
        return

    db_path = project_root / ".opc" / "cache" / "artifact-index" / "context.db"
    if not db_path.exists():
        print(json.dumps({"result": "continue"}))
        return

    ledger_dir = project_root / "thoughts" / "ledgers"
    try:
        ledger_files = [
            f.name for f in ledger_dir.iterdir()
            if f.name.startswith("CONTINUITY-") and f.name.endswith(".md")
        ]
    except OSError:
        print(json.dumps({"result": "continue"}))
        return

    if not ledger_files:
        print(json.dumps({"result": "continue"}))
        return

    most_recent = max(ledger_files,
                      key=lambda n: (ledger_dir / n).stat().st_mtime)
    session_name = most_recent.replace("CONTINUITY-", "").replace(".md", "")

    handoff_dir = project_root / "thoughts" / "shared" / "handoffs" / session_name
    if not handoff_dir.is_dir():
        print(json.dumps({"result": "continue"}))
        return

    try:
        handoff_files = sorted(
            (f.name for f in handoff_dir.iterdir()
             if f.name.endswith(".md") and re.match(r"^\d{4}-\d{2}-\d{2}_", f.name)),
            reverse=True,
        )
    except OSError:
        print(json.dumps({"result": "continue"}))
        return

    if not handoff_files:
        print(json.dumps({"result": "continue"}))
        return

    handoff_name = handoff_files[0].replace(".md", "")
    db_rel = db_path.relative_to(project_root) if db_path.is_relative_to(project_root) else db_path

    message = f"""

─────────────────────────────────────────────────
Session ended: {session_name}
Latest handoff: {handoff_name}

To mark outcome and improve future sessions:

  cd {project_root} && uv run python opc/scripts/core/artifact_mark.py \\
    --handoff <handoff-id> \\
    --outcome SUCCEEDED|PARTIAL_PLUS|PARTIAL_MINUS|FAILED

To find handoff ID, query the database:

  sqlite3 {db_rel} \\
    "SELECT id, file_path FROM handoffs WHERE session_name='{session_name}' ORDER BY indexed_at DESC LIMIT 1"

Outcome meanings:
  SUCCEEDED      - Task completed successfully
  PARTIAL_PLUS   - Mostly done, minor issues remain
  PARTIAL_MINUS  - Some progress, major issues remain
  FAILED         - Task abandoned or blocked
─────────────────────────────────────────────────
"""

    print(json.dumps({"result": "continue", "message": message}))


if __name__ == "__main__":
    main()
