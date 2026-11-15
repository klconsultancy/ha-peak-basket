"""Constants for Peak Basket."""

from __future__ import annotations

import logging

DOMAIN = "peak_basket"
LOGGER = logging.getLogger(__package__)

# Config entry data (sources)
CONF_PROFILE = "profile"
CONF_GRID_POWER = "grid_power"
CONF_GRID_IMPORT_ENERGY = "grid_import_energy"
CONF_LOAD_POWER = "load_power"
CONF_MONTH_PEAK = "month_peak"
CONF_VOLTAGES = "voltages"
CONF_VOLTAGES_FALLBACK = "voltages_fallback"
CONF_WINDOW_AVERAGE = "window_average"

# Options: the tunables
CONF_PEAK_GOAL = "peak_goal_w"
CONF_MARGIN = "margin_w"
CONF_REACTION = "reaction_s"
CONF_MIN_CHARGE = "min_charge_s"
CONF_EXTRA_A = "extra_a"
CONF_MIN_A = "min_a"
CONF_MAX_A = "max_a"
CONF_MAX_AGE = "max_age_s"

DEFAULT_OPTIONS: dict[str, float] = {
    CONF_PEAK_GOAL: 5500.0,
    CONF_MARGIN: 100.0,
    CONF_REACTION: 90.0,
    CONF_MIN_CHARGE: 300.0,
    CONF_EXTRA_A: 2.0,
    CONF_MIN_A: 6.0,
    CONF_MAX_A: 16.0,
    CONF_MAX_AGE: 60.0,
}

TICK_SECONDS = 5
