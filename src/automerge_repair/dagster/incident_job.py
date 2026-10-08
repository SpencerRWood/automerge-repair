"""Explicit normalized ingress job; no polling, recovery, or external writes."""

import os

from dagster import (
    AssetKey,
    AssetObservation,
    Config,
    MetadataValue,
    OpExecutionContext,
    in_process_executor,
    job,
    json_console_logger,
    mem_io_manager,
    op,
)
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from automerge_repair.config import load_config
from automerge_repair.incidents import Candidate
from automerge_repair.store import IncidentStore


class IngestConfig(Config):
    candidate_json: str


@op
def ingest_incident(context: OpExecutionContext, config: IngestConfig) -> str:
    """Persist first, then replay stable audit IDs into Dagster on every retry."""
    try:
        candidate = Candidate.model_validate_json(config.candidate_json)
    except ValidationError:
        # Do not print raw rejected input (it may contain accidental secrets).
        raise ValueError("Invalid normalized incident candidate") from None
    policy = load_config()
    database_url = os.environ.get("AUTOMERGE_REPAIR_INCIDENT_DATABASE_URL")
    if not database_url:
        raise ValueError("AUTOMERGE_REPAIR_INCIDENT_DATABASE_URL is required")
    store = IncidentStore.from_url(database_url)
    try:
        store.initialize()
        incident = store.ingest(candidate, policy, context.run_id)
    except SQLAlchemyError:
        raise RuntimeError(
            "Incident persistence unavailable; no transition accepted"
        ) from None
    finally:
        store.engine.dispose()
    for event in incident.audit:
        context.log_event(
            AssetObservation(
                asset_key=AssetKey(["automerge_repair", incident.incident_id]),
                metadata={
                    "audit": MetadataValue.json(event.model_dump(mode="json")),
                    "incident_id": incident.incident_id,
                    "concurrency_keys": MetadataValue.json(
                        list(incident.concurrency_keys)
                    ),
                    "approvals": MetadataValue.json(
                        [approval.model_dump() for approval in incident.approvals]
                    ),
                },
            )
        )
    result = {
        "incident_id": incident.incident_id,
        "state": incident.state,
        "version": incident.version,
        "reason": incident.reason,
        "eligibility_verified": incident.eligibility_verified,
        "repair_enabled": incident.repair_enabled,
    }
    context.log.info(
        "incident_ingested", extra={"event": "incident_ingested", **result}
    )
    context.add_output_metadata(result)
    return incident.incident_id


@job(
    executor_def=in_process_executor,
    resource_defs={"io_manager": mem_io_manager},
    logger_defs={"console": json_console_logger},
)
def incident_ingest_job() -> None:
    ingest_incident()
