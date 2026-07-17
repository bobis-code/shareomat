# SELF LEG — Home Assistant Add-on

Swiss LEG/ZEV settlement engine running as a standalone Home Assistant Add-on.

Imports smart meter interval data, calculates local energy sharing between
producers and participants, and publishes billing results to Home Assistant
via MQTT Discovery — no HA Python dependency, no custom component.

---

## Architecture

```
SELF LEG Add-on Container
        ↓ MQTT Discovery
Home Assistant (Mosquitto broker)
```

SELF LEG remains fully independent. HA only sees MQTT sensors.

---

## Installation

1. In Home Assistant: **Settings → Add-ons → Add-on Store**
2. Click the three-dot menu → **Repositories**
3. Add: `https://github.com/ha-bt-usr/self-leg`
4. Find **SELF LEG** and click **Install**

---

## Configuration

| Option | Description | Default |
|--------|-------------|---------|
| `mqtt_host` | MQTT broker hostname or IP | `core-mosquitto` |
| `mqtt_port` | MQTT broker port | `1883` |
| `mqtt_username` | MQTT username (optional) | |
| `mqtt_password` | MQTT password (optional) | |
| `base_topic` | Root MQTT topic prefix | `self_leg` |
| `discovery_prefix` | HA MQTT Discovery prefix | `homeassistant` |
| `command_topic_enabled` | Listen on `self_leg/cmd/run_once` for manual trigger | `true` |
| `community_id` | Unique ZEV/LEG community identifier | `ZEV-001` |
| `community_name` | Display name in HA device registry | |
| `local_rate_chf_kwh` | Local energy tariff in CHF/kWh | `0.12` |
| `grid_rate_chf_kwh` | Grid energy tariff in CHF/kWh | `0.28` |
| `feed_in_rate_chf_kwh` | Feed-in tariff in CHF/kWh | `0.08` |
| `timezone` | Timezone for report timestamps | `Europe/Zurich` |
| `log_level` | Log verbosity (`debug/info/warning/error`) | `info` |
| `dry_run` | Parse and match without archiving inbox files | `false` |
| `unknown_meter_policy` | What to do with meter IDs in the data not found in config: `fail` stops the run, `skip` ignores them | `skip` |
| `metering_points` | List of MPIDs with label and role | |
| `email_enabled` | Poll an IMAP mailbox for meter-data attachments | `false` |
| `email_imap_host` | IMAP server hostname | `imap.gmail.com` |
| `email_imap_port` | IMAP server port (TLS) | `993` |
| `email_username` | Mailbox address (e.g. a dedicated Gmail account) | |
| `email_password` | IMAP password — for Gmail use an **App Password**, not the account password | |
| `email_folder` | IMAP folder to watch | `INBOX` |
| `email_allowed_senders` | Only accept attachments from these sender addresses; empty = accept any sender | `[]` |
| `email_poll_interval_seconds` | How often to check the mailbox | `300` |

### Email import

Use a mailbox dedicated to meter data only — e.g. if your grid operator sends
readings by email, or you want to forward them yourself. The mailbox is
never modified: messages are read via `BODY.PEEK[]` (no `\Seen` flag set) and
nothing is deleted. Already-seen messages are tracked locally by IMAP UID in
`/config/self_leg/state/email_import_state.json`.

For Gmail: enable 2-Step Verification on the account, then create an
**App Password** under Google Account → Security → App passwords, and use
that as `email_password`.

### `metering_points` format

```yaml
metering_points:
  - mpid: "CH0012345678901234500000000000001"
    label: "PV Roof"
    role: "producer"
  - mpid: "CH0012345678901234500000000000002"
    label: "Apartment 1"
    role: "producer_consumer"
```

Roles: `producer` · `consumer` · `producer_consumer` · `grid`

`role` is used only as a startup plausibility check (at least one producer and one consumer must exist). Matching and billing are fully data-driven based on measured import/export direction per slot.

---

## Data Storage

All runtime data is persisted in the HA config area (survives add-on updates):

```
/config/self_leg/
├── leg_config.yaml   ← auto-generated from add-on options on each start
├── inbox/            ← drop CSV or S-DAT XML files here
├── archive/          ← processed input files moved here
├── reports/          ← billing_*.csv, billing_*.json, match_detail_*.csv
└── state/            ← processed_files.json (SHA-256 deduplication)
```

Drop meter data files into `/config/self_leg/inbox/` via SSH or the
HA File Editor add-on. SELF LEG scans inbox on each startup and on
every `self_leg/cmd/run_once` MQTT command.

---

## MQTT Topics

| Topic | Retained | Description |
|-------|----------|-------------|
| `self_leg/status` | yes | `starting` · `ok` · `error` · `offline` (LWT) |
| `self_leg/last_run` | yes | ISO timestamp of last successful run |
| `self_leg/billing/{mpid}/total_cost_chf` | yes | Total billing cost |
| `self_leg/billing/{mpid}/local_share_kwh` | yes | Locally shared energy |
| `self_leg/billing/{mpid}/grid_import_kwh` | yes | Grid-sourced energy |
| `self_leg/cmd/run_once` | **no** | Publish any payload to trigger a run |

---

## Manual Trigger

```bash
mosquitto_pub -h core-mosquitto -t "self_leg/cmd/run_once" -m "run"
```

---

## Building

The add-on build context contains a generated copy of the canonical Python
source from the project root. Refresh it before building or publishing:

```bash
./prepare_addon.sh
```

### Local Docker build (from project root)

```bash
docker build -f ha_addon/Dockerfile ha_addon
```

### Sync check

```bash
python tools/prepare_addon.py --check
```

---

## Input File Formats

### CSV

```
timestamp,mpid,value_kwh,direction
2024-06-01T12:00:00+00:00,CH001...,0.500,export
2024-06-01T12:00:00+00:00,CH002...,0.200,import
```

### S-DAT XML (Experimental)

Standard Swiss S-DAT metering data exchange format.
Validate output against source files before production use.
