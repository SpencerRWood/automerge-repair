"""Read-only GitHub PR enrichment; callers own authenticated bounded transport.

This is deliberately not a monitor or an API mutation client. A missing or
truncated response leaves associations incomplete and correlation fails closed.
"""

from typing import Protocol

from pydantic import ValidationError

from automerge_repair.config import AppConfig
from automerge_repair.incidents import (
    AutoMergeProvenance,
    Candidate,
    Failure,
    PullRequest,
    ReleaseProvenance,
)

AUTOMERGE_QUERY = """
query IncidentAutomerge($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      timelineItems(first: 100, itemTypes: [AUTO_SQUASH_ENABLED_EVENT,
        AUTO_MERGE_ENABLED_EVENT, AUTO_REBASE_ENABLED_EVENT,
        AUTO_MERGE_DISABLED_EVENT, MERGED_EVENT]) {
        pageInfo { hasNextPage }
        nodes {
          __typename
          ... on AutoSquashEnabledEvent { id createdAt actor { login __typename } }
          ... on AutoMergeEnabledEvent { id createdAt actor { login __typename } }
          ... on AutoRebaseEnabledEvent { id createdAt actor { login __typename } }
          ... on AutoMergeDisabledEvent { id createdAt }
          ... on MergedEvent { id createdAt actor { login __typename } commit { oid } }
        }
      }
    }
  }
}
"""


class GitHubReader(Protocol):
    """Transport must use bounded timeouts/retries and raise on non-success."""

    def get(self, path: str) -> object: ...

    def graphql(self, query: str, variables: dict[str, str | int]) -> object: ...


def _actor_login(actor: dict[str, str]) -> str:
    """GraphQL omits REST's [bot] suffix; type must independently be Bot."""
    login = actor["login"]
    if not isinstance(login, str):
        raise TypeError("Malformed GitHub actor login")
    if actor["__typename"] == "Bot" and not login.endswith("[bot]"):
        return f"{login}[bot]"
    return login


def _automerge(
    reader: GitHubReader, failure: Failure, pr: PullRequest
) -> AutoMergeProvenance | None:
    """Require complete history with last squash enablement followed by merge."""
    owner, name = failure.repository.split("/")
    response = reader.graphql(
        AUTOMERGE_QUERY, {"owner": owner, "name": name, "number": pr.number}
    )
    if not isinstance(response, dict) or response.get("errors"):
        return None
    timeline = response["data"]["repository"]["pullRequest"]["timelineItems"]
    if timeline["pageInfo"]["hasNextPage"] is not False:
        return None
    nodes = timeline["nodes"]
    if not isinstance(nodes, list) or len(nodes) < 2:
        return None
    enabled, merged = nodes[-2:]
    if (
        enabled["__typename"] != "AutoSquashEnabledEvent"
        or merged["__typename"] != "MergedEvent"
        or sum(node["__typename"] == "MergedEvent" for node in nodes) != 1
        or _actor_login(merged["actor"]) != pr.merged_by
        or merged["actor"]["__typename"] != "Bot"
    ):
        return None
    return AutoMergeProvenance(
        mode="platform-squash",
        enabled_by=_actor_login(enabled["actor"]),
        enabled_actor_type=enabled["actor"]["__typename"],
        enabled_event_id=enabled["id"],
        merged_event_id=merged["id"],
        enabled_at=enabled["createdAt"],
        merged_at=merged["createdAt"],
        merge_commit=merged["commit"]["oid"],
        history_complete=True,
        disabled_after_enable=False,
        authority=f"https://github.com/{failure.repository}/pull/{pr.number}",
    )


def collect_candidate(  # noqa: PLR0911 -- explicit fail-closed boundaries
    failure: Failure,
    config: AppConfig,
    reader: GitHubReader,
    release: ReleaseProvenance | None = None,
) -> Candidate:
    """Read exact commit associations and merged PR facts; no nearest-PR search."""
    candidate = Candidate(failure=failure, release=release)
    if config.repository_policy(failure.repository) is None:
        return candidate
    prefix = f"/repos/{failure.repository}"
    try:
        associations = reader.get(
            f"{prefix}/commits/{failure.merge_commit}/pulls?per_page=100"
        )
        if not isinstance(associations, list) or len(associations) != 1:
            return candidate
        association = associations[0]
        if (
            not isinstance(association, dict)
            or type(association.get("number")) is not int
        ):
            return candidate
        number = association["number"]
        detail = reader.get(f"{prefix}/pulls/{number}")
        if not isinstance(detail, dict):
            return candidate
        author = detail["user"]
        merger = detail["merged_by"]
        base = detail["base"]
        # Full repository identity comes from GitHub's base repository.
        pr = PullRequest(
            repository=base["repo"]["full_name"],
            number=detail["number"],
            author=author["login"],
            author_type=author["type"],
            base_branch=base["ref"],
            merged=detail["merged"],
            merge_commit=detail["merge_commit_sha"],
            merged_by=merger["login"],
            merge_actor_type=merger["type"],
            authority=detail["html_url"],
            merge_authority=f"https://api.github.com{prefix}/pulls/{number}",
            dependency_change_authority=(
                f"https://api.github.com{prefix}/pulls/{number}/files"
            ),
        )
        if pr.number != number:
            return candidate
        pr = pr.model_copy(update={"automerge": _automerge(reader, failure, pr)})
        return candidate.model_copy(
            update={"pull_requests": (pr,), "associations_complete": True}
        )
    except KeyError, TypeError, ValidationError, OSError:
        # Persist human attention through the normal ingestion path. The transport
        # must not include response bodies or credentials in exceptions/logs.
        return candidate
