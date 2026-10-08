"""Dagster execution persists and replays attributable structured audit events."""

from pathlib import Path

import pytest

from automerge_repair.dagster.definitions import defs
from automerge_repair.incidents import AuditEvent, Candidate, State
from automerge_repair.store import IncidentStore

ROOT = Path(__file__).resolve().parents[2]


def configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    monkeypatch.setenv("AUTOMERGE_REPAIR_POLICY_FILE", str(ROOT / "config/policy.toml"))
    monkeypatch.setenv("AUTOMERGE_REPAIR_ENVIRONMENT", "dev")
    url = f"sqlite:///{tmp_path / 'state.sqlite'}"
    monkeypatch.setenv("AUTOMERGE_REPAIR_INCIDENT_DATABASE_URL", url)
    return url


def job_config(candidate: Candidate) -> dict[str, object]:
    return {
        "loggers": {"console": {"config": {"log_level": "INFO"}}},
        "ops": {
            "ingest_incident": {
                "config": {
                    "candidate_json": candidate.model_dump_json(),
                }
            }
        },
    }


def test_ingest_and_replay_audit(
    candidate: Candidate,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url = configure(monkeypatch, tmp_path)
    monkeypatch.setenv("DAGSTER_POSTGRES_PASSWORD", "secret-must-not-be-logged")
    job = defs.resolve_job_def("incident_ingest_job")
    result = job.execute_in_process(run_config=job_config(candidate))
    assert result.success
    incident_id = result.output_for_node("ingest_incident")
    store = IncidentStore.from_url(url)
    try:
        saved = store.get(incident_id)
        assert saved is not None
        assert saved.state == State.CORRELATED
        observations = result.get_asset_observation_events()
        assert len(observations) == 3
        last = observations[-1].asset_observation_data.asset_observation
        assert last is not None
        audit = AuditEvent.model_validate(last.metadata["audit"].value)
        assert audit.run_id == result.run_id
        assert audit.action == "transition"
        retry = job.execute_in_process(run_config=job_config(candidate))
        assert retry.success
        assert store.get(incident_id) == saved
        replay = retry.get_asset_observation_events()[
            -1
        ].asset_observation_data.asset_observation
        assert replay is not None
        assert replay.metadata["audit"].value == last.metadata["audit"].value
        captured = capsys.readouterr()
        assert "secret-must-not-be-logged" not in captured.err + captured.out
        assert "incident_ingested" in captured.err
    finally:
        store.engine.dispose()


def test_human_attention_outcome(
    candidate: Candidate,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    url = configure(monkeypatch, tmp_path)
    incomplete = candidate.model_copy(update={"pull_requests": ()})
    result = defs.resolve_job_def("incident_ingest_job").execute_in_process(
        run_config=job_config(incomplete),
    )
    assert result.success
    store = IncidentStore.from_url(url)
    try:
        incident = store.get(candidate.failure.incident_id)
        assert incident is not None
        assert incident.state == State.HUMAN
        assert incident.reason == "ambiguous_pull_request"
    finally:
        store.engine.dispose()


@pytest.mark.parametrize("failure", ["policy", "database", "storage", "input"])
def test_ingest_fails_closed(
    candidate: Candidate,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    configure(monkeypatch, tmp_path)
    config = job_config(candidate)
    if failure == "policy":
        monkeypatch.delenv("AUTOMERGE_REPAIR_POLICY_FILE")
    elif failure == "database":
        monkeypatch.delenv("AUTOMERGE_REPAIR_INCIDENT_DATABASE_URL")
    elif failure == "storage":
        monkeypatch.setenv(
            "AUTOMERGE_REPAIR_INCIDENT_DATABASE_URL",
            f"sqlite:///{tmp_path / 'missing' / 'state.sqlite'}",
        )
    else:
        config = {"ops": {"ingest_incident": {"config": {"candidate_json": "{}"}}}}
    result = defs.resolve_job_def("incident_ingest_job").execute_in_process(
        run_config=config,
        raise_on_error=False,
    )
    assert not result.success
