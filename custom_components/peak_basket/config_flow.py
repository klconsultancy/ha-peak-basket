"""Config flow: the grid connection, its tunables, and its loads as subentries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from .const import (
    CONF_DEMAND,
    CONF_EXTRA_A,
    CONF_GRID_EXPORT_ENERGY,
    CONF_GRID_IMPORT_ENERGY,
    CONF_GRID_POWER,
    CONF_LOAD_POWER,
    CONF_LOAD_VOLTAGES,
    CONF_MARGIN,
    CONF_MAX_A,
    CONF_MAX_AGE,
    CONF_METER_VOLTAGES,
    CONF_MIN_A,
    CONF_MIN_CHARGE,
    CONF_MONTH_PEAK,
    CONF_PEAK_GOAL,
    CONF_PRIORITY,
    CONF_PROFILE,
    CONF_PV_POWER,
    CONF_REACTION,
    CONF_WINDOW_AVERAGE,
    DEFAULT_ENTRY_OPTIONS,
    DEFAULT_LOAD,
    DOMAIN,
    SUBENTRY_LOAD,
)
from .model import PROFILES

if TYPE_CHECKING:
    from collections.abc import Mapping


def _sensor(device_class: str, *, multiple: bool = False) -> EntitySelector:
    return EntitySelector(
        EntitySelectorConfig(domain="sensor", device_class=device_class, multiple=multiple)
    )


def _number(lo: float, hi: float, step: float, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=lo, max=hi, step=step, mode=NumberSelectorMode.BOX)
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


SOURCE_FIELDS = {
    probatio.Required(CONF_GRID_POWER): _sensor("power"),
    probatio.Optional(CONF_PV_POWER): _sensor("power"),
    probatio.Optional(CONF_WINDOW_AVERAGE): _sensor("power"),
    probatio.Optional(CONF_GRID_IMPORT_ENERGY): _sensor("energy"),
    probatio.Optional(CONF_GRID_EXPORT_ENERGY): _sensor("energy"),
    probatio.Optional(CONF_MONTH_PEAK): _sensor("power"),
    probatio.Optional(CONF_METER_VOLTAGES): _sensor("voltage", multiple=True),
}

CONNECTION_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_NAME, default="Peak Basket"): TextSelector(),
        probatio.Required(CONF_PROFILE, default="be_flanders"): SelectSelector(
            SelectSelectorConfig(options=list(PROFILES), translation_key=CONF_PROFILE)
        ),
        **SOURCE_FIELDS,
    }
)

# Reconfigure: the sources only. The region stays: its window and rules shape every sum.
SOURCES_SCHEMA = probatio.Schema(SOURCE_FIELDS)


OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_PEAK_GOAL): _number(0, 50000, 100, "W"),
        probatio.Required(CONF_MARGIN): _number(0, 2000, 10, "W"),
        probatio.Required(CONF_MAX_AGE): _number(5, 900, 5, "s"),
    }
)

LOAD_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_NAME): TextSelector(),
        probatio.Required(CONF_LOAD_POWER): _sensor("power"),
        probatio.Optional(CONF_LOAD_VOLTAGES): _sensor("voltage", multiple=True),
        probatio.Required(CONF_PRIORITY, default=DEFAULT_LOAD[CONF_PRIORITY]): probatio.All(
            _number(0, 99, 1), probatio.Coerce(int)
        ),
        probatio.Optional(CONF_DEMAND): EntitySelector(
            EntitySelectorConfig(domain=["binary_sensor", "switch", "input_boolean"])
        ),
        probatio.Required(CONF_MIN_A, default=DEFAULT_LOAD[CONF_MIN_A]): _number(1, 32, 0.5, "A"),
        probatio.Required(CONF_MAX_A, default=DEFAULT_LOAD[CONF_MAX_A]): _number(1, 63, 0.5, "A"),
        probatio.Required(CONF_REACTION, default=DEFAULT_LOAD[CONF_REACTION]): _number(
            0, 600, 5, "s"
        ),
        probatio.Required(CONF_MIN_CHARGE, default=DEFAULT_LOAD[CONF_MIN_CHARGE]): _number(
            0, 1800, 10, "s"
        ),
        probatio.Required(CONF_EXTRA_A, default=DEFAULT_LOAD[CONF_EXTRA_A]): _number(
            0, 16, 0.5, "A"
        ),
    }
)


def _load_errors(entry_data: Mapping[str, Any], user_input: Mapping[str, Any]) -> dict[str, str]:
    if user_input[CONF_LOAD_POWER] == entry_data.get(CONF_GRID_POWER):
        return {CONF_LOAD_POWER: "same_as_grid"}
    return {}


def _split_load(user_input: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    data = {k: v for k, v in user_input.items() if k != CONF_NAME}
    return str(user_input[CONF_NAME]), data


class PeakBasketConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a grid connection and its first load."""

    VERSION = 2

    def __init__(self) -> None:
        """Start without a connection."""
        self._connection: dict[str, Any] = {}

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the grid connection's sources."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_WINDOW_AVERAGE) or user_input.get(CONF_GRID_IMPORT_ENERGY):
                self._connection = user_input
                return await self.async_step_load()
            errors["base"] = "no_energy_source"
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CONNECTION_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_load(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the first load."""
        errors: dict[str, str] = {}
        if user_input is not None and not (errors := _load_errors(self._connection, user_input)):
            title, load = _split_load(user_input)
            return self.async_create_entry(
                title=self._connection[CONF_NAME],
                data={k: v for k, v in self._connection.items() if k != CONF_NAME},
                options=dict(DEFAULT_ENTRY_OPTIONS),
                subentries=[
                    {
                        "data": load,
                        "subentry_type": SUBENTRY_LOAD,
                        "title": title,
                        "unique_id": None,
                    }
                ],
            )
        return self.async_show_form(
            step_id="load",
            data_schema=self.add_suggested_values_to_schema(LOAD_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the grid connection's sources; its loads and entity IDs stay."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            loads = {sub.data.get(CONF_LOAD_POWER) for sub in entry.subentries.values()}
            if user_input[CONF_GRID_POWER] in loads:
                errors[CONF_GRID_POWER] = "grid_is_a_load"
            elif user_input.get(CONF_WINDOW_AVERAGE) or user_input.get(CONF_GRID_IMPORT_ENERGY):
                return self.async_update_reload_and_abort(
                    entry, data={CONF_PROFILE: entry.data[CONF_PROFILE], **user_input}
                )
            else:
                errors["base"] = "no_energy_source"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                SOURCES_SCHEMA, user_input or dict(entry.data)
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the flow for the connection's tunables."""
        return PeakBasketOptionsFlow()

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the loads, the one kind of subentry."""
        return {SUBENTRY_LOAD: LoadSubentryFlow}


class PeakBasketOptionsFlow(OptionsFlow):
    """Change the grid connection's tunables."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the tunables, prefilled with the current values."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, {**DEFAULT_ENTRY_OPTIONS, **self.config_entry.options}
            ),
        )


class LoadSubentryFlow(ConfigSubentryFlow):
    """Add or change a load."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a load."""
        errors: dict[str, str] = {}
        if user_input is not None and not (
            errors := _load_errors(self._get_entry().data, user_input)
        ):
            title, data = _split_load(user_input)
            return self.async_create_entry(title=title, data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(LOAD_SCHEMA, user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a load."""
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None and not (errors := _load_errors(entry.data, user_input)):
            title, data = _split_load(user_input)
            return self.async_update_and_abort(entry, subentry, title=title, data=data)
        suggested = user_input or {CONF_NAME: subentry.title, **subentry.data}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(LOAD_SCHEMA, suggested),
            errors=errors,
        )
