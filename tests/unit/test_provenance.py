"""Correlation requires complete, exact Renovate deployment provenance."""

from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError

from automerge_repair.config import AppConfig
from automerge_repair.incidents import (
    Candidate,
    Failure,
    PullRequest,
    ReleaseProvenance,
)
from automerge_repair.provenance import correlate


def test_exact_renovate_automerge(candidate: Candidate, app_config: AppConfig) -> None:
    decision = correlate(candidate, app_config)
    assert decision.eligible
    assert not decision.repair_enabled
    assert len(decision.policy_sha256) == 64
    assert decision.reason == "renovate_platform_automerge"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"repository": "OtherOwner/infrastructure"}, "repository_not_configured"),
        ({"environment": "prod"}, "environment_not_configured"),
        ({"kind": "health"}, "not_authoritative_trigger"),
        ({"kind": "release"}, "not_authoritative_trigger"),
        ({"conclusion": "success"}, "not_failed"),
        ({"conclusion": "cancelled"}, "not_failed"),
        ({"conclusion": "skipped"}, "not_failed"),
        ({"target_commit": "b" * 40}, "target_commit_mismatch"),
        (
            {"authority": "https://github.com/OtherOwner/repo/actions/1"},
            "authority_repository_mismatch",
        ),
    ],
)
def test_failure_ineligible(
    candidate: Candidate, app_config: AppConfig, changes: dict[str, Any], reason: str
) -> None:
    failure = Failure.model_validate({**candidate.failure.model_dump(), **changes})
    decision = correlate(candidate.model_copy(update={"failure": failure}), app_config)
    assert not decision.eligible
    assert decision.reason == reason


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"repository": "OtherOwner/infrastructure"}, "pull_request_target_mismatch"),
        ({"merge_commit": "b" * 40}, "pull_request_target_mismatch"),
        ({"merged": False}, "pull_request_target_mismatch"),
        ({"base_branch": "dev"}, "pull_request_target_mismatch"),
        ({"author": "human"}, "not_renovate_authored"),
        ({"author_type": "User"}, "not_renovate_authored"),
        ({"merged_by": "human"}, "automatic_merge_unverified"),
        ({"merge_actor_type": "User"}, "automatic_merge_unverified"),
        ({"automerge": None}, "automatic_merge_unverified"),
        (
            {"authority": "https://github.com/SpencerRWood/infrastructure/pull/43"},
            "pull_request_authority_mismatch",
        ),
    ],
)
def test_pr_ineligible(
    candidate: Candidate, app_config: AppConfig, changes: dict[str, Any], reason: str
) -> None:
    pr = PullRequest.model_validate(
        {**candidate.pull_requests[0].model_dump(), **changes}
    )
    decision = correlate(
        candidate.model_copy(update={"pull_requests": (pr,)}), app_config
    )
    assert not decision.eligible
    assert decision.reason == reason


@pytest.mark.parametrize("count", [0, 2])
def test_ambiguous_associations(
    candidate: Candidate, app_config: AppConfig, count: int
) -> None:
    observed = candidate.model_copy(
        update={"pull_requests": candidate.pull_requests * count}
    )
    assert correlate(observed, app_config).reason == "ambiguous_pull_request"
    incomplete = candidate.model_copy(update={"associations_complete": False})
    assert not correlate(incomplete, app_config).eligible


@pytest.mark.parametrize("kind", ["deployment", "promotion"])
@pytest.mark.parametrize("same_commit", [True, False])
def test_release_lineage(
    candidate: Candidate, app_config: AppConfig, kind: str, same_commit: bool
) -> None:
    failure = candidate.failure.model_copy(
        update={
            "target_commit": candidate.failure.merge_commit
            if same_commit
            else "b" * 40,
            "kind": kind,
        }
    )
    release = ReleaseProvenance(
        repository=failure.repository,
        source_commit=failure.merge_commit,
        target_commit=failure.target_commit,
        target=failure.target,
        authority=f"https://github.com/{failure.repository}/releases/tag/{failure.target}",
        source_authority=f"https://github.com/{failure.repository}/actions/runs/122",
    )
    observed = candidate.model_copy(update={"failure": failure, "release": release})
    assert correlate(observed, app_config).eligible
    for field, value in {
        "repository": "OtherOwner/infrastructure",
        "source_commit": "c" * 40,
        "target_commit": "c" * 40,
        "target": "v1.2.4",
    }.items():
        conflict = observed.model_copy(
            update={"release": release.model_copy(update={field: value})}
        )
        assert not correlate(conflict, app_config).eligible


def test_both_policy_targets(candidate: Candidate, app_config: AppConfig) -> None:
    homelab = Candidate.model_validate_json(
        candidate.model_dump_json()
        .replace(candidate.failure.repository, "SpencerRWood/homelab")
        .replace("infrastructure-dev", "homelab")
    )
    assert correlate(homelab, app_config).eligible
    enabled = replace(
        app_config,
        repositories=tuple(
            replace(p, repair_enabled=True) for p in app_config.repositories
        ),
    )
    assert correlate(candidate, enabled).repair_enabled


@pytest.mark.parametrize(
    "changes",
    [
        {"merge_commit": "shortsha"},
        {"run_attempt": True},
        {"run_attempt": 0},
        {"deployment_id": ""},
        {"authority": "https://github.com/repo/actions?token=secret"},
        {"authority": "https://secret@github.com/repo/actions"},
        {"authority": "https://example.com/log"},
        {"raw_log": "secret"},
    ],
)
def test_malformed_evidence_rejected(
    candidate: Candidate, changes: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        Failure.model_validate({**candidate.failure.model_dump(), **changes})
