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

Edit `config/leg_config.yaml`:

| Key | Description |
|-----|-------------|
| `leg.community_id` | Unique ID for the ZEV/LEG community |
| `metering_points[].role` | `producer` or `participant` |
| `tariffs.local_rate_chf_kwh` | CHF/kWh for locally shared energy |
| `tariffs.grid_rate_chf_kwh` | CHF/kWh for grid-sourced energy |
| `processing.archive_processed` | Move processed files to archive (default `true`) |

## Matching algorithm

For each 15-minute slot, local energy is shared proportionally across meters — but **a meter can never supply itself**.

### Core rule

```
exporter.meter_id ≠ importer.meter_id  for every flow
```

A prosumer meter can have both export and import in the same slot. Its export goes into the community pool for *other* meters. Its import is covered from *other* exporters. The same electricity cannot leave and re-enter the same meter.

### Algorithm

**Step 1 — Cross-meter flows**

For each exporter E (export = X_E):
distribute X_E proportionally among all importers *except E*:

```
flow[E → I] = X_E × (import_I / Σ_{J ≠ E} import_J)   for all I ≠ E
```

**Step 2 — Scale if demand-limited**

```
total_raw  = Σ all cross-meter flows
scale      = min(1.0,  total_import / total_raw)
local_shared = total_raw × scale
```

**Step 3 — Residuals go to grid**

```
grid_export_E = export_E  − local_supplied_E
grid_import_I = import_I  − local_received_I
```

### Example

```
Slot 12:00

Meter 1:  export 5 kWh,  import 5 kWh   (prosumer)
Meter 2:  export 3 kWh,  import 1 kWh   (prosumer)
Meter 3:  export 0 kWh,  import 20 kWh  (consumer)

total_export = 8,  total_import = 26
local_shared = 8  (all export used locally)

Meter 1 → Meter 2: 5 × (1/21)  = 0.24 kWh
Meter 1 → Meter 3: 5 × (20/21) = 4.76 kWh
Meter 2 → Meter 1: 3 × (5/25)  = 0.60 kWh
Meter 2 → Meter 3: 3 × (20/25) = 2.40 kWh

Meter 1 receives 0.60 kWh local,  4.40 kWh from grid
Meter 2 receives 0.24 kWh local,  0.76 kWh from grid
Meter 3 receives 7.16 kWh local, 12.84 kWh from grid
```

With **one meter only**, local_shared = 0 — there is no one else to share with.

## Running tests

```bash
pip install -r requirements.txt
pytest tests/
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
