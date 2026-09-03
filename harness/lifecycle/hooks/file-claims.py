#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""PreToolUse:Edit Hook - Check and claim files for conflict prevention (Python port).

Port of .claude/hooks/src/file-claims.ts:
  1. Checks if another session has claimed the file
  2. Warns if file is being edited by another session
  3. Claims the file for the current session

Part of the coordination layer architecture (Phase 1).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import session_id  # noqa: E402
from _lib import db_utils_pg  # noqa: E402
from _payload import read_stdin, tool_input, tool_name  # noqa: E402


def main() -> None:
    payload = read_stdin(timeout=2.0)

    # Only process Edit tool
    if tool_name(payload) != "Edit":
        print(json.dumps({"result": "continue"}))
        return

    # Extract file path from input
    params = tool_input(payload)
    file_path = params.get("file_path") or params.get("filePath")
    if not file_path:
        print(json.dumps({"result": "continue"}))
        return

    current_session_id = session_id.get_session_id()
    project = session_id.get_project()

    # Check if file is claimed by another session
    claim_check = db_utils_pg.check_file_claim(file_path, project, current_session_id)

    if claim_check.get("claimed"):
        # File is being edited by another session - warn but allow
        file_name = str(file_path).split("/")[-1] or file_path
        claimed_by = claim_check.get("claimedBy") or "unknown"
        output: dict[str, Any] = {
            "result": "continue",  # Allow edit, just warn
            "message": (
                f"\u26A0\uFE0F **File Conflict Warning**\n"
                f"`{file_name}` is being edited by Session {claimed_by}\n"
                f"Consider coordinating with the other session to avoid conflicts."
            ),
        }
    else:
        # Claim the file for this session
        db_utils_pg.claim_file(file_path, project, current_session_id)
        output = {"result": "continue"}

    print(json.dumps(output))


if __name__ == "__main__":
    main()
