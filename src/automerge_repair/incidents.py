"""Versioned, bounded incident input and authoritative state contracts.

Ingress is a trusted collector boundary, not a webhook authentication endpoint.
Only references and normalized facts belong here, never raw logs or credentials.
"""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

RepositoryName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=200)
]
Commit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Identifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_.:/-]+$", min_length=1, max_length=200)
]
Login = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+(?:\[bot\])?$", max_length=100)
]
# Authority references deliberately exclude query strings, fragments, and userinfo.
Authority = Annotated[
    str,
    StringConstraints(
        pattern=r"^https://(?:api\.)?github\.com/[A-Za-z0-9_./-]+$", max_length=500
    ),
]


class Record(BaseModel):
    """Reject unknown fields and accidental coercion at the collector boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Failure(Record):
    """Exact failed target; workflow run SHA alone is not deployment provenance."""

    repository: RepositoryName
    merge_commit: Commit
    deployment_id: Identifier
    environment: Identifier
    kind: Literal["deployment", "promotion", "health", "release"]
    conclusion: Literal["failure", "success", "cancelled", "skipped"]
    target: Identifier
    target_commit: Commit
    authority: Authority
    run_id: Identifier
    run_attempt: int = Field(ge=1)
    health_authorities: tuple[Authority, ...] = Field(default=(), max_length=20)

    @property
    def incident_id(self) -> str:
        """Run attempts and health observations do not change incident identity."""
        identity = [
            self.repository,
            self.merge_commit,
            self.environment,
            self.deployment_id,
        ]
        return "ar-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()


class AutoMergeProvenance(Record):
    """Historical platform-squash enablement followed by an exact merge."""

    mode: Literal["platform-squash"]
    enabled_by: Login
    enabled_actor_type: Literal["Bot", "User"]
    enabled_event_id: Identifier
    merged_event_id: Identifier
    enabled_at: Annotated[
        str, StringConstraints(pattern=r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
    ]
    merged_at: Annotated[
        str, StringConstraints(pattern=r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
    ]
    merge_commit: Commit
    history_complete: bool
    disabled_after_enable: bool
    authority: Authority


class PullRequest(Record):
    repository: RepositoryName
    number: int = Field(ge=1)
    author: Login
    author_type: Literal["Bot", "User"]
    base_branch: Identifier
    merged: bool
    merge_commit: Commit
    merged_by: Login
    merge_actor_type: Literal["Bot", "User"]
    authority: Authority
    merge_authority: Authority
    dependency_change_authority: Authority
    automerge: AutoMergeProvenance | None = None


class ReleaseProvenance(Record):
    """Explicit release lineage when a release commit differs from the source merge."""

    repository: RepositoryName
    source_commit: Commit
    target_commit: Commit
    target: Identifier
    authority: Authority
    source_authority: Authority


class Candidate(Record):
    """All PR associations must be supplied, with collection completeness explicit."""

    schema_version: Literal[1] = 1
    failure: Failure
    pull_requests: tuple[PullRequest, ...] = Field(default=(), max_length=20)
    associations_complete: bool = False
    release: ReleaseProvenance | None = None


class State(StrEnum):
    DETECTED = "detected"
    CORRELATED = "correlated"
    HUMAN = "needs_human_intervention"


class ApprovalPlaceholder(Record):
    """A durable future gate, never approval authorization."""

    purpose: Literal["repair", "redeploy"]
    status: Literal["not_requested"] = "not_requested"


class AuditEvent(Record):
    sequence: int
    event_id: str
    timestamp: str
    actor: str
    run_id: str
    action: str
    previous_state: str | None
    state: str
    reason: str
    evidence_sha256: str
    policy_sha256: str


class Projection(Record):
    """Linked artifacts cannot overwrite incident state or approval gates."""

    system: Literal["telegram", "github", "openproject", "codex"]
    artifact_id: Identifier | None = None


class Incident(Record):
    incident_id: str
    state: str
    version: int
    candidate: Candidate
    reason: str
    eligibility_verified: bool
    repair_enabled: bool
    policy_sha256: str
    concurrency_keys: tuple[str, ...]
    approvals: tuple[ApprovalPlaceholder, ...]
    evidence: tuple[Candidate, ...]
    audit: tuple[AuditEvent, ...]
    projections: tuple[Projection, ...]
