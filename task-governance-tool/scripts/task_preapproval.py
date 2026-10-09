#!/usr/bin/env python3
"""Optional host approval hook; proposal is read-only and never changes trust."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
source_bootstrap = Path(__file__).absolute().parent / "source_imports.py"
source_namespace = {}
exec(compile(source_bootstrap.read_bytes(), str(source_bootstrap), "exec"), source_namespace)
source_namespace["install"](source_bootstrap.parent)
from task_governance_tool.task_preapproval import main

if __name__ == "__main__":
    raise SystemExit(main())
