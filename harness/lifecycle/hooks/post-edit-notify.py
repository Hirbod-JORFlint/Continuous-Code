#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Post-Edit Notification Hook (Python port).

Port of .claude/hooks/src/post-edit-notify.ts:
Notifies the TLDR daemon after file edits for dirty-count tracking.
Triggers automatic semantic re-indexing when threshold is reached.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import daemon_client  # noqa: E402
from _payload import read_stdin, tool_input, tool_name  # noqa: E402


def main() -> None:
    payload = read_stdin(timeout=2.0)

    # Only notify on successful Edit/Write operations
    name = tool_name(payload)
    if name != "Edit" and name != "Write":
        print("{}")
        return

    params = tool_input(payload)
    file_path = params.get("file_path") or params.get("filePath")
    if not file_path:
        print("{}")
        return

    # Notify daemon of file change
    try:
        project_dir = os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
        response = daemon_client.query_daemon_sync({"cmd": "notify", "file": file_path}, project_dir)

        # Track hook activity for flush threshold
        daemon_client.track_hook_activity_sync(
            "post-edit-notify",
            project_dir,
            True,
            {
                "edits_notified": 1,
                "reindexes_triggered": 1 if response.get("reindex_triggered") else 0,
            },
        )

        # If reindex was triggered, optionally inform user
        if response.get("reindex_triggered"):
            output: dict[str, Any] = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        f"[Semantic reindex triggered: {response.get('dirty_count')}/"
                        f"{response.get('threshold')} files changed]"
                    ),
                }
            }
            print(json.dumps(output))
            return
    except Exception:
        # Daemon not running - silently ignore
        pass

    print("{}")


if __name__ == "__main__":
    main()
