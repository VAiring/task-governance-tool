"""Legacy-protocol handoff operations over explicitly selected review state.

The context owns temporary review state and local resolver patches. Shared
operations accept a context registrar so a calling test can own its patches
without borrowing another TestCase's setup or cleanup lifecycle.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, fields
import json
import subprocess
from types import SimpleNamespace
from unittest import mock

from tests.review_answer_fixture_support import ReviewFixture, review_fixture
from tests.test_review_continuation import A
from tests.test_review_handoff import packet_for
from tests.test_review_results import document, encode
from task_governance_tool import review_preparation as preparation
from task_governance_tool import review_handoff_continuation as continuation
from task_governance_tool.review_results import review_result_template


def physical_handoff(state, *, enter_context):
    subprocess.run(["git", "init", "--quiet", str(state.repo)], check=True, capture_output=True)
    with (state.repo / ".gitignore").open("a", encoding="utf-8") as target:
        target.write("\n/reviews/\n")
    (state.repo / "reviews").mkdir()
    state.path = state.repo / "reviews/packet.json"
    state.packet = packet_for(document(state.task_id, revision=state.basis["contract_revision"],
        generation=state.basis["target_generation"]))
    state.packet["review_target"] = {key: state.basis["target_" + key] for key in ("kind", "value", "base_revision", "generation")}
    state.packet["result_template"] = review_result_template(state.task_id, state.basis["contract_revision"], state.packet["review_target"])
    state.packet["task"].update(verification="Fixture verification", verification_not_required_reason="")
    state.packet["review_session_context"] = dict(version=1, project_id=state.basis["project_id"], execution_id=state.basis["execution_id"])
    state.path.write_bytes(encode(state.packet))
    state.root = state.repo / "operational"
    state.root.mkdir()
    paths = SimpleNamespace(review_wait_root=state.root, fixed_root=state.repo,
        review_preparation_store=lambda *_: state.root / "preparation.sqlite",
        review_finalization_store=lambda *_: state.root / "finalization.sqlite")
    state.resolution = SimpleNamespace(target=state.target, paths=paths)
    enter_context(mock.patch.object(preparation, "resolve", return_value=state.resolution))


def publish_handoff(state, *, route="manual", caller=None):
    return preparation.publish_ready(state.repo, "reviews/packet.json", ["reviews/a.json", "reviews/b.json"], caller or state.caller, route=route)


def ready_handoff(state, *, enter_context):
    state.physical()
    state.root_preparation = state.publish()
    paths = state.resolution.paths
    paths.review_wait_request_store = lambda *_: state.root / "request.sqlite"
    paths.review_wait_dispatch_store = lambda *_: state.root / "dispatch.sqlite"
    paths.review_continuation_store = lambda *_: state.root / "transition.sqlite"
    enter_context(mock.patch.object(continuation, "resolve", return_value=state.resolution))
    state.args = SimpleNamespace(task_id=state.task_id, target_generation=state.basis["target_generation"], directory="reviews/continue-1", records_only=False)
    state.raw = json.dumps(dict(version=1, reviewers=[dict(session_id=A, action="supplement", prior_receipt_id=None, reason="Restore the missing source material")])).encode()


def call_handoff(state):
    return continuation.continue_reviews(state.repo, state.args, state.raw, caller=state.caller)


@dataclass
class HandoffFixture(ReviewFixture):
    _contexts: ExitStack

    def physical(self):
        physical_handoff(self, enter_context=self._contexts.enter_context)

    publish = publish_handoff

    def ready(self):
        ready_handoff(self, enter_context=self._contexts.enter_context)

    call = call_handoff


@contextmanager
def handoff_fixture(*, schema_version, contract):
    with ExitStack() as contexts:
        state = contexts.enter_context(review_fixture(schema_version=schema_version, contract=contract))
        yield HandoffFixture(**{field.name: getattr(state, field.name) for field in fields(ReviewFixture)},
            _contexts=contexts)
