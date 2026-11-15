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
> monthly peak, 2.5 kW minimum). One controllable load per entry.

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
  import energy counter
- A sensor for the load's power

## Installation

The integration installs through [HACS](https://hacs.xyz/) as a custom repository:

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/klconsultancy/ha-peak-basket` with the type **Integration**.
3. Search for **Peak Basket**, download it and restart Home Assistant.

## Configuration

1. Go to **Settings → Devices & services → Add integration** and pick **Peak Basket**.
2. Pick the region and the sensors. Prefer the window average over the import counter: it
   is exact, also right after a restart. The phase voltages are optional; per phase the
   load's voltage is used, else the meter's, else 230 V.
3. Under **Configure**, set the peak goal (the window average you allow, 5500 W by
   default) and, if needed, the margin, reaction time and minimum run time.

## What you get

| Sensor | Meaning |
|---|---|
| **Allowed load power** | The power the load may draw now (W): the one to steer on |
| **Grid limit** | The total import allowed now, house and load together, for a circuit limit |
| **Mode** | Continuous, pulse on, pulse off, or fallback |
| **Run minutes left** | How long the load can still run in this window |
| **Target**, **House power** | The peak steered to, and the estimated house load |
| **Basket**, **Used**, **Remaining**, **House reserve**, **Load headroom**, **Margin** | The window's energy budget (Wh) |

Names come in English and Dutch.

### Using it with EVCC

Give the charger's circuit a dynamic limit read from the **Grid limit** sensor, and use the
loadpoint's fast mode so the circuit is the only brake. EVCC keeps its static limit when
Home Assistant is unreachable.

## Reporting a problem

Open an [issue](https://github.com/klconsultancy/ha-peak-basket/issues) with what happened,
the sensors you picked and their units. Report security issues privately as described in
[SECURITY.md](SECURITY.md).

## Disclaimer

Peak Basket estimates; your grid operator's meter decides. Use it at your own risk.

## License

[MIT](LICENSE)
