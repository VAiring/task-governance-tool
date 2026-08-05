import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import viewer as viewer_module  # noqa: E402
from task_governance_tool.viewer import (  # noqa: E402
    REFRESH_INTERVAL_PLACEHOLDER,
    TEMPLATE_PLACEHOLDER,
    ViewerError,
    encode_snapshot,
    render_viewer_html,
    viewer_template_path,
)


EXPECTED_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; "
    "media-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; "
    "manifest-src 'none'; base-uri 'none'; form-action 'none'"
)


class TemplateAuditParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.label_targets = []
        self.url_attributes = []
        self.inline_handlers = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "id" and value is not None:
                self.ids.append(value)
            if tag == "label" and name == "for" and value is not None:
                self.label_targets.append(value)
            if name in {"href", "src", "action", "formaction"}:
                self.url_attributes.append((tag, name, value))
            if name.startswith("on"):
                self.inline_handlers.append((tag, name))


def sample_snapshot(title="Safe task"):
    return {
        "snapshot_version": 4,
        "generated_at": "2026-07-17T00:00:00Z",
        "project": {
            "project_id": "viewer-project-123456789abc",
            "display_name": "Viewer project",
        },
        "source_schema_version": 18,
        "counts": {
            "total": 1,
            "ready": 1,
            "in_progress": 0,
            "paused": 0,
            "blocked": 0,
            "review_pending": 0,
            "done": 0,
            "cancelled": 0,
        },
        "tasks": [
            {
                "task_id": "tg_task_viewer",
                "project_id": "viewer-project-123456789abc",
                "title": title,
                "description": "Viewer description",
                "kind": "optional",
                "lane": "VIEWER",
                "lane_order": None,
                "priority": "high",
                "status": "ready",
                "blocked_reason": "",
                "pause_reason": "",
                "review_tier": 2,
                "verification": "Offline checks",
                "tags": "viewer,offline",
                "created_at": "2026-07-17T00:00:00Z",
                "updated_at": "2026-07-17T00:00:00Z",
                "completed_at": None,
                "completion_commit_required": 1,
                "completion_commit_hash": "",
                "completion_evidence_kind": "git_commit",
                "completion_evidence_revision": "a" * 40,
                "completion_evidence_reason": "",
                "external_revision_approved": 0,
                "review_target_kind": "git_commit",
                "review_target_value": "a" * 40,
                "review_target_generation": 1,
                "review_evidence": {
                    "target": {"kind": "git_commit", "value": "a" * 40, "generation": 1},
                    "gate": {
                        "review_tier": 2,
                        "required_independent_passes": 2,
                        "qualifying_independent_passes": 1,
                        "fallback_kind": None,
                        "satisfied": False,
                    },
                    "counts": {
                        "receipts_total": 1,
                        "receipts_current_generation": 1,
                        "open_high": 0,
                        "open_medium": 0,
                        "open_low": 1,
                    },
                    "blocking_findings": [{
                        "review_finding_id": "tg_review_finding_blocking_viewer",
                        "severity": "medium",
                        "status": "resolved",
                        "summary": "Fresh review is required",
                        "reviewer_key": "reviewer-a",
                        "target_generation": 1,
                        "blocking_reason": "fresh_review_required",
                        "created_at": "2026-07-17T00:00:00Z",
                    }],
                    "recent_receipts": [{
                        "review_receipt_id": "tg_review_receipt_viewer",
                        "reviewer_key": "reviewer-a",
                        "receipt_kind": "independent",
                        "verdict": "pass",
                        "summary": "Review passed",
                        "target_generation": 1,
                        "created_at": "2026-07-17T00:00:00Z",
                    }],
                    "recent_findings": [{
                        "review_finding_id": "tg_review_finding_viewer",
                        "severity": "low",
                        "status": "open",
                        "summary": "Polish later",
                        "reviewer_key": "reviewer-a",
                        "target_generation": 1,
                        "created_at": "2026-07-17T00:00:00Z",
                    }],
                },
                "completion_history": {
                    "total": 1,
                    "returned_count": 1,
                    "truncated": False,
                    "legacy_history_incomplete": False,
                    "cycles": [{
                        "completion_cycle_id": "tg_completion_cycle_0123456789abcdef",
                        "saved_cycle_ordinal": 1,
                        "origin": "native_done",
                        "completeness": "complete",
                        "completed_at": "2026-07-17T00:00:00Z",
                        "contract_revision": 0,
                        "review_tier": 2,
                        "verification_expectation": "specified",
                        "verification_attestation": True,
                        "completion_evidence": {
                            "kind": "git_commit",
                            "revision": "private-revision-value",
                            "reason": "private-reason-value",
                            "external_revision_approved": False,
                            "completion_commit_required": True,
                            "completion_commit_hash": "private-hash-value",
                        },
                        "review_target": {
                            "kind": "git_commit",
                            "value": "private-target-value",
                            "base_revision": "",
                            "generation": 1,
                        },
                        "gate_basis": {
                            "version": 1,
                            "kind": "independent_passes",
                            "required_independent_passes": 2,
                            "qualifying_independent_passes": 2,
                            "changes_requested": 0,
                            "open_high": 0,
                            "open_medium": 0,
                            "fresh_review_required": 0,
                            "qualifying_receipt_ids": [
                                "private-receipt-a",
                                "private-receipt-b",
                            ],
                        },
                    }],
                },
                "events": [
                    {
                        "task_event_id": "tg_event_viewer",
                        "task_id": "tg_task_viewer",
                        "project_id": "viewer-project-123456789abc",
                        "event_type": "task_added",
                        "summary": "Task registered",
                        "created_at": "2026-07-17T00:00:00Z",
                    }
                ],
            }
        ],
    }


