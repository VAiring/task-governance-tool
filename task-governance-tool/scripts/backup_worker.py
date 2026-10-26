#!/usr/bin/env python3
"""Internal one-shot backup child; not a public CLI command."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ENTRY = Path(__file__).resolve()
sys.path.insert(0, str(ENTRY.parent))

from task_governance_tool.async_backup import worker_main

if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(2)
    raise SystemExit(worker_main(Path(sys.argv[1]), sys.argv[2], skill_root=ENTRY.parent.parent))
