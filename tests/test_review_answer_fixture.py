"""Explicit review fixture conditions and cleanup, including failed preparation."""
from contextlib import ExitStack, closing, contextmanager
from importlib import import_module
from pathlib import Path
import unittest
from unittest import mock

from tests import review_answer_fixture_support as support
from tests.test_m242_r3b_schema20_activation import _SCHEMA20_RUNTIME_PATCH_TARGETS
from task_governance_tool import storage


CONTRACT = support.ReviewContract(
    scope="Exercise isolated review preparation",
    acceptance="Preserve the declared Contract",
    authority_ref="user:isolated-fixture-check",
)


def runtime_versions():
    return {path: getattr(import_module(path.rsplit(".", 1)[0]), path.rsplit(".", 1)[1])
            for path in _SCHEMA20_RUNTIME_PATCH_TARGETS}


@contextmanager
def observe_directories():
    actual = support.tempfile.TemporaryDirectory
    paths = []

    def create(*args, **kwargs):
        directory = actual(*args, **kwargs)
        paths.append(Path(directory.name))
        return directory

    with mock.patch.object(support.tempfile, "TemporaryDirectory", side_effect=create):
        yield paths


class ReviewFixtureTests(unittest.TestCase):
    def test_declared_schema_and_contract_are_captured_before_review_target(self):
        before = runtime_versions()
        for version in (29, 30, 31, 32, 33, 34):
            for contract in (None, CONTRACT):
                with self.subTest(schema=version, contract=contract is not None):
                    with support.review_fixture(schema_version=version, contract=contract) as fixture:
                        directory = fixture.repo.parent
                        self.assertEqual(set(runtime_versions().values()), {version})
                        with closing(storage.connect_initialized_readonly(fixture.target)) as connection:
                            self.assertEqual(storage.current_schema_version(connection), version)
                            task = connection.execute("SELECT * FROM tasks WHERE task_id=?",
                                (fixture.task_id,)).fetchone()
                            self.assertEqual(task["status"], "in_progress")
                            self.assertEqual(task["current_contract_revision"], 1 if contract else 0)
                            self.assertEqual(task["review_target_acceptance_criterion_id"],
                                fixture.basis["acceptance_criterion_id"])
                        self.assertEqual(fixture.basis["contract_revision"], 1 if contract else 0)
                        self.assertEqual(fixture.basis["acceptance_criterion_id"] is not None, contract is not None)
                        self.assertEqual(fixture.packet["contract"]["revision"], 1 if contract else 0)
                    self.assertFalse(directory.exists())
                    self.assertEqual(runtime_versions(), before)

    def test_nested_fixtures_keep_distinct_state_and_restore_caller_after_body_failure(self):
        before = runtime_versions()
        with support.review_fixture(schema_version=29, contract=None) as outer:
            original = outer.db.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "fixture body interrupted"):
                with support.review_fixture(schema_version=31, contract=CONTRACT) as inner:
                    self.assertNotEqual(inner.repo, outer.repo)
                    self.assertNotEqual(inner.db, outer.db)
                    inner_directory = inner.repo.parent
                    self.assertEqual(set(runtime_versions().values()), {31})
                    raise RuntimeError("fixture body interrupted")
            self.assertFalse(inner_directory.exists())
            self.assertTrue(outer.db.exists())
            self.assertEqual(outer.db.read_bytes(), original)
            self.assertEqual(set(runtime_versions().values()), {29})
            self.assertEqual(outer.evidence()["gate"]["qualifying_independent_passes"], 0)
        self.assertFalse(outer.repo.parent.exists())
        self.assertEqual(runtime_versions(), before)

    def test_preparation_failure_cleans_state_and_all_runtime_versions(self):
        before = runtime_versions()
        failures = ((support, "initialize"), (support, "edit_contract"),
                    (support.schema29, "migrate"), (support.schema30, "migrate"),
                    (support.schema31, "migrate"), (support, "attach_existing"))
        for owner, name in failures:
            with self.subTest(stage=owner.__name__ + "." + name), observe_directories() as directories:
                with mock.patch.object(owner, name, side_effect=RuntimeError("fixture setup interrupted")):
                    with self.assertRaisesRegex(RuntimeError, "fixture setup interrupted"):
                        with support.review_fixture(schema_version=31, contract=CONTRACT):
                            self.fail("failed preparation cannot yield a fixture")
                self.assertTrue(directories)
                self.assertTrue(all(not path.exists() for path in directories))
                self.assertEqual(runtime_versions(), before)

    def test_attach_existing_preserves_rows_bytes_and_caller_owned_lifetime(self):
        with support.review_fixture(schema_version=30, contract=CONTRACT) as fixture:
            before = fixture.db.read_bytes()
            versions = runtime_versions()
            with ExitStack() as patches:
                for owner, name in ((support, "initialize"), (support.schema29, "migrate"),
                                    (support.schema30, "migrate"), (support.schema31, "migrate")):
                    patches.enter_context(mock.patch.object(owner, name,
                        side_effect=AssertionError("attachment may not initialize or migrate")))
                attached = support.attach_existing(db=fixture.db, repo=fixture.repo,
                    task_id=fixture.task_id, schema_version=30, target=fixture.target)
            self.assertEqual(attached.basis, fixture.basis)
            self.assertEqual(attached.task, fixture.task)
            self.assertEqual(attached.repo, fixture.repo)
            self.assertEqual(attached.db, fixture.db)
            self.assertEqual(fixture.db.read_bytes(), before)
            self.assertEqual(runtime_versions(), versions)
            with self.assertRaisesRegex(AssertionError, "declared version"):
                support.attach_existing(db=fixture.db, repo=fixture.repo,
                    task_id=fixture.task_id, schema_version=31, target=fixture.target)
            self.assertEqual(fixture.db.read_bytes(), before)
            self.assertTrue(fixture.db.exists())

    def test_invalid_preconditions_fail_without_acquiring_state(self):
        with mock.patch.object(support.tempfile, "TemporaryDirectory",
                side_effect=AssertionError("invalid input acquired temporary state")):
            for version in (28, 35, True, "30"):
                with self.subTest(version=version), self.assertRaises(ValueError):
                    with support.review_fixture(schema_version=version, contract=None):
                        self.fail("invalid schema accepted")
            with self.assertRaises(TypeError):
                with support.review_fixture(schema_version=31, contract="implicit"):
                    self.fail("implicit Contract accepted")

    def test_handoff_body_failure_restores_resolvers_and_owning_runtime(self):
        from tests.review_handoff_fixture_support import handoff_fixture
        from task_governance_tool import review_preparation, review_handoff_continuation

        versions = runtime_versions()
        original_preparation = review_preparation.resolve
        original_continuation = review_handoff_continuation.resolve
        with self.assertRaisesRegex(RuntimeError, "handoff interrupted"):
            with handoff_fixture(schema_version=30, contract=None) as fixture:
                directory = fixture.repo.parent
                fixture.ready()
                self.assertEqual(set(runtime_versions().values()), {30})
                self.assertIsNot(review_preparation.resolve, original_preparation)
                self.assertIsNot(review_handoff_continuation.resolve, original_continuation)
                raise RuntimeError("handoff interrupted")
        self.assertFalse(directory.exists())
        self.assertIs(review_preparation.resolve, original_preparation)
        self.assertIs(review_handoff_continuation.resolve, original_continuation)
        self.assertEqual(runtime_versions(), versions)


if __name__ == "__main__":
    unittest.main()
