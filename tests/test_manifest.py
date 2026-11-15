import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_versions_agree() -> None:
    """release-please bumps both; Home Assistant and HACS read the manifest."""
    manifest = json.loads(
        (ROOT / "custom_components/peak_basket/manifest.json").read_text(encoding="utf-8")
    )
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["version"] == pyproject["project"]["version"]


def test_no_requirements() -> None:
    """A calculation helper: it talks to no device and needs no library."""
    manifest = json.loads(
        (ROOT / "custom_components/peak_basket/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["requirements"] == []
    assert manifest["iot_class"] == "calculated"
    assert manifest["integration_type"] == "helper"
