"""Policy boundaries must fail closed before later orchestration consumes them."""

from pathlib import Path

import pytest

from automerge_repair.config import load_config

ROOT = Path(__file__).resolve().parents[2]


def test_configured_repositories_have_actions_disabled() -> None:
    config = load_config(
        {"AUTOMERGE_REPAIR_POLICY_FILE": str(ROOT / "config/policy.toml")}
    )
    assert config.environment == "dev"
    assert {p.name for p in config.repositories} == {"infrastructure", "homelab"}
    assert all(
        not p.repair_enabled and p.rollback == "notification-only"
        for p in config.repositories
    )


def test_missing_policy_fails_closed() -> None:
    with pytest.raises(ValueError, match="POLICY_FILE"):
        load_config({})


@pytest.mark.parametrize(
    ("change", "replacement"),
    [
        ("infrastructure", "unrelated"),
        ('environments = ["dev"]', 'environments = ["dev", "dev"]'),
        ('environments = ["dev"]', 'environments = [["dev"]]'),
        ('rollback = "notification-only"', 'rollback = "guess"'),
        ("repair_enabled = false", 'repair_enabled = "false"'),
        ("verification_window_seconds = 300", "verification_window_seconds = 0"),
        ("verification_window_seconds = 300", "verification_window_seconds = true"),
        ("repair_enabled = false", 'repair_enabled = false\npassword = "forbidden"'),
        ('full_name = "SpencerRWood/infrastructure"', 'full_name = "infrastructure"'),
        ('renovate_login = "renovate[bot]"', 'renovate_login = "human"'),
        ('automerge_mode = "platform-squash"', 'automerge_mode = "unknown"'),
        (
            'deployment_environments = { dev = "infrastructure-dev" }',
            'deployment_environments = { prod = "infrastructure-dev" }',
        ),
        (
            'deployment_environments = { dev = "infrastructure-dev" }',
            'deployment_environments = { dev = "" }',
        ),
    ],
)
def test_invalid_policy_is_rejected(
    tmp_path: Path, change: str, replacement: str
) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text(
        (ROOT / "config/policy.toml").read_text().replace(change, replacement)
    )
    with pytest.raises(ValueError, match=r".+"):
        load_config({"AUTOMERGE_REPAIR_POLICY_FILE": str(policy)})


def test_unconfigured_environment_is_rejected() -> None:
    with pytest.raises(ValueError, match="supports"):
        load_config(
            {
                "AUTOMERGE_REPAIR_POLICY_FILE": str(ROOT / "config/policy.toml"),
                "AUTOMERGE_REPAIR_ENVIRONMENT": "prod",
            }
        )


@pytest.mark.parametrize(
    "document",
    [
        "",
        "repositories = []",
        "[repositories]",
        "[repositories.infrastructure]",
        '[repositories.infrastructure]\nenvironments = ["dev"]',
        (ROOT / "config/policy.toml")
        .read_text()
        .replace('environments = ["dev"]', "environments = []"),
    ],
)
def test_incomplete_policy_is_rejected(tmp_path: Path, document: str) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text(document)
    with pytest.raises(ValueError, match=r".+"):
        load_config({"AUTOMERGE_REPAIR_POLICY_FILE": str(policy)})


def test_invalid_runtime_environment_is_rejected() -> None:
    with pytest.raises(ValueError, match="dev or prod"):
        load_config(
            {
                "AUTOMERGE_REPAIR_POLICY_FILE": str(ROOT / "config/policy.toml"),
                "AUTOMERGE_REPAIR_ENVIRONMENT": "unknown",
            }
        )


def test_missing_policy_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config({"AUTOMERGE_REPAIR_POLICY_FILE": str(tmp_path / "absent.toml")})


def test_malformed_toml_is_rejected(tmp_path: Path) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text("[broken")
    with pytest.raises(ValueError, match="Expected"):
        load_config({"AUTOMERGE_REPAIR_POLICY_FILE": str(policy)})
