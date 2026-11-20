# Context

Terms used in code, docs and issues. [`docs/design.md`](docs/design.md) explains how they fit
together.

**Window** (*Meetperiode* in Dutch; *kwartier* where the region is known)
: The metering period the tariff averages over, in fixed clock blocks: 15 minutes in
Flanders, 60 in Sweden and Norway. Never a sliding average. Dutch names may say
*kwartier* (*Kwartierbudget*) as long as every profile used in Dutch is 15 minutes.

**Basket** (*Window budget* in the UI, *Kwartierbudget* in Dutch)
: The energy one window may hold: the peak limit times the window length. It starts full
every window; what is left does not carry over.

**Target** (*Peak limit* in the UI, *Piekgrens* in Dutch)
: The highest of the peak already reached this period, the peak goal and the region's
billed minimum: a window average for the whole connection, in W.

**Peak goal** (*Peak goal*, *Piekdoel*)
: The window average the user allows, an option of the entry.

**Steering limit** (`goal` in the code; never in the UI)
: The peak limit minus the safety margin: what the model steers to.

**Margin** (*Safety margin*, *Veiligheidsmarge*)
: Power kept free in every window for measurement and timing errors, an option of the
entry. In energy, the margin times the window length is the **reserve**.

**Reserve** (*Reserve*)
: The safety margin in energy: the part of the window budget kept free (Wh). Not what a
house or a load will still draw: that is the expected import.

**Expected import** (*Expected import*, *Verwachte afname*; `house_reserve`, `load_reserve`)
: What the house, or a measured-only load, will still draw from the grid until the window
ends at its current power, out of what remains (Wh). A measured-only load's is its grid
share, out of what the house leaves.

**Grid connection**
: One meter and its basket: the config entry. Its sources (grid power, window average or
import counter, monthly peak, meter voltages) and its tunables (peak goal, safety margin,
maximum source age) belong to it.

**House** (*House*, *Huis*)
: The default load: everything the connection uses that Peak Basket does not measure as a
load, the grid import minus the power of every load, measured-only loads included. It has
its own device under the connection's, but no subentry: it cannot be removed and needs no
configuration.

**Load** (*Afnemer* in Dutch)
: A consumer Peak Basket measures, such as an EV charger, a boiler, a heat pump or a battery
charging: a subentry of the grid connection, with its own device and sensors. Its power
sensor and priority belong to it, and, when steered, its phase voltages, current range,
reaction time and minimum run time. A load is steered or only measured.

**Steered load** (*Steer this load* on, *Deze afnemer sturen*)
: A load that follows its allowed power. It goes through the basket in priority order and
has the decision's sensors (allowed power, grid limit, mode, ...).

**Measured-only load** (*Steer this load* off)
: A load Peak Basket only measures, such as a heat pump that cannot follow an allowed power.
It counts as house for every steered load and never claims. It has only its power, its grid
and solar import and its expected import. Its priority only orders the measured-only loads
among themselves for solar. Solar goes to it after the steered loads, before the house.

**Priority**
: The order loads go in, lower first. A load counts the loads before it as house, and the
loads after it yield to it: they get what it leaves unused right now.

**Allowed load power**
: What the load may draw now: the pull output that EVCC, Node-RED or an automation reads.

**Grid limit** (*Grid limit*, *Netlimiet*)
: Per load, the total grid import allowed now, an instantaneous power: the house, the load's
allowed power and the loads after it, which yield. For a circuit limit of the system that
drives the load. Not the peak limit, which is a window average for the whole connection.

**Claim**
: The allowed power of a load that may start but does not draw yet. The loads after it
count it as house, while the load has demand.

**Demand** (*Wants power* in the UI, *Wil vermogen* in Dutch)
: Whether a load wants power now, from an optional on/off entity per load (vehicle
connected, thermostat calling for heat). Only matters with more than one load. Without one,
an idle load's claim lasts its reaction time.

**Stale reading, lagging reading** (never in the UI)
: Stale: older than the maximum age of a reading; a stale grid power puts the loads in safe
mode, a stale load power counts as 0 W. Lagging: fresh, but behind the grid power (a load
sensor every 30 s, the meter every 5 s); within the **lag limit** (45 s) a grid step since
the load's reading counts as the load's start or stop.

