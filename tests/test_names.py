"""One word, one meaning, per language: every sensor's name, kind, unit and entity ID."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.util import slugify

from custom_components.peak_basket import sensor

ROOT = Path(__file__).parent.parent
COMPONENT = ROOT / "custom_components" / "peak_basket"
POWER, ENERGY, NEUTRAL = "power", "energy", "neutral"
DIAGNOSTIC = EntityCategory.DIAGNOSTIC


class Name(NamedTuple):
    """One sensor's row of the spec (issue #79)."""

    kind: str
    en: str
    nl: str
    category: EntityCategory | None


NAMES: dict[str, Name] = {
    "basket": Name(ENERGY, "Window budget", "Kwartierbudget", None),
    "used": Name(ENERGY, "Grid import", "Afname net", None),
    "remaining": Name(ENERGY, "Budget left", "Resterend budget", None),
    "margin": Name(ENERGY, "Reserve", "Reserve", DIAGNOSTIC),
    "target": Name(POWER, "Peak limit", "Piekgrens", None),
    "solar": Name(ENERGY, "Solar production", "Zonneproductie", None),
    "export": Name(ENERGY, "Export", "Injectie", None),
    "house_power": Name(POWER, "Power", "Vermogen", None),
    "house_grid": Name(ENERGY, "Grid import", "Afname net", None),
    "house_solar": Name(ENERGY, "Solar import", "Afname zon", None),
    "house_reserve": Name(ENERGY, "Expected import", "Verwachte afname", DIAGNOSTIC),
    "power": Name(POWER, "Power", "Vermogen", None),
    "allowed_power": Name(POWER, "Allowed power", "Toegestaan vermogen", None),
    "grid_limit": Name(POWER, "Grid limit", "Netlimiet", None),
    "mode": Name(NEUTRAL, "Mode", "Modus", None),
    "charge_minutes": Name(NEUTRAL, "Run minutes left", "Resterende minuten", None),
    "load_room": Name(ENERGY, "Budget available", "Beschikbaar budget", None),
    "load_reserve": Name(ENERGY, "Expected import", "Verwachte afname", None),
    "load_grid": Name(ENERGY, "Grid import", "Afname net", None),
    "load_solar": Name(ENERGY, "Solar import", "Afname zon", None),
}
# Keys that mean the same thing on another device, so share their name.
SAME_MEANING = (
    {"used", "house_grid", "load_grid"},
    {"house_solar", "load_solar"},
    {"house_reserve", "load_reserve"},
    {"house_power", "power"},
)
UNITS: dict[str, set[str]] = {POWER: {UnitOfPower.WATT}, ENERGY: {UnitOfEnergy.WATT_HOUR}}


def _descriptions() -> dict[str, Any]:
    every = [
        *sensor.CONNECTION_SENSORS,
        sensor.HOUSE_GRID,
        *sensor.SOLAR,
        sensor._export(from_register=True),  # noqa: SLF001
        sensor.LOAD_POWER,
        *sensor.LOAD_SENSORS,
        sensor.LOAD_RESERVE,
        sensor.LOAD_GRID,
        sensor.LOAD_SOLAR,
    ]
    return {d.key: d for d in every}


def _names(file: str) -> dict[str, str]:
    data = json.loads((COMPONENT / file).read_text(encoding="utf-8"))
    return {k: v["name"] for k, v in data["entity"]["sensor"].items()}


def _context_names() -> tuple[set[str], set[str]]:
    """The English and Dutch cells of CONTEXT.md's "Names in the UI" table, split on commas."""
    text = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    table = text.split("## Names in the UI", 1)[1]
    english: set[str] = set()
    dutch: set[str] = set()
    for line in table.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or set(cells[0]) <= {"-"} or cells[0] == "Code":
            continue
        english |= {re.sub(r"\s*\(.*\)$", "", n).strip() for n in cells[-2].split(",")}
        dutch |= {re.sub(r"\s*\(.*\)$", "", n).strip() for n in cells[-1].split(",")}
    return english, dutch


def test_every_sensor_is_in_the_table() -> None:
    assert set(_descriptions()) == set(NAMES)
    assert set(_names("strings.json")) == set(NAMES)


@pytest.mark.parametrize(
    ("file", "language"),
    [("translations/en.json", "en"), ("strings.json", "en"), ("translations/nl.json", "nl")],
)
def test_the_names_are_the_table(file: str, language: str) -> None:
    assert _names(file) == {key: getattr(row, language) for key, row in NAMES.items()}


def test_every_name_is_in_context() -> None:
    english, dutch = _context_names()
    assert {row.en for row in NAMES.values()} <= english
    assert {row.nl for row in NAMES.values()} <= dutch


@pytest.mark.parametrize("language", ["en", "nl"])
def test_one_name_one_meaning(language: str) -> None:
    by_name: dict[str, set[str]] = {}
    for key, row in NAMES.items():
        by_name.setdefault(getattr(row, language), set()).add(key)
    shared = [keys for keys in by_name.values() if len(keys) > 1]
    assert all(keys in SAME_MEANING for keys in shared), shared


@pytest.mark.parametrize("key", NAMES)
def test_the_unit_says_the_kind(key: str) -> None:
    unit = _descriptions()[key].native_unit_of_measurement
    kind = NAMES[key].kind
    if kind == NEUTRAL:
        assert unit not in UNITS[POWER] | UNITS[ENERGY]
    else:
        assert unit in UNITS[kind]


@pytest.mark.parametrize("key", NAMES)
def test_the_entity_id_is_the_english_name(key: str) -> None:
    assert sensor.SUFFIXES[key] == slugify(NAMES[key].en)


@pytest.mark.parametrize("key", NAMES)
def test_the_category(key: str) -> None:
    assert _descriptions()[key].entity_category == NAMES[key].category


@pytest.mark.parametrize("file", ["strings.json", "translations/nl.json"])
@pytest.mark.parametrize("field", ["peak_goal_w", "margin_w"])
def test_a_power_setting_says_what_it_is_in_energy(file: str, field: str) -> None:
    """The peak goal is a window budget, the safety margin a reserve: say both in Wh."""
    data = json.loads((COMPONENT / file).read_text(encoding="utf-8"))
    description = data["options"]["step"]["init"]["data_description"][field]
    assert re.search(r"\d Wh\b", description)


@pytest.mark.parametrize("file", ["strings.json", "translations/nl.json"])
@pytest.mark.parametrize("old", ["target peak", "streefpiek", "korf"])
def test_old_names_are_gone(file: str, old: str) -> None:
    assert old not in (COMPONENT / file).read_text(encoding="utf-8").lower()
