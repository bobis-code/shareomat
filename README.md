# self_leg

> **🚧 Work in Progress — under active development, not production-ready.**

Hallo

Dieses Repository ist mein Versuch, ein einfaches und möglichst offenes Abrechnungssystem für Schweizer LEG- und ZEV-Gemeinschaften zu entwickeln.

Die Idee entstand, weil ich bisher keine wirklich schlanke Lösung gefunden habe, die sich auf das Wesentliche konzentriert: Messdaten des Netzbetreibers möglichst automatisch zu importieren, den Verbrauch der LEG-Gemeinschaft korrekt zwischen lokal erzeugter Energie und Netzbezug aufzuteilen und daraus nachvollziehbare Abrechnungen zu erstellen.

Aktuell befindet sich das Projekt noch im Aufbau und ist weit von einer produktiven Version entfernt. Ich entwickle es hauptsächlich nebenbei an Wochenenden und in meiner Freizeit. Entsprechend wird sich noch einiges ändern.

Das langfristige Ziel ist es, eine Lösung zu schaffen, mit der kleinere LEG- oder ZEV-Gemeinschaften ihre Energieabrechnung mit möglichst wenig manuellem Aufwand durchführen können – idealerweise ohne teure Spezialsoftware.

---

LEG/ZEV — calculates local energy sharing.


## Quick start

```bash
# First-time local setup:
cp config/leg_config.example.yaml config/leg_config.yaml

# Drop CSV or S-DAT files into data/inbox/, then:
docker compose up --build
```

Reports are written to `data/reports/`. Processed files move to `data/archive/`.

## Input formats

### CSV

```
timestamp,mpid,value_kwh,direction[,quality]
2024-06-01T12:00:00+00:00,CH001...,0.125,export
2024-06-01T12:00:00+00:00,CH002...,0.080,import
```

`quality` defaults to `valid`. Use `invalid` to exclude a reading.

### S-DAT XML

Standard Swiss S-DAT metering data exchange format. Both namespaced (`xmlns="http://www.strom.ch/sdat/MeteringData"`) and plain variants are accepted.

## Configuration

Copy `config/leg_config.example.yaml` to `config/leg_config.yaml` and edit the
local file. `config/leg_config.yaml` is intentionally ignored by Git because it
may contain real meter IDs, broker addresses, and MQTT credentials.

| Key | Description |
|-----|-------------|
| `leg.community_id` | Unique ID for the ZEV/LEG community |
| `participants[].participant_id` | Business/billing identity such as house, flat, or tenant |
| `meters[].meter_id` | Official meter ID from the grid/operator data |
| `meters[].role` | `producer`, `consumer`, `producer_consumer`, or `grid` |
| `tariffs.local_rate_chf_kwh` | CHF/kWh for locally shared energy |
| `tariffs.grid_rate_chf_kwh` | CHF/kWh for grid-sourced energy |
| `processing.archive_processed` | Move processed files to archive (default `true`) |

Never commit real passwords or private MQTT credentials. Use the Home Assistant
add-on options for add-on deployments, or keep local credentials only in the
ignored `config/leg_config.yaml`.

## Matching algorithm

For each 15-minute slot, local energy is shared proportionally across meters — but **a meter can never supply itself**.

### Core rule

```
exporter.meter_id ≠ importer.meter_id  for every flow
```

A prosumer meter can have both export and import in the same slot. Its export goes into the community pool for *other* meters. Its import is covered from *other* exporters. The same electricity cannot leave and re-enter the same meter.

### Algorithm

**Step 1 — Eligible importers per exporter**

For each exporter E, the eligible importers are **all meters that have import, except E itself**.
Their combined import is called `eligible_import_E`:

```
eligible_import_E = Σ import_J   for all J ≠ E
```

This per-exporter denominator is what makes the self-exclusion rule work: if E has both export
and import in the same slot, E's own import is not counted in the denominator and E receives
nothing from its own export.

**Step 2 — Proportional cross-meter flows**

Exporter E distributes its full export proportionally among the eligible importers:

```
flow[E → I] = export_E × (import_I / eligible_import_E)   for all I ≠ E
```

Every eligible importer gets a share proportional to how much it needs relative to all other
eligible importers. Exporters with no eligible importers (single-meter community) contribute 0
to local_shared.

**Step 3 — Scale if demand is the limiting factor**

