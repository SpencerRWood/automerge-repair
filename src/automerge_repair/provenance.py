"""Pure correlation against repository policy and explicit authority facts."""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime

from automerge_repair.config import AppConfig
from automerge_repair.incidents import AutoMergeProvenance, Candidate


def _valid_automerge(automerge: AutoMergeProvenance) -> bool:
    try:
        return (
            automerge.history_complete
            and not automerge.disabled_after_enable
            and datetime.fromisoformat(automerge.enabled_at)
            <= datetime.fromisoformat(automerge.merged_at)
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class Correlation:
    eligible: bool
    reason: str
    repair_enabled: bool
    policy_sha256: str


def correlate(candidate: Candidate, config: AppConfig) -> Correlation:  # noqa: PLR0911
    """Fail closed; never infer automerge from a PR title or configured flag."""
    failure = candidate.failure
    policy = config.repository_policy(failure.repository)
    fingerprint = hashlib.sha256(
        json.dumps(asdict(config), sort_keys=True).encode()
    ).hexdigest()

    def result(reason: str, *, eligible: bool = False) -> Correlation:
        return Correlation(
            eligible, reason, bool(policy and policy.repair_enabled), fingerprint
        )

    if policy is None:
        return result("repository_not_configured")
    if (
        dict(policy.deployment_environments).get(config.environment)
        != failure.environment
    ):
        return result("environment_not_configured")
    if failure.kind not in {"deployment", "promotion"}:
        return result("not_authoritative_trigger")
    if failure.conclusion != "failure":
        return result("not_failed")
    release = candidate.release
    if (release is None and failure.target_commit != failure.merge_commit) or (
        release is not None
        and (
            release.repository != failure.repository
            or release.source_commit != failure.merge_commit
            or release.target_commit != failure.target_commit
            or release.target != failure.target
        )
    ):
        return result("target_commit_mismatch")
    if not candidate.associations_complete or len(candidate.pull_requests) != 1:
        return result("ambiguous_pull_request")
    pr = candidate.pull_requests[0]
    if (
        pr.repository != failure.repository
        or pr.merge_commit != failure.merge_commit
        or not pr.merged
        or pr.base_branch != "main"
    ):
        return result("pull_request_target_mismatch")
    if pr.author != policy.renovate_login or pr.author_type != "Bot":
        return result("not_renovate_authored")
    # A bot-authored PR alone cannot prove automatic merge completion.
    if pr.merged_by != policy.renovate_login or pr.merge_actor_type != "Bot":
        return result("automatic_merge_unverified")
    automerge = pr.automerge
    if (
        automerge is None
        or automerge.mode != policy.automerge_mode
        or automerge.enabled_by != policy.renovate_login
        or automerge.enabled_actor_type != "Bot"
        or automerge.merge_commit != pr.merge_commit
        or not _valid_automerge(automerge)
    ):
        return result("automatic_merge_unverified")
    authority_prefixes = (
        f"https://github.com/{failure.repository}/",
        f"https://api.github.com/repos/{failure.repository}/",
    )
    authorities = (
        failure.authority,
        *failure.health_authorities,
        pr.authority,
        pr.merge_authority,
        pr.dependency_change_authority,
        automerge.authority,
        *((release.authority, release.source_authority) if release else ()),
    )
    if any(not authority.startswith(authority_prefixes) for authority in authorities):
        return result("authority_repository_mismatch")
    if pr.authority != f"https://github.com/{pr.repository}/pull/{pr.number}":
        return result("pull_request_authority_mismatch")
    return result("renovate_platform_automerge", eligible=True)
