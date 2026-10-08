"""Bounded read-only collection from exact GitHub associations and PR details."""

import json
from typing import Any

import pytest

from automerge_repair.config import AppConfig
from automerge_repair.github import collect_candidate
from automerge_repair.incidents import Candidate
from automerge_repair.provenance import correlate


class Reader:
    def __init__(self, associations: object, detail: object) -> None:
        self.associations = associations
        self.detail = detail
        self.calls: list[str] = []
        self.timeline: object = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "timelineItems": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [
                                {
                                    "__typename": "AutoSquashEnabledEvent",
                                    "id": "enable-1",
                                    "createdAt": "2026-10-08T10:00:00Z",
                                    "actor": {
                                        "login": "renovate",
                                        "__typename": "Bot",
                                    },
                                },
                                {
                                    "__typename": "MergedEvent",
                                    "id": "merge-1",
                                    "createdAt": "2026-10-08T10:05:00Z",
                                    "actor": {
                                        "login": "renovate",
                                        "__typename": "Bot",
                                    },
                                    "commit": {"oid": "a" * 40},
                                },
                            ],
                        }
                    }
                }
            },
        }
        self.graphql_calls: list[dict[str, str | int]] = []

    def get(self, path: str) -> object:
        self.calls.append(path)
        return self.associations if "commits/" in path else self.detail

    def graphql(self, _query: str, variables: dict[str, str | int]) -> object:
        self.graphql_calls.append(variables)
        return self.timeline


@pytest.fixture
def detail(candidate: Candidate) -> dict[str, Any]:
    pr = candidate.pull_requests[0]
    return {
        "number": pr.number,
        "user": {"login": pr.author, "type": pr.author_type},
        "merged_by": {"login": pr.merged_by, "type": pr.merge_actor_type},
        "base": {"repo": {"full_name": pr.repository}, "ref": pr.base_branch},
        "merged": pr.merged,
        "merge_commit_sha": pr.merge_commit,
        "html_url": pr.authority,
    }


def test_reads_exact_commit_and_pr(
    candidate: Candidate, app_config: AppConfig, detail: dict[str, Any]
) -> None:
    reader = Reader([{"number": 42}], detail)
    collected = collect_candidate(candidate.failure, app_config, reader)
    assert collected == candidate
    assert correlate(collected, app_config).eligible
    assert reader.calls == [
        f"/repos/{candidate.failure.repository}/commits/"
        f"{candidate.failure.merge_commit}/pulls?per_page=100",
        f"/repos/{candidate.failure.repository}/pulls/42",
    ]
    assert reader.graphql_calls == [
        {"owner": "SpencerRWood", "name": "infrastructure", "number": 42}
    ]


@pytest.mark.parametrize(
    "associations",
    [
        [],
        [{"number": 42}, {"number": 43}],
        [{"number": 42}] * 100,
        None,
        {},
        ["invalid"],
        [{"number": "42"}],
        [{"number": True}],
    ],
)
def test_incomplete_or_ambiguous_collection(
    candidate: Candidate,
    app_config: AppConfig,
    detail: dict[str, Any],
    associations: object,
) -> None:
    reader = Reader(associations, detail)
    collected = collect_candidate(candidate.failure, app_config, reader)
    assert not collected.associations_complete
    assert not correlate(collected, app_config).eligible
    assert len(reader.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"merged_by": None},
        {"base": {}},
        {"user": {"login": "renovate[bot]"}},
        {"merged": "true"},
        {"number": 43},
        {"merge_commit_sha": None},
    ],
)
def test_malformed_details_fail_closed(
    candidate: Candidate,
    app_config: AppConfig,
    detail: dict[str, Any],
    changes: dict[str, Any],
) -> None:
    reader = Reader([{"number": 42}], {**detail, **changes})
    assert not collect_candidate(
        candidate.failure, app_config, reader
    ).associations_complete


def test_non_object_pr_details(candidate: Candidate, app_config: AppConfig) -> None:
    reader = Reader([{"number": 42}], [])
    assert not collect_candidate(
        candidate.failure, app_config, reader
    ).associations_complete


def test_unknown_repository_does_not_read_api(
    candidate: Candidate, app_config: AppConfig, detail: dict[str, Any]
) -> None:
    reader = Reader([{"number": 42}], detail)
    failure = candidate.failure.model_copy(
        update={"repository": "OtherOwner/infrastructure"}
    )
    assert not collect_candidate(failure, app_config, reader).associations_complete
    assert not reader.calls


def test_reader_failure_is_incomplete(
    candidate: Candidate, app_config: AppConfig
) -> None:
    class UnavailableReader:
        def get(self, _path: str) -> object:
            raise OSError("provider unavailable")

        def graphql(self, _query: str, _variables: dict[str, str | int]) -> object:
            raise OSError("provider unavailable")

    collected = collect_candidate(candidate.failure, app_config, UnavailableReader())
    assert not collected.associations_complete
    assert not correlate(collected, app_config).eligible


@pytest.mark.parametrize(
    "fault",
    [
        "truncated",
        "empty",
        "disabled",
        "human_enable",
        "human_merge",
        "wrong_commit",
        "missing_fields",
        "errors",
        "not_object",
        "multiple_merge",
        "wrong_mode",
        "invalid_date",
        "enable_after_merge",
        "wrong_bot",
        "user_named_renovate",
        "malformed_actor",
    ],
)
def test_platform_automerge_history_fails_closed(  # noqa: PLR0912 -- fault matrix
    candidate: Candidate,
    app_config: AppConfig,
    detail: dict[str, Any],
    fault: str,
) -> None:
    reader = Reader([{"number": 42}], detail)
    response = json.loads(json.dumps(reader.timeline))
    timeline = response["data"]["repository"]["pullRequest"]["timelineItems"]
    nodes = timeline["nodes"]
    if fault == "truncated":
        timeline["pageInfo"]["hasNextPage"] = True
    elif fault == "empty":
        timeline["nodes"] = []
    elif fault == "disabled":
        nodes.insert(1, {"__typename": "AutoMergeDisabledEvent"})
    elif fault == "human_enable":
        nodes[0]["actor"] = {"login": "human", "__typename": "User"}
    elif fault == "human_merge":
        nodes[1]["actor"] = {"login": "human", "__typename": "User"}
    elif fault == "wrong_commit":
        nodes[1]["commit"]["oid"] = "b" * 40
    elif fault == "missing_fields":
        response = {}
    elif fault == "errors":
        response["errors"] = [{"message": "unavailable"}]
    elif fault == "not_object":
        response = None
    elif fault == "multiple_merge":
        nodes.insert(0, nodes[-1])
    elif fault == "wrong_mode":
        nodes[0]["__typename"] = "AutoRebaseEnabledEvent"
    elif fault == "invalid_date":
        nodes[0]["createdAt"] = "2026-99-99T10:00:00Z"
    elif fault == "enable_after_merge":
        nodes[0]["createdAt"] = "2026-10-08T11:00:00Z"
    elif fault == "wrong_bot":
        nodes[0]["actor"]["login"] = "another-bot"
    elif fault == "user_named_renovate":
        nodes[0]["actor"] = {"login": "renovate", "__typename": "User"}
    else:
        nodes[0]["actor"]["login"] = 123
    reader.timeline = response
    assert not correlate(
        collect_candidate(candidate.failure, app_config, reader), app_config
    ).eligible


def test_snapshot_is_serializable(candidate: Candidate) -> None:
    assert (
        Candidate.model_validate_json(json.dumps(candidate.model_dump(mode="json")))
        == candidate
    )
