"""Test-only prerequisite check; never classify a product Setup failure."""

import os
from pathlib import Path
import tempfile


class RestoreEnvironmentUnavailable(RuntimeError):
    """The isolated test cannot exercise real hard-link publication."""


def require_restore_hardlinks(directory: Path) -> None:
    """Probe disposable closed files on the fixture's filesystem, then clean up.

    Call only with an owned temporary test directory, before removing its DB.
    Success establishes a prerequisite, not a successful Setup restore. Only
    an observed permission denial from os.link gets the environmental label.
    """
    with tempfile.TemporaryDirectory(prefix=".restore-link-probe-", dir=directory) as temporary:
        source = Path(temporary) / "source"
        destination = Path(temporary) / "link"
        source.write_bytes(b"restore prerequisite")
        try:
            os.link(source, destination)
        except PermissionError as exc:
            raise RestoreEnvironmentUnavailable(
                "restore_environment_unavailable: isolated hard-link probe denied "
                f"(errno={exc.errno}, winerror={getattr(exc, 'winerror', None)}); "
                "actual Setup restore is unverified. See README.md#setup-restore-test-environment."
            ) from exc
        if not source.samefile(destination):
            raise RuntimeError("hard-link probe did not create the same file")
