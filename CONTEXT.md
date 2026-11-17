# Context

Terms used in code, docs and issues. [`docs/design.md`](docs/design.md) explains how they fit
together.

**Window** (*Meetperiode* in Dutch; *kwartier* where the region is known)
: The metering period the tariff averages over, in fixed clock blocks: 15 minutes in
Flanders, 60 in Sweden and Norway. Never a sliding average.

**Basket**
: The energy one window may hold: the target times the window length. It starts full every
window; what is left does not carry over.

**Target** (*Target peak* in the UI, *Streefpiek* in Dutch)
: The highest of the peak already reached this period, the peak goal and the region's
billed minimum.

**Peak goal**
: The window average the user allows, an option of the entry.

**Goal**
: The target minus the margin: what the model steers to.

**Margin**
: Power kept free in every window for measurement and timing errors.

**Grid connection**
: One meter and its basket: the config entry. Its sources (grid power, window average or
import counter, monthly peak, meter voltages) and its tunables (peak goal, margin, maximum
source age) belong to it.

**House** (*House*, *Huis*)
: The default load: everything the connection uses that Peak Basket does not measure as a
load, the grid import minus the power of every load, measured-only loads included. It has its own device under the connection's,
but no subentry: it cannot be removed and needs no configuration.

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
It counts as house for every steered load, never claims, and has only its grid and solar
import and a reserve: its power until the window ends, out of what the house leaves, so
the house reserve, the measured reserves and the first steered load's room add up to what
remains. Its priority only orders the measured-only loads among themselves for solar. Solar goes to it after the steered loads, before the house.

**Priority**
: The order loads go in, lower first. A load counts the loads before it as house, and the
loads after it yield to it: they get what it leaves unused right now.

**Allowed load power**
: What the load may draw now: the pull output that EVCC, Node-RED or an automation reads.

**Grid limit**
: Per load, the total grid import allowed now: the house, the load's allowed power and the
loads after it, which yield. For a circuit limit of the system that drives the load.

**Claim**
: The allowed power of a load that may start but does not draw yet. The loads after it
count it as house, while the load has demand.

**Demand** (*Wants power* in the UI, *Wil vermogen* in Dutch)
: Whether a load wants power now, from an optional on/off entity per load (vehicle
connected, thermostat calling for heat). Only matters with more than one load. Without one,
an idle load's claim lasts its reaction time.

**Mode**
: Continuous (budget for the load's minimum power until the window ends), pulse on (a burst
at minimum power fits), pulse off (only the house fits) or fallback (*Safe mode*, *Veilige stand*: inputs missing
or stale, a static limit at the goal).

**Remaining, house reserve, load room** (*Budget left*, *Reserve*, *Budget left*)
: The basket fields: the goal budget left this window, the house's expected use until the
window ends, and what is left for the load.

**Window average**
: The meter's running average for the current window (DSMR 1.4.0), which some meters
report: the energy so far divided by the window length.

**Profile**
: The tariff rules of one region, as data: window length and billed minimum.

## Names in the UI

The code keeps its own names (`house_*`, `used`, `load_room`); only what the user reads
changes. Every part of the connection, the house included, names what it drew the same way.

| Code | English | Dutch |
|---|---|---|
| load | Load | Afnemer (never *verbruiker*) |
| house | House | Huis |
| window | Window | Meetperiode, kwartier |
| target | Target peak | Streefpiek |
| fallback | Safe mode | Veilige stand |
| grid connection | Grid connection | Aansluiting |
| PV power | Solar power (inverter) | Vermogen zonnepanelen (omvormer) |
| window average | Meter's window average | Kwartiergemiddelde van de meter |
| import / export register | Import counter, Export counter | Afnameteller, Injectieteller |
| `used`, `house_grid`, `load_grid` | Grid import | Afname net |
| `house_solar`, `load_solar` | Solar import | Afname zon |
| `solar` | Solar production | Zonneproductie |
| `remaining`, `load_room` | Budget left | Resterend budget |
| `charge_minutes` | Run minutes left | Resterende minuten |
| `extra_a` | Catch-up current | Inhaalstroom |

Per window: the connection's grid import is the house's plus every load's, and the solar
production is the house's and every load's solar import plus the export.
