#!/usr/bin/env python3
"""SessionStart hook: Persist OPC_PROJECT_DIR to OPC_ENV_FILE.

Neutral Python port of the legacy persist-project-dir.sh (Step 11, Phase B).
Makes the project directory available to all subsequent bash commands by
appending an export to the configured env file.

Hooks run in the project directory, but we resolve via the payload/OPC_PROJECT_DIR
so the value is correct across drivers.
"""

from __future__ import annotations

import os

from _payload import read_stdin, project_dir


def main() -> None:
    payload = read_stdin()
    env_file = os.environ.get("OPC_ENV_FILE")
    if not env_file:
        return
    proj = project_dir(payload, default=os.getcwd())
    try:
        with open(env_file, "a", encoding="utf-8") as fh:
            fh.write(f'export OPC_PROJECT_DIR="{proj}"\n')
    except OSError:
        pass


if __name__ == "__main__":
    main()
