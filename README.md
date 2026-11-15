# Peak Basket for Home Assistant

A Home Assistant integration that keeps your grid import within a **capacity-tariff** budget
per metering window, and tells controllable loads, such as an EV charger, how much power they
may draw right now.

> **Status:** early development, in release candidates. The first region is Belgium –
> Flanders (15-minute windows, monthly peak, 2.5 kW minimum).

## The idea

A capacity tariff bills the **average** import over a fixed window (a quarter hour in
Flanders), not the instantaneous power. A fixed power cap wastes that: it leaves budget unused
in a quiet window, and stops the charger when the average would still have fit.

Peak Basket treats every window as a **basket** of energy: your goal × the window length
(5.5 kW × 15 min = 1375 Wh). The house takes from it first, a small margin stays in it, and
your loads get the rest:

| Mode | When | The load may draw |
|---|---|---|
| **Continuous** | The budget left covers at least the load's minimum power until the window ends | Up to what keeps the window's average at the goal |
| **Pulse – on** | Not enough for that, but enough for a burst at minimum power | Its minimum power, for as long as the budget lasts |
| **Pulse – off** | The budget for this window is used up | Nothing: only the house fits |
| **Fallback** | A source is missing or too old | A static limit at your goal, never "unlimited" |

The next window starts with a full basket. The design, the model and the rules are in
[docs/design.md](docs/design.md).

It talks to no device. It reads entities you already have and publishes sensors; EVCC,
Node-RED, ESPHome or an automation reads them and drives the load, with its own safety logic.

## Requirements

- Home Assistant 2026.10 or later.
- The **grid power** (import positive), for example from a P1 meter.
- The meter's **running window average** (DSMR 1.4.0, "average demand" on HomeWizard), or
  else an **import energy counter**. The average must cover the region's window (15 minutes
  in Flanders) and drop back to about 0 at every window start: a 5-minute or an hourly
  average gives a wrong budget without a warning.
