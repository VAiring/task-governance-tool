"""Source compatibility entry for the packaged review-wait implementation."""

import importlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-governance-tool/scripts"))
_implementation = importlib.import_module("task_governance_tool.review_wait_runtime.review_wait_direct_repository")
if __name__ == "__main__":
    raise SystemExit(_implementation.main())
sys.modules[__name__] = _implementation
