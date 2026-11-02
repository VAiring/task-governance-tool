"""Paired disposable DBs with the same selected Task and review evidence."""
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
import io
from itertools import count
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
from unittest import mock

from tests.m14_test_support import make_physical_install
from tests.registration_output_support import registration_task
from tests.review_test_helpers import seed_review_evidence_connection
from task_governance_tool import cli
from task_governance_tool.backup_metadata_repository import configure_project_maintenance
from task_governance_tool.state_resolver import resolve_project_state


def public_command(target, *args):
    """Enter through the installed CLI layout and measure maintenance separately."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = cli.main([*args, "--repo", str(target.project.canonical_repo), "--json"],
                        _maintenance_enabled=False)
    if code:
        raise AssertionError(stdout.getvalue() + stderr.getvalue())
    result = json.loads(stdout.getvalue())
    if not result["ok"] or result.get("warnings"):
        raise AssertionError(result)
    return result


def verified_task(target, title, *, selected=False):
    added = public_command(target, "task", "add", "--title", title, "--status", "in_progress",
                           "--review-tier", "2" if selected else "0", "--verification", "Fixture verification")
    task = registration_task(added)["task_id"]
    with closing(sqlite3.connect(target.db_path)) as connection:
        seed_review_evidence_connection(connection, task, repo_path=target.project.canonical_repo)
        connection.commit()
    public_command(target, "verification", "receipt", "add", task, "--result", "pass",
                   "--duration-ms", "25", "--scope-coverage", "full", "--expected-target-generation", "1")
    if not selected:
        public_command(target, "task", "complete", task, "--verification-complete",
                       "--review-complete", "--commit-not-required")
    return task


@contextmanager
def fixture_id_band(prefix):
    """Keep indexed range endings comparable while unrelated rows are added."""
    sequence = count(1)
    original = secrets.token_hex
    def token_hex(nbytes=None):
        return f'{prefix}{next(sequence):015x}' if nbytes == 8 else original(nbytes)
    with mock.patch.object(secrets, 'token_hex', token_hex):
        yield


@contextmanager
def fixed_completion_histories():
    with tempfile.TemporaryDirectory(prefix='taskgov-fixed-completion-') as directory:
        root = Path(directory)
        install = make_physical_install(root)
        setup = install.run("setup", "--json")
        if setup.returncode:
            raise AssertionError(setup.stdout + setup.stderr)
        target = resolve_project_state(skill_root=install.skill_root, repo=install.project_root,
                                       operation_local=True).target
        if target is None or not target.canonical_fixed:
            raise AssertionError("Expected an admitted canonical target")
        configure_project_maintenance(target, requested_interval_minutes=None, requested_generations=None,
                                      enabled_at="2026-10-25T00:00:00Z")
        other_session = {'CODEX_THREAD_ID': '9fd51b75-e959-46f7-a19e-51d094d27a44'}
        # Put real completed rows on both sides of the selected keys in both
        # copies. An indexed seek can otherwise take an extra VM instruction
        # simply because one copy exhausts its B-tree and the other does not.
        with mock.patch.object(cli, "cli_script_path", return_value=install.entrypoint):
            with mock.patch.dict(os.environ, other_session):
                for prefix in ('1', 'd'):
                    with fixture_id_band(prefix):
                        verified_task(target, f"Initial history {prefix}")
            with fixture_id_band('8'):
                task = verified_task(target, "Selected fixed Task", selected=True)

            def receipts(history):
                with closing(sqlite3.connect(target.db_path)) as connection:
                    count = connection.execute("SELECT COUNT(*) FROM verification_receipts").fetchone()[0]
                    rows = connection.execute("SELECT * FROM verification_receipts WHERE task_id=?", (task,)).fetchall()
                if count != history + 1 or len(rows) != 1:
                    raise AssertionError("Every fixture Task must have one manual Receipt")
                return rows[0]

            def histories():
                original = receipts(2)
                # All handles are closed. Restore the same baseline at the same
                # canonical path before adding history; keep both variants disposable.
                baseline = root / "baseline"
                shutil.copytree(install.fixed_root, baseline)
                yield 2, target
                install.fixed_root.rename(root / "measured-small")
                baseline.rename(install.fixed_root)
                with mock.patch.dict(os.environ, other_session):
                    for prefix, indexes in (('4', range(2, 8)), ('a', range(8, 14))):
                        with fixture_id_band(prefix):
                            for index in indexes:
                                verified_task(target, f"Unrelated history {index}")
                if receipts(14) != original:
                    raise AssertionError("Selected Receipt changed between histories")
                yield 14, target
            yield task, histories()
