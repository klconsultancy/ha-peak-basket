"""Build the zip HACS installs, with the manifest version set to the release's.

The tag on main still has the version of the last release in ``manifest.json``:
release-please raises it only in the release pull request. A release candidate would
then show that old version in Home Assistant, so the zip carries the tag's.

Usage: ``python -m script.release_zip <tag> <output.zip>``
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "peak_basket"


def build(tag: str, out: Path) -> None:
    """Zip the integration's files at the root, with ``version`` set to ``tag``."""
    version = tag.removeprefix("v")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(INTEGRATION.rglob("*")):
            name = path.relative_to(INTEGRATION).as_posix()
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if name == "manifest.json":
                manifest = json.loads(path.read_text(encoding="utf-8"))
                manifest["version"] = version
                archive.writestr(name, json.dumps(manifest, indent=2) + "\n")
            else:
                archive.write(path, name)


if __name__ == "__main__":  # pragma: no cover
    build(sys.argv[1], Path(sys.argv[2]))
