"""The strings: the same keys in every language, and Dutch without English jargon."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "peak_basket"
FILES = ("strings.json", "translations/en.json", "translations/nl.json")
# Words the first users stumbled over: English jargon, or a term the integration replaced.
NOT_DUTCH = ("target", "fallback", "venster", "ruimte", "verbruiker", "pv-")


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
    return data


def _leaves(node: Any, path: str = "") -> dict[str, str]:  # noqa: ANN401
    if isinstance(node, dict):
        return {
            k: v for key, child in node.items() for k, v in _leaves(child, f"{path}.{key}").items()
        }
    return {path: node}


def test_english_strings_are_the_source() -> None:
    assert _load("strings.json") == _load("translations/en.json")


@pytest.mark.parametrize("name", FILES)
def test_every_language_has_every_key(name: str) -> None:
    assert _leaves(_load(name)).keys() == _leaves(_load("strings.json")).keys()


@pytest.mark.parametrize("word", NOT_DUTCH)
def test_dutch_reads_dutch(word: str) -> None:
    texts = _leaves(_load("translations/nl.json"))
    found = {k: v for k, v in texts.items() if re.search(rf"\b{re.escape(word)}", v, re.IGNORECASE)}
    assert not found
