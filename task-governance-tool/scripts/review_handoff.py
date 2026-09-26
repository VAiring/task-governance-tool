#!/usr/bin/env python3
"""Caller-owned review handoff: prepare, save/confirm, submit via taskgov stdin."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).absolute().parent))

from task_governance_tool.review_handoff import main

if __name__ == "__main__":
    raise SystemExit(main())
