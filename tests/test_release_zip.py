"""The zip HACS installs: the integration, with the version set to the release's."""

from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING

from script.release_zip import build

if TYPE_CHECKING:
    from pathlib import Path


def test_zip_carries_the_tag_version(tmp_path: Path) -> None:
    out = tmp_path / "peak_basket.zip"
    build("v0.1.0-rc.3", out)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["version"] == "0.1.0-rc.3"
    assert manifest["domain"] == "peak_basket"
    # The integration's files sit at the root, as HACS unpacks them into its folder.
    assert "__init__.py" in names
    assert "translations/nl.json" in names
    assert "model/engine.py" in names
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)


def test_zip_without_a_v(tmp_path: Path) -> None:
    out = tmp_path / "peak_basket.zip"
    build("0.1.0", out)
    with zipfile.ZipFile(out) as archive:
        assert json.loads(archive.read("manifest.json"))["version"] == "0.1.0"


def test_hacs_installs_the_zip() -> None:
    from pathlib import Path  # noqa: PLC0415

    hacs = json.loads((Path(__file__).parent.parent / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["zip_release"] is True
    assert hacs["filename"] == "peak_basket.zip"
