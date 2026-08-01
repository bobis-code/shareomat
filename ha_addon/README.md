# Shareomat — Home Assistant Add-on

Swiss LEG/ZEV settlement engine running as a standalone Home Assistant Add-on.

Imports smart meter interval data, calculates local energy sharing between
producers and participants, and publishes billing results to Home Assistant
via MQTT Discovery — no HA Python dependency, no custom component.

---

## Architecture

```
Shareomat Add-on Container
        ↓ MQTT Discovery
Home Assistant (Mosquitto broker)
```

Shareomat remains fully independent. HA only sees MQTT sensors.

---

## Installation

1. In Home Assistant: **Settings → Add-ons → Add-on Store**
2. Click the three-dot menu → **Repositories**
3. Add: `https://github.com/bobis-code/shareomat`
4. Find **Shareomat** and click **Install**

---

## Configuration

The add-on options below cover technical infrastructure only. **Gemeinschaft,
Teilnehmer, Messpunkte, Tarife, and automation settings are configured in the
Shareomat web interface itself** (open the add-on's Web UI / Ingress panel),
not here — see "Ersteinrichtung" below.

| Option | Description | Default |
|--------|-------------|---------|
| `mqtt_host` | MQTT broker hostname or IP | `core-mosquitto` |
| `mqtt_port` | MQTT broker port | `1883` |
| `mqtt_username` | MQTT username (optional) | |
| `mqtt_password` | MQTT password (optional) | |
| `base_topic` | Root MQTT topic prefix | `shareomat` |
| `discovery_prefix` | HA MQTT Discovery prefix | `homeassistant` |
| `command_topic_enabled` | Listen on `shareomat/cmd/run_once` for manual trigger | `true` |
| `timezone` | Timezone for report timestamps | `Europe/Zurich` |
| `log_level` | Log verbosity (`debug/info/warning/error`) | `info` |
| `mqtt_tls` / `mqtt_ca_cert` | MQTT broker TLS | `false` / |
| `ingress_enabled` | Enable the Shareomat web interface | `true` |
| `share_inbox` | External share folder to watch for meter files | |
| `email_enabled` | Poll an IMAP mailbox for meter-data attachments | `false` |
| `email_imap_host` | IMAP server hostname | `imap.gmail.com` |
| `email_imap_port` | IMAP server port (TLS) | `993` |
| `email_username` | Mailbox address (e.g. a dedicated Gmail account) | |
| `email_password` | IMAP password — for Gmail use an **App Password**, not the account password | |
| `email_folder` | IMAP folder to watch | `INBOX` |
| `email_allowed_senders` | Only accept attachments from these sender addresses; empty = accept any sender | `[]` |
| `email_poll_interval_seconds` | How often to check the mailbox | `300` |

## Ersteinrichtung

Nach der Installation öffnen Sie die Shareomat Weboberfläche (Ingress-Panel
des Add-ons). Beim ersten Start ist die Datenbank leer und ein
Einrichtungsassistent führt durch: Gemeinschaft → Teilnehmer → Messpunkte →
Tarif. Danach lassen sich Teilnehmer, Messpunkte, Tarife und die Automatik
jederzeit über die Weboberfläche verwalten — Änderungen gelten sofort, ohne
Add-on-Neustart.

### Email import

Use a mailbox dedicated to meter data only — e.g. if your grid operator sends
readings by email, or you want to forward them yourself. The mailbox is
never modified: messages are read via `BODY.PEEK[]` (no `\Seen` flag set) and
nothing is deleted. Already-seen messages are tracked locally by IMAP UID in
`/config/shareomat/state/email_import_state.json`.

For Gmail: enable 2-Step Verification on the account, then create an
**App Password** under Google Account → Security → App passwords, and use
that as `email_password`.

Roles: `producer` · `consumer` · `producer_consumer` · `grid`

`role` is used only as a startup plausibility check (at least one producer and one consumer must exist). Matching and billing are fully data-driven based on measured import/export direction per slot.

---

## Data Storage

Runtime data is persisted in the HA config area, and admin data in the
add-on's own persistent `/data` (both survive add-on updates):

```
/config/shareomat/
├── leg_config.yaml   ← auto-generated technical config from add-on options
├── inbox/            ← drop CSV or S-DAT XML files here
├── archive/          ← processed input files moved here
├── reports/          ← billing_*.csv, billing_*.json, match_detail_*.csv
└── state/            ← processed_files.json (SHA-256 deduplication)

/data/
└── shareomat.db       ← Gemeinschaft/Teilnehmer/Messpunkte/Tarife/Automatik
```

Drop meter data files into `/config/shareomat/inbox/` via SSH or the
HA File Editor add-on. Shareomat scans inbox on each startup and on
every `shareomat/cmd/run_once` MQTT command.

---

## MQTT Topics

| Topic | Retained | Description |
|-------|----------|-------------|
| `shareomat/status` | yes | `starting` · `ok` · `error` · `offline` (LWT) |
| `shareomat/last_run` | yes | ISO timestamp of last successful run |
| `shareomat/billing/{mpid}/total_cost_chf` | yes | Total billing cost |
| `shareomat/billing/{mpid}/local_share_kwh` | yes | Locally shared energy |
| `shareomat/billing/{mpid}/grid_import_kwh` | yes | Grid-sourced energy |
| `shareomat/cmd/run_once` | **no** | Publish any payload to trigger a run |

---

## Manual Trigger

```bash
mosquitto_pub -h core-mosquitto -t "shareomat/cmd/run_once" -m "run"
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
