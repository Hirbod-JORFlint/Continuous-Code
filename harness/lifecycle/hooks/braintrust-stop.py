#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["httpx"]
# ///
"""Braintrust stop lifecycle hook wrapper (neutral, Step 11 re-home).

Thin wrapper that dispatches to the shared braintrust core module.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.argv = [sys.argv[0], "stop"]

from braintrust import main  # noqa: E402, I001

if __name__ == "__main__":
    main()
