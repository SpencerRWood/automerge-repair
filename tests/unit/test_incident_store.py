"""Identity, durability, atomic writes and persistent concurrency exclusion."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from automerge_repair.config import AppConfig
from automerge_repair.incidents import Candidate, State
from automerge_repair.store import CLAIMS, IncidentStore, StateConflictError


def test_identity_and_duplicate_delivery(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    incident = store.ingest(candidate, app_config, "dagster-run-1")
    assert incident.state == State.CORRELATED
    assert incident.version == 1
    assert [event.sequence for event in incident.audit] == [1, 2, 3]
    assert all(event.timestamp.endswith("+00:00") for event in incident.audit)
    assert all(event.run_id == "dagster-run-1" for event in incident.audit)
    assert all(event.actor == "dagster:incident_ingest" for event in incident.audit)
    assert all(approval.status == "not_requested" for approval in incident.approvals)
    assert len(incident.projections) == 4
    assert all(projection.artifact_id is None for projection in incident.projections)
    assert store.ingest(candidate, app_config, "dagster-run-2") == incident
    assert store.get(incident.incident_id) == incident
    assert store.get("missing") is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "OtherOwner/infrastructure"),
        ("merge_commit", "b" * 40),
        ("environment", "prod"),
        ("deployment_id", "deploy-124"),
    ],
)
def test_identity_boundaries(candidate: Candidate, field: str, value: str) -> None:
    assert (
        candidate.failure.model_copy(update={field: value}).incident_id
        != candidate.failure.incident_id
    )
    retry = candidate.failure.model_copy(update={"run_attempt": 2, "run_id": "124"})
    assert retry.incident_id == candidate.failure.incident_id
    assert (
        candidate.failure.model_copy(update={"kind": "promotion"}).incident_id
        == candidate.failure.incident_id
    )


def test_restarts_and_supporting_evidence(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    original = store.ingest(candidate, app_config, "first")
    reopened = IncidentStore.from_url(str(store.engine.url))
    reopened.engine.update_execution_options(**store.engine.get_execution_options())
    reopened.initialize()
    try:
        assert reopened.get(original.incident_id) == original
        retry = candidate.model_copy(
            update={
                "failure": candidate.failure.model_copy(
                    update={
                        "run_attempt": 2,
                        "health_authorities": (
                            f"https://github.com/{candidate.failure.repository}/actions/runs/123",
                        ),
                    }
                )
            }
        )
        updated = reopened.ingest(retry, app_config, "second")
        assert updated.state == State.CORRELATED
        assert updated.candidate == candidate
        assert len(updated.evidence) == 2
        assert len(updated.audit) == 4
        assert updated.audit[-1].action == "evidence_added"
        assert reopened.ingest(retry, app_config, "third") == updated
    finally:
        reopened.engine.dispose()


def test_conflicting_provenance_never_reauthorizes(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    store.ingest(candidate, app_config, "first")
    conflict = candidate.model_copy(update={"associations_complete": False})
    incident = store.ingest(conflict, app_config, "conflict")
    assert incident.state == State.HUMAN
    assert not incident.eligibility_verified
    assert incident.reason == "conflicting_provenance_or_policy"
    assert store.ingest(candidate, app_config, "retry") == incident
    with store.engine.connect() as connection:
        assert not connection.execute(select(CLAIMS)).all()


def test_policy_changes_fail_closed_and_deduplicate(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    store.ingest(candidate, app_config, "first")
    new_config = replace(
        app_config,
        repositories=tuple(
            replace(p, repair_enabled=True) for p in app_config.repositories
        ),
    )
    changed = store.ingest(candidate, new_config, "changed-policy")
    assert changed.state == State.HUMAN
    assert changed.audit[-1].policy_sha256 != changed.policy_sha256
    assert store.ingest(candidate, new_config, "retry") == changed


def test_persistent_repository_concurrency(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    store.ingest(candidate, app_config, "first")
    another = candidate.model_copy(
        update={
            "failure": candidate.failure.model_copy(update={"deployment_id": "other"})
        }
    )
    blocked = store.ingest(another, app_config, "second")
    assert blocked.state == State.HUMAN
    assert blocked.reason == "concurrent_repository_incident"
    assert store.ingest(another, app_config, "third") == blocked
    with store.engine.connect() as connection:
        assert len(connection.execute(select(CLAIMS)).all()) == 3


def test_concurrent_duplicate_workers(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda run: store.ingest(candidate, app_config, str(run)), range(8)
            )
        )
    assert len({incident.incident_id for incident in results}) == 1
    assert all(len(incident.audit) == 3 for incident in results)
    with store.engine.connect() as connection:
        assert len(connection.execute(select(CLAIMS)).all()) == 3


def test_transaction_failure_leaves_no_partial_state(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    def reject_update(
        _connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.startswith("UPDATE"):
            raise OperationalError("test", {}, OSError("disk unavailable"))

    event.listen(store.engine, "before_cursor_execute", reject_update)
    try:
        with pytest.raises(OperationalError):
            store.ingest(candidate, app_config, "failure")
    finally:
        event.remove(store.engine, "before_cursor_execute", reject_update)
    assert store.get(candidate.failure.incident_id) is None
    with store.engine.connect() as connection:
        assert not connection.execute(select(CLAIMS)).all()
    assert store.ingest(candidate, app_config, "retry").state == State.CORRELATED


def test_concurrent_observations_preserve_all_evidence(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    initial = store.ingest(candidate, app_config, "first")
    retries = [
        candidate.model_copy(
            update={
                "failure": candidate.failure.model_copy(update={"run_attempt": attempt})
            }
        )
        for attempt in range(2, 6)
    ]
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda value: store.ingest(value, app_config, "retry"), retries
            )
        )
    assert all(result.state == State.CORRELATED for result in results)
    final = store.get(initial.incident_id)
    assert final is not None
    assert {item.failure.run_attempt for item in final.evidence} == set(range(1, 6))
    assert final.version == 5


def test_racing_distinct_incidents_cannot_both_claim_repository(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    another = candidate.model_copy(
        update={
            "failure": candidate.failure.model_copy(update={"deployment_id": "other"})
        }
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda value: store.ingest(value, app_config, "race"),
                (candidate, another),
            )
        )
    assert sorted(result.state for result in results) == sorted(
        [State.CORRELATED, State.HUMAN]
    )


def test_terminal_state_transition_is_rejected(
    candidate: Candidate, app_config: AppConfig, store: IncidentStore
) -> None:
    invalid = candidate.model_copy(update={"pull_requests": ()})
    incident = store.ingest(invalid, app_config, "first")
    assert incident.state == State.HUMAN
    with pytest.raises(StateConflictError, match="terminal"):
        store._audit(incident, State.CORRELATED, "bypass", "run", "transition")


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_memory_storage_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="requires"):
        IncidentStore.from_url(url)


def test_contention_is_bounded_and_accepts_no_state(
    candidate: Candidate,
    app_config: AppConfig,
    store: IncidentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = []

    def conflicting_write(*_args: object) -> None:
        attempts.append("conflict")
        raise StateConflictError("Concurrent worker changed authoritative state")

    monkeypatch.setattr(store, "_ingest", conflicting_write)
    with pytest.raises(StateConflictError, match="contention"):
        store.ingest(candidate, app_config, "run")
    assert len(attempts) == 3
    assert store.get(candidate.failure.incident_id) is None


def test_missing_parent_directory_unavailable(tmp_path: Path) -> None:
    store = IncidentStore.from_url(f"sqlite:///{tmp_path / 'missing' / 'state.sqlite'}")
    try:
        with pytest.raises(OperationalError):
            store.initialize()
    finally:
        store.engine.dispose()
