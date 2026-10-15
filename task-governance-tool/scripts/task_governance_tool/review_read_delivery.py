"""Bound the first read, including Packet context and optional helper guidance."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from task_governance_tool import review_handoff_transport as files
from task_governance_tool.review_delivery import GUIDANCE, encode, fits, range_result, require_fit


def bound(view, repo, packet_path, raw, *, section="all", start=0, expected=None, records_only=False):
    from task_governance_tool.review_handoff_preparation import _shell
    digest = hashlib.sha256(raw).hexdigest()
    entry = Path(__file__).parent.parent / "review_handoff.py"

    def command(name, offset=0, content_hash=None):
        return _shell([sys.executable, "-I", "-S", "-B", str(entry),
                       *(["--records-only"] if records_only else []), "read", "--repo=" + str(repo),
                       "--packet=" + packet_path, "--packet-sha256=" + digest, "--section=" + name,
                       "--start=" + str(offset), *(["--content-sha256=" + content_hash] if content_hash else [])])

    if section == "all":
        if start or expected:
            files.fail("handoff_invalid_arguments")
        if fits(view):
            return view
        return require_fit({"status": "partial", "packet_sha256": digest, "review_target": view["review_target"],
                            "context_command": command("context"), "material_command": command("material"),
                            "verification_command": command("verification"),
                            "workspace_command": command("workspace") if "verification_workspace" in view["review_material"] else None,
                            "instructions": "Context/material/verification are separate complete documents. Retrieve all their contiguous ranges before judgment; workspace is optional. No Packet context was excluded or summarized. " + GUIDANCE})
    if section == "context":
        payload = {key: value for key, value in view.items()
                   if key not in ("review_material", "verification_evidence", "verification_guidance")}
    elif section == "material":
        payload = {key: value for key, value in view["review_material"].items() if key != "verification_workspace"}
        if payload.get("changes") is not None:
            payload.pop("changes")
            payload["instructions"] = ["Retrieve changes_command for all whole changed entries; it does not select relevance.", *payload["instructions"]]
    elif section == "verification":
        # Observation time changes on each validated read; keep it outside the
        # fixed content digest so a resumed range can still match that evidence.
        evidence = dict(view["verification_evidence"])
        evidence.pop("observed_at", None)
        payload = {"verification_evidence": evidence, "verification_guidance": view["verification_guidance"]}
    elif section == "workspace":
        if start or expected:
            files.fail("handoff_invalid_arguments")
        return require_fit({"status": "complete", "packet_sha256": digest,
                            "verification_workspace": view["review_material"].get("verification_workspace", {"status": "unavailable"})})
    else:
        files.fail("handoff_invalid_arguments")
    return range_result(encode(payload), {"packet_sha256": digest, "section": section},
                        lambda offset, content_hash: command(section, offset, content_hash),
                        start=start, expected=expected,
                        metadata={"observed_at": view["verification_evidence"].get("observed_at")} if section == "verification" else None)
