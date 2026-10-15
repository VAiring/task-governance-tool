"""Lossless budgets, pagination and fixed-identity range recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock
from types import SimpleNamespace

from tests import m14_test_support  # Set the source-package import path.
from task_governance_tool import review_delivery as delivery
from task_governance_tool import review_material_collection as collection
from task_governance_tool.review_handoff_transport import HandoffError
from task_governance_tool import review_handoff_preparation as preparation
from task_governance_tool import review_read_delivery


class DeliveryBudgetTests(unittest.TestCase):
    def command(self, start, digest):
        return f"fixed-read --start={start} --content-sha256={digest}"

    def test_records_only_admits_fixed_pagination_without_extra_permissions(self):
        from task_governance_tool.task_record_policy import validate_record_arguments, RecordPolicyError
        for words in (
            ["read", "--packet=p", "--section=context", "--start=5000", "--content-sha256=hash", "--packet-sha256=packet"],
            ["material", "blob", "id", "--start=5000", "--content-sha256=hash"],
            ["material", "diff", "before", "after", "--start=5000", "--content-sha256=hash"],
            ["material", "dependency", "revision", "--path=known", "--start=5000"],
            ["material", "directory", "revision", "--path=", "--start=5000"],
            ["material", "batch", "--start=20", "--content-sha256=hash"],
            ["material", "collect", "--packet=p", "--packet-sha256=packet", "--start=20", "--selection-sha256=selection"],
            ["material", "changes", "--packet=p", "--packet-sha256=packet", "--start=20"],
            ["material", "inventory", "--packet=p", "--packet-sha256=packet", "--start=20"],
        ):
            arguments = ["--records-only", "--repo=/fixed", *words]
            self.assertEqual(validate_record_arguments("review_handoff.py", arguments), "/fixed")
            for extra in ("--output=elsewhere", "--repo=/different", "--fetch", "--db=other"):
                with self.assertRaises(RecordPolicyError):
                    validate_record_arguments("review_handoff.py", [*arguments, extra])

    def test_token_allowance_boundaries_and_lossless_giant_line(self):
        for raw in (b"x" * 4999, b"x" * 5000, b"x" * 5001,
                    ("日本語\\\"\r\n" * 1900).encode(), b"x" * 90000):
            with self.subTest(size=len(raw)):
                start, pieces, expected = 0, [], None
                while True:
                    page = delivery.range_result(raw, {"object_id": "fixed"}, self.command, start=start, expected=expected)
                    self.assertTrue(delivery.fits(page))
                    piece = page["text"].encode()
                    self.assertLessEqual(len(piece), 5000)
                    self.assertEqual(raw[start:page["end"]], piece)
                    pieces.append(piece)
                    expected = page["content_sha256"]
                    if page["next_start"] is None:
                        break
                    self.assertGreater(page["next_start"], start)
                    start = page["next_start"]
                self.assertEqual(b"".join(pieces), raw)
                self.assertEqual(expected, hashlib.sha256(raw).hexdigest())

    def test_wrappers_are_charged_separately_from_text(self):
        payload = {"text": "\"\\" * 2500}
        self.assertLess(len(delivery.encode(payload)), delivery.transport_size(payload))
        page = delivery.range_result(payload["text"].encode(), {}, self.command)
        self.assertEqual(page["status"], "partial")
        self.assertLess(page["end"], 5000)
        self.assertLessEqual(delivery.transport_size(page), delivery.OUTPUT_BUDGET)

    def test_bad_offset_or_digest_is_closed(self):
        for start, expected in ((-1, None), (4, None), (1, None), (0, "f" * 64)):
            with self.subTest(start=start, expected=expected), self.assertRaises(HandoffError):
                delivery.range_result("日".encode(), {}, self.command, start=start, expected=expected)

    def test_stream_keeps_only_requested_window_and_validates_all_bytes(self):
        raw = ("日本語" * 5000 + "\r\n").encode()
        def stream(repo, args, consume):
            for offset in range(0, len(raw), 101):
                consume(raw[offset:offset + 101])
        with mock.patch.object(delivery, "run_git_stream", side_effect=stream):
            page = delivery.git_range(Path("unused"), [], {}, self.command, start=9000)
        self.assertEqual(page["total_bytes"], len(raw))
        self.assertEqual(page["content_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(page["text"].encode(), raw[9000:page["end"]])
        for bad in (b"x" * 9000 + b"\xff", b"x" * 9000 + b"\0"):
            with mock.patch.object(delivery, "run_git_stream", side_effect=lambda repo, args, consume: consume(bad)):
                with self.assertRaises(HandoffError) as raised:
                    delivery.git_range(Path("unused"), [], {}, self.command)
                self.assertEqual(raised.exception.code, "review_material_non_text")

    def test_body_and_patch_budgets_are_independent_and_deduplicated(self):
        values = {"before": b"a" * 2500, "after": b"b" * 2500, "extra": b"c", "patch": b"d" * 5000}
        def stream(repo, args, consume):
            consume(values["patch"] if args[0] == "diff" else values[args[-1]])
        bodies = collection._Bodies(Path("unused"))
        with mock.patch.object(collection, "run_git_stream", side_effect=stream):
            before = bodies.source("one", "r", "git_commit", "before", "100644", "before")
            after = bodies.source("one", "r", "git_commit", "after", "100644", "after")
            self.assertEqual(before["status"], "provided")
            self.assertEqual(after["status"], "provided")
            self.assertEqual(bodies.patch("before", "after")["status"], "provided")
            self.assertEqual(bodies.remaining, 0)
            self.assertEqual(bodies.patch_remaining, 0)
            self.assertEqual(bodies.source("two", "r", "git_commit", "after", "100644", "after")["status"], "provided")
            self.assertEqual(bodies.source("extra", "r", "git_commit", "after", "100644", "extra")["status"], "range_required")
            self.assertEqual(bodies.patch("different", "pair")["status"], "provided")
        self.assertEqual(len(bodies.patches), 1)

    def test_inventory_exact_array_byte_and_count_boundaries(self):
        oid = "a" * 40
        def inventory(rows, start=0):
            entries = [SimpleNamespace(path=row["path"].encode(), mode=b"100644", object_id=oid.encode()) for row in rows]
            with mock.patch("task_governance_tool.git_snapshot.stream_tree_entries",
                            side_effect=lambda *args, consume_entry, **kw: [consume_entry(row) for row in entries]):
                return preparation._dependency_inventory(Path("unused"), oid, [], start=start)
        for count in (0, 20, 21):
            rows = [{"path": f"p{i:03}.py", "mode": "100644", "object_id": oid} for i in range(count)]
            page = inventory(rows)
            self.assertEqual(page["entries"], rows[:20])
            self.assertEqual(page["total"], count)
            self.assertEqual(page["truncated"], count > 20)
            if count > 20:
                self.assertEqual(inventory(rows, start=20)["entries"], rows[20:])
        for size in (4095, 4096, 4097):
            rows = [{"path": f"p{i:03}" + "x" * 115, "mode": "100644", "object_id": oid} for i in range(20)]
            difference = size - len(delivery.encode(rows))
            rows[-1]["path"] = rows[-1]["path"] + "x" * difference if difference >= 0 else rows[-1]["path"][:difference]
            self.assertTrue(0 < len(rows[-1]["path"].encode()) <= 240)
            self.assertEqual(len(delivery.encode(rows)), size)
            page = inventory(rows)
            self.assertEqual(page["entries"], rows if size <= 4096 else rows[:-1])
        rows = [{"path": f"p{i:03}日本語'" + "日" * 50, "mode": "100644", "object_id": oid} for i in range(21)]
        page = inventory(rows)
        self.assertLessEqual(len(delivery.encode(page["entries"])), 4096)
        self.assertLessEqual(len(page["entries"]), 20)

    def test_large_context_ranges_keep_exact_document_and_evidence_time_outside_hash(self):
        view = {"task": {"title": "日本語" * 5000}, "review_target": {"value": "fixed"},
                "review_material": {"changes": [], "instructions": [], "changes_command": "fixed changes"},
                "verification_evidence": {"observed_at": "first", "value": "x" * 5100},
                "verification_guidance": "guidance", "context_check": "matched_at_read", "warnings": []}
        raw = b"fixed original"
        nav = review_read_delivery.bound(view, Path("repo"), "reviews/packet.json", raw)
        self.assertEqual(nav["status"], "partial")
        self.assertTrue(delivery.fits(nav))
        chunks, start, expected = [], 0, None
        while True:
            page = review_read_delivery.bound(view, Path("repo"), "reviews/packet.json", raw,
                                              section="context", start=start, expected=expected)
            self.assertTrue(delivery.fits(page))
            self.assertEqual(page["start"], start)
            chunks.append(page["text"].encode())
            expected = page["content_sha256"]
            if page["next_start"] is None:
                break
            start = page["next_start"]
        complete = json.loads(b"".join(chunks))
        self.assertEqual(complete, {k:v for k,v in view.items() if k not in ("review_material", "verification_evidence", "verification_guidance")})
        first = review_read_delivery.bound(view, Path("repo"), "reviews/packet.json", raw, section="verification")
        view["verification_evidence"]["observed_at"] = "second"
        second = review_read_delivery.bound(view, Path("repo"), "reviews/packet.json", raw,
                                            section="verification", start=first["next_start"], expected=first["content_sha256"])
        self.assertEqual(second["observed_at"], "second")
        self.assertEqual(first["content_sha256"], second["content_sha256"])
        view["verification_evidence"]["value"] = "changed"
        with self.assertRaises(HandoffError):
            review_read_delivery.bound(view, Path("repo"), "reviews/packet.json", raw,
                                       section="verification", start=first["next_start"], expected=first["content_sha256"])


if __name__ == "__main__":
    unittest.main()
