"""Selected/pending identity closure; no process launch or live project state."""
from contextlib import closing
import sqlite3
import unittest

from task_governance_tool import storage
from task_governance_tool import verification_runner_repository as repository


class RunnerInventoryScopeTests(unittest.TestCase):
    def fixture(self, *, terminal=False):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        for name in ('resolutions', 'attempts', 'observations', 'sandbox_events'):
            connection.execute(f'CREATE TABLE verification_runner_{name}('
                'project_id, task_id, target_generation, '
                'verification_runner_resolution_id, verification_runner_attempt_id, '
                'verification_runner_observation_id, verification_runner_sandbox_event_id, terminal_observation_id)')
        connection.execute("INSERT INTO verification_runner_resolutions(project_id,task_id,target_generation,verification_runner_resolution_id) VALUES('project','task',1,'resolution')")
        connection.execute("INSERT INTO verification_runner_attempts(project_id,task_id,target_generation,verification_runner_resolution_id,verification_runner_attempt_id) VALUES('project','task',1,'resolution','attempt')")
        if terminal:
            connection.execute("INSERT INTO verification_runner_observations(project_id,task_id,target_generation,verification_runner_resolution_id,verification_runner_attempt_id,verification_runner_observation_id) VALUES('project','task',1,'resolution','attempt','observation')")
            connection.execute("INSERT INTO verification_runner_sandbox_events(project_id,task_id,target_generation,verification_runner_attempt_id,verification_runner_sandbox_event_id,terminal_observation_id) VALUES('project','task',1,'attempt','cleanup','observation')")
        return connection

    def test_complete_pending_terminal_and_restart_edges_are_admitted(self):
        for terminal in (False, True):
            connection = self.fixture(terminal=terminal)
            repository._validate_runner_inventory_edges(connection, 'project')
            if terminal:
                connection.execute('DELETE FROM verification_runner_observations')
                connection.execute('UPDATE verification_runner_sandbox_events SET terminal_observation_id=NULL')
                repository._validate_runner_inventory_edges(connection, 'project')

    def test_missing_or_conflicting_edges_never_prove_absence(self):
        mutations = (
            'DELETE FROM verification_runner_resolutions',
            'DELETE FROM verification_runner_attempts',
            'DELETE FROM verification_runner_observations',
            'DELETE FROM verification_runner_sandbox_events',
            'UPDATE verification_runner_sandbox_events SET terminal_observation_id=NULL',
            "UPDATE verification_runner_sandbox_events SET verification_runner_attempt_id='missing'",
            "UPDATE verification_runner_attempts SET verification_runner_resolution_id='missing'",
            "UPDATE verification_runner_observations SET verification_runner_resolution_id='missing'",
            'INSERT INTO verification_runner_attempts SELECT * FROM verification_runner_attempts',
            'INSERT INTO verification_runner_sandbox_events SELECT * FROM verification_runner_sandbox_events',
            'UPDATE verification_runner_attempts SET target_generation=1.5',
            "UPDATE verification_runner_attempts SET target_generation='1'",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), closing(self.fixture(terminal=True)) as connection:
                connection.execute(mutation)
                with self.assertRaises(storage.StorageError):
                    repository._validate_runner_inventory_edges(connection, 'project')

    def test_each_foreign_endpoint_is_seen_through_its_other_identity_arm(self):
        for table in ('resolutions', 'attempts', 'observations', 'sandbox_events'):
            for field, value in (('project_id', 'foreign'), ('task_id', 'foreign'), ('target_generation', 2)):
                with self.subTest(table=table, field=field), closing(self.fixture(terminal=True)) as connection:
                    connection.execute(f'UPDATE verification_runner_{table} SET {field}=?', (value,))
                    with self.assertRaises(storage.StorageError):
                        repository._validate_runner_inventory_edges(connection, 'project')
                    with self.assertRaises(storage.StorageError):
                        repository._selected_runner_rows(connection, ('project', 'task', 1))


if __name__ == '__main__':
    unittest.main()