**Mode**
: Continuous (budget for the load's minimum power until the window ends), pulse on (a burst
at minimum power fits), pulse off (only the house fits) or fallback (*Safe mode*, *Veilige stand*: inputs missing
or stale, a static limit at the steering limit).

**Budget left, budget available** (*Budget left*, *Resterend budget*; *Budget available*,
*Beschikbaar budget*; `remaining`, `load_room`)
: Budget left is what the connection may still import this window, at the steering limit.
Budget available is what is left of it for a steered load, after the house and the loads
before it.

**Window average**
: The meter's running average for the current window (DSMR 1.4.0), which some meters
report: the energy so far divided by the window length.

**Profile**
: The tariff rules of one region, as data: window length and billed minimum.

## Names in the UI

Two rules, in English and in Dutch:

1. **One word, one meaning.** The same word on two devices means the same thing; two
   meanings get two words. This table is the source: a name that is not in it is not
   added.
2. **Every name says power or energy.** The tariff is set in power (a peak is an average
   power over the window); the basket counts energy per window.

| Kind | Unit | Words (English / Dutch) |
|---|---|---|
| Power | W | power / vermogen, peak / piek, limit / limiet, grens; current / stroom (A) |
| Energy per window | Wh | budget, import / afname, production / productie, export / injectie, reserve; counter / teller |

A name that is neither (*Mode*, *Run minutes left*) is neutral. The pairs: **peak limit
(W) ↔ window budget (Wh)**, **safety margin (W) ↔ reserve (Wh)**, **grid import (so far) ↔
expected import (still to come)**. One window's budget reads as one sentence: grid import +
expected import (house, measured-only loads) + budget available (first steered load) +
reserve = window budget.

The code keeps its own names (`house_*`, `used`, `load_room`); only what the user reads
changes. Every part of the connection, the house included, names what it drew the same way.
An entity ID's suffix is the slug of the English name, in every language:
`sensor.peak_basket_<suffix>` on the connection, `sensor.peak_basket_house_<suffix>` on the
house, `sensor.peak_basket_<load>_<suffix>` on a load.

| Code | Kind | English | Dutch |
|---|---|---|---|
| load | – | Load | Afnemer (never *verbruiker*) |
| house | – | House | Huis |
| window | – | Window | Meetperiode, kwartier |
| fallback | – | Safe mode | Veilige stand |
| grid connection | – | Grid connection | Aansluiting |
| PV power | power | Solar power (inverter) | Vermogen zonnepanelen (omvormer) |
| window average | power | Meter's window average | Kwartiergemiddelde van de meter |
| import / export register | energy | Import counter, Export counter | Afnameteller, Injectieteller |
| `peak_goal_w` | power | Peak goal | Piekdoel |
| `margin_w` | power | Safety margin | Veiligheidsmarge |
| `extra_a` | power | Catch-up current | Inhaalstroom |
| `target` | power | Peak limit | Piekgrens |
| `house_power`, `power` | power | Power | Vermogen |
| `allowed_power` | power | Allowed power | Toegestaan vermogen |
| `grid_limit` | power | Grid limit | Netlimiet |
| `basket` | energy | Window budget | Kwartierbudget |
| `used`, `house_grid`, `load_grid` | energy | Grid import | Afname net |
| `house_solar`, `load_solar` | energy | Solar import | Afname zon |
| `solar` | energy | Solar production | Zonneproductie |
| `export` | energy | Export | Injectie |
| `remaining` | energy | Budget left | Resterend budget |
| `load_room` | energy | Budget available | Beschikbaar budget |
| `house_reserve`, `load_reserve` | energy | Expected import | Verwachte afname |
| `margin` | energy | Reserve | Reserve |
| `mode` | neutral | Mode | Modus |
| `charge_minutes` | neutral | Run minutes left | Resterende minuten |

Per window: the connection's grid import is the house's plus every load's, and the solar
production is the house's and every load's solar import plus the export.
