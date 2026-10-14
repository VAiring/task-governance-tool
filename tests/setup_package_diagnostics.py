"""Test-only observation of the package inspection used by one Setup call."""

from contextlib import contextmanager
import json
from pathlib import Path
from unittest import mock

from task_governance_tool import project_scope


class SetupPackageDiagnostic:
    def __init__(self, install):
        self.project_root = str(install.project_root)
        self.skill_root = str(install.skill_root)
        self.entrypoint = str(install.entrypoint)
        self.inspection_count = 0
        self.observation = None
        self.diagnostic_status = "not_observed"

    def message(self, *, expected, actual):
        # Only bounded package metadata from the actual inspection is retained.
        # Never inspect again after failure or retain package file contents.
        return json.dumps({
            "fixture": self.project_root,
            "package_root": self.skill_root,
            "entrypoint": self.entrypoint,
            "expected": expected,
            "actual": actual,
            "diagnostic_status": self.diagnostic_status,
            "inspection_count": self.inspection_count,
            "package_inspection": self.observation,
        }, ensure_ascii=True, sort_keys=True)


def _summary(status):
    data = status.to_data()
    return {key: data[key] for key in (
        "package_name", "package_version", "release_origin", "manifest_version",
        "status", "changed_core_count", "changed_core_paths",
        "changed_core_paths_truncated", "unknown_reasons",
    )}


@contextmanager
def observe_setup_package(install):
    """Capture the same result Setup consumes; leave its outcome unchanged.

    One context belongs to one invocation and physical fixture. Unrelated
    inspections still run normally and cannot replace this fixture's evidence.
    The last matching result is the one at the package admission boundary.
    """
    diagnostic = SetupPackageDiagnostic(install)
    inspect = project_scope.inspect_local_package

    def observed(skill_root, **kwargs):
        matches = Path(skill_root) == Path(install.skill_root)
        try:
            result = inspect(skill_root, **kwargs)
        except Exception:
            if matches:
                diagnostic.inspection_count += 1
                diagnostic.observation = None
                diagnostic.diagnostic_status = "inspection_raised"
            raise
        if matches:
            diagnostic.inspection_count += 1
            try:
                diagnostic.observation = _summary(result)
                diagnostic.diagnostic_status = "observed"
            except Exception:
                diagnostic.observation = None
                diagnostic.diagnostic_status = "capture_unavailable"
        return result

    with mock.patch.object(project_scope, "inspect_local_package", side_effect=observed):
        yield diagnostic
