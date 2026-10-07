"""Fixed public host operations for an admitted single-call review wait."""

from uuid import uuid4

from task_governance_tool.task_values import validate_task_id
from .review_wait_host import PublicMcpHost, HostAdapterError, _automation_id, _uuid, parse_read_thread
from . import review_wait_mcp_relay as protocol


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
        super().__init__(**kwargs)

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
        return (
            f"レビュー待機通知 {probe}。元Task {self.task_id} の独立レビューが終了し、"
            "待機予約の削除を確認しました。保存済みの元Task・Packetのレビュー原本を回収し、"
            "既存のレビュー結果処理を続けてください。個別ACKやroutine status/viewは不要です。"
            "この通知自体はPASSやTask完了の証拠ではありません。予約の再作成や通知の再送は不要です。"
        )

    def observe_receipt(self, probe, original_turn):
        """Correlate the host's structured incoming event with an actual new turn.

        Neither unrelated user input nor a plain assistant echo is a receipt.
        The bounded event text is compared transiently and never persisted.
        """
        _uuid(probe)
        _uuid(original_turn)
        value = self._json_call("read_thread", {"threadId": self.parent_thread_id, "hostId": "local",
            "turnLimit": 1, "includeOutputs": True, "maxOutputCharsPerItem": 4096})
        parent = parse_read_thread(value, self.parent_thread_id)
        if parent.turn_id == original_turn:
            return None
        expected = ("<codex_delegation>\n  <source_thread_id>" + self.parent_thread_id
            + "</source_thread_id>\n  <input>" + self._direct_prompt(probe)
            + "</input>\n</codex_delegation>")
        for item in value["turns"][0]["items"]:
            if (type(item) is dict and item.get("type") == "functionCallOutput"
                    and item.get("name") == "send_message_to_thread"
                    and item.get("namespace") == "codex_app"):
                output = item.get("output")
                if (type(output) is dict and output.get("truncated") is False
                        and output.get("text") == expected):
                    return parent.turn_id
        return None
