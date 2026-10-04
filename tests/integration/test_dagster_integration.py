"""Execute read-only jobs through Dagster, including fail-closed policy."""

import json
from pathlib import Path

import pytest

from automerge_repair.dagster.definitions import defs

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_smoke_needs_no_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOMERGE_REPAIR_POLICY_FILE", raising=False)
    result = defs.resolve_job_def("runtime_smoke_job").execute_in_process()
    assert result.success
    assert result.output_for_node("runtime_smoke") == "ok"


def test_health_checks_configured_policy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AUTOMERGE_REPAIR_POLICY_FILE", str(ROOT / "config/policy.toml"))
    monkeypatch.setenv("AUTOMERGE_REPAIR_ENVIRONMENT", "dev")
    monkeypatch.setenv("DAGSTER_POSTGRES_PASSWORD", "secret-must-not-be-logged")
    result = defs.resolve_job_def("foundation_health_job").execute_in_process()
    assert result.success
    assert result.output_for_node("foundation_health") == {
        "status": "ok",
        "environment": "dev",
        "repository_count": 2,
    }
    captured = capsys.readouterr()
    records = [json.loads(line) for line in captured.err.splitlines()]
    health = next(
        record for record in records if record.get("event") == "foundation_health"
    )
    assert health["status"] == "ok"
    assert health["environment"] == "dev"
    assert health["repository_count"] == 2
    assert health["dagster_meta"]["run_id"] == result.run_id
    assert "secret-must-not-be-logged" not in captured.err + captured.out


def test_health_fails_closed_without_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOMERGE_REPAIR_POLICY_FILE", raising=False)
    result = defs.resolve_job_def("foundation_health_job").execute_in_process(
        raise_on_error=False,
    )
    assert not result.success
