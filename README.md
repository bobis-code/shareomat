# Shareomat

> **🚧 Work in Progress — under active development, not production-ready.**

Hallo

Dieses Repository ist mein Versuch, ein einfaches und möglichst offenes Abrechnungssystem für Schweizer LEG- und ZEV-Gemeinschaften zu entwickeln.

Die Idee entstand, weil ich bisher keine wirklich schlanke Lösung gefunden habe, die sich auf das Wesentliche konzentriert: Messdaten des Netzbetreibers möglichst automatisch zu importieren, den Verbrauch der LEG-Gemeinschaft korrekt zwischen lokal erzeugter Energie und Netzbezug aufzuteilen und daraus nachvollziehbare Abrechnungen zu erstellen.

Aktuell befindet sich das Projekt noch im Aufbau und ist weit von einer produktiven Version entfernt. Ich entwickle es hauptsächlich nebenbei an Wochenenden und in meiner Freizeit. Entsprechend wird sich noch einiges ändern.

Das langfristige Ziel ist es, eine Lösung zu schaffen, mit der kleinere LEG- oder ZEV-Gemeinschaften ihre Energieabrechnung mit möglichst wenig manuellem Aufwand durchführen können – idealerweise ohne teure Spezialsoftware.

**Shareomat läuft als Home-Assistant-Add-on oder komplett eigenständig, ganz
ohne Home Assistant** — dieselbe Kernanwendung (Weboberfläche, SQLite-
Datenbank, Abrechnungslogik) in beiden Fällen. Getestet habe ich bisher
primär den Weg als HA-Add-on (siehe Quick Start unten); der eigenständige
Weg per Docker Compose oder nativ mit Python sollte genauso funktionieren,
ist aber noch nicht im gleichen Umfang durchgespielt — es gibt dort einen
echten, bisher ungetesteten Code-Pfad (kein Ingress-Pfad-Präfix, siehe
"Standalone" weiter unten). Feedback dazu ist besonders willkommen.

---

## Quick start (Home Assistant add-on)

1. In Home Assistant: **Settings → Add-ons → Add-on Store**
2. Add this repository as a custom repository:
   `https://github.com/bobis-code/shareomat`
3. Install **Shareomat**, configure MQTT/e-mail if wanted, start it.
4. Open it from the sidebar (Ingress panel) and complete the setup wizard
   (Gemeinschaft → Teilnehmer → Messpunkte → Vertrag).

Gemeinschaft, Teilnehmer, Messpunkte, Vertrag, and automation settings are
managed entirely through this web interface — not through `leg_config.yaml`
— and are stored in a SQLite database. Add-on options cover only technical
runtime settings (MQTT, e-mail import).

Prices are set on the **Vertrag** page, not the Tarife page: publishing a
contract version there creates the tariff that governs billing for its
validity period, with the notice periods from the LEG-Mustervertrag enforced
before publishing. The Tarife page still exists, but only as a manual
fallback for first-time setup or emergencies — see its own warning banner.

Once set up, drop CSV or S-DAT files into the inbox (or upload them through
the "Messdaten" page) and trigger a run from the web interface. Reports are
written to the reports folder; processed files move to the archive.

The canonical Python source lives in `shareomat/` and `main.py`. The
`ha_addon/` directory contains a synced build copy so Home Assistant can
build the add-on from `ha_addon/` as its Docker context — it is not a
separate codebase, just a mirror kept in sync by a git pre-commit hook.

After changing application code, refresh the add-on copy manually if needed:

```bash
./prepare_addon.sh
```

To verify that the add-on copy is current:

```bash
python tools/prepare_addon.py --check
```

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

## External data sources

Beyond importing meter readings, the "Externe Daten" page pulls in official
Swiss reference data on demand — no API key needed except where noted:

| Source | What it fetches | Used for |
|---|---|---|
| **ElCom** (LINDAS SPARQL, `energy.ld.admin.ch/elcom`) | Grid operator lookup and official tariff components per municipality/category | Comparing/sanity-checking local tariffs |
| **BFE** (opendata.swiss, "Referenz-Marktpreise gemäss Art. 15 EnFV") | Official quarterly/monthly PV reference market price | The authoritative rate for final feed-in settlement |
| **SNB** (`data.snb.ch`) | EUR/CHF exchange rates | Converting EUR-denominated inputs (e.g. ENTSO-E) to CHF |
| **ENTSO-E** (Transparency Platform) | Swiss day-ahead prices and generation profiles — needs a free personal API token | A non-official price *forecast* for the dashboard/tariff planning only |

The BFE value is the only one of these used for actual settlement — ElCom,
SNB, and ENTSO-E feed a forecast/comparison view, never a billing run
directly. Every fetch is persisted (so results survive without re-fetching)
and logged in a recent-imports history on the same page.

## Configuration

Shareomat splits configuration into two places, deliberately:

- **`leg_config.yaml`** (technical runtime settings only) — paths, MQTT
  broker, e-mail import, web server port. For the add-on this is generated
  from the add-on options; for standalone use, copy
  `config/leg_config.example.yaml` to `config/leg_config.yaml` and edit the
  local file. It is intentionally ignored by Git because it may contain
  broker addresses and MQTT/IMAP credentials.
- **The admin web interface** (backed by `data/shareomat.db`) —
  Gemeinschaft, Teilnehmer, Messpunkte, Vertrag (which governs Tarife), and
  automation settings. This is the data that actually changes as a LEG/ZEV
  community grows, so it is managed live in the UI instead of a config file
  that requires a restart.

