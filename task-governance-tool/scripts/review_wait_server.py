#!/usr/bin/env python3
"""Project review-wait MCP entry point; discovery performs no operations."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    scripts = Path(__file__).resolve().parent
    identity_source = scripts / "task_governance_tool/review_wait_runtime/runtime_identity.py"
    identity_bytes = identity_source.read_bytes()
    identity_namespace = {"__file__": str(identity_source)}
    exec(compile(identity_bytes, str(identity_source), "exec", dont_inherit=True), identity_namespace)
    try:
        entry_code = sys._getframe().f_code
    except Exception:
        entry_code = None
    runtime_identity = identity_namespace["RuntimeIdentity"].install(
        scripts, bootstrap_source=identity_bytes, entry_code=entry_code)
    from task_governance_tool.review_wait_runtime.project_server import main
except (Exception, KeyboardInterrupt):
    sys.stderr.write("Review-wait server unavailable.\n")
    raise SystemExit(1) from None

if __name__ == "__main__":
    raise SystemExit(main(runtime_identity=runtime_identity))
