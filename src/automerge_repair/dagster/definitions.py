"""Infrastructure loads this standalone code location."""

from dagster import Definitions

from automerge_repair.dagster.incident_job import incident_ingest_job
from automerge_repair.dagster.jobs import foundation_health_job, runtime_smoke_job

defs = Definitions(jobs=[runtime_smoke_job, foundation_health_job, incident_ingest_job])
