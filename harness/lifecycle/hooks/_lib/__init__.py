#!/usr/bin/env python3
"""Shared neutral Python library for harness lifecycle hooks (stdlib, 3.9+).

Mirrors the removed TypeScript `shared/` helpers (db-utils-pg.ts,
daemon-client.ts, session-id.ts, opc-path.ts) as pure-Python modules so the
re-homed hooks stay driver-neutral and have no runtime dependency beyond the
DS.

Modules:
  - session_id.py   : cross-process session id generation + persistence
  - db_utils_pg.py  : PostgreSQL coordination layer (asyncpg via opc env)
  - daemon_client.py: TLDR daemon query client (socket/TCP) + hook tracking
"""

from __future__ import annotations
