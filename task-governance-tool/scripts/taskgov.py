#!/usr/bin/env python3
"""Entry point for the task-governance-tool CLI."""

import sys
from pathlib import Path


sys.dont_write_bytecode = True
ENTRYPOINT_PATH = Path(__file__).absolute()
sys.path.insert(0, str(ENTRYPOINT_PATH.resolve().parent))

from task_governance_tool.cli import main, set_cli_script_path


if __name__ == "__main__":
    set_cli_script_path(ENTRYPOINT_PATH)
    raise SystemExit(main())
