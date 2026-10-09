"""Fixed public host operations for an admitted single-call review wait."""

from uuid import uuid4
import json
import re

from task_governance_tool.task_values import validate_task_id
from .review_wait_host import PublicMcpHost, HostAdapterError, _automation_id, _uuid, parse_read_thread
from . import review_wait_mcp_relay as protocol


def _host_text_length(value):
    # The public desktop host limits JavaScript strings in UTF-16 code units.
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def fallback_prompt(task_id):
    validate_task_id(task_id)
    return (
        f"元Task {task_id} の独立レビューを確認する時刻です。保存済みの元Packet・レビュー依頼と"
        "実際のレビュワーを使って既存のレビュー結果処理を続けてください。"
        "健全な未完了レビューがある場合は、同じTaskと実レビュワーを渡す review_wait_wait を"
        "一度呼んで再待機してください。予約の作成・削除や個別ACK・状態確認は不要です。"
        "この通知をPASSやTask完了の証拠にせず、不明な操作を再試行しないでください。"
        "変化や要対応事項がなければ静かに終了し、完了・失敗・ユーザー対応が必要な場合に知らせてください。"
    )


class ManagedHost(PublicMcpHost):
    def __init__(self, *, task_id, **kwargs):
        self.task_id = validate_task_id(task_id)
        self._finalization_prompts = {}
        self._receipt_headers = {}
        super().__init__(**kwargs)

    def set_finalization_result(self, probe, result):
        """Keep the complete report transiently; history limits never trim it."""
        _uuid(probe)
        if probe in self._finalization_prompts or result.get("task_id") != self.task_id:
            raise HostAdapterError("host_call_failed")
        target = result.get("target")
        if (type(target) is not dict or set(target) != {"contract_revision", "target_kind", "target_value",
                                                       "target_base_revision", "target_generation"}
                or type(target["contract_revision"]) is not int or target["contract_revision"] < 0
                or type(target["target_generation"]) is not int or target["target_generation"] < 1
                or target["target_kind"] != "git_snapshot"
                or type(target["target_value"]) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", target["target_value"])
                or type(target["target_base_revision"]) is not str
                or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target["target_base_revision"])):
            raise HostAdapterError("host_call_failed")
        value = dict(result)
        value["delivery"] = {"source_body": "complete", "omitted_fields": [],
                             "receipt_scope": "notification_correlation_only"}
        identity = {"version": 1, "notification_id": probe, "parent_thread_id": self.parent_thread_id,
                    "task_id": self.task_id, "target": target}
        prefix = (f"レビュー結果通知 {probe}。元Task {self.task_id} の処理結果です。\n"
                  + "通知照合: " + json.dumps(identity, ensure_ascii=False, separators=(",", ":")) + "\n")
        # The complete identity must precede the variable body in a bounded read.
        if _host_text_length(prefix) > 2000:
            raise HostAdapterError("host_call_failed")
        suffix = "\n成功済みの処理を繰り返さず、この実結果で報告・必要な対応を続けてください。個別ACKや通知の再送は不要です。"
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        (prefix + raw + suffix).encode("utf-8")  # Reject invalid text before size-only fallback.
        try:
            self._tool_call_bytes("send_message_to_thread", {"threadId": self.parent_thread_id,
                "hostId": "local", "prompt": prefix + raw + suffix})
        except protocol.RelayError:
            # This is the actual local relay frame bound, not an assumed host
            # send limit. Do not present this exceptional notice as full delivery.
            included = {"ok", "status", "task_id", "target", "task_title", "stages", "registered_receipt_ids",
                        "commit_id", "candidate_commit_id", "task_status", "blocking_code", "next_action", "unavailable"}
            value = {key: item for key, item in result.items() if key in included}
            value["delivery"] = {"source_body": "incomplete", "omitted_fields": sorted(set(result) - included),
                "receipt_scope": "notification_correlation_only", "limitation": "local_relay_frame_limit",
                "max_frame_bytes": protocol.MAX_MESSAGE_BYTES,
                "recovery": "Full result delivery failed before sending: the encoded public tool call exceeds the local relay frame limit. Use the retained finalization_command with --check to recover the omitted results. Do not treat this notice as complete delivery."}
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            self._tool_call_bytes("send_message_to_thread", {"threadId": self.parent_thread_id,
                "hostId": "local", "prompt": prefix + raw + suffix})
        self._finalization_prompts[probe] = prefix + raw + suffix
        self._receipt_headers[probe] = prefix

    def create_heartbeat(self, rule):
        """The caller has already saved creation intent; one call, no retries."""
        try:
            texts = self._call("automation_update", {
                "mode": "create", "kind": "heartbeat", "destination": "thread",
                "targetThreadId": self.parent_thread_id,
                "name": "Taskgov review wait " + uuid4().hex,
                "prompt": fallback_prompt(self.task_id), "rrule": rule, "status": "PAUSED",
            })
            if len(texts) != 2:
                raise ValueError
            receipt = protocol.strict_json_loads(texts[1])
            if (type(receipt) is not dict or set(receipt) != {"automationId", "mode", "status"}
                    or receipt["mode"] != "create" or receipt["status"] != "PAUSED"):
                raise ValueError
            automation = _automation_id(receipt["automationId"])
            if len(automation) > 128:
                raise ValueError
            self._automation_id = automation
            return automation
        except Exception:
            raise HostAdapterError("heartbeat_create_unknown") from None

    def verify_created(self, rule):
        config = self._view_config()
        if (config.snapshot.status != "PAUSED" or config.snapshot.rule != rule
                or config.prompt != fallback_prompt(self.task_id)):
            raise HostAdapterError("heartbeat_changed")
        return config.snapshot

    def _direct_prompt(self, probe):
        if probe in self._finalization_prompts:
            return self._finalization_prompts[probe]
        return (
            f"レビュー待機通知 {probe}。元Task {self.task_id} の独立レビューが終了し、"
            "待機予約の削除を確認しました。保存済みの元Task・Packetのレビュー原本を回収し、"
            "既存のレビュー結果処理を続けてください。個別ACKやroutine status/viewは不要です。"
            "この通知自体はPASSやTask完了の証拠ではありません。予約の再作成や通知の再送は不要です。"
        )

    def observe_receipt(self, probe, original_turn):
        """Correlate the host's structured incoming event with an actual new turn.

        Neither unrelated user input nor a plain assistant echo is a receipt.
        A truncated history prefix can establish correlation, never full-body
        integrity. The bounded event text is transient and never persisted.
        """
        _uuid(probe)
        _uuid(original_turn)
        maximum = 20000 if probe in self._finalization_prompts else 4096
        value = self._json_call("read_thread", {"threadId": self.parent_thread_id, "hostId": "local",
            "turnLimit": 1, "includeOutputs": True, "maxOutputCharsPerItem": maximum})
        parent = parse_read_thread(value, self.parent_thread_id)
        if parent.turn_id == original_turn:
            return None
        envelope = ("<codex_delegation>\n  <source_thread_id>" + self.parent_thread_id
                    + "</source_thread_id>\n  <input>")
        expected = envelope + self._direct_prompt(probe) + "</input>\n</codex_delegation>"
        for item in value["turns"][0]["items"]:
            if (type(item) is dict and item.get("type") == "functionCallOutput"
                    and item.get("name") == "send_message_to_thread"
                    and item.get("namespace") == "codex_app"):
                output = item.get("output")
                if (type(output) is dict and output.get("truncated") is False
                        and output.get("text") == expected):
                    return parent.turn_id
                if type(output) is not dict or output.get("truncated") is not True:
                    continue
                text = output.get("text")
                original = output.get("originalChars")
                header = self._receipt_headers.get(probe)
                # Public read_thread returns a UTF-16 prefix and originalChars.
                # Require the whole identity, the matching visible prefix and
                # host-reported length. Neither a prose echo nor sender digest
                # authenticates a receipt; event/parent/new-turn checks do that.
                if (header is not None and type(text) is str and type(original) is int
                        and original == _host_text_length(expected) > maximum
                        and _host_text_length(text) == maximum
                        and text.startswith(envelope + header)
                        and expected.encode("utf-16-le", errors="surrogatepass").startswith(
                            text.encode("utf-16-le", errors="surrogatepass"))):
                    return parent.turn_id
        return None