| `leg_config.yaml` key | Description |
|-----|-------------|
| `paths.inbox` / `archive` / `reports` / `state` | Runtime filesystem paths |
| `paths.share_inbox` | External share folder to watch for meter files; empty = disabled |
| `mqtt.enabled` / `broker` / `port` | Optional — publish status/prices/tariffs as MQTT topics (with Home Assistant MQTT Discovery) for Home Assistant or any other MQTT-aware system. Off by default; Shareomat runs fully without it. |
| `email.enabled` | Poll an IMAP mailbox (e.g. a dedicated Gmail account) for meter-data attachments |
| `email.allowed_senders` | Only accept attachments from these sender addresses; empty = accept any sender |
| `web.enabled` / `port` | Admin web interface — always available; standalone (`http://localhost:8099`) or, if run as the HA add-on, also behind Home Assistant Ingress |

Never commit real passwords or private MQTT/email credentials. Use the Home
Assistant add-on options for add-on deployments, or keep local credentials
only in the ignored `config/leg_config.yaml`. For Gmail, use an App Password
(requires 2-Step Verification), never the account password.

Upgrading from a version where `leg_config.yaml` still contained
`participants`/`meters`/`tariffs`/`leg`? Those sections are imported into
`data/shareomat.db` automatically, once, the first time you start the new
version — after that, the database is authoritative and those YAML sections
are ignored.

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
pip install -r requirements-dev.txt
pytest tests/
```

(`requirements-dev.txt` pulls in `requirements.txt` plus `pytest` — plain
`requirements.txt` alone does not include a test runner.)

## Standalone (without Home Assistant)

> **Not yet tested end-to-end by me** — the code path differs from the HA
> add-on in one concrete way (no `X-Ingress-Path` header, so URL generation
> takes the "no prefix" branch instead — see `shareomat/web/navigation.py`).
> It should work, since it's the same core application, but I have only
> verified the HA add-on path live so far. Reports welcome either way.

```bash
git clone https://github.com/bobis-code/shareomat.git
cd shareomat
cp config/leg_config.example.yaml config/leg_config.yaml

docker compose up --build
```

Open the web interface at `http://localhost:8099` and complete the same
setup wizard as above (Gemeinschaft → Teilnehmer → Messpunkte → Vertrag).

For a more detailed walkthrough (including a native Python setup without
Docker, sample test data, and troubleshooting), see
[`docs/local-setup.md`](docs/local-setup.md).

## Related: Emsomat (optional)

Shareomat can optionally publish LEG reference prices, recent local/grid
history, and a short-term consumption forecast over MQTT (see
[`docs/emsomat-integration.md`](docs/emsomat-integration.md)). One consumer
of this is [Emsomat](https://github.com/bobis-code/Emsomat), a separate
Home Assistant integration for local battery/heat-pump/EV optimization —
it uses the feed as extra context, but works fully on its own without
Shareomat too. Neither project requires the other. Note: unlike Shareomat,
Emsomat is a Home Assistant *integration* and is installed via HACS, not
the Add-on Store — see its own README.

## Project layout

```
shareomat/                    Python package
  leg_const.py               Domain constants
  config.py                  Technical runtime config (paths/MQTT/e-mail/web) + LegConfig + validation
  core/
    leg_runner.py            Pipeline orchestration — run(config: LegConfig)
    collector/                Data intake
      leg_import.py            Inbox scan & archive
      leg_scheduler.py         Cron-based run scheduler
      leg_watcher.py           Inbox file watcher
      leg_share_importer.py    Share folder → inbox importer
      leg_storage.py           Processed-file state (SHA-256 dedup)
    pipeline/                  Settlement processing
      leg_parser.py            CSV / S-DAT / XLSX parser
      leg_normalizer.py        Reading normalization
      leg_matcher.py           Proportional energy sharing
      leg_billing.py           Period aggregation & cost calculation
      raw/
        ebl_xlsx.py            EBL Excel format parser
    report/
      leg_report.py            CSV + JSON report writers
  database/                   SQLite admin database (source of truth for master data)
    sqlite.py                  Connection, schema, migrations
    community.py / participants.py / meters.py / tariffs.py / settings.py
    contract_versions.py       Versioned LEG contract terms — draft/publish/withdraw/end,
                                the source of truth for tariffs (see models/contract.py)
    contract_settings.py       Prefill defaults for a brand-new contract draft
    participant_contract.py    Which contract version/tariff a participant accepted
    config_builder.py          Combines RuntimeConfig + database into a LegConfig
    yaml_import.py             One-time import of a pre-existing leg_config.yaml
  models/                     Domain dataclasses
    community.py / participant.py / meter.py / tariff.py / settings.py
    contract.py                  ContractVersion / ContractSettings
    meter_data.py               IntervalReading / EnergySlot / ImportFile
    billing.py                  MatchResult / BillingRecord
  web/                        Admin web interface (standalone or behind HA Ingress)
    server.py                  HTTP server, routing, upload/download, CSRF
    state.py                   Shared engine <-> web state
    navigation.py               Sidebar structure + URL generation
    reports.py                  Report discovery/rendering helpers
    pages/                      One module per page (dashboard, participants, meters, ...)
    templates/                  Jinja2 templates
    static/                     app.css, banner.png
  ha/
    mqtt_runtime.py          MQTT client lifecycle
    mqtt_discovery.py        Home Assistant MQTT Discovery
    mqtt_entities.py         HA entity definitions
ha_addon/                    Home Assistant Add-on
  config.yaml                 Add-on manifest (technical options only)
  Dockerfile                  Multi-arch container image
  run.sh                      Container entrypoint
  generate_runtime_config.py  options.json → technical leg_config.yaml
main.py                      Entry point
data/inbox/                  Drop input files here
data/archive/                Processed files land here
data/reports/                billing_*.csv/json, match_detail_*.csv
data/state/                  processed_files.json
data/shareomat.db            Admin database (Gemeinschaft/Teilnehmer/Messpunkte/Vertrag/Tarife)
```
