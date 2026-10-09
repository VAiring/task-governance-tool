#!/usr/bin/env python3
"""Caller-owned review handoff: prepare, save/confirm, submit via taskgov stdin."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
if sys.argv[1:2] == ["--records-only"]:
    source_bootstrap = Path(__file__).absolute().parent / "source_imports.py"
    source_namespace = {}
    exec(compile(source_bootstrap.read_bytes(), str(source_bootstrap), "exec"), source_namespace)
    source_namespace["install"](source_bootstrap.parent)
else:
    sys.path.insert(0, str(Path(__file__).absolute().parent))

from task_governance_tool.review_handoff import main

if __name__ == "__main__":
    raise SystemExit(main())
