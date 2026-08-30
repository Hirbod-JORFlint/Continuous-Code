#!/bin/bash
set -e
cd "$OPC_PROJECT_DIR/.claude/hooks"
cat | node dist/import-error-detector.mjs
