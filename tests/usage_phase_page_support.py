"""Real inputs shared by phase-page regression and offline measurements."""

from contextlib import closing
from dataclasses import replace

from tests import test_usage_reconciliation_changes_runtime as changed
from tests.test_usage_collection import THREAD, event, usage
from tests.test_usage_attribution import turn
from task_governance_tool import storage, tasks
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.usage_collection import register_source


PHASES = ("operations", "participants", "event_locations", "pending_source",
          "uncertainty_rule", "interval_metadata", "diagnostics", "execution")


def fixture(*, cycles=0, turns=0):
    test = changed.ChangedRuntimeTests()
    try:
        test.prepare_fixture(real_initialization=False)
        for number in range(cycles):
            test.transition("paused", f"pause-{number}", pause_reason="Isolated phase fixture")
            test.transition("in_progress", f"resume-{number}")
        if turns:
            test.append(*(row for n in range(2, turns + 2) for row in (
                event("event_msg", type="task_started", turn_id=turn(n), started_at=n),
                event("turn_context", turn_id=turn(n), model="fixture-model"),
                usage(f"extra-{n}", turn=turn(n)))))
        return test
    except BaseException:
        test.doCleanups()
        raise


def add_tasks_and_sources(test, *, tasks_count=12, sources=4, distinct_threads=False):
    test.transition("ready", "release-initial")
    for number in range(tasks_count):
        with closing(storage.connect_initialized(test.fixture.target)) as core:
            result = tasks.add_task(core, test.fixture.target.project, database_target=test.fixture.target,
                title=f"Isolated phase Task {number}", status="in_progress", caller=CallerIdentity(THREAD))
            core.commit()
        test.fixture.task_id = result.task["task_id"]
        with test.reader() as core:
            generation = core.execute("SELECT generation FROM task_owner_transitions WHERE task_id=? ORDER BY rowid DESC LIMIT 1",
                                      (test.fixture.task_id,)).fetchone()[0]
        acknowledgement = {"ok": True, "command": "task.add", "project_id": test.repository.basis[0],
            "data": {"task": {**result.task, "ownership": {"generation": generation}}, "event": result.event}}
        test.append(*test.fixture.operation(f"add-{number}", acknowledgement, f"add-{number}", direct=True))
        if number + 1 < tasks_count:
            test.transition("ready", f"release-{number}")
    for number in range(sources):
        thread = f"22222222-2222-4222-8222-{number + 1:012x}" if distinct_threads else THREAD
        source = replace(test.fixture.source, thread_id=thread, path=test.fixture.root / f"source-{number}.jsonl")
        register_source(test.repository, source, CallerIdentity(thread))
        test.fixture.write(test.fixture.records(
            event("event_msg", type="task_started", turn_id=turn(1), started_at=1),
            event("turn_context", turn_id=turn(1), model="fixture-model"),
            usage(f"source-{number}", thread=thread, turn=turn(1)), thread=thread), path=source.path)
        test.repository.collect_source(source)
