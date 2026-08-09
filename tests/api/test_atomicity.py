from pathlib import Path

import pytest

from apps.api.models import ApprovalStatus, Stage
from apps.api.service import DEMO_TASK_ID, ResearchOpsService
from apps.api.store import SQLiteStore


def test_failed_execute_entry_rolls_back_approval_task_evidence_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStore(str(tmp_path / "atomic.sqlite3"))
    service = ResearchOpsService(store)
    service.reset_demo("happy_path")
    paused = service.autorun(DEMO_TASK_ID)
    pending = paused["task"]["pending_approval"]
    decision = service.decide_approval(
        pending["id"], "approved", "atomicity-test", pending["action_digest"]
    )

    def fail_before_execution_evidence(*_args: object) -> None:
        raise RuntimeError("simulated executor handoff failure")

    monkeypatch.setattr(service, "_enter_execute", fail_before_execution_evidence)
    with pytest.raises(RuntimeError, match="simulated executor handoff failure"):
        service.advance(DEMO_TASK_ID, approval_token=decision["approval_token"])

    task = store.get_task(DEMO_TASK_ID)
    approval = store.latest_approval(task.id, task.generation)
    assert task.stage == Stage.APPROVAL
    assert approval is not None and approval.status == ApprovalStatus.APPROVED
    assert not any(record.kind.value == "code" for record in store.list_evidence(task.id, task.generation))
    events = store.list_events(task.id, task.generation, limit=1000)
    assert not any(event.event_type == "approval.token.consumed" for event in events)
    assert store.verify_event_chain(task.id, task.generation) is True
