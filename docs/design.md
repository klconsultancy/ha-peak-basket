# Design

## The problem

Under a capacity tariff you pay for your peak grid import, measured as the average power
over a fixed **window**. In Flanders (Fluvius):

- the meter averages the import per quarter hour, in clock blocks (:00, :15, :30, :45);
- the highest window of the month is the **monthly peak**;
- the bill uses the average of the last 12 monthly peaks, with a minimum of 2.5 kW.

So one high quarter hour weighs on the bill for a year. An EV charger, a heat pump, a boiler
or a home battery easily causes one. Load managers that cap the **instantaneous** power
(EVCC load management, fixed power ceilings, battery "capacity protection") miss that the
bill is about a **window average**: they leave budget unused, or stop a load that would have
fit.

Other regions have similar tariffs with other rules (see [Region profiles](#region-profiles)).

## The basket

Every window is a **basket** with a fixed amount of energy:

```text
basket (Wh) = allowed average power (W) x window length (h)     e.g. 5500 W x 0.25 h = 1375 Wh
```

- The **target** is the highest of the peak already reached this period, the configured
  peak goal and the billed minimum. Up to the peak you pay anyway, import is free.
- The **goal** is the target minus a safety **margin**: the model steers to the goal.
- The house (not controllable) uses from the basket first; the controllable load gets the
  rest.
- Every window starts with a full basket. What is left does not carry over.

Meter rules, for Flanders:

| What | Behaviour | Consequence |
|---|---|---|
| Phases at the same moment | The meter sums the three phases | The basket counts the total, not per phase |
| Import and export over time | Separate registers (1.8.x, 2.8.x), no netting within a window | Export does not refill the basket |
| Solar used on the spot | Never passes the meter | An extra tap next to the basket, not a bigger basket |

The main fuse (current per phase, instantaneous) is a separate, safety limit, not part of
the bill and not handled here.

### Solar attribution

For display only, solar self-consumption goes to the controllable loads first, in priority
order, and the rest to the house (`model/solar.py`). Then the house carries most of the
grid import, and you see how much the flexible loads leaned on the basket. Physically, and
for the grid operator, it makes no difference.

### Who drew from the basket

Per window the integration publishes what each load drew from the grid and, with a PV
source, from solar, plus the house's share and the export (Wh). Between two evaluations
the coordinator splits the interval with `allocate_solar` and adds the loads' shares up.
An interval counts at the powers reported at its start, and only when they were all known
and it is no longer than the maximum source age, so a gap or a sleeping inverter adds
nothing.

The house is the rest, not a sum of its own: **house from grid** is the import the meter
counted (*Used*) minus what the loads drew from the grid, never below zero, so the two
always add up to the import. **House from solar** is solar minus export minus what the
loads took from solar.

Export comes from the export register (2.8.x) when one is configured, counted from the
last reading before the window like the import; else from a negative grid power. Solar
sensors exist only with a PV source; the export sensor with a PV source or an export
register.

After a restart the sums the coordinator adds up itself (each load's grid and solar,
solar, and export from grid power) come back from the sensors' last states when those
are from the running window. The rest follows from the meter.

## Rules

1. **Every controllable load goes through the basket**, a home battery that charges too.
2. **Decide on measurements, never on status texts.** A load runs when its measured power
   is above 100 W, not when its status says "charging".
3. **Tariff is not engine.** Region rules are data (profiles); the engine is generic.
4. **Windows are clock blocks** of the local clock (Home Assistant's time zone), not a sliding
   average. The hour that repeats when daylight saving time ends has windows of its own.
5. **Missing or stale data gives a safe, lower limit**, never "unlimited".
6. **No API of its own, no required external system.** EVCC is one consumer, not a
   requirement.

## Integration model

Peak Basket talks to no device and no cloud. Everything it needs already exists as entities
from other integrations, and everything it decides is published as entities.

| | |
|---|---|
| Entry | One grid connection: grid power, the window average or the import register, optionally the monthly peak and the meter's phase voltages; the peak goal, margin and maximum source age |
| Subentries | One per controllable load: its power sensor, optionally its phase voltages, its priority, current range, reaction time and minimum run time |
| Output | The connection's device: target, house power and the basket fields. Each load's device: its allowed power, grid limit, mode, run minutes and headroom |
| Manifest | `iot_class: calculated`, `integration_type: service` (an integration card with its own device), no requirements, no credentials |

**Pull first.** The load's allowed power, and the total grid limit, are sensors that any
system reads itself: EVCC (a circuit's dynamic limit from Home Assistant), Node-RED, an
automation, ESPHome. The executing system keeps its own safety logic and its own fallback
when Home Assistant is away. Push adapters (a mapping from the advice to a `number`,
`switch`, `select` or `climate` service call) are planned, never a protocol of their own.

### Sources

- **Units and signs** are normalised at the boundary (`coordinator.py`): W or kW, Wh, kWh
  or MWh. Negative grid power (export) counts as zero import.
- **Used energy in the window.** Preferred is the meter's running window average (DSMR
  1.4.0, "average demand" on HomeWizard), which is the energy so far divided by the window
  length. It must average over the profile's window: the coordinator multiplies it by that
  window's length, so a sensor over another period gives a wrong budget without a
  warning. It is exact, also right after a restart. The meter resets it a second or two after the
  boundary; a value reported before the window started still belongs to the previous
  window and counts as nothing used yet. Otherwise the coordinator takes the import
  register's difference since the window start. Its start value is the last fresh reading
  before the boundary; that may count a few seconds of the previous window, which errs on
  the safe side. After a restart mid-window, it reads the register at the window start from
  the recorder; without it, the window falls back.
- **Staleness.** A source older than the configured maximum age counts as unavailable. The
  monthly peak is exempt: it changes rarely.
- **Phase voltages.** Per phase, the load's voltage, then the meter's, then 230 V. A
  voltage of 180 V or less counts as missing. They set the load's minimum (6 A) and maximum
  (16 A) power.

### Several loads

Loads go in priority order, lower first. For a load, the house is the grid import minus
its own power and the power of the loads after it: the loads before it count as house,
the loads after it yield. So the first load gets the whole basket after the house, and a
later load gets what the earlier ones leave unused right now. When an earlier load starts
drawing more, a later one's allowed power drops on the next evaluation. When the power of
any load is unknown, every load falls back, because no load can tell the house from the
others.

The connection's own figures (house power, used, remaining, house reserve) take every
load out of the house.

## The model

Every evaluation (on each grid-power update, and at least every 5 s):

```text
house  = max(grid - load, 0)
t      = seconds into the window,  rest = window - t
goal   = target - margin
e_rest = goal x window - used energy           # J left in the basket
p_avg  = e_rest / max(rest, 1)                 # average allowed for the rest of the window
p_min  = 6 A x sum(V),  p_max = 16 A x sum(V)

if p_avg - house >= p_min:                     # CONTINUOUS
    limit = min(p_avg, house + p_max)
    base  = max(goal, house + p_min)
    last 300 s: limit <= base + extra_a x sum(V)   # no sprint at the end
    last 90 s:  limit <= base
else:                                          # PULSE
    e_load    = e_rest - house x rest
    p_load    = max(load, p_min)
    threshold = p_load x (reaction + (0 if load > 100 W else min_charge_time))
    on:  limit = house + p_min + 200           when e_load > threshold
    off: limit = house
```

The output is the total grid limit (`limit`), the load's allowed power (`limit - house`),
the minutes the load can still run, and the basket fields:

- **remaining** = `e_rest`, may be negative when the window is over budget;
- **house reserve** = the house's expected use until the window ends, at most what remains;
- **load room** = `e_load`, at least zero.

Without the grid power, the load power or the used energy, the model falls back to a
static circuit at the goal: the load may use `goal - house`, or nothing when the house is
unknown.

### Publishing

Every published change is a row in the recorder, and a meter can report every second. So
powers are published rounded down to 10 W (never above the model's value), energies to
1 Wh and minutes to whole minutes. The model runs on every grid-power update, but the
sensors change at most once per 5 s tick, except when the mode changes or the grid limit
or the load's allowed power goes down: those are published at once.

`tests/test_jinja_parity.py` holds the model to the Jinja template it was ported from:
6480 cases rendered by Home Assistant itself.

### Tunables

| Parameter | Default | Why |
|---|---|---|
| Margin | 100 W | Absorbs measurement and timing errors |
| Reaction time | 90 s | From a lower limit to the load actually dropping (EVCC: ~20 s plus a 30 s cache) |
| Minimum run time | 300 s | Fewer starts and stops; some cars do not always resume after a long pause |
| Extra current | 2 A | Damps catching up in the last 5 minutes |

### Known behaviour

- A house base load of about 1.0 to 1.4 kW at night sits right on the edge between
  continuous and pulse, so accurate voltages and margin matter.
- Under a 5.5 kW target and a 1.65 kW house load, pulse mode keeps the load running about
  85 % of the time, where a fixed circuit stops it for the whole window.
- Some cars do not resume charging after a long period without power: worth a
  notification.

## Region profiles

What differs per region (to be verified per grid operator):

| | Flanders | Elsewhere |
|---|---|---|
| Window | 15 min, clock blocks | Sweden, Norway: 60 min |
| Peak per month | the highest window | Sweden: average of the 2 or 3 highest hours; Norway: of the 3 highest hours on different days |
| Over months | average of 12 months | often per month |
| When it counts | always | Sweden: often weekdays, daytime, winter; Wallonia and the Netherlands (planned): weighted time blocks |
| Minimum | 2.5 kW | differs, or none |

What stays the same: the basket is the allowed power times the window length, and the
engine (continuous, pulse, reserve, room) works for any window. A profile supplies the
window length, the billed minimum and, later, the allowed power per moment and the peak
rule that sets the target. Only exotic peak rules ("3 different days") need code.

## Prior art

| Project | What | Difference |
|---|---|---|
| hass-peaq / Peaqev | EV charging under a dynamic monthly peak and spot price | EV only, hourly, a fixed set of chargers; CC BY-NC-ND, so not forkable |
| Marstek Venus Energy Manager | Marstek batteries over Modbus, capacity protection | Batteries only, instantaneous limit, talks to the hardware |
| node-red-contrib-effekttariff | Node-RED, Victron inverter current limit per window | Victron and Node-RED, experimental |

What sets Peak Basket apart: an exact window budget with prediction (continuous and pulse),
loads with priority and solar attribution, region profiles, no hardware of its own
(pull-first), and a permissive license.

## Roadmap

1. Load kinds beyond a current range: on/off (a boiler) and minimum pause times (a heat
   pump's compressor).
2. Push adapters through Home Assistant services.
3. Profiles for other regions, and the 12-month target strategy.
4. A Lovelace card: the basket per window as a stacked bar (house, load, reserve, room,
   margin), with solar beside it.
