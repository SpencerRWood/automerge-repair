"""Transactional incident authority shared by Dagster workers.

State, evidence, audit, and persistent concurrency claims commit together.
SQLite file storage supports offline tests; deployment supplies PostgreSQL.
"""

import hashlib
from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from automerge_repair.config import AppConfig
from automerge_repair.incidents import (
    ApprovalPlaceholder,
    AuditEvent,
    Candidate,
    Incident,
    Projection,
    State,
)
from automerge_repair.provenance import Correlation, correlate

SCHEMA = MetaData()
INCIDENTS = Table(
    "automerge_repair_incidents_v1",
    SCHEMA,
    Column("incident_id", String(67), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("document", Text, nullable=False),
)
CLAIMS = Table(
    "automerge_repair_claims_v1",
    SCHEMA,
    Column("claim_key", String(500), primary_key=True),
    Column("incident_id", String(67), nullable=False),
)


class StateConflictError(Exception):
    """The caller must retry from authoritative state, never overwrite it."""


def concurrency_keys(candidate: Candidate) -> tuple[str, ...]:
    failure = candidate.failure
    return (
        f"repository:{failure.repository}",
        f"commit:{failure.repository}:{failure.merge_commit}",
        f"deployment:{failure.repository}:{failure.environment}:{failure.deployment_id}",
    )


class IncidentStore:
    """No external side effects; state transitions are bounded to Story #481."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def from_url(cls, url: str) -> IncidentStore:
        """Require explicit durable storage; never silently use in-memory state."""
        engine = create_engine(url, hide_parameters=True)
        if engine.dialect.name not in {"sqlite", "postgresql"} or (
            engine.dialect.name == "sqlite"
            and engine.url.database in {None, "", ":memory:"}
        ):
            engine.dispose()
            raise ValueError("Incident storage requires PostgreSQL or a SQLite file")
        return cls(engine)

    def initialize(self) -> None:
        """Create versioned tables without altering or dropping existing state."""
        SCHEMA.create_all(self.engine)

    def get(self, incident_id: str) -> Incident | None:
        with self.engine.connect() as connection:
            return self._get(connection, incident_id)

    @staticmethod
    def _get(connection: Connection, incident_id: str) -> Incident | None:
        document = connection.execute(
            select(INCIDENTS.c.document).where(INCIDENTS.c.incident_id == incident_id)
        ).scalar_one_or_none()
        return Incident.model_validate_json(document) if document else None

    @staticmethod
    def _audit(  # noqa: PLR0913 -- explicit attribution for persisted events
        incident: Incident,
        state: State,
        reason: str,
        run_id: str,
        action: str,
        *,
        candidate: Candidate | None = None,
        policy_sha256: str | None = None,
    ) -> Incident:
        previous = incident.state if incident.audit else None
        if action == "transition" and (previous, state) not in {
            (State.DETECTED, State.CORRELATED),
            (State.DETECTED, State.HUMAN),
            (State.CORRELATED, State.HUMAN),
        }:
            raise StateConflictError("Invalid or terminal incident transition")
        sequence = len(incident.audit) + 1
        event = AuditEvent(
            sequence=sequence,
            event_id=f"{incident.incident_id}:{sequence}",
            timestamp=datetime.now(UTC).isoformat(),
            actor="dagster:incident_ingest",
            run_id=run_id,
            action=action,
            previous_state=previous,
            state=str(state),
            reason=reason,
            evidence_sha256=IncidentStore.evidence_sha256(
                candidate or incident.candidate
            ),
            policy_sha256=policy_sha256 or incident.policy_sha256,
        )
        return incident.model_copy(
            update={
                "state": str(state),
                "reason": reason,
                "audit": (*incident.audit, event),
            }
        )

    @staticmethod
    def _claim(connection: Connection, incident: Incident) -> bool:
        owners = connection.execute(
            select(CLAIMS.c.incident_id).where(
                CLAIMS.c.claim_key.in_(incident.concurrency_keys)
            )
        ).scalars()
        if any(owner != incident.incident_id for owner in owners):
            return False
        for key in incident.concurrency_keys:
            connection.execute(
                insert(CLAIMS).values(claim_key=key, incident_id=incident.incident_id)
            )
        return True

    @staticmethod
    def _new(candidate: Candidate, decision: Correlation) -> Incident:
        return Incident(
            incident_id=candidate.failure.incident_id,
            state=str(State.DETECTED),
            version=0,
            candidate=candidate,
            reason="failure_observed",
            eligibility_verified=decision.eligible,
            repair_enabled=decision.repair_enabled,
            policy_sha256=decision.policy_sha256,
            concurrency_keys=concurrency_keys(candidate),
            approvals=(
                ApprovalPlaceholder(purpose="repair"),
                ApprovalPlaceholder(purpose="redeploy"),
            ),
            evidence=(candidate,),
            audit=(),
            projections=tuple(
                Projection(system=system)
                for system in ("telegram", "github", "openproject", "codex")
            ),
        )

    def ingest(self, candidate: Candidate, config: AppConfig, run_id: str) -> Incident:
        """Reconcile atomically; retry races three times then fail closed."""
        decision = correlate(candidate, config)
        for _ in range(3):
            try:
                with self.engine.begin() as connection:
                    return self._ingest(connection, candidate, decision, run_id)
            except IntegrityError, StateConflictError:
                continue
        raise StateConflictError("Incident write contention; rerun from saved input")

    def _ingest(
        self,
        connection: Connection,
        candidate: Candidate,
        decision: Correlation,
        run_id: str,
    ) -> Incident:
        incident = self._get(connection, candidate.failure.incident_id)
        if incident is None:
            incident = self._new(candidate, decision)
            connection.execute(
                insert(INCIDENTS).values(
                    incident_id=incident.incident_id,
                    version=0,
                    document=incident.model_dump_json(),
                )
            )
            incident = self._audit(
                incident, State.DETECTED, "failure_observed", run_id, "detected"
            )
            incident = self._audit(
                incident,
                State.DETECTED,
                "repair_and_redeploy_not_requested",
                run_id,
                "approval_placeholders",
            )
            claimed = decision.eligible and self._claim(connection, incident)
            state = State.CORRELATED if claimed else State.HUMAN
            reason = (
                "concurrent_repository_incident"
                if decision.eligible and not claimed
                else decision.reason
            )
            incident = self._audit(incident, state, reason, run_id, "transition")
        else:
            same_policy = decision.policy_sha256 == incident.policy_sha256
            observed_policy = any(
                event.policy_sha256 == decision.policy_sha256
                for event in incident.audit
            )
            if candidate in incident.evidence and observed_policy:
                return incident
            incident = incident.model_copy(
                update={
                    "evidence": (*incident.evidence, candidate)
                    if candidate not in incident.evidence
                    else incident.evidence
                }
            )
            incident = self._audit(
                incident,
                State(incident.state),
                incident.reason,
                run_id,
                "evidence_added",
                candidate=candidate,
                policy_sha256=decision.policy_sha256,
            )
            # Retries may contribute health evidence or run attempts, never replace
            # canonical provenance or authorize a previously human-attention case.
            original = incident.candidate
            conflicting = (
                not decision.eligible
                or not same_policy
                or candidate.pull_requests != original.pull_requests
                or candidate.failure.target != original.failure.target
                or candidate.failure.target_commit != original.failure.target_commit
                or candidate.failure.kind != original.failure.kind
                or candidate.release != original.release
            )
            if incident.state == State.CORRELATED and conflicting:
                incident = self._audit(
                    incident,
                    State.HUMAN,
                    "conflicting_provenance_or_policy",
                    run_id,
                    "transition",
                    candidate=candidate,
                    policy_sha256=decision.policy_sha256,
                ).model_copy(update={"eligibility_verified": False})
        if incident.state == State.HUMAN:
            connection.execute(
                delete(CLAIMS).where(CLAIMS.c.incident_id == incident.incident_id)
            )
        old_version = incident.version
        incident = incident.model_copy(update={"version": old_version + 1})
        updated = connection.execute(
            update(INCIDENTS)
            .where(
                INCIDENTS.c.incident_id == incident.incident_id,
                INCIDENTS.c.version == old_version,
            )
            .values(version=incident.version, document=incident.model_dump_json())
        )
        if updated.rowcount != 1:
            raise StateConflictError("Incident version changed during ingestion")
        return incident

    @staticmethod
    def evidence_sha256(candidate: Candidate) -> str:
        return hashlib.sha256(candidate.model_dump_json().encode()).hexdigest()
