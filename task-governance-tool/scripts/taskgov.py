#!/usr/bin/env python3
"""Entry point for the task-governance-tool CLI."""

import sys
from pathlib import Path


sys.dont_write_bytecode = True
ENTRYPOINT_PATH = Path(__file__).absolute()
if sys.argv[1:2] == ["--records-only"]:
    source_bootstrap = ENTRYPOINT_PATH.parent / "source_imports.py"
    source_namespace = {}
    exec(compile(source_bootstrap.read_bytes(), str(source_bootstrap), "exec"), source_namespace)
    source_namespace["install"](ENTRYPOINT_PATH.parent)
else:
    sys.path.insert(0, str(ENTRYPOINT_PATH.resolve().parent))

from task_governance_tool.cli import main, set_cli_script_path


if __name__ == "__main__":
    set_cli_script_path(ENTRYPOINT_PATH)
    raise SystemExit(main())
