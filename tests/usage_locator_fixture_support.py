"""Actual operational assignments over a caller-owned isolated review fixture."""

from pathlib import Path
from types import SimpleNamespace

from task_governance_tool.review_answer_collection_repository import CollectionRepository
from task_governance_tool.review_preparation_repository import PreparationRecord, PreparationRepository
from task_governance_tool.state_resolver import canonical_state_paths


def publish_collection(fixture, paths, root, metadata, continuation_id=None):
    native = fixture.basis
    repository = CollectionRepository(paths.review_answer_collection_store(native["parent_session_id"],
        native["task_id"], native["target_generation"], root.preparation_id, continuation_id))
    repository.initialize(tuple(item["basis"] for item in metadata))
    with repository.serial() as lease:
        repository.update_many(lease, {index: lambda record, item=item: dict(record, binding=item["binding"],
            attempts=[dict(record["attempts"][0], turn_id=item["binding"].get("initial_turn_id"))])
            for index, item in enumerate(metadata, 1)})
    return repository


def prepare_pending_locator_assignment(fixture):
    """Prepare physical stores, but no patches, sources, migrations or host calls.

    Callers own review_fixture lifetime and the admission._paths injection.
    fixture.add(connection, 1) subsequently registers the exact selected source.
    """
    paths = canonical_state_paths(Path(__file__).resolve().parents[1] / "task-governance-tool", repo=fixture.repo)
    paths.review_wait_root.mkdir(parents=True)
    metadata, _ = fixture.metadata(1)
    other, _ = fixture.metadata(2)
    native = fixture.basis
    root = PreparationRecord(version=2, preparation_id=metadata["basis"]["preparation_id"],
        basis=native, route="manual", packet_path="reviews/packet.json", packet_digest=metadata["basis"]["packet_digest"][7:],
        result_paths=("reviews/a.json", "reviews/b.json"), finalization_store=None,
        created_at="2026-10-24T00:00:00Z", collection_bases=(metadata["basis"], other["basis"]))
    preparation = PreparationRepository(paths.review_preparation_store(
        native["parent_session_id"], native["task_id"], native["target_generation"]))
    with preparation.serial(initial=root):
        pass
    collection = publish_collection(fixture, paths, root, (metadata, other))
    return SimpleNamespace(paths=paths, metadata=metadata, other=other, root=root,
        preparation=preparation, collection=collection, parent=native["parent_session_id"],
        reviewer=metadata["binding"]["reviewer_session_id"])
