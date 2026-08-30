#!/bin/bash
set -e
cd "$OPC_PROJECT_DIR/.claude/hooks"
cat | npx tsx src/path-rules.ts