If total eligible demand exceeds total supply, all flows are scaled down uniformly:

```
total_raw    = Σ flow[E → I]   (all cross-meter flows)
scale        = min(1.0,  total_import / total_raw)
local_shared = total_raw × scale
```

When supply ≤ demand (typical solar community), scale = 1.0 and all export is shared locally.

**Step 4 — Residuals go to grid**

```
grid_export_E = export_E  − local_supplied_E
grid_import_I = import_I  − local_received_I
```

### Worked example

```
Slot 12:00

Meter 1:  export 5 kWh,  import 5 kWh   (prosumer)
Meter 2:  export 3 kWh,  import 1 kWh   (prosumer)
Meter 3:  export 0 kWh,  import 20 kWh  (consumer)

total_export =  8 kWh
total_import = 26 kWh
```

**Meter 1 distributes 5 kWh**

Eligible importers: Meter 2 (1 kWh) + Meter 3 (20 kWh)  — Meter 1 excluded from its own denominator
eligible_import_1 = 1 + 20 = 21 kWh

```
Meter 1 → Meter 2:  5 × ( 1 / 21) = 0.238 kWh
Meter 1 → Meter 3:  5 × (20 / 21) = 4.762 kWh
```

**Meter 2 distributes 3 kWh**

Eligible importers: Meter 1 (5 kWh) + Meter 3 (20 kWh)  — Meter 2 excluded from its own denominator
eligible_import_2 = 5 + 20 = 25 kWh

```
Meter 2 → Meter 1:  3 × ( 5 / 25) = 0.600 kWh
Meter 2 → Meter 3:  3 × (20 / 25) = 2.400 kWh
```

**Totals**

```
total_raw = 0.238 + 4.762 + 0.600 + 2.400 = 8.000 kWh
scale     = min(1.0, 26 / 8) = 1.0   → no scaling needed, all export goes local

local_shared = 8.000 kWh

Meter 1:  local_received = 0.600 kWh,  grid_import =  4.400 kWh
Meter 2:  local_received = 0.238 kWh,  grid_import =  0.762 kWh
Meter 3:  local_received = 7.162 kWh,  grid_import = 12.838 kWh
```

With **one meter only**, eligible_import = 0 for that meter → local_shared = 0.
There must be at least two meters for any local sharing to occur.

## Running tests

```bash
pip install -r requirements.txt
pytest tests/
```

## Home Assistant add-on source

The canonical Python source lives in `self_leg/` and `main.py`. The add-on
directory contains a build copy so Home Assistant can build the add-on from
`ha_addon/` as its Docker context.

After changing application code, refresh the add-on copy:

```bash
./prepare_addon.sh
```

To verify that the add-on copy is current:

```bash
python tools/prepare_addon.py --check
```

## Project layout

```
self_leg/                    Python package
  leg_const.py               Domain constants
  core/
    leg_config.py            YAML config loader + validation
    leg_runner.py            Pipeline orchestration
    leg_parser.py            CSV / S-DAT / XLSX parser
    leg_matcher.py           Proportional energy sharing
    leg_billing.py           Period aggregation & cost calculation
    leg_report.py            CSV + JSON report writers
    leg_storage.py           Processed-file state (SHA-256 dedup)
    leg_import.py            Inbox scan & archive
    leg_scheduler.py         Cron-based run scheduler
    leg_watcher.py           Inbox file watcher
    leg_share_importer.py    Share folder → inbox importer
    raw/
      ebl_xlsx.py            EBL Excel format parser
  ha/
    mqtt_runtime.py          MQTT client lifecycle
    mqtt_discovery.py        Home Assistant MQTT Discovery
    mqtt_entities.py         HA entity definitions
    ingress.py               HA Ingress web dashboard
  models/
    invoice.py               BillingRecord dataclass
    meter.py                 ImportFile / IntervalReading
    participant.py           Participant dataclass
ha_addon/                    Home Assistant Add-on
  config.yaml                Add-on manifest
  Dockerfile                 Multi-arch container image
  run.sh                     Container entrypoint
  generate_config.py         options.json → leg_config.yaml
main.py                      Entry point
data/inbox/                  Drop input files here
data/archive/                Processed files land here
data/reports/                billing_*.csv/json, match_detail_*.csv
data/state/                  processed_files.json
```
