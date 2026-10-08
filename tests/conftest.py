"""Explicit provenance fixtures; no external services or secrets."""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.schema import CreateSchema

from automerge_repair.config import AppConfig, load_config
from automerge_repair.incidents import (
    AutoMergeProvenance,
    Candidate,
    Failure,
    PullRequest,
)
from automerge_repair.store import IncidentStore

REPOSITORY = "SpencerRWood/infrastructure"
SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app_config() -> AppConfig:
    return load_config(
        {"AUTOMERGE_REPAIR_POLICY_FILE": str(ROOT / "config/policy.toml")}
    )


@pytest.fixture
def candidate() -> Candidate:
    return Candidate(
        failure=Failure(
            repository=REPOSITORY,
            merge_commit=SHA,
            deployment_id="deploy-123",
            environment="infrastructure-dev",
            kind="deployment",
            conclusion="failure",
            target="v1.2.3",
            target_commit=SHA,
            authority=f"https://github.com/{REPOSITORY}/actions/runs/123/attempts/1",
            run_id="123",
            run_attempt=1,
        ),
        pull_requests=(
            PullRequest(
                repository=REPOSITORY,
                number=42,
                author="renovate[bot]",
                author_type="Bot",
                base_branch="main",
                merged=True,
                merge_commit=SHA,
                merged_by="renovate[bot]",
                merge_actor_type="Bot",
                automerge=AutoMergeProvenance(
                    mode="platform-squash",
                    enabled_by="renovate[bot]",
                    enabled_actor_type="Bot",
                    enabled_event_id="enable-1",
                    merged_event_id="merge-1",
                    enabled_at="2026-10-08T10:00:00Z",
                    merged_at="2026-10-08T10:05:00Z",
                    merge_commit=SHA,
                    history_complete=True,
                    disabled_after_enable=False,
                    authority=f"https://github.com/{REPOSITORY}/pull/42",
                ),
                authority=f"https://github.com/{REPOSITORY}/pull/42",
                merge_authority=f"https://api.github.com/repos/{REPOSITORY}/pulls/42",
                dependency_change_authority=(
                    f"https://api.github.com/repos/{REPOSITORY}/pulls/42/files"
                ),
            ),
        ),
        associations_complete=True,
    )


@pytest.fixture
def store(tmp_path: Path) -> Iterator[IncidentStore]:
    test_url = os.environ.get("AUTOMERGE_REPAIR_TEST_POSTGRES_URL")
    result = IncidentStore.from_url(
        test_url or f"sqlite:///{tmp_path / 'incidents.sqlite'}"
    )
    if test_url:
        # Supplemental checks use only an explicitly supplied disposable database.
        # Each test gets a fresh schema; container removal owns eventual cleanup.
        schema = f"ar_test_{uuid4().hex}"
        with result.engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        result.engine.update_execution_options(schema_translate_map={None: schema})
    result.initialize()
    yield result
    result.engine.dispose()
