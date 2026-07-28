import base64
import json
import re
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

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
        "snapshot_version": 3,
        "generated_at": "2026-07-17T00:00:00Z",
        "project": {
            "project_id": "viewer-project-123456789abc",
            "display_name": "Viewer project",
        },
        "source_schema_version": 5,
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
        snapshot["snapshot_version"] = 2

        with self.assertRaises(ViewerError) as failure:
            render_viewer_html(snapshot)

        self.assertEqual(failure.exception.code, "internal_error")

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
        initialization = template.split("try {", 1)[1].split(
            "} catch (error)",
            1,
        )[0]
        self.assertLess(
            initialization.rfind("renderTasks();"),
            initialization.index("startAutoRefresh();"),
        )


if __name__ == "__main__":
    unittest.main()
