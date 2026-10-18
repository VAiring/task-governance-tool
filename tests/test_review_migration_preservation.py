"""Supported review predecessors must preserve values and their row identities."""
from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager
from unittest import mock

from tests.test_review_session_migration import candidate_runtime
from task_governance_tool import storage
from task_governance_tool import schema_task_event_notes as schema28
from task_governance_tool import schema_review_continuation as schema29
from task_governance_tool import schema_review_answers as schema30
from task_governance_tool import schema_criterion_link_collector as schema31


SCHEMAS = {28: schema28, 29: schema29, 30: schema30, 31: schema31}


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _snapshot(connection, *, aliases=None):
    """Independent oracle includes schema, all columns, history and sequence."""
    aliases = aliases or {}
    objects = tuple(tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
    rowid_tables = {row[1] for row in connection.execute("PRAGMA main.table_list")
        if row[0] == "main" and row[2] == "table" and not row[4]}
    rows = {}
    for kind, name, _, _ in objects:
        if kind != "table":
            continue
        # An explicit None is used only for the deliberately inaccessible
        # identity fixture; ordinary tables always retain actual rowid pairs.
        identity = aliases.get(name, "rowid") if name in rowid_tables else None
        prefix = f"observed.{_quote(identity)}," if identity is not None else ""
        values = connection.execute(f"SELECT {prefix}observed.* FROM {_quote(name)} AS observed")
        columns = tuple(tuple(row) for row in connection.execute(f"PRAGMA table_xinfo({_quote(name)})"))
        rows[name] = (columns, tuple(sorted((tuple(row) for row in values), key=repr)))
    return objects, rows


class ReviewMigrationPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = {}
        for version in (28, 29):
            connection = sqlite3.connect(":memory:")
            cls.addClassCleanup(connection.close)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            with candidate_runtime(version):
                storage.apply_migrations(connection)
                SCHEMAS[version].validate_storage(connection)
            cls.sources[version] = connection

    @contextmanager
    def source(self, version):
        connection = sqlite3.connect(":memory:")
        try:
            self.sources[version].backup(connection)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("CREATE TABLE unrelated_keep(value BLOB)")
            connection.executemany("INSERT INTO unrelated_keep(rowid,value) VALUES (?,?)",
                ((1, b"first"), (2, b"second"), (3, b"third")))
            connection.execute("CREATE TABLE sqliteXkeep(value BLOB)")
            connection.execute("INSERT INTO sqliteXkeep(rowid,value) VALUES (41,X'00FF')")
            connection.execute("CREATE TABLE unrelated_auto(identity INTEGER PRIMARY KEY AUTOINCREMENT,value BLOB)")
            connection.execute("INSERT INTO unrelated_auto(identity,value) VALUES (71,X'00FF')")
            connection.execute("CREATE INDEX unrelated_keep_value ON unrelated_keep(value)")
            connection.execute("CREATE VIEW unrelated_keep_view AS SELECT value FROM unrelated_keep")
            connection.commit()
            with candidate_runtime(version + 1):
                yield connection
        finally:
            connection.close()

    def assert_idle(self, connection):
        self.assertFalse(connection.in_transaction)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0], 0)

    def assert_rollback(self, connection, version, *, aliases=None):
        SCHEMAS[version].validate_storage(connection)
        before = _snapshot(connection, aliases=aliases)
        with self.assertRaises(storage.StorageError):
            SCHEMAS[version + 1].migrate(connection)
        self.assertEqual(_snapshot(connection, aliases=aliases), before)
        self.assertEqual(storage.current_schema_version(connection), version)
        SCHEMAS[version].validate_storage(connection)
        self.assert_idle(connection)
        # sqlite_sequence preservation includes the next observable identity.
        cursor = connection.execute("INSERT INTO unrelated_auto(value) VALUES (X'3344')")
        self.assertEqual(cursor.lastrowid, 72)
        connection.rollback()

    def test_marker_mutations_of_values_identities_sequences_and_history_roll_back(self):
        mutations = {
            "value": "UPDATE unrelated_keep SET value=X'1122';",
            "rowid": "UPDATE unrelated_keep SET rowid=-rowid;",
            "reassociation": "UPDATE unrelated_keep SET rowid=-rowid; UPDATE unrelated_keep SET rowid=4+rowid;",
            "legal_sqlitex": "UPDATE sqliteXkeep SET value=X'1122';",
            "sequence": "UPDATE sqlite_sequence SET seq=900 WHERE name='unrelated_auto';",
            "old_history": "UPDATE schema_migrations SET applied_at='2001-01-01T00:00:00Z' WHERE version=1;",
            "new_history": "UPDATE schema_migrations SET applied_at='2001-01-01T00:00:00Z' WHERE version=NEW.version;",
        }
        for version in (28, 29):
            for label, statement in mutations.items():
                with self.subTest(version=version, mutation=label), self.source(version) as connection:
                    connection.execute("CREATE TRIGGER unrelated_marker_change AFTER INSERT ON schema_migrations "
                        f"WHEN NEW.version={version + 1} BEGIN {statement} END")
                    connection.commit()
                    self.assert_rollback(connection, version)

    def test_before_marker_mutations_roll_back(self):
        for version in (28, 29):
            for statement in (
                "UPDATE unrelated_keep SET rowid=-rowid",
                "UPDATE unrelated_keep SET value=X'1122'",
                "UPDATE sqliteXkeep SET value=X'1122'",
                "UPDATE sqlite_sequence SET seq=900 WHERE name='unrelated_auto'",
                "UPDATE schema_migrations SET applied_at='2001-01-01T00:00:00Z' WHERE version=1",
            ):
                with self.subTest(version=version, mutation=statement), self.source(version) as connection:
                    schema = SCHEMAS[version + 1]
                    apply = schema._apply_delta_locked

                    def changed(current):
                        apply(current)
                        current.execute(statement)

                    with mock.patch.object(schema, "_apply_delta_locked", side_effect=changed):
                        self.assert_rollback(connection, version)

    def test_injected_delta_failure_restores_every_object_row_and_flag(self):
        for version in (28, 29):
            with self.subTest(version=version), self.source(version) as connection:
                schema = SCHEMAS[version + 1]
                apply = schema._apply_delta_locked

                def interrupted(current):
                    apply(current)
                    raise sqlite3.OperationalError("isolated migration interruption")

                with mock.patch.object(schema, "_apply_delta_locked", side_effect=interrupted):
                    self.assert_rollback(connection, version)

    def test_identity_oracle_detects_reassociation_with_unchanged_ids_and_values(self):
        with self.source(28) as connection:
            before = _snapshot(connection)[1]["unrelated_keep"][1]
            connection.execute("UPDATE unrelated_keep SET rowid=-rowid")
            connection.execute("UPDATE unrelated_keep SET rowid=4+rowid")
            after = _snapshot(connection)[1]["unrelated_keep"][1]
            self.assertEqual(sorted(row[0] for row in before), sorted(row[0] for row in after))
            self.assertEqual(sorted(row[1] for row in before), sorted(row[1] for row in after))
            self.assertNotEqual(before, after)
            connection.rollback()

    def test_supported_identity_shapes_survive_the_full_upgrade_chain(self):
        with self.source(28) as connection:
            connection.execute('CREATE TABLE unrelated_wr(key TEXT PRIMARY KEY,"rowid" TEXT,'
                '"_rowid_" TEXT,"oid" TEXT,value BLOB) WITHOUT ROWID')
            connection.execute("INSERT INTO unrelated_wr(key,value) VALUES ('kept',X'00FF')")
            connection.execute('CREATE TABLE unrelated_generated(value BLOB,"rowid" INTEGER '
                'GENERATED ALWAYS AS (length(value)) STORED,"_rowid_" INTEGER '
                'GENERATED ALWAYS AS (42) VIRTUAL)')
            connection.execute("INSERT INTO unrelated_generated(oid,value) VALUES (41,X'00FF')")
            connection.execute('CREATE TABLE unrelated_ipk("rowid" TEXT,"_rowid_" TEXT,"oid" TEXT,'
                'identity INTEGER PRIMARY KEY,value BLOB)')
            connection.execute("INSERT INTO unrelated_ipk(identity,value) VALUES (-17,X'00FF')")
            connection.commit()
            aliases = {"unrelated_generated": "oid", "unrelated_ipk": "identity"}
            before = _snapshot(connection, aliases=aliases)
            history = before[1]["schema_migrations"][1]
            for version in (29, 30, 31):
                with candidate_runtime(version):
                    self.assertTrue(SCHEMAS[version].migrate(connection))
                    SCHEMAS[version].validate_storage(connection)
                    current = _snapshot(connection, aliases=aliases)
                    for table, (metadata, original_rows) in before[1].items():
                        if table == "schema_migrations":
                            continue
                        # Compare every original named column even when this
                        # migration legitimately appends a new schema column.
                        columns = [row[1] for row in metadata]
                        identity = None if table == "unrelated_wr" else aliases.get(table, "rowid")
                        projection = ([identity] if identity else []) + columns
                        observed = tuple(sorted((tuple(row) for row in connection.execute(
                            f"SELECT {','.join(_quote(name) for name in projection)} FROM {_quote(table)}")),
                            key=repr))
                        self.assertEqual(observed, original_rows, table)
                    self.assertTrue(set(history).issubset(current[1]["schema_migrations"][1]))
                    self.assert_idle(connection)
                    connection.execute("PRAGMA query_only=ON")
                    self.assertFalse(SCHEMAS[version].migrate(connection))
                    self.assertEqual(_snapshot(connection, aliases=aliases), current)
                    connection.execute("PRAGMA query_only=OFF")

    def test_inaccessible_identity_is_rejected_before_the_delta(self):
        for version in (28, 29):
            for primary_key in ("identity TEXT PRIMARY KEY", "identity INTEGER PRIMARY KEY DESC"):
                with self.subTest(version=version, primary_key=primary_key), self.source(version) as connection:
                    connection.execute('CREATE TABLE unrelated_inaccessible("rowid" TEXT,"_rowid_" TEXT,'
                        f'"oid" TEXT,{primary_key},value BLOB)')
                    connection.execute("INSERT INTO unrelated_inaccessible(identity,value) VALUES (17,X'00FF')")
                    connection.commit()
                    schema = SCHEMAS[version + 1]
                    with mock.patch.object(schema, "_apply_delta_locked", wraps=schema._apply_delta_locked) as apply:
                        self.assert_rollback(connection, version, aliases={"unrelated_inaccessible": None})
                        apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
