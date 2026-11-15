# Context

Terms used in code, docs and issues. [`docs/design.md`](docs/design.md) explains how they fit
together.

**Window**
: The metering period the tariff averages over, in fixed clock blocks: 15 minutes in
Flanders, 60 in Sweden and Norway. Never a sliding average.

**Basket**
: The energy one window may hold: the target times the window length. It starts full every
window; what is left does not carry over.

**Target**
: The highest of the peak already reached this period, the peak goal and the region's
billed minimum.

**Peak goal**
: The window average the user allows, an option of the entry.

**Goal**
: The target minus the margin: what the model steers to.

**Margin**
: Power kept free in every window for measurement and timing errors.

**House**
: Everything that is not controlled: the grid import minus the load's power.

**Load**
: A controllable consumer, such as an EV charger. Today an entry has one.

**Allowed load power**
: What the load may draw now: the pull output that EVCC, Node-RED or an automation reads.

**Grid limit**
: The total grid import allowed now, house and load together: for a circuit limit.

**Mode**
: Continuous (budget for the load's minimum power until the window ends), pulse on (a burst
at minimum power fits), pulse off (only the house fits) or fallback (inputs missing or
stale: a static limit at the goal).

**Remaining, house reserve, load room**
: The basket fields: the goal budget left this window, the house's expected use until the
window ends, and what is left for the load.

**Window average**
: The meter's running average for the current window (DSMR 1.4.0), which some meters
report: the energy so far divided by the window length.

**Profile**
: The tariff rules of one region, as data: window length and billed minimum.
