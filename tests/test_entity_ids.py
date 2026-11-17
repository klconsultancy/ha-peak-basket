"""One entity-ID convention, the same in every language: the slug of the English name."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.helpers import entity_registry as er

from .conftest import SOURCES, set_sources, setup_entry

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PV = "sensor.pv_power"

CONNECTION_IDS = {
    "basket": "sensor.peak_basket_window_budget",
    "used": "sensor.peak_basket_grid_import",
    "remaining": "sensor.peak_basket_budget_left",
    "margin": "sensor.peak_basket_reserve",
    "target": "sensor.peak_basket_peak_limit",
    "solar": "sensor.peak_basket_solar_production",
    "export": "sensor.peak_basket_export",
    "house_power": "sensor.peak_basket_house_power",
    "house_grid": "sensor.peak_basket_house_grid_import",
    "house_solar": "sensor.peak_basket_house_solar_import",
    "house_reserve": "sensor.peak_basket_house_expected_import",
}
STEERED_SUFFIXES = {
    "power": "power",
    "allowed_power": "allowed_power",
    "grid_limit": "grid_limit",
    "mode": "mode",
    "charge_minutes": "run_minutes_left",
    "load_room": "budget_available",
    "load_grid": "grid_import",
    "load_solar": "solar_import",
}
MEASURED_SUFFIXES = {
    "power": "power",
    "load_reserve": "expected_import",
    "load_grid": "grid_import",
    "load_solar": "solar_import",
}


def _ids(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """Entity IDs by unique ID without the entry's prefix."""
    return {
        e.unique_id.removeprefix(f"{entry.entry_id}_"): e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


async def _add_load(
    hass: HomeAssistant, entry: ConfigEntry, name: str, power: str, *, steer: bool = True
) -> str:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": name, "load_power": power, "priority": 2, "steer": steer}
    )
    if steer:
        await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    return next(s.subentry_id for s in entry.subentries.values() if s.title == name)


@pytest.mark.parametrize("language", ["en", "nl"])
async def test_entity_ids_are_the_english_names_in_every_language(
    hass: HomeAssistant, language: str
) -> None:
    hass.config.language = language
    set_sources(hass, 1000, 100.0, 0)
    hass.states.async_set(PV, "0", {"unit_of_measurement": "W"})
    entry = await setup_entry(hass, {**SOURCES, "pv_power": PV})
    for subentry_id in list(entry.subentries):
        assert hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()
    charger = await _add_load(hass, entry, "Laadpaal", "sensor.charger_power")
    heat_pump = await _add_load(hass, entry, "Warmtepomp", "sensor.heat_pump_power", steer=False)
    expected = dict(CONNECTION_IDS)
    expected |= {
        f"{charger}_{key}": f"sensor.peak_basket_laadpaal_{suffix}"
        for key, suffix in STEERED_SUFFIXES.items()
    }
    expected |= {
        f"{heat_pump}_{key}": f"sensor.peak_basket_warmtepomp_{suffix}"
        for key, suffix in MEASURED_SUFFIXES.items()
    }
    assert _ids(hass, entry) == expected


async def _bare_entry(hass: HomeAssistant) -> ConfigEntry:
    """A connection whose first load is gone, so the test names every load itself."""
    set_sources(hass, 1000, 100.0, 0)
    entry = await setup_entry(hass)
    for subentry_id in list(entry.subentries):
        assert hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()
    return entry


async def test_renaming_a_load_keeps_its_entity_ids(hass: HomeAssistant) -> None:
    entry = await _bare_entry(hass)
    charger = await _add_load(hass, entry, "Laadpaal", "sensor.charger_power")
    registry = er.async_get(hass)
    registry.async_update_entity(
        "sensor.peak_basket_laadpaal_mode", new_entity_id="sensor.my_charger_mode"
    )
    before = _ids(hass, entry)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "load"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": charger},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Wallbox", "load_power": "sensor.charger_power", "priority": 2, "steer": True},
    )
    await hass.config_entries.subentries.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert entry.subentries[charger].title == "Wallbox"
    assert _ids(hass, entry) == before
    assert before[f"{charger}_mode"] == "sensor.my_charger_mode"


async def test_loads_never_share_an_entity_id(hass: HomeAssistant) -> None:
    entry = await _bare_entry(hass)
    first = await _add_load(hass, entry, "Laadpaal", "sensor.a_power")
    second = await _add_load(hass, entry, "laadpaal!", "sensor.b_power")
    house = await _add_load(hass, entry, "House", "sensor.c_power", steer=False)
    house_load = await _add_load(hass, entry, "House load", "sensor.d_power", steer=False)
    ids = _ids(hass, entry)
    assert ids[f"{first}_mode"] == "sensor.peak_basket_laadpaal_mode"
    assert ids[f"{second}_mode"] == "sensor.peak_basket_laadpaal_mode_2"
    assert ids[f"{house}_power"] == "sensor.peak_basket_house_load_power"
    assert ids[f"{house_load}_power"] == "sensor.peak_basket_house_load_load_power"
    assert len(set(ids.values())) == len(ids)
    loads = {v for k, v in ids.items() if "_" in k and k.split("_", 1)[0] in entry.subentries}
    assert not loads & set(CONNECTION_IDS.values())
