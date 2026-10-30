"""Task-local change hints for a worker admitted by the normal resolver.

This repository returns only whether fresh admission is needed. It never
establishes Task eligibility or lends a snapshot/validation proof to effects.
"""

from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3

from .sqlite_connection import StorageError, connect_readonly
from .sqlite_file_access import file_reader
from .state_paths import StatePathError, inspect_physical_file
from .review_wait_runtime.review_wait_repository import ObservationBusy, RepositoryError


# Every subquery is bound to this Task; no history body or other Task is read.
# Open findings span its receipt generations because old unresolved blockers
# can still prevent the current completion gate.
_CONTROL_SQL = """
SELECT task.status, task.current_contract_revision,
       task.review_target_kind, task.review_target_value,
       task.review_target_base_revision, task.review_target_generation,
       task.review_target_artifact_manifest_id, task.review_target_authority_snapshot_id,
       task.review_target_acceptance_criterion_id, task.review_target_verification_criterion_id,
       task.review_tier, task.completion_commit_required, task.external_revision_approved,
       project.identity_scheme, project.canonical_path_hash, project.binding_generation,
       owner.execution_id, owner.generation, owner.state,
       owner.owner_session_id, owner.completion_session_id,
       (SELECT COUNT(*) FROM review_receipts AS receipt
         WHERE receipt.project_id=task.project_id AND receipt.task_id=task.task_id
           AND receipt.target_generation=task.review_target_generation),
       (SELECT COUNT(*) FROM review_receipts AS receipt
          CROSS JOIN review_findings AS finding
         WHERE receipt.project_id=task.project_id AND receipt.task_id=task.task_id
           AND finding.review_receipt_id=receipt.review_receipt_id
           AND finding.status='open' AND finding.severity IN ('high', 'medium'))
  FROM tasks AS task
  JOIN project_meta AS project ON project.project_id=task.project_id
  JOIN task_ownership AS owner ON owner.project_id=task.project_id AND owner.task_id=task.task_id
 WHERE task.project_id=? AND task.task_id=?
"""


def _busy(error):
    cause = error.__cause__ if isinstance(error, StorageError) else error
    code = getattr(cause, "sqlite_errorcode", None)
    return type(code) is int and code & 0xFF == sqlite3.SQLITE_BUSY


class ReviewWaitControlObservation:
    """Construct only with the canonical path after successful fresh admission.

    The first call returns True and establishes a change baseline. That result
    also requires the caller's fresh basis/result checks after capture; it must
    not be discarded merely because admission preceded capture. Busy reads
    leave the prior baseline intact and carry no statement about Task state.
    """

    def __init__(self, path, project_id, task_id):
        self.path = Path(path)
        if not self.path.is_absolute() or not project_id or not task_id:
            raise RepositoryError("state_path_invalid")
        self.project_id, self.task_id = project_id, task_id
        self._previous_hint = None
        self._previous_control = None

    def _hint(self):
        path, identity = inspect_physical_file(self.path, root=Path(self.path.anchor))
        with file_reader(path) as reader:
            opened = os.fstat(reader.descriptor)
            if (opened.st_dev, opened.st_ino) != (identity.device, identity.inode):
                raise RepositoryError("state_unreadable")
            header = reader.read(44)
        # Fixed metadata only. The change counter notices same-timestamp commits;
        # these bytes are not a header validator or a database-content hash.
        return (identity, header[24:28], header[40:44],
                tuple(os.path.lexists(str(path) + suffix) for suffix in ("-wal", "-shm")))

    def _read_control(self):
        with closing(connect_readonly(self.path, timeout=0)) as connection:
            rows = connection.execute(_CONTROL_SQL, (self.project_id, self.task_id)).fetchall()
            if len(rows) != 1:
                raise RepositoryError("state_unreadable")
            # Different SQLite scalar types must not compare equal (1 versus 1.0).
            return tuple((type(value), value) for value in rows[0])

    def __call__(self):
        try:
            before = self._hint()
            if before == self._previous_hint:
                return False
            if self._previous_hint is not None:
                old = self._previous_hint[0]
                new = before[0]
                if (old.device, old.inode) != (new.device, new.inode):
                    raise RepositoryError("state_unreadable")
            control = self._read_control()
            after = self._hint()
            if before != after:
                # Never attach a newer file hint to an older snapshot. Ask for
                # fresh admission now and inspect again on the next observation.
                return True
            changed = (self._previous_control is None or control != self._previous_control
                       or self._previous_hint[2] != before[2])
            self._previous_hint, self._previous_control = after, control
            return changed
        except (sqlite3.Error, StorageError) as error:
            if _busy(error):
                raise ObservationBusy() from None
            raise RepositoryError("state_unreadable") from None
        except (OSError, StatePathError):
            raise RepositoryError("state_unreadable") from None
