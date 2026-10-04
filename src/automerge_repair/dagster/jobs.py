"""Read-only foundation checks; no repair workflow is enabled in S1."""

from dagster import (
    OpExecutionContext,
    Out,
    in_process_executor,
    job,
    json_console_logger,
    mem_io_manager,
    op,
)

from automerge_repair.config import load_config


@op
def runtime_smoke(context: OpExecutionContext) -> str:
    """Exercise Dagster run/event storage without application secrets."""
    context.log.info("runtime_smoke", extra={"event": "runtime_smoke", "status": "ok"})
    return "ok"


@job(
    executor_def=in_process_executor,
    resource_defs={"io_manager": mem_io_manager},
    logger_defs={"console": json_console_logger},
    config={"loggers": {"console": {"config": {"log_level": "INFO"}}}},
)
def runtime_smoke_job() -> None:
    runtime_smoke()


@op(out=Out(dict))
def foundation_health(context: OpExecutionContext) -> dict[str, str | int]:
    """Validate policy and emit bounded, non-secret structured metadata."""
    config = load_config()
    result: dict[str, str | int] = {
        "status": "ok",
        "environment": config.environment,
        "repository_count": len(config.repositories),
    }
    context.log.info(
        "foundation_health", extra={"event": "foundation_health", **result}
    )
    context.add_output_metadata(result)
    return result


@job(
    executor_def=in_process_executor,
    resource_defs={"io_manager": mem_io_manager},
    logger_defs={"console": json_console_logger},
    config={"loggers": {"console": {"config": {"log_level": "INFO"}}}},
)
def foundation_health_job() -> None:
    foundation_health()
