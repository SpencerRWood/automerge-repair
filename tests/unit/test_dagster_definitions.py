"""The foundation must stay loadable without application credentials."""

from dagster import Definitions

from automerge_repair.dagster.definitions import defs


def test_definitions_load_without_application_secrets() -> None:
    Definitions.validate_loadable(defs)
    assert defs.resolve_job_def("runtime_smoke_job").name == "runtime_smoke_job"
    assert defs.resolve_job_def("foundation_health_job").name == "foundation_health_job"
    assert not defs.sensors
    assert not defs.schedules
