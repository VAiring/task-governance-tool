#!/usr/bin/env python3
"""Opt-in trusted local lifecycle adapter; not a taskgov CLI command."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).absolute().parent))

from task_governance_tool.usage_lifecycle import main

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    raise SystemExit(main(stdin=sys.stdin.buffer, stdout=sys.stdout,
                          skill_root=Path(__file__).absolute().parent.parent,
                          argv=sys.argv[1:]))
