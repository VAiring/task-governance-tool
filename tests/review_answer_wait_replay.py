"""Offline Desktop transport for the installed wait/resume integration test."""

from contextlib import nullcontext
from dataclasses import replace
import json
from pathlib import Path
import sys


def replay(installed_scripts, repo, request):
    sys.path.insert(0, str(installed_scripts))
    # Load the installed implementation before importing source-tree test peers.
    from task_governance_tool.review_wait_runtime.project_server import ProjectConfig, ProjectReviewWaitSession
    from task_governance_tool.review_wait_runtime.review_wait_host import ChildTurn
    from task_governance_tool.review_wait_runtime.review_wait_server import _call
    from task_governance_tool.review_answer_runtime import bound_turn, sources_for_reviewers
    sys.path.extend((str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)))
    from tests.test_review_wait_direct import DirectHost
    from tests.test_review_answer_host import page, turn
    from tests.test_review_answer_tracking_installed import answer

    sample = request["history"]
    parent, original = sample["parent_id"], sample["turn_id"]
    reviewers = sample["reviewers"]
    children = [row["id"] for row in reviewers]
    turns = {row["id"]: row["turn_id"] for row in reviewers}
    items = sample["items"]
    resumed = request["resume"]
    current = "71000000-0000-4000-8000-000000000099" if resumed else original
    metadata = dict(threadId=parent, turnId=current)
    automation = "taskgov-review-wait-" + "1" * 32
    reads = []

    def rpc(method, *, threadId, **args):
        assert not resumed, "resume attempted to infer dispatch"
        if method == "thread/read":
            return dict(thread=dict(id=threadId, historyMode="paginated"))
        if method == "thread/turns/list":
            tid = original if threadId == parent else turns[threadId]
            return dict(data=[dict(id=tid, itemsView="notLoaded", items=[], status="inProgress",
                                   error=None)], nextCursor=None)
        assert method == "thread/items/list" and threadId == parent and args["turnId"] == original
        cursor = args["cursor"]
        start = (next(i + 1 for i, item in enumerate(items) if item["id"] == cursor["itemId"])
                 if isinstance(cursor, dict) else int(cursor or 0))
        end = start + args["limit"]
        return dict(data=[dict(turnId=original, item=item) for item in items[start:end]],
                    nextCursor=str(end) if end < len(items) else None)

    class Desktop(DirectHost):
        def item_binding_options(self):
            return dict(verify_whole_turn_tail=True, reviewer_paths={r["id"]:r["path"] for r in reviewers},
                        item_reader=(lambda: nullcontext(rpc)) if request["route"] == "paged" else None)

        def read_child(self, child):
            return ChildTurn(child, turns[child], "inProgress", "active")

        def read_parent(self):
            return ChildTurn(parent, current, "inProgress", "active")

        def create_heartbeat(self, rule):
            self.value = replace(self.value, id=automation, parent_thread_id=parent, rule=rule,
                                 timezone_source="host_node_intl")
            return automation

        def verify_created(self, rule):
            assert self.value.rule == rule
            return self.value

        def _json_call(self, method, args):
            assert method == "read_thread"
            thread, limit = args["threadId"], args["turnLimit"]
            reads.append([thread, limit])
            if thread == parent:
                assert not resumed
                return page(parent, limit, turn(original, items=items, status="inProgress"))
            slot = children.index(thread) + 1
            body = [dict(type="agentMessage", id=f"answer-{slot}", phase="final_answer",
                         text=answer(slot))] if resumed else []
            return page(thread, limit, turn(turns[thread], items=body,
                                            status="completed" if resumed else "inProgress"))

    peer = Desktop()
    def factory(**kwargs):
        peer._metadata = kwargs["metadata"]
        return peer

    config = ProjectConfig(repo, repo / "server.mjs", repo, "UTC")
    session = ProjectReviewWaitSession(config, managed_host_factory=factory)
    task = request["task_id"]
    try:
        name = "review_answer_collect" if resumed else "review_wait_wait"
        response = _call(dict(name=name, arguments=dict(task_id=task, reviewer_ids=children),
                              _meta=metadata), session)
        body = json.loads(response["content"][0]["text"])
        assert body["ok"], body
        binding = session.basis_factory(repo, task, parent, "answer-parent",
                                        helper=installed_scripts / "review_handoff.py")()
        group, = sources_for_reviewers(repo, session.skill, binding, children)
        records = group.service.repository.read()
        saved = [(row["binding"]["reviewer_session_id"], bound_turn(row),
                  row["initial_observation"]["item_count"]) for row in records]
        return dict(response=body, saved=saved, reads=reads)
    finally:
        session.close()


if __name__ == "__main__":
    print(json.dumps(replay(Path(sys.argv[1]), Path(sys.argv[2]), json.load(sys.stdin))))
