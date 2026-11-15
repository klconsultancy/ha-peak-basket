"""Config flow: pick existing HA entities as sources; tunables in the options flow."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
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
    CONF_EXTRA_A,
    CONF_GRID_IMPORT_ENERGY,
    CONF_GRID_POWER,
    CONF_LOAD_POWER,
    CONF_MARGIN,
    CONF_MAX_A,
    CONF_MAX_AGE,
    CONF_MIN_A,
    CONF_MIN_CHARGE,
    CONF_MONTH_PEAK,
    CONF_PEAK_GOAL,
    CONF_PROFILE,
    CONF_REACTION,
    CONF_VOLTAGES,
    CONF_VOLTAGES_FALLBACK,
    CONF_WINDOW_AVERAGE,
    DEFAULT_OPTIONS,
    DOMAIN,
)
from .model import PROFILES


def _sensor(device_class: str, *, multiple: bool = False) -> EntitySelector:
    return EntitySelector(
        EntitySelectorConfig(domain="sensor", device_class=device_class, multiple=multiple)
    )


def _number(lo: float, hi: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=lo, max=hi, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_NAME, default="Peak Basket"): TextSelector(),
        probatio.Required(CONF_PROFILE, default="be_flanders"): SelectSelector(
            SelectSelectorConfig(options=list(PROFILES), translation_key=CONF_PROFILE)
        ),
        probatio.Required(CONF_GRID_POWER): _sensor("power"),
        probatio.Optional(CONF_WINDOW_AVERAGE): _sensor("power"),
        probatio.Optional(CONF_GRID_IMPORT_ENERGY): _sensor("energy"),
        probatio.Required(CONF_LOAD_POWER): _sensor("power"),
        probatio.Optional(CONF_MONTH_PEAK): _sensor("power"),
        probatio.Optional(CONF_VOLTAGES): _sensor("voltage", multiple=True),
        probatio.Optional(CONF_VOLTAGES_FALLBACK): _sensor("voltage", multiple=True),
    }
)

OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_PEAK_GOAL): _number(0, 50000, 100, "W"),
        probatio.Required(CONF_MARGIN): _number(0, 2000, 10, "W"),
        probatio.Required(CONF_REACTION): _number(0, 600, 5, "s"),
        probatio.Required(CONF_MIN_CHARGE): _number(0, 1800, 10, "s"),
        probatio.Required(CONF_EXTRA_A): _number(0, 16, 0.5, "A"),
        probatio.Required(CONF_MIN_A): _number(1, 32, 0.5, "A"),
        probatio.Required(CONF_MAX_A): _number(1, 63, 0.5, "A"),
        probatio.Required(CONF_MAX_AGE): _number(5, 900, 5, "s"),
    }
)


class PeakBasketConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick the source entities."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the sources, and check that the choice can work."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_GRID_POWER] == user_input[CONF_LOAD_POWER]:
                errors[CONF_LOAD_POWER] = "same_as_grid"
            elif not (
                user_input.get(CONF_WINDOW_AVERAGE) or user_input.get(CONF_GRID_IMPORT_ENERGY)
            ):
                errors["base"] = "no_energy_source"
            else:
                data = {k: v for k, v in user_input.items() if k != CONF_NAME}
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=data, options=dict(DEFAULT_OPTIONS)
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:  # noqa: ARG004
        """Return the flow for the tunables."""
        return PeakBasketOptionsFlow()


class PeakBasketOptionsFlow(OptionsFlow):
    """Change the model's tunables."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the tunables, prefilled with the current values."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, {**DEFAULT_OPTIONS, **self.config_entry.options}
            ),
        )
