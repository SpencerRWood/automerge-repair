"""The foundation must stay loadable without application credentials."""

import pytest
from dagster import Definitions
from dagster._core.execution.api import create_execution_plan

from automerge_repair.dagster.definitions import defs


def test_definitions_load_without_application_secrets() -> None:
    Definitions.validate_loadable(defs)
    assert defs.resolve_job_def("runtime_smoke_job").name == "runtime_smoke_job"
    assert defs.resolve_job_def("foundation_health_job").name == "foundation_health_job"
    assert not defs.sensors
    assert not defs.schedules


@pytest.mark.parametrize("job_name", ["runtime_smoke_job", "foundation_health_job"])
def test_remote_launch_can_plan_read_only_jobs(job_name: str) -> None:
    # Unlike execute_in_process, planning preserves the deployed executor and
    # rejects in-memory outputs when a job requires cross-process persistence.
    plan = create_execution_plan(defs.resolve_job_def(job_name))
    assert len(plan.get_all_steps_in_topo_order()) == 1