def embedded_snapshot(html):
    match = re.search(
        r'<script id="taskgov-snapshot" type="application/octet-stream">([^<]+)</script>',
        html,
    )
    if match is None:
        raise AssertionError("embedded snapshot was not found")
    return json.loads(base64.b64decode(match.group(1)).decode("utf-8"))


class ViewerRendererTests(unittest.TestCase):
    def test_bundled_template_has_exactly_one_placeholder_of_each_kind(self):
        template = viewer_template_path().read_text(encoding="utf-8")

        self.assertEqual(template.count(TEMPLATE_PLACEHOLDER), 1)
        self.assertEqual(template.count(REFRESH_INTERVAL_PLACEHOLDER), 1)
        self.assertIn("<title>Task Viewer</title>", template)
        self.assertIn('id="search-filter"', template)
        self.assertIn('id="task-detail"', template)
        self.assertIn('id="terminal-filter"', template)

    def test_snapshot_base64_round_trip_preserves_utf8_and_is_deterministic(self):
        snapshot = sample_snapshot("Japanese task: \u30bf\u30b9\u30af")

        first = encode_snapshot(snapshot)
        second = encode_snapshot(snapshot)

        self.assertEqual(first, second)
        self.assertEqual(json.loads(base64.b64decode(first).decode("utf-8")), snapshot)

    def test_render_inserts_encoded_data_without_raw_html_shaped_text(self):
        malicious = '</script><img src=x onerror="globalThis.taskgovInjected=true">'
        snapshot = sample_snapshot(malicious)

        rendered = render_viewer_html(snapshot)

        self.assertNotIn(malicious, rendered)
        self.assertNotIn(TEMPLATE_PLACEHOLDER, rendered)
        self.assertNotIn(REFRESH_INTERVAL_PLACEHOLDER, rendered)
        self.assertIn(
            'data-taskgov-refresh-interval-seconds="0"',
            rendered,
        )
        self.assertEqual(embedded_snapshot(rendered), snapshot)

    def test_render_rejects_missing_multiple_and_unreadable_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invalid_templates = {
                "missing_both.html": "<html></html>",
                "missing_snapshot.html": REFRESH_INTERVAL_PLACEHOLDER,
                "multiple_snapshot.html": (
                    TEMPLATE_PLACEHOLDER
                    + TEMPLATE_PLACEHOLDER
                    + REFRESH_INTERVAL_PLACEHOLDER
                ),
                "missing_refresh.html": TEMPLATE_PLACEHOLDER,
                "multiple_refresh.html": (
                    TEMPLATE_PLACEHOLDER
                    + REFRESH_INTERVAL_PLACEHOLDER
                    + REFRESH_INTERVAL_PLACEHOLDER
                ),
            }

            for name, content in invalid_templates.items():
                path = root / name
                path.write_text(content, encoding="utf-8")
                with self.subTest(path=path.name):
                    with self.assertRaises(ViewerError) as failure:
                        render_viewer_html(sample_snapshot(), template_path=path)
                    self.assertEqual(failure.exception.code, "internal_error")

            with self.assertRaises(ViewerError) as unreadable:
                render_viewer_html(sample_snapshot(), template_path=root / "absent.html")
            self.assertEqual(unreadable.exception.code, "internal_error")

    def test_render_accepts_disabled_or_configured_decimal_interval_only(self):
        for interval in (0, 5, 30, 3600):
            with self.subTest(interval=interval):
                rendered = render_viewer_html(
                    sample_snapshot(),
                    refresh_interval_seconds=interval,
                )
                self.assertIn(
                    f'data-taskgov-refresh-interval-seconds="{interval}"',
                    rendered,
                )

        for value in (True, -1, 1, 4, 3601, 30.0, "30"):
            with self.subTest(value=value):
                with self.assertRaises(ViewerError) as failure:
                    render_viewer_html(
                        sample_snapshot(),
                        refresh_interval_seconds=value,
                    )
                self.assertEqual(failure.exception.code, "internal_error")

    def test_render_rejects_unsupported_snapshot_version(self):
        snapshot = sample_snapshot()
        snapshot["snapshot_version"] = 3

        with self.assertRaises(ViewerError) as failure:
            render_viewer_html(snapshot)

        self.assertEqual(failure.exception.code, "internal_error")

    def test_render_rejects_artifact_above_existing_64_mib_cap(self):
        rendered = render_viewer_html(sample_snapshot())
        rendered_size = len(rendered.encode("utf-8"))
        with mock.patch.object(
            viewer_module,
            "MAX_VIEWER_ARTIFACT_BYTES",
            rendered_size,
        ):
            self.assertEqual(render_viewer_html(sample_snapshot()), rendered)
        with mock.patch.object(
            viewer_module,
            "MAX_VIEWER_ARTIFACT_BYTES",
            rendered_size - 1,
        ):
            with self.assertRaises(ViewerError) as failure:
                render_viewer_html(sample_snapshot())

        self.assertEqual(failure.exception.code, "internal_error")
        self.assertEqual(
            failure.exception.message,
            "viewer artifact exceeds the supported size",
        )

    def test_template_uses_exact_csp_and_no_external_or_persistent_apis(self):
        template = viewer_template_path().read_text(encoding="utf-8")

        self.assertIn(f'content="{EXPECTED_CSP}"', template)
        forbidden = (
            "http://",
            "https://",
            "fetch(",
            "XMLHttpRequest",
            "WebSocket",
            "EventSource",
            "sendBeacon",
            "serviceWorker",
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "document.cookie",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, template)

    def test_template_contains_bounded_one_shot_reload_state_contract(self):
        template = viewer_template_path().read_text(encoding="utf-8")

        expected_keys = (
            "owner",
            "schema_version",
            "captured_at_ms",
            "status",
            "kind",
            "lane",
            "priority",
            "tag",
            "terminal",
            "selected_task_id",
            "scroll_x",
            "scroll_y",
            "focus_id",
        )
        keys_block = template.split(
            "const reloadStateKeys = [",
            1,
        )[1].split("];", 1)[0]
        self.assertEqual(
            re.findall(r'"([a-z_]+)"', keys_block),
            list(expected_keys),
        )
        for marker in (
            'const reloadStateOwner = "taskgov-viewer-auto-reload";',
            "const reloadStateMaxBytes = 4096;",
            "const reloadStateMaxAgeMilliseconds = 300000;",
            "const reloadStateMaxDynamicBytes = 1024;",
            "const reloadStateMaxTaskIdCharacters = 128;",
            "const reloadStateMaxCoordinate = 2147483647;",
            '"scrollRestoration" in window.history',
            'window.history.scrollRestoration = "manual";',
            'window.history.scrollRestoration === "manual"',
            'window.location.protocol !== "file:"',
            'entries[0].type === "reload"',
            "new TextEncoder().encode(serialized).byteLength",
            "Object.keys(value)",
            "Date.now()",
            "window.scrollTo(0, 0)",
            "window.scrollTo(state.scroll_x, state.scroll_y)",
            'focus({ preventScroll: true })',
            "restoredFocusTarget.blur()",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, template)

        for focus_id in (
            "",
            "search-filter",
            "status-filter",
            "kind-filter",
            "lane-filter",
            "priority-filter",
            "tag-filter",
            "terminal-filter",
            "reset-filters",
        ):
            self.assertIn(f'"{focus_id}"', template)

        replace_calls = re.findall(
            r"window\.history\.replaceState\(([^;\n]+)\);",
            template,
        )
        self.assertEqual(replace_calls, ['null, ""', 'candidate, ""'])
        self.assertEqual(template.count("window.history.state"), 2)
        for forbidden in (
            "pushState",
            "popstate",
            "beforeunload",
            "pagehide",
            "hashchange",
            "console.",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, template)

        capture_body = template.split(
            "const captureAutoReloadState = () => {",
            1,
        )[1].split("const saveAutoReloadState", 1)[0]
        self.assertNotIn("elements.search", capture_body)
        self.assertNotIn("snapshot:", capture_body)
        self.assertIn("selected_task_id: selectedTaskId", capture_body)
        self.assertIn("focus_id: focusId", capture_body)

        prepare_index = template.index("prepareReloadState();")
        decode_index = template.index("snapshot = decodeSnapshot();")
        self.assertLess(prepare_index, decode_index)
        initialization = template.split(
            "      prepareReloadState();\n      try {",
            1,
        )[1].split(
            "      } catch (error) {\n        elements.workspace.hidden",
            1,
        )[0]
        self.assertLess(
            initialization.index("initializeFilters();"),
            initialization.index("validatePendingReloadState()"),
        )
        self.assertLess(
            initialization.index("applyValidatedReloadState("),
            initialization.index("renderTasks();"),
        )
        self.assertLess(
            initialization.index("renderTasks();"),
            initialization.index("restoreReloadEffects(restoredReloadState);"),
        )
        self.assertLess(
            initialization.index("restoreReloadEffects(restoredReloadState);"),
            initialization.index("startAutoRefresh();"),
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_exact_shipped_history_logic_in_fake_browser(self):
        completed = subprocess.run(
            [
                shutil.which("node"),
                str(ROOT / "tests" / "viewer_history_harness.mjs"),
                str(viewer_template_path()),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stderr or completed.stdout,
        )
        self.assertEqual(
            completed.stdout.strip(),
            "M15.6 exact shipped History harness PASS",
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_exact_shipped_completion_history_panel_in_fake_browser(self):
        completed = subprocess.run(
            [
                shutil.which("node"),
                str(ROOT / "tests" / "viewer_completion_history_harness.mjs"),
                str(viewer_template_path()),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stderr or completed.stdout,
        )
        self.assertEqual(
            completed.stdout.strip(),
            "M18.3 exact shipped completion-history harness PASS",
        )

    def test_template_avoids_html_execution_sinks_and_inline_handlers(self):
        template = viewer_template_path().read_text(encoding="utf-8")

        forbidden = (
            ".innerHTML",
            "insertAdjacentHTML",
            "document.write",
            "eval(",
            "Function(",
            "setAttribute(\"href\"",
            "setAttribute(\"src\"",
        )
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, template)
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", template, re.IGNORECASE))
        self.assertNotIn("'unsafe-eval'", template)

    def test_template_ids_labels_and_attributes_are_structurally_safe(self):
        template = viewer_template_path().read_text(encoding="utf-8")
        parser = TemplateAuditParser()

        parser.feed(template)

        self.assertEqual(len(parser.ids), len(set(parser.ids)))
        self.assertTrue(set(parser.label_targets).issubset(set(parser.ids)))
        self.assertEqual(parser.url_attributes, [])
        self.assertEqual(parser.inline_handlers, [])

    def test_template_contains_responsive_and_filter_behavior_contracts(self):
        template = viewer_template_path().read_text(encoding="utf-8")

        for marker in (
            "@media (max-width: 820px)",
            "@media (max-width: 620px)",
            "snapshot.tasks.filter(matchesFilters)",
            "terminalStatuses.has(task.status)",
            "renderDetail",
            "completion_commit_hash",
            "completion_evidence_kind",
            "review_evidence",
            "blocking_findings",
            "blocking_reason",
            "recent_receipts",
            "recent_findings",
            'paused: "Paused"',
            'addDetailValue(values, "Pause reason", task.pause_reason)',
            "task.events.forEach",
            'titleButton.setAttribute(\n          "aria-pressed"',
            "focusTarget.focus()",
            'id="selection-announcement" aria-live="polite"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, template)

    def test_template_renders_bounded_text_only_completion_history(self):
        template = viewer_template_path().read_text(encoding="utf-8")
        history_body = template.split(
            "const renderCompletionHistory = (history) => {",
            1,
        )[1].split("const renderDetail = (task) => {", 1)[0]

        for marker in (
            '"Completion history"',
            "history.cycles.slice(0, 10)",
            '"legacy history incomplete"',
            '"No completion cycles recorded."',
            "record.saved_cycle_ordinal",
            "record.origin",
            "record.completeness",
            "record.completed_at",
            "record.completion_cycle_id",
            "record.contract_revision",
            "record.review_tier",
            "record.verification_expectation",
            "record.verification_attestation",
            "evidence.kind",
            "evidence.revision",
            "evidence.reason",
            "evidence.external_revision_approved",
            "evidence.completion_commit_required",
            "evidence.completion_commit_hash",
            "target.kind",
            "target.value",
            "target.base_revision",
            "target.generation",
            "gate.version",
            "gate.kind",
            "gate.required_independent_passes",
            "gate.qualifying_independent_passes",
            "gate.changes_requested",
            "gate.open_high",
            "gate.open_medium",
            "gate.fresh_review_required",
            "gate.qualifying_receipt_ids",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, history_body)
        detail_body = template.split(
            "const renderDetail = (task) => {",
            1,
        )[1].split("const renderTasks = () => {", 1)[0]
        self.assertLess(
            detail_body.index(
                "renderCompletionHistory(task.completion_history);"
            ),
            detail_body.index(
                "renderBlockingReviewFindings(task.review_evidence);"
            ),
        )

    def test_template_contains_bounded_visibility_scheduler(self):
        template = viewer_template_path().read_text(encoding="utf-8")

        for marker in (
            'window.location.protocol === "file:"',
            'document.visibilityState !== "visible"',
            "performance.now()",
            "refreshTimeoutHandle",
            'document.addEventListener("visibilitychange"',
            "window.clearTimeout(refreshTimeoutHandle)",
            "window.setTimeout(() =>",
            "remainingMilliseconds",
            "reloadRequested = true",
            "window.location.reload()",
            'new TextDecoder("utf-8", { fatal: true })',
            "startAutoRefresh();",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, template)
        self.assertNotIn("setInterval", template)
        self.assertEqual(template.count("window.setTimeout("), 1)
        self.assertEqual(template.count("window.location.reload()"), 1)
        self.assertIn(
            """        const remainingMilliseconds = (
          refreshIntervalMilliseconds
          - (performance.now() - refreshEpochMilliseconds)
        );""",
            template,
        )
        self.assertIn(
            """        if (remainingMilliseconds <= 0) {
          reloadRequested = true;
          saveAutoReloadState();
          window.location.reload();
          return;
        }""",
            template,
        )
        self.assertIn(
            """        refreshTimeoutHandle = window.setTimeout(() => {
          refreshTimeoutHandle = null;
          reconcileAutoRefresh();
        }, remainingMilliseconds);""",
            template,
        )
        start_body = template.split(
            "const startAutoRefresh = () => {",
            1,
        )[1].split("};", 1)[0]
        self.assertLess(
            start_body.index(
                "refreshEpochMilliseconds = performance.now();"
            ),
            start_body.index(
                'document.addEventListener("visibilitychange"'
            ),
        )
        initialization = template.split(
            "      prepareReloadState();\n      try {",
            1,
        )[1].split(
            "      } catch (error) {\n        elements.workspace.hidden",
            1,
        )[0]
        self.assertLess(
            initialization.rfind("renderTasks();"),
            initialization.index("startAutoRefresh();"),
        )


if __name__ == "__main__":
    unittest.main()
