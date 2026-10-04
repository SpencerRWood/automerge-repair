"""Non-secret policy; runtime credentials are injected by Infisical."""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

RollbackMode = Literal["notification-only", "enabled", "required"]


@dataclass(frozen=True)
class RepositoryPolicy:
    """Explicit per-repository boundaries."""

    name: str
    environments: tuple[str, ...]
    rollback: RollbackMode
    repair_enabled: bool
    verification_window_seconds: int


@dataclass(frozen=True)
class AppConfig:
    """Validated policy without credentials or capacity interpretation."""

    environment: str
    repositories: tuple[RepositoryPolicy, ...]


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Fail closed on missing, unknown, or malformed policy."""
    values = os.environ if environ is None else environ
    policy_file = values.get("AUTOMERGE_REPAIR_POLICY_FILE")
    environment = values.get("AUTOMERGE_REPAIR_ENVIRONMENT", "dev")
    if not policy_file:
        raise ValueError("AUTOMERGE_REPAIR_POLICY_FILE is required")
    with Path(policy_file).open("rb") as stream:
        document = tomllib.load(stream)
    if set(document) != {"repositories"}:
        raise ValueError("Policy must contain only repositories")
    repositories = document["repositories"]
    if not isinstance(repositories, dict) or not repositories:
        raise ValueError("At least one repository policy is required")
    policies = []
    for name, policy in repositories.items():
        if name not in {"infrastructure", "homelab"}:
            raise ValueError("Repository is outside the R1 allowlist")
        fields = {
            "environments",
            "rollback",
            "repair_enabled",
            "verification_window_seconds",
        }
        if not isinstance(policy, dict) or set(policy) != fields:
            raise ValueError("Repository policy has missing or unknown fields")
        environments = policy["environments"]
        if (
            not isinstance(environments, list)
            or not environments
            or any(
                not isinstance(e, str) or e not in {"dev", "prod"} for e in environments
            )
            or len(set(environments)) != len(environments)
        ):
            raise ValueError("Repository environments must be unique dev/prod names")
        rollback = policy["rollback"]
        if not isinstance(rollback, str) or rollback not in {
            "notification-only",
            "enabled",
            "required",
        }:
            raise ValueError("Unknown rollback mode")
        repair_enabled = policy["repair_enabled"]
        window = policy["verification_window_seconds"]
        if type(repair_enabled) is not bool or type(window) is not int or window <= 0:
            raise ValueError("Repair flag and verification window are invalid")
        policies.append(
            RepositoryPolicy(
                name,
                tuple(environments),
                cast(RollbackMode, rollback),
                repair_enabled,
                window,
            )
        )
    if environment not in {"dev", "prod"}:
        raise ValueError("Runtime environment must be dev or prod")
    if not any(environment in p.environments for p in policies):
        raise ValueError("No configured repository supports the runtime environment")
    return AppConfig(environment, tuple(policies))
