"""Validate the required Actions jobs for one release-candidate attempt."""

from __future__ import annotations

import json
import os
import sys

from tools.test_lanes import CI_PYTHON_VERSIONS
from tools.windows_ci_shards import SHARDS


def verify_candidate_jobs(pages, *, sha: str, run_id: int, attempt: int) -> None:
    required = {
        "Repository test policy",
        "Platform checks (ubuntu-24.04, Python 3.12)",
        "Platform checks (macos-15, Python 3.12)",
    }
    for version in CI_PYTHON_VERSIONS:
        required.add(f"Check shard coverage (Python {version})")
        required.update(f"Windows shard {shard} (Python {version})" for shard in SHARDS)

    seen = set()
    for page in pages:
        for job in page["jobs"]:
            name = job["name"]
            if name not in required:
                continue
            if name in seen:
                raise ValueError(f"duplicate required job: {name}")
            if (job["head_sha"], job["run_id"], job["run_attempt"]) != (sha, run_id, attempt):
                raise ValueError(f"job belongs to a different candidate or attempt: {name}")
            if (job["status"], job["conclusion"]) != ("completed", "success"):
                raise ValueError(f"required job did not succeed: {name}")
            seen.add(name)
    if missing := required - seen:
        raise ValueError("missing required jobs: " + ", ".join(sorted(missing)))


def main() -> int:
    try:
        verify_candidate_jobs(
            json.load(sys.stdin),
            sha=os.environ["GITHUB_SHA"],
            run_id=int(os.environ["GITHUB_RUN_ID"]),
            attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        print(f"Release-candidate check failed: {exc}", file=sys.stderr)
        return 1
    print("Verified policy, six Windows shards, both coverage checks and both platform checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
