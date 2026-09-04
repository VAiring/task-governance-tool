import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.m214c_test_support import valid_stored_task_row


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "task-governance-tool"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
try:
    from task_governance_tool import tasks as tasks_module
    from task_governance_tool.tasks import (
        COMBINED_PRIVACY_PATTERN,
        PRIVACY_PATTERNS,
        TASK_VERIFICATION_INPUT_LIMIT,
        TEXT_LIMITS,
        TaskValidationError,
        _legacy_m19_7_stored_guard_value,
        validate_legacy_m19_7_stored_text,
        validate_event_summary,
        validate_stored_task_rows,
        validate_task_input,
    )
    from task_governance_tool.storage import (
        StorageError,
        stored_task_verification_limit,
    )
finally:
    sys.path.pop(0)


class TaskValidationTests(unittest.TestCase):
    def assert_validation_error(self, code, func, *args, field=None, **kwargs):
        with self.assertRaises(TaskValidationError) as caught:
            func(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        if field is not None:
            self.assertEqual(caught.exception.field, field)

    def assert_privacy_pattern_parity(self, value):
        original_match = any(
            pattern.search(value) for pattern in PRIVACY_PATTERNS
        )
        combined_match = COMBINED_PRIVACY_PATTERN.search(value) is not None
        self.assertEqual(combined_match, original_match)

    def test_valid_task_input_normalizes_review_tier_and_lane_order(self):
        validated = validate_task_input(
            title="  Add validation  ",
            kind="sequential",
            lane="  TG-M2  ",
            lane_order="10",
            priority="high",
            status="ready",
            review_tier="2",
            verification="python -m unittest",
            tags="task,validation",
        )

        self.assertEqual(validated["title"], "Add validation")
        self.assertEqual(validated["kind"], "sequential")
        self.assertEqual(validated["lane"], "TG-M2")
        self.assertEqual(validated["lane_order"], 10)
        self.assertEqual(validated["review_tier"], 2)

    def test_title_is_required(self):
        self.assert_validation_error("invalid_argument", validate_task_input, title="", field="title")
        self.assert_validation_error("invalid_argument", validate_task_input, title="   ", field="title")

    def test_enum_fields_reject_unknown_values(self):
        cases = (
            ("invalid_kind", "kind", "parallel"),
            ("invalid_priority", "priority", "soon"),
            ("invalid_status", "status", "waiting"),
        )
        for code, field, value in cases:
            with self.subTest(field=field):
                self.assert_validation_error(
                    code,
                    validate_task_input,
                    title="Task",
                    field=field,
                    **{field: value},
                )

    def test_review_tier_must_be_integer_0_1_or_2(self):
        for value in ("x", "1.5", -1, 3, True):
            with self.subTest(value=value):
                self.assert_validation_error(
                    "invalid_review_tier",
                    validate_task_input,
                    title="Task",
                    review_tier=value,
                    field="review_tier",
                )

    def test_lane_order_must_be_integer_when_present(self):
        for value in ("1.5", object(), False):
            with self.subTest(value=value):
                self.assert_validation_error(
                    "invalid_argument",
                    validate_task_input,
                    title="Task",
                    lane_order=value,
                    field="lane_order",
                )

    def test_lane_order_uses_sqlite_signed_64_bit_bounds(self):
        minimum = -(1 << 63)
        maximum = (1 << 63) - 1
        self.assertEqual(
            validate_task_input(title="Minimum", lane_order=str(minimum))["lane_order"],
            minimum,
        )
        self.assertEqual(
            validate_task_input(title="Maximum", lane_order=str(maximum))["lane_order"],
            maximum,
        )
        for value in (str(minimum - 1), str(maximum + 1), "9" * 5000):
            with self.subTest(value_length=len(value)):
                self.assert_validation_error(
                    "invalid_argument",
                    validate_task_input,
                    title="Out of range",
                    lane_order=value,
                    field="lane_order",
                )

    def test_blocked_status_requires_blocked_reason(self):
        self.assert_validation_error(
            "blocked_reason_required",
            validate_task_input,
            title="Task",
            status="blocked",
            field="blocked_reason",
        )
        validated = validate_task_input(
            title="Task",
            status="blocked",
            blocked_reason="Waiting for user decision",
        )
        self.assertEqual(validated["blocked_reason"], "Waiting for user decision")

    def test_initial_done_status_is_forbidden(self):
        self.assert_validation_error(
            "initial_done_forbidden",
            validate_task_input,
            title="Already complete",
            status="done",
            field="status",
        )

    def test_size_limits_are_enforced(self):
        cases = (
            ("title", TEXT_LIMITS["title"]),
            ("description", TEXT_LIMITS["description"]),
            ("tags", TEXT_LIMITS["tags"]),
            ("add_note", TEXT_LIMITS["add_note"]),
        )
        for field, limit in cases:
            with self.subTest(field=field):
                kwargs = {"title": "Task", field: "x" * (limit + 1)}
                if field == "title":
                    kwargs = {"title": "x" * (limit + 1)}
                self.assert_validation_error(
                    "invalid_argument",
                    validate_task_input,
                    field=field,
                    **kwargs,
                )

        self.assert_validation_error(
            "invalid_argument",
            validate_task_input,
            title="Task",
            verification="x" * (TASK_VERIFICATION_INPUT_LIMIT + 1),
            field="verification",
        )

        self.assert_validation_error(
            "invalid_argument",
            validate_event_summary,
            "x" * (TEXT_LIMITS["event_summary"] + 1),
            field="event_summary",
        )

    def test_size_limit_boundaries_are_accepted(self):
        validated = validate_task_input(
            title="x" * TEXT_LIMITS["title"],
            description="x" * TEXT_LIMITS["description"],
            verification="x" * TASK_VERIFICATION_INPUT_LIMIT,
            tags="x" * TEXT_LIMITS["tags"],
            add_note="x" * TEXT_LIMITS["add_note"],
        )

        self.assertEqual(len(validated["title"]), TEXT_LIMITS["title"])
        self.assertEqual(len(validated["description"]), TEXT_LIMITS["description"])
        self.assertEqual(
            len(validated["verification"]),
            TASK_VERIFICATION_INPUT_LIMIT,
        )
        self.assertEqual(len(validated["tags"]), TEXT_LIMITS["tags"])
        self.assertEqual(len(validated["add_note"]), TEXT_LIMITS["add_note"])
        self.assertEqual(len(validate_event_summary("x" * TEXT_LIMITS["event_summary"])), 1000)

    def test_privacy_error_takes_precedence_over_size_error(self):
        value = "token=secret " + ("x" * TEXT_LIMITS["add_note"])
        self.assert_privacy_pattern_parity(value)
        self.assert_validation_error(
            "privacy_rejected",
            validate_task_input,
            title="Task",
            add_note=value,
            field="add_note",
        )

        verification = "token=secret " + (
            "x" * TASK_VERIFICATION_INPUT_LIMIT
        )
        self.assert_privacy_pattern_parity(verification)
        self.assert_validation_error(
            "privacy_rejected",
            validate_task_input,
            title="Task",
            verification=verification,
            field="verification",
        )

    def test_v17_stored_verification_classifier_is_privacy_first(self):
        self.assertEqual(stored_task_verification_limit(17), 500)
        base = valid_stored_task_row()
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE task_contract_revisions (project_id, task_id, revision)"
        )
        for name, value, reason in (
            ("boundary", "x" * 500, None),
            ("capacity", "x" * 501, "capacity"),
            (
                "privacy_before_capacity",
                "token=stored-secret " + ("x" * 501),
                "privacy",
            ),
        ):
            with self.subTest(name=name):
                self.assert_privacy_pattern_parity(value)
                result = validate_stored_task_rows(
                    [valid_stored_task_row(verification=value)],
                    connection=connection,
                    source_schema_version=17,
                    expected_project_id=base["project_id"],
                    verification_rejection_is_local=True,
                )
                self.assertEqual(result.verification_rejection, reason)

        for local_reason, local_value in (
            ("capacity", "x" * 501),
            ("privacy", "token=stored-secret"),
        ):
            with self.subTest(
                local_reason=local_reason,
                later_value="malformed",
            ):
                self.assert_privacy_pattern_parity(local_value)
                with self.assertRaises(StorageError) as structural:
                    validate_stored_task_rows(
                        [
                            valid_stored_task_row(
                                task_id="a_local",
                                verification=local_value,
                            ),
                            valid_stored_task_row(
                                task_id="z_structural",
                                verification=sqlite3.Binary(b"not-text"),
                            ),
                        ],
                        connection=connection,
                        source_schema_version=17,
                        expected_project_id=base["project_id"],
                        verification_rejection_is_local=True,
                    )
                self.assertEqual(
                    structural.exception.code,
                    "project_state_unreadable",
                )

    def test_stored_privacy_success_cache_is_field_bound_local_and_bounded(self):
        base = valid_stored_task_row()
        rows = [
            valid_stored_task_row(
                task_id=f"tg_task_cache_{index:04d}",
                title=f"Safe repeated value {index:04d}",
                description=f"Safe repeated value {index:04d}",
            )
            for index in range(100)
        ]
        with mock.patch.object(
            tasks_module,
            "reject_private_or_raw_content",
            wraps=tasks_module.reject_private_or_raw_content,
        ) as privacy_check:
            validate_stored_task_rows(
                rows,
                source_schema_version=7,
                expected_project_id=base["project_id"],
            )
            first_call_count = privacy_check.call_count
            first_calls = [call.args for call in privacy_check.call_args_list]
            validate_stored_task_rows(
                rows,
                source_schema_version=7,
                expected_project_id=base["project_id"],
            )

        self.assertLessEqual(first_call_count, (3 * len(rows)) + 20)
        self.assertEqual(privacy_check.call_count, 2 * first_call_count)
        self.assertEqual(
            first_calls.count(("title", "Safe repeated value 0000")),
            1,
        )
        self.assertEqual(
            first_calls.count(("description", "Safe repeated value 0000")),
            1,
        )

    def test_stored_privacy_success_cache_never_caches_rejection(self):
        base = valid_stored_task_row()
        private_value = "token=stored-private-value"
        rows = [
            valid_stored_task_row(
                task_id=f"tg_task_private_cache_{index}",
                verification=private_value,
            )
            for index in range(2)
        ]
        with mock.patch.object(
            tasks_module,
            "reject_private_or_raw_content",
            wraps=tasks_module.reject_private_or_raw_content,
        ) as privacy_check:
            result = validate_stored_task_rows(
                rows,
                source_schema_version=7,
                expected_project_id=base["project_id"],
                verification_rejection_is_local=True,
            )

        self.assertEqual(result.verification_rejection, "privacy")
        self.assertEqual(
            [call.args for call in privacy_check.call_args_list].count(
                ("verification", private_value)
            ),
            2,
        )

    def test_event_summary_is_required(self):
        self.assert_validation_error(
            "invalid_argument",
            validate_event_summary,
            "",
            field="event_summary",
        )

    def test_privacy_and_raw_dump_patterns_are_rejected(self):
        patterns = (
            "Authorization: Bearer secret",
            "Authorization: Token abc123",
            "Authorization: ApiKey abc123",
            "Authorization: secret-value",
            "AUTHORIZATION=Basic dXNlcjpwYXNz",
            "AUTHORIZATION=secret-value",
            "X-Authorization: secret-value",
            "Proxy-Authorization=secret-value",
            "HTTP_AUTHORIZATION=credential-value",
            "PROXY_AUTHORIZATION=opaque-value",
            "client_authorization=secret-value",
            "dispatch_authorization=1",
            "`dispatch_authorization=1`",
            "M19.7 records dispatch_authorization=2 before dispatch.",
            '{"dispatch_authorization":1}',
            '{"dispatch_authorization":null}',
            '{"dispatch_authorization":"1"}',
            "dispatch_authorization=secret-value",
            "dispatch_authorization=0",
            "dispatch_authorization=01",
            "dispatch_authorization=1.0",
            "dispatch_authorization=1suffix",
            "x-dispatch_authorization=1",
            "object.dispatch_authorization=1",
            "Authorization=dispatch_authorization=1",
            "token=dispatch_authorization=1",
            "Authorization=operation_sequence=1",
            "token=operation_sequence=1",
            "operation_sequence=1 token=secret",
            "Authorization Basic dXNlcjpwYXNz",
            "Authorization Bearer=secret",
            "authorization basic=abc",
            "Basic dXNlcjpwYXNz",
            "Basic dXNlcjpwYXNz,",
            "Basic dXNlcjpwYXNz:",
            "Cookie: sessionid=secret",
            "cookie=sessionid=secret",
            "SESSION_COOKIE=secret",
            "credentials=secret",
            '{"api_key": "sk-test"}',
            '{"api key": "sk-test"}',
            '{"access_token": "abc"}',
            '{"password": "secret"}',
            '{"client secret": "abc123"}',
            '{"client secret key": "abc123"}',
            '{"Cookie": "sid=abc"}',
            '{"Authorization": "Basic dXNlcjpwYXNz"}',
            "Set-Cookie: sid=secret",
            "X-Api-Key: secret",
            "X-Auth-Token: secret",
            "api key=abc123",
            "secret key: abc123",
            "access key: abc123",
            "client secret key: abc123",
            "private key: abc123",
            "PRIVATE_KEY=abc123",
            "SSH_PRIVATE_KEY=abc123",
            "private-key: abc123",
            "private_key: abc123",
            "Bearer secret",
            "Bearer secret-token",
            "Bearer secret-token,",
            "Bearer secret-token:",
            "Bearer verysecrettoken",
            "Bearer abc123",
            "Bearer abc123:",
            "Bearer sk-test",
            "bearer=secret",
            "bearer: secret",
            "Basic=abc",
            "-----BEGIN PRIVATE KEY-----",
            "-----begin private key-----",
            "-----END PRIVATE KEY-----",
            "password=secret",
            "password: secret",
            "DB_PASSWORD=secret",
            "client_secret=abc",
            "CLIENT_SECRET: secret",
            "SECRET-KEY=secret",
            "secret-key: abc",
            "client-secret-key=abc",
            "SECRET_KEY_BASE=secret",
            "token=secret",
            "access_token=secret",
            "refresh_token=secret",
            "AWS_SECRET_ACCESS_KEY=secret",
            "api_key=secret",
            "OPENAI_API_KEY=secret",
            "Traceback (most recent call last)",
            "Environment:\nPATH=secret",
            "Environment:\nPATH: secret",
            "Environment:\nPath=C:\\Windows",
            "Environment\nPATH=secret",
            "Environment PATH=secret",
            "Environment PATH: secret",
            "Environment dump:\nPATH=secret",
            "Environment dump PATH=secret",
            "Environment variables:\nPATH=secret",
            "Environment variables:\nPath: C:\\Windows",
            "Environment variables\nPATH=secret",
            "Command output Environment variables:\nPATH=secret",
            "env:\nHOME=secret",
            "env:\nHOME: secret",
            "env PATH=secret",
            "ENV HOME: secret",
            "ENV Path: C:\\Windows",
            "PATH=secret\nHOME=secret",
            "PATH: secret\nHOME: secret",
            "Path: C:\\Windows\nTemp: C:\\Temp",
            "ENV VARS:\nHOME=secret",
            "ENV VARS HOME=secret",
            "env\nHOME=secret",
            "ENV DUMP:\nHOME=secret",
            "ENV DUMP\nHOME=secret",
            "Command output ENV DUMP:\nHOME=secret",
            "Command output:\nline 1",
            "Command output: all tests passed",
            "Command output secret output",
            "Command log:\nline 1",
            "Log output:\nline 1",
            "Log output: build succeeded",
            "Log output secret output",
            "Log output\nline 1",
            "raw log:\nline 1",
            "raw log: build succeeded",
            "Raw log\nline 1",
            "STDOUT:\nsecret output",
            "STDOUT\nsecret output",
            "stdout: hello world",
            "stdout secret output",
            "raw stdout: build succeeded",
            "raw stdout secret output",
            "stdout dump:\nsecret output",
            "stdout - secret output",
            "Standard output:\nsecret output",
            "standard output: hello world",
            "standard output - secret output",
            "standard output secret output",
            "Standard error\nsecret output",
            "standard error failure log",
            "stdout: secret output",
            "Command failed stdout: secret output",
            "Command failed stdout=secret output",
            "stderr: failure log",
            "stderr: permission denied",
            "stderr - failure log",
            "stderr failure log",
            "raw stderr: build failed",
            "raw stderr secret output",
            "Command failed raw stderr: secret output",
            "Command failed raw stderr=secret output",
            "Command failed raw stderr dump secret output",
            "Raw stderr dump secret output",
            "Command failed stdout dump secret output",
            "=== stderr ===\nsecret output",
            "raw stderr: secret output",
            "Command log - line 1",
            "diff --git a/a b/a\ncontent",
            "    diff --git a/a b/a\ncontent",
            "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n-secret\n+secret",
            "@@ -1 +1 @@\n-secret\n+secret",
            "    at render (app.js:12:34)",
            "    at async render (app.js:12:34)",
            "   at Namespace.Class.Method() in C:\\src\\App.cs:line 42",
            "    at com.example.App.main(App.java:42)",
            "    at java.base/java.util.ArrayList.get(ArrayList.java:427)",
            "Stack trace:\n#0 /var/www/app.php(12): run()",
            "Stack trace\n#0 /var/www/app.php(12): run()",
            "panic: fatal\ngoroutine 1 [running]:\nmain.main()",
            "goroutine 1 [running]:\nmain.main()",
            "Exception in thread \"main\" java.lang.RuntimeException",
            "Caused by: java.lang.IllegalStateException",
        )
        for text in patterns:
            with self.subTest(text=text.splitlines()[0]):
                self.assert_privacy_pattern_parity(text)
                self.assert_validation_error(
                    "privacy_rejected",
                    validate_task_input,
                    title="Task",
                    add_note=text,
                    field="add_note",
                )

    def test_event_summary_rejects_private_or_raw_content(self):
        for text in (
            "access_token=secret",
            "Raw stderr dump\nsecret",
            "raw stdout: build succeeded",
            "-----END PRIVATE KEY-----",
        ):
            with self.subTest(text=text.splitlines()[0]):
                self.assert_privacy_pattern_parity(text)
                self.assert_validation_error(
                    "privacy_rejected",
                    validate_event_summary,
                    text,
                    field="event_summary",
                )

    def test_privacy_patterns_are_rejected_before_storage_for_core_fields(self):
        for field in (
            "title",
            "description",
            "lane",
            "verification",
            "tags",
            "blocked_reason",
        ):
            with self.subTest(field=field):
                self.assert_privacy_pattern_parity("token=secret")
                kwargs = {"title": "Task", field: "token=secret"}
                if field == "title":
                    kwargs = {"title": "token=secret"}
                self.assert_validation_error(
                    "privacy_rejected",
                    validate_task_input,
                    field=field,
                    **kwargs,
                )

    def test_title_rejects_raw_output_values_but_allows_task_wording(self):
        self.assert_privacy_pattern_parity("stdout: hello world")
        self.assert_validation_error(
            "privacy_rejected",
            validate_task_input,
            title="stdout: hello world",
            field="title",
        )
        for title in ("Command output: improve formatting", "Command output: refine formatting"):
            with self.subTest(title=title):
                self.assert_privacy_pattern_parity(title)
                validated = validate_task_input(title=title)
                self.assertEqual(validated["title"], title)

    def test_privacy_patterns_allow_benign_task_wording(self):
        for title in (
            "Basic task validation",
            "Add basic validation layer",
            "Bearer token support",
            "Document bearer token behavior",
            "Bearer authentication support",
            "Document bearer authentication behavior",
            "Improve command output formatting",
            "Command output: improve formatting",
            "Command output: refine formatting",
            "Reject stack trace headings",
            "Stack trace: add detection",
            "Document environment variables support",
            "Environment variables: document support",
            "stdout formatting task",
            "Standard output wording update",
        ):
            with self.subTest(title=title):
                self.assert_privacy_pattern_parity(title)
                validated = validate_task_input(title=title)
                self.assertEqual(validated["title"], title)

    def test_privacy_guard_allows_neutral_operation_sequence_without_exception(self):
        for note in (
            "operation_sequence=1",
            "`operation_sequence=1`",
            "operation_sequence=2; continue observation",
            "Future release evidence uses operation_sequence=3.",
            '{"operation_sequence":4}',
        ):
            with self.subTest(note=note):
                self.assert_privacy_pattern_parity(note)
                validated = validate_task_input(title="Task", add_note=note)
                self.assertEqual(validated["add_note"], note)

    def test_privacy_guard_preserves_non_secret_json_schema_values(self):
        for note in (
            '{"token":null}',
            '{"authorization":false}',
            '{"password":0}',
        ):
            with self.subTest(note=note):
                self.assert_privacy_pattern_parity(note)
                validated = validate_task_input(title="Task", add_note=note)
                self.assertEqual(validated["add_note"], note)

    def test_numeric_metadata_examples_survive_caller_and_stored_validation(self):
        for value in (
            "max_tokens=4096",
            "token_count=1024",
            "password_length=12",
        ):
            with self.subTest(value=value):
                self.assert_privacy_pattern_parity(value)
                validated = validate_task_input(
                    title=value,
                    description=value,
                    verification=value,
                    add_note=value,
                )
                for field in ("title", "description", "verification", "add_note"):
                    self.assertEqual(validated[field], value)
                row = valid_stored_task_row(
                    title=value,
                    description=value,
                    verification=value,
                )
                original = dict(row)
                result = validate_stored_task_rows(
                    [row],
                    source_schema_version=7,
                    expected_project_id=row["project_id"],
                )
                self.assertIsNone(result.verification_rejection)
                self.assertEqual(row, original)

    def test_numeric_metadata_accepts_only_documented_integer_syntax(self):
        for value in (
            "max_tokens=0",
            "token_count=0001024",
            "password_length \t=\t 12",
            "max_tokens=4096 ",
            "token_count=1024\tverified",
            "password_length=12\nverified",
            "`max_tokens=4096`",
            "token_count=1024, verified",
            "password_length=12; verified",
            "(max_tokens=4096)",
            "[token_count=1024]",
            "{password_length=12}",
            "max_tokens=4096 token_count=1024 password_length=12",
            "max_tokens=" + ("9" * 600),
        ):
            with self.subTest(value=value):
                self.assert_privacy_pattern_parity(value)
                validated = validate_task_input(title="Task", add_note=value)
                self.assertEqual(validated["add_note"], value)

    def test_numeric_metadata_rejects_other_keys_and_nonnumeric_assignments(self):
        for value in (
            "MAX_TOKENS=4096",
            "Token_count=1024",
            "Password_length=12",
            "api_token_count=1024",
            "password_length_hint=12",
            "max_tokens_extra=4096",
            "custom.max_tokens=4096",
            "custom-token_count=1024",
            ".max_tokens=4096",
            "-token_count=1024",
            "--password_length=12",
            "max_tokens:4096",
            "max_tokens=secret",
            "token_count=removed",
            "password_length=fixed",
            "max_tokens=-1",
            "max_tokens=+1",
            "token_count=1.5",
            "token_count=1e3",
            "password_length=0x10",
            "max_tokens=4096suffix",
            "token_count=1024_token",
            "password_length=12/secret",
            "max_tokens=4096.",
            'max_tokens="4096"',
            "token_count='1024'",
            '{"password_length":"12"}',
            "max_tokens=\u0661\u0662",
            "token_count=\uff11\uff12",
            "password_length=12\u0661",
            "max_tokens\n=4096",
            "token_count=\n1024",
            "password_length\u00a0=12",
            "Fix token=removed",
            "Document password=fixed",
        ):
            with self.subTest(value=value):
                self.assert_privacy_pattern_parity(value)
                self.assert_validation_error(
                    "privacy_rejected",
                    validate_task_input,
                    title="Task",
                    add_note=value,
                    field="add_note",
                )

    def test_numeric_metadata_does_not_hide_credentials_or_dumps(self):
        for value in (
            ".max_tokens=4096",
            "-token_count=1024",
            "--password_length=12",
            "max_tokens=4096 token=secret",
            "password=secret token_count=1024",
            "password_length=12 Authorization: Bearer secret",
            "Authorization=max_tokens=4096",
            "token=max_tokens=4096",
            '{"token":"max_tokens=4096"}',
            "Fix max_tokens=4096 token=removed",
            "token_count=1024\nTraceback (most recent call last)",
            "password_length=12\nstdout: private output",
        ):
            with self.subTest(value=value):
                self.assert_privacy_pattern_parity(value)
                self.assert_validation_error(
                    "privacy_rejected",
                    validate_task_input,
                    title="Task",
                    description=value,
                    field="description",
                )
                row = valid_stored_task_row(description=value)
                with self.assertRaises(StorageError) as caught:
                    validate_stored_task_rows(
                        [row],
                        source_schema_version=7,
                        expected_project_id=row["project_id"],
                    )
                self.assertEqual(caught.exception.code, "project_state_unreadable")

    def test_fully_redacted_bearer_examples_preserve_caller_and_stored_text(self):
        for value in (
            "Authorization: Bearer <redacted>",
            "authorization:\tbearer\t<redacted>",
            "Authorization \t: \tBearer <redacted>",
            "Example `Authorization: Bearer <redacted>`",
            "Authorization: Bearer <redacted>, documented",
            "Authorization: Bearer <redacted>; max_tokens=4096",
            "(Authorization: Bearer <redacted>)",
            "[Authorization: Bearer <redacted>]",
            "{Authorization: Bearer <redacted>}",
            "Authorization: Bearer <redacted>\nNonsecret explanation",
        ):
            with self.subTest(value=value):
                self.assert_privacy_pattern_parity(value)
                validated = validate_task_input(title=value, description=value, add_note=value)
                self.assertEqual(validated["title"], value)
                self.assertEqual(validated["description"], value)
                self.assertEqual(validated["add_note"], value)
                row = valid_stored_task_row(title=value, description=value)
                original = dict(row)
                validate_stored_task_rows(
                    [row], source_schema_version=7, expected_project_id=row["project_id"]
                )
                self.assertEqual(row, original)

    def test_redacted_bearer_boundaries_and_mixed_content_remain_rejected(self):
        for value in (
            "Authorization: Bearer <redacted>suffix",
            "Authorization: Bearer prefix<redacted>",
            "Authorization: Bearer <redacted>.<redacted>",
            "Authorization: Bearer <redacted>.",
            "Authorization: Bearer <redacted",
            "Authorization: Bearer redacted>",
            "Authorization: Bearer <REDACTED>",
            "Authorization: Bearer <removed>",
            "Authorization: Bearer removed",
            "Authorization: Bearer<redacted>",
            "Authorization: Bearer\n<redacted>",
            "Authorization\n: Bearer <redacted>",
            "Authorization:\u00a0Bearer <redacted>",
            "Authorization=Bearer <redacted>",
            "Authorization Bearer <redacted>",
            "Authorization: Basic <redacted>",
            "Authorization: Token <redacted>",
            "Proxy-Authorization: Bearer <redacted>",
            ".Authorization: Bearer <redacted>",
            "_Authorization: Bearer <redacted>",
            "\u03b1Authorization: Bearer <redacted>",
            '"Authorization": "Bearer <redacted>"',
            "token=<redacted>",
            "Fix token=removed; Authorization: Bearer <redacted>",
            "token=synthetic-secret Authorization: Bearer <redacted>",
            "Authorization: Bearer <redacted> token=synthetic-secret",
            "Authorization: Bearer <redacted>; Authorization: Bearer synthetic-secret",
            "token=Authorization: Bearer <redacted>",
            "Authorization: Bearer <redacted>\nTraceback (most recent call last)",
            "Authorization: Bearer <redacted>\nstdout: synthetic output",
        ):
            with self.subTest(value=value):
                self.assert_privacy_pattern_parity(value)
                self.assert_validation_error(
                    "privacy_rejected", validate_task_input,
                    title="Task", description=value, field="description",
                )
                row = valid_stored_task_row(description=value)
                with self.assertRaises(StorageError) as raised:
                    validate_stored_task_rows(
                        [row], source_schema_version=7, expected_project_id=row["project_id"]
                    )
                self.assertEqual(raised.exception.code, "project_state_unreadable")

    def test_legacy_m19_7_stored_guard_is_exact_and_preserves_original_bytes(self):
        accepted = (
            "dispatch_authorization=1",
            "`dispatch_authorization=2`",
            "M19.7 records dispatch_authorization=3 before dispatch.",
            '{"dispatch_authorization": 4, "schema":"m19.7-approval-v1"}',
        )
        for value in accepted:
            with self.subTest(value=value):
                self.assert_privacy_pattern_parity(value)
                self.assert_privacy_pattern_parity(
                    _legacy_m19_7_stored_guard_value(value)
                )
                self.assertEqual(
                    validate_legacy_m19_7_stored_text("stored", value),
                    value,
                )

        rejected = (
            "dispatch_authorization=0",
            "dispatch_authorization=01",
            "dispatch_authorization=1suffix",
            '{"dispatch_authorization":"1"}',
            '{"dispatch_authorization":1 true}',
            '{"dispatch_authorization":1]',
            '{"dispatch_authorization":1.0}',
            '{"dispatch_authorization":1e3}',
            '{"dispatch_authorization":1suffix}',
            "Authorization=dispatch_authorization=1",
            "token=dispatch_authorization=1",
            '{"dispatch_authorization":1,"token":"secret"}',
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assert_privacy_pattern_parity(value)
                self.assert_privacy_pattern_parity(
                    _legacy_m19_7_stored_guard_value(value)
                )
                self.assert_validation_error(
                    "privacy_rejected",
                    validate_legacy_m19_7_stored_text,
                    "stored",
                    value,
                    field="stored",
                )

    def test_combined_privacy_pattern_preserves_inline_flag_semantics(self):
        cases = (
            ("prefix\nPRIVATE PROMPT: hidden", True),
            ("Environment variables:\nPath: C:\\Windows", True),
            ("prefix\n    at render (app.js:12:34)", True),
            ("prefix\nTraceback (most recent call last)", True),
            ("prefix\nOPENAI_API_KEY=secret", True),
            ("Document private prompt rejection", False),
            ("Document environment variables support", False),
            ("Review stack trace detection", False),
        )
        for value, expected in cases:
            with self.subTest(value=value.splitlines()[-1]):
                self.assert_privacy_pattern_parity(value)
                self.assertEqual(
                    COMBINED_PRIVACY_PATTERN.search(value) is not None,
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
