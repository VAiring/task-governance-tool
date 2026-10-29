"""Reviewed static inventory, paired with native API differential tests.

This inventories attribute access as well as calls, so bound-method aliases
cannot disappear from the map. It is not a general Python type/dataflow proof.

Connection ownership behind the refreshed application entries:
- evidence_incremental_repository borrows publication's operation-local,
  query-only storage connection; selected closure helpers borrow that same
  observed connection, and publication metadata uses the initialized writer.
- review_continuation_repository borrows the caller's connection; native and
  collected registration reach it through storage.connect_initialized, and
  read-only preparation uses storage.connect_initialized_readonly.
- review_wait_control_observation opens sqlite_connection.connect_readonly.
- task_show_projection borrows CLI read_task_detail's initialized Task read,
  or task-context's retained connection from that same shared read boundary.
Those storage openers delegate to sqlite_connection, then connect_traced and
its default observed cursor. Injected test connections and logging suppression
retain their existing exceptions; an inventory entry does not enable logging.

The usage_reconciliation modules borrow the admitted numerical connection from
UsageRepository and the operation-local read-only core connection supplied by
usage_evidence_service/usage_locator_collection. Both retain the observed SQL
cursor. Separate stage transactions persist only numerical work; no direct
native SQL dispatch, connection bypass or core writer is added.
"""
import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'task-governance-tool/scripts/task_governance_tool'
API = {'execute', 'executemany', 'executescript', 'fetchone', 'fetchmany', 'fetchall'}
# These are fixed-target Git finalizers, not database APIs (all occurrences).
NON_SQL = {'review_handoff.py', 'review_wait_runtime/managed_direct.py'}
INFRASTRUCTURE = {'sqlite_sql_trace.py', 'sqlite_transaction_trace.py'}
FIXTURE = ROOT / 'tests/fixtures/sql_api_inventory.json'


def inventory():
    owners = {}
    escapes = []
    for path in sorted(PACKAGE.rglob('*.py')):
        name = path.relative_to(PACKAGE).as_posix()
        tree = ast.parse(path.read_text(encoding='utf-8'))
        counts = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in API:
                counts[node.attr] = counts.get(node.attr, 0) + 1
            if isinstance(node, ast.Call) and name not in INFRASTRUCTURE:
                # Explicit native dispatch and dynamic dispatch need review,
                # unlike bound aliases which retain the observed instance.
                if isinstance(node.func, ast.Attribute) and node.func.attr in API:
                    owner = ast.unparse(node.func.value)
                    if 'sqlite3.Cursor' in owner or 'sqlite3.Connection' in owner:
                        escapes.append([name, node.lineno, 'native_dispatch'])
                if isinstance(node.func, ast.Name) and node.func.id == 'getattr' and len(node.args) > 1:
                    key = node.args[1]
                    if isinstance(key, ast.Constant) and key.value in API:
                        escapes.append([name, node.lineno, 'dynamic_dispatch'])
        if counts and name not in INFRASTRUCTURE:
            owners[name] = {'kind': 'non_sql_finalizer' if name in NON_SQL else 'observed_connection',
                            'attributes': dict(sorted(counts.items()))}
    return {'owners': owners, 'dispatch_requiring_review': escapes}


class SQLInventoryTests(unittest.TestCase):
    def test_application_sql_inventory_has_no_unreviewed_paths(self):
        self.assertEqual(inventory(), json.loads(FIXTURE.read_text(encoding='utf-8')))
        self.assertEqual(inventory()['dispatch_requiring_review'], [])
