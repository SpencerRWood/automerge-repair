import tomllib
from importlib import import_module
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_pyproject() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_package_can_be_imported() -> None:
    package = import_module("automerge_repair")

    assert package.__all__ == ()


def test_template_project_metadata_describes_scaffold() -> None:
    pyproject = load_pyproject()
    project = pyproject["project"]

    assert project["name"] == "automerge-repair"
    assert project["description"].startswith("Bounded recovery orchestration")
    assert project["requires-python"] == ">=3.14"
    assert "dagster==1.13.16" in project["dependencies"]


def test_template_declares_typed_src_package() -> None:
    pyproject = load_pyproject()
    project = pyproject["project"]
    tool = pyproject["tool"]
    hatch_targets = tool["hatch"]["build"]["targets"]

    assert (ROOT / "src" / "automerge_repair" / "py.typed").is_file()
    assert "Typing :: Typed" in project["classifiers"]
    assert hatch_targets["wheel"]["packages"] == [
        "src/automerge_repair",
    ]
    assert tool["coverage"]["run"]["source"] == ["automerge_repair"]


def test_docker_entrypoint_targets_definitions_module() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "automerge_repair.dagster.definitions" in dockerfile
    assert "dagster api grpc" in dockerfile


def test_centralized_release_contract() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert (
        "SpencerRWood/workflows/.github/workflows/release-container.yml@v3" in workflow
    )
    release_config = tomllib.loads(
        (ROOT / ".github/release.toml").read_text(encoding="utf-8")
    )
    assert release_config["build"]["python_package"] is True
    assert release_config["release"]["semantic_release"] is True
    assert release_config["dagster"]["runtime_validation"] is True
    assert release_config["container"]["publish"] is True
    assert "pytest-coverage" in release_config["validation"]["checks"]
    assert release_config["coverage"]["target"] == "automerge_repair"
    validation = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
    assert "SpencerRWood/workflows/.github/workflows/validate.yml@v3" in validation