- For each load: its **power** sensor. The load must follow the **Allowed power** Peak
  Basket gives it (see [Several loads](#several-loads)).

Optional: the monthly peak, the phase voltages of the meter and of each load, PV power, and
the export counter.

## Installation

Peak Basket installs through [HACS](https://hacs.xyz/) as a custom repository:

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/klconsultancy/ha-peak-basket` with the type **Integration**.
3. Release candidates are pre-releases: turn on **Show beta versions** for Peak Basket.
4. Download it and restart Home Assistant.

## Configuration

1. **Settings → Devices & services → Add integration → Peak Basket.**
2. **The grid connection:** the tariff region and the meter's sensors. Prefer the window
   average over the import counter: it is exact, also right after a restart.
3. **The first load,** such as the EV charger: its power sensor, its phase voltages (per
   phase the load's voltage is used, else the meter's, else 230 V), optionally a
   **demand** entity (on while it wants power, such as "car plugged in"), its current
   range, its reaction time and its minimum run time. With more than one load, the demand
   entity keeps an idle load that cannot start, such as a car that is not plugged in, from
   holding on to room the others could use.
4. **More loads:** **Add load** on the Peak Basket page. **Reconfigure** on a load changes it.
   **Reconfigure** on the Peak Basket entry changes the grid connection's sensors, such as
   adding a PV source or an export counter later: the loads, the entity IDs and the history
   stay, and the sensors of a source you remove go with it. The region cannot change.
5. **Configure** on the grid connection sets:

| Option | Default | Meaning |
|---|---|---|
| Peak goal | 5500 W | The window average you allow. The target is the highest of this goal, the monthly peak reached so far and the region's minimum: up to those, a window costs nothing extra. |
| Safety margin | 100 W | Kept free in every window for measurement and timing errors. |
| Maximum source age | 60 s | Older values count as missing and give the fallback. A load's power that is missing or too old counts as 0 W instead, so a load at rest can still start. |

### Several loads

Each load has a **priority**, lower first. A load gets what the loads before it leave
unused, and the loads after it yield to it. A load that may start but does not draw yet
keeps its room from the loads after it while it has **demand**: while its demand entity is
not off, or, without one, for its reaction time. Add only loads that follow their **Allowed
power**: a load that keeps drawing on top of it takes budget the others were promised.
Consumers you cannot steer are part of the house.

## Sensors

On the **Grid connection** device ("Aansluiting" in Dutch):

| Sensor | Entity ID | Meaning |
|---|---|---|
| Target | `sensor.peak_basket_target` | The window average steered to (W) |
| House power | `sensor.peak_basket_house_power` | The grid import without the loads (W) |
| Basket | `sensor.peak_basket_basket` | Target × window length (Wh) |
| Used | `sensor.peak_basket_used` | Imported this window so far (Wh) |
| Remaining | `sensor.peak_basket_remaining` | Budget left this window, at the goal (Wh) |
| House reserve | `sensor.peak_basket_house_reserve` | What the house is expected to use until the window ends (Wh) |
| Margin | `sensor.peak_basket_margin` | The margin's share of the basket (Wh) |
| House from grid | `sensor.peak_basket_house_grid` | The house's share of **Used** (Wh) |
| Solar, House from solar | `sensor.peak_basket_solar`, `…_house_solar` | With PV power: this window's solar, and the house's share of it (Wh) |
| Export | `sensor.peak_basket_export` | With PV power or an export counter: exported this window (Wh) |

These entity IDs are the same in every language.

On **each load's** device, named after the load:

| Sensor | Meaning |
|---|---|
| **Allowed power** | The power the load may draw now (W): **the one to steer on** |
| **Grid limit** | The total import allowed now: the house, this load, and the loads after it (W). For a circuit limit, such as EVCC's |
| **Mode** | Continuous, pulse – on, pulse – off, or fallback |
| **Run minutes left** | How long the load can still run in this window |
| **Headroom** | The energy left for this load in this window (Wh) |
| **From grid**, **From solar** | What this load drew this window (Wh); from solar only with PV power |

A load's entity IDs follow its name and the language Home Assistant has when the load is
added: for a load named "EV charger", the grid limit is `sensor.ev_charger_grid_limit` on
an English system and `sensor.ev_charger_netlimiet` on a Dutch one. Copy them from
**Settings → Entities**. Installations from before 0.1.0-rc.3 keep the entity IDs they have.

Every window's energy sensors start from zero; the value just before the reset is the
window's total. **House from grid** plus every load's **From grid** is the window's
**Used**. Solar counts for the loads first, in priority order, then for the house.

## Using it

### EVCC

Give the root circuit a dynamic limit read from the charger load's **Grid limit**, keep a
static `maxPower` as the fallback, and charge in **fast** mode, so the circuit is the only
brake:

```yaml
circuits:
  - name: main
    title: Grid connection
    maxPower: 5500 # used when Home Assistant cannot be reached
    meter: grid # the limit then applies to the whole import
    GetMaxPower:
      source: http
      uri: http://homeassistant.local:8123/api/states/sensor.ev_charger_grid_limit
      headers:
        - Authorization: Bearer <long-lived access token>
      jq: .state | tonumber
      cache: 30s

loadpoints:
  - title: Driveway
    circuit: main
    # charger, vehicle, ...
```

The `cache` and EVCC's own reaction add up to the load's **reaction time** (90 s by
default): lower it only when you have measured shorter.

### Other consumers

Read **Allowed power** and turn it into the load's setpoint, for example a charging
current of `allowed power ÷ (voltage × phases)`, rounded down. Stop the load in
**Pulse – off**, and choose your own safe state for **Fallback**.

### A history of who drew from the basket

Until Peak Basket has its own cards, [apexcharts-card](https://github.com/RomRider/apexcharts-card)
can stack the energy sensors per window. Replace the load's entity IDs with yours:

```yaml
type: custom:apexcharts-card
header:
  show: true
  title: Basket per quarter hour
graph_span: 12h
stacked: true
apex_config:
  chart:
    stacked: true
    stackOnlyBar: false
  legend:
    onItemClick:
      toggleDataSeries: false
  yaxis:
    - decimalsInFloat: 0
      title:
        text: Wh
series:
  - entity: sensor.peak_basket_house_grid
    name: House (grid)
    type: column
    extend_to: false
    group_by: { duration: 15min, func: last, fill: zero }
  - entity: sensor.ev_charger_from_grid
    name: EV charger (grid)
    type: column
    extend_to: false
    group_by: { duration: 15min, func: last, fill: zero }
  - entity: sensor.ev_charger_from_solar
    name: EV charger (solar)
    type: column
    extend_to: false
    group_by: { duration: 15min, func: last, fill: zero }
```

What took trial and error with apexcharts-card 2.2.3:

- Give no series a `yaxis_id`: series on named axes do not stack. Set the one axis in
  `apex_config.yaxis`.
- Set both `stacked: true` and `apex_config.chart.stacked: true`, with `stackOnlyBar:
  false`, and only `column` series.
- Use `extend_to: false` and `group_by: { duration: 15min, func: last, fill: zero }` on
  every series, or the bars shift or turn thin.
- Turn legend toggling off (`toggleDataSeries: false`): with stacked, nearly empty series,
  hiding one hides other layers too.

## Troubleshooting

| You see | Check |
|---|---|
| **Mode** is **Fallback** | The grid power and the window average (or import counter) must be available and younger than the maximum source age. Some sources only report a change: raise the maximum age, or pick a sensor that reports regularly. |
| **Used** does not drop to about 0 at a window start | The window average sensor does not cover the region's window. Use the meter's quarter-hour average, or the import counter. |
| **Basket** never changes | That is right: it changes only when the peak goal changes, or when the monthly peak passes it. Watch **Used** and **Remaining**. |
| The load stops at the end of every window | **Pulse – off** means the budget is used up. Lower the house load at that time, or raise the peak goal. |

## Reporting a problem

Open an [issue](https://github.com/klconsultancy/ha-peak-basket/issues) with what happened,
the sensors you picked and their units. Report security issues privately as described in
[SECURITY.md](SECURITY.md).

## Disclaimer

Peak Basket estimates; your grid operator's meter decides. Use it at your own risk.

## License

[MIT](LICENSE)
