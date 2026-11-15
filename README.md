# Peak Basket for Home Assistant

A Home Assistant integration that keeps your grid import within a **capacity-tariff** budget per
metering window, and tells a controllable load, such as an EV charger, how much power it may
draw right now.

Capacity tariffs bill the average import over a fixed window (a quarter hour in Flanders),
not the instantaneous power. Peak Basket treats every window as a **basket** of energy: the
house uses from it first, and the load gets what is left, continuously while the budget
allows its minimum power, or in bursts at minimum power when it doesn't. A fixed power cap
leaves that budget unused, or stops the load when it would have fit.

> **Status:** early development. The first region is Belgium – Flanders (15-minute windows,
> monthly peak, 2.5 kW minimum).

## How it works

- **It talks to no device.** It reads entities you already have: the grid power and import
  of your P1 meter, the power of the load and, optionally, phase voltages and the monthly
  peak.
- **Pull first.** It publishes the load's allowed power and the total grid limit as
  sensors. EVCC, Node-RED, an automation or ESPHome reads them and drives the load, with its
  own safety logic and fallback.
- **Safe when data is missing.** A source that is unavailable or too old drops the limit
  to a static one at your goal, never to "unlimited".

The design, the model and the rules are in [docs/design.md](docs/design.md).

## Requirements

- Home Assistant 2026.10 or later
- A sensor for the grid power (import positive), for example from a P1 meter
- The meter's running window average (DSMR 1.4.0, "average demand" on HomeWizard), or an
  import energy counter. The average must cover the region's window (15 minutes in
  Flanders) and drop back to about 0 at every window start: a 5-minute or an hourly
  average gives a wrong budget, without a warning.
- A sensor for the load's power

## Installation

The integration installs through [HACS](https://hacs.xyz/) as a custom repository:

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/klconsultancy/ha-peak-basket` with the type **Integration**.
3. Search for **Peak Basket**, download it and restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & services → Add integration** and pick **Peak Basket**.
2. Describe the **grid connection**: the region and the meter's sensors. Prefer the window
   average over the import counter: it is exact, also right after a restart. The meter's
   phase voltages are optional.
3. Describe the **first load**, such as the EV charger: its power sensor and, optionally,
   its phase voltages (per phase the load's voltage is used, else the meter's, else
   230 V), a **demand** entity (on while it wants power, such as "car plugged in"), its
   current range, reaction time and minimum run time. With more than one load, the
   demand entity keeps an idle load that cannot start (a car not plugged in) from holding
   on to room the others could use.
4. Add more loads with **Add load** on the Peak Basket card. Each has a **priority**
   (lower goes first): a load gets what the loads before it leave unused, and the loads
   after it yield to it. **Reconfigure** on a load changes it.
5. Under **Configure**, set the peak goal (the window average you allow, 5500 W by
   default) and, if needed, the margin.

## What you get

On the grid connection's device:

| Sensor | Meaning |
|---|---|
| **Target**, **House power** | The peak steered to, and the house load without any load |
| **Basket**, **Used**, **Remaining**, **House reserve**, **Margin** | The window's energy budget (Wh) |
| **House from grid** | The house's share of the window's import so far (Wh) |
| **Solar**, **House from solar**, **Export** | With a PV source (export also with an export register): the window's energy so far (Wh) |

On each load's device:

| Sensor | Meaning |
|---|---|
| **Allowed power** | The power the load may draw now (W): the one to steer on |
| **Grid limit** | The total import allowed now: house, this load, and the loads after it, which yield. For a circuit limit |
| **Mode** | Continuous, pulse on, pulse off, or fallback |
| **Run minutes left** | How long the load can still run in this window |
| **Headroom** | The energy left for this load in this window (Wh) |
| **From grid** | What this load drew from the grid this window (Wh) |
| **From solar** | With a PV source: what this load drew from solar this window (Wh) |

The **From grid** sensors exist without solar too. With a PV source come **From solar**,
**Solar** and **House from solar**; with a PV source or an export register, **Export**.
The house is the rest: **House from grid** plus every load's **From grid** is always the
window's **Used**. Solar counts for the loads first, in priority order, then for the house.
Every window starts from zero; the value just before the reset is the window's total.

### A history of who drew from the basket

Until Peak Basket has its own cards, [apexcharts-card](https://github.com/RomRider/apexcharts-card)
can stack these sensors per window. Replace the loads' entity IDs with yours (they depend on the
load's name and the language, see above):

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
  false`, and only `column` series: no lines in the stack.
- Use `extend_to: false` and `group_by: { duration: 15min, func: last, fill: zero }` on
  every series, or the bars shift or turn thin.
- Turn legend toggling off (`toggleDataSeries: false`): with stacked, nearly empty series,
  hiding one hides other layers too.

Names come in English and Dutch. The connection's device is called **Grid connection**
("Aansluiting" in Dutch), and its sensors get entity IDs that are the same in every
language: `sensor.peak_basket_` followed by `target`, `house_power`, `basket`, `used`,
`remaining`, `house_reserve`, `margin`, `house_grid`, `solar`, `house_solar` or `export`.

A load's sensors follow the load's name and the language Home Assistant has when they are
created: the grid limit is `sensor.ev_charger_grid_limit` on an English system and
`sensor.ev_charger_netlimiet` on a Dutch one, for a load named "EV charger". Look a load's
sensor up by its name, and copy its entity ID from **Settings → Entities** before you put
it in EVCC, Node-RED or an automation.

Installations from before 0.1.0-rc.3 keep the entity IDs they have.

### Using it with EVCC

Give the charger's circuit a dynamic limit read from the charger load's **Grid limit**
sensor (its entity ID depends on the language, see above), and use the loadpoint's fast mode so the circuit is the only brake. EVCC keeps its static limit when
Home Assistant is unreachable.

## Reporting a problem

Open an [issue](https://github.com/klconsultancy/ha-peak-basket/issues) with what happened,
the sensors you picked and their units. Report security issues privately as described in
[SECURITY.md](SECURITY.md).

## Disclaimer

Peak Basket estimates; your grid operator's meter decides. Use it at your own risk.

## License

[MIT](LICENSE)
