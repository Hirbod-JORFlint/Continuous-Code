#!/bin/bash
# SessionStart hook: Persist OPC_PROJECT_DIR to OPC_ENV_FILE
# This makes the project dir available to all subsequent bash commands
# Uses pwd since hooks run in the project directory

# Debug log
echo "[persist-project-dir] OPC_ENV_FILE=${OPC_ENV_FILE:-NOT_SET} PWD=$(pwd)" >> /tmp/claude-hook-debug.log

if [ -n "$OPC_ENV_FILE" ]; then
    echo "export OPC_PROJECT_DIR=\"$(pwd)\"" >> "$OPC_ENV_FILE"
    echo "[persist-project-dir] Wrote to $OPC_ENV_FILE" >> /tmp/claude-hook-debug.log
fi
