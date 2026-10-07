#!/usr/bin/env python3
"""Project review-wait MCP entry point; discovery performs no operations."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_governance_tool.review_wait_runtime.project_server import main

if __name__ == "__main__":
    raise SystemExit(main())
