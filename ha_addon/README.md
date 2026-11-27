# Shareomat — Home Assistant Add-on

Swiss LEG settlement engine running as a standalone Home Assistant Add-on
(ZEV/vZEV planned, not yet supported).

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
| `sparkplug` → `mode` | Emsomat connection: `auto` finds the Emsomat on this Home Assistant, `manual` uses the two values below, `off` disables it | `auto` |
| `sparkplug` → `node_id` | Emsomat node ID, only used with `manual` | `emsomat-node` |
| `sparkplug` → `group_id` | Emsomat Sparkplug group, only used with `manual` | `emsomat` |
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
| `email_allowed_senders` | Only accept attachments whose `From` header matches one of these addresses. The header can be spoofed, so this is a filter, not sender authentication. Empty = accept any sender | `[]` |
| `email_poll_interval_seconds` | How often to check the mailbox | `300` |

## Ersteinrichtung

**Erstes Login (ab 0.6.0):** Shareomat ist mit einem Passwort geschützt.
Nach dem Start steht im **Protokoll** des Add-ons eine Zeile
«Shareomat Ersteinrichtung: Einmal-Code XXXX-XXXX-XXXX». Shareomat öffnen,
den Code eintragen, Benutzername und ein Passwort mit mindestens 12 Zeichen
festlegen. Auf dem eigenen Handy «30 Tage angemeldet bleiben» ankreuzen.
Passwort vergessen: Option `reset_login` einschalten, Add-on neu starten,
neuen Code aus dem Protokoll verwenden, Option wieder ausschalten.
Schritt für Schritt: `docs/Erstes_Login.md` im Repository.

Nach dem Login führt beim ersten Start (leere Datenbank) ein
Einrichtungsassistent durch: Gemeinschaft → Teilnehmer → Messpunkte →
Vertrag. Danach lassen sich Teilnehmer, Messpunkte, Vertrag und die Automatik
jederzeit über die Weboberfläche verwalten — Änderungen gelten sofort, ohne
Add-on-Neustart.

Preise werden auf der Vertrag-Seite festgelegt, nicht auf der Tarife-Seite:
das Veröffentlichen einer Vertragsversion erzeugt den Tarif, der für ihre
Gültigkeitsdauer massgebend ist, unter Einhaltung der im LEG-Mustervertrag
vorgesehenen Ankündigungsfristen. Die Tarife-Seite existiert weiterhin, aber
nur als manueller Notfall-/Erstinbetriebnahme-Fallback.

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
├── shareomat.db       ← Gemeinschaft/Teilnehmer/Messpunkte/Vertrag/Tarife/Automatik
└── documents/beitritte/ ← uploaded signed join declarations

/share/shareomat/backups/  ← backups from Einstellungen → Sicherung (visible over Samba)
```

### Backups (Einstellungen → Sicherung)

`/data` is private to the add-on and not reachable over Samba. The button
**Sicherung erstellen** writes one ZIP to `/share/shareomat/backups/`:

* `shareomat.db`: a consistent copy made with SQLite's online backup API,
  checked with `PRAGMA integrity_check` before it is kept,
* `documents/beitritte/`: the uploaded signed declarations,
* `manifest.json`: schema version and SHA-256 of every file (re-checked
  after writing).

Add-on options (MQTT, IMAP, Swisseldex, SMTP credentials) are **not** included. Exception for
now: the ENTSO-E token entered under *Externe Daten* is stored in the database and therefore is
in the backup; it moves to the add-on options with the rebuild. The file contains all personal
data of the community, so store it safely.
Downloading needs the password again if it was last entered more than
10 minutes ago, and every creation and download is in the audit log.
Shareomat never deletes old backups.

Since 0.6.32 every backup passes a **restore probe** before it is kept: the ZIP is checked entry
by entry and the database is extracted and opened as a restore would do it.

**Restoring** works on the command line, for a standalone installation (Docker, Linux, a new
computer): `python -m shareomat.restore <backup.zip> --db <path/to/shareomat.db>` with
Shareomat stopped. Existing data is moved to `vor-wiederherstellung-<time>/`, never deleted;
see `docs/local-setup.md` (German). Inside the add-on there is no command line yet: to restore
the add-on itself, use a Home Assistant backup (it contains `/data`). Restoring from the web
interface follows with the rebuild.

### Automatic Swisseldex sync

Externe Daten → Swisseldex: tick **Automatisch abholen**, choose the interval (1, 3, 6, 12 or
24 hours) and save. It applies at once, without restarting the add-on, and is stored in
Shareomat. Until it is saved there, the add-on options `swisseldex.auto_sync` and
`sync_interval_hours` apply as the initial value. Each sync fetches new files, backs them up
(see below) and imports them. Billing and sending are never automatic. Swisseldex keeps
messages for at most 30 days, so leave it on.

### Raw backup of the Swisseldex originals (network storage)

Settings → **Rohdatensicherung**. Shareomat copies every file fetched from the Swisseldex
Datahub, unchanged and write-once, to a network storage, before it archives or imports it. It
then reads the copy back and checks its SHA-256 (ADR-0010). Add the storage in Home Assistant
(Settings → System → Storage) with usage **Share**: it appears as `/share/<name>`, and the
add-on already maps `share:rw`. Next to each file there is a `<name>.sha256` proof file.
Shareomat never overwrites, renames or deletes anything there. With the mode "Pflicht"
(required), a file without a verified backup is neither archived nor imported. An interrupted
copy is completed automatically on the next sync. **Speicher testen** writes one small test
file to `verbindungstest/`, reads it back and proves that the storage refuses overwriting.
Switching to "Pflicht" runs the test first.

### Sending documents by e-mail

Shareomat can send invoices, credit notes, cancellation notices, reminders and the welcome
e-mail (with the contract package) itself, each with the document as a PDF (ADR-0011). It only
sends when you press the button, after a preview of recipient, text and PDF.

1. Configuration → section **smtp**: `enabled`, `host` (Gmail: `smtp.gmail.com`), `port`
   465 with `security: ssl` or 587 with `starttls`, `username`, `password`, `from_address`,
   optionally `from_name`. Gmail and other providers with 2-Step Verification need an
   **App Password**. There is no unencrypted mode.
2. Restart the add-on.
3. **E-Mail-Vorlagen** → **Test senden**: enter any test address and choose a sample invoice,
   a sample credit note or the plain connection test. The sample runs exactly like a real
   document: your template, letterhead, bank account and QR payment part, PDF attached. It is
   made for the fictitious person Max Muster, never stored, and marked: the subject starts with
   `[TEST]`, the PDF says "MUSTER – nicht bezahlen", and so does the QR message. At most 20 test
   e-mails per hour, each one in the audit log. Open the PDF and scan the QR payment part once
   with a banking app.

**Contract notice** (Checkliste → *Mitteilungen senden*): choose the participants, the way
(e-mail, letter, handed over) and the date, then **Vorschau** (each e-mail with the contract as
PDF attachment, plus *Test an mich senden*) or **Senden**. A notice counts as given only once the
e-mail was sent or the letter was created; both are filed under Dokumente → *Meldungen &
Mitteilungen*. The text is the e-mail template *Vertragsmitteilung*. The same form, for one
participant, is in that participant's section *Mitteilung / Zustimmung*; there *Mitteilung
zurücknehmen* removes a wrongly recorded notice so it can be sent again (the sent e-mail stays
under Dokumente).

The PDF is made from the frozen document, exactly as it prints. Nothing is fetched while it is
made. The only font in the image is Liberation Sans, which the QR-bill rules permit. Every
attempt is logged. If the connection breaks after the hand-over, the document shows
"Ergebnis unklar": check the mailbox's "Gesendet" folder before sending again.

### Home Assistant backups

The add-on is backed up **cold** (`backup: cold` in `config.yaml`): Home
Assistant stops Shareomat for the duration of its backup, so
`/data/shareomat.db` is never copied in the middle of a write. For
Shareomat this is the same as a normal add-on stop.

For daily backups set up Home Assistant's automatic backup (Settings →
System → Backups) with Shareomat among the apps and a location outside the
machine (external disk or network storage with usage "Backup"). Home
Assistant backups are encrypted by default: keep the emergency kit (encryption key)
and the login outside the Home Assistant machine, otherwise a backup can't
be restored on new hardware. `/share/shareomat/backups/` is only in a Home
Assistant backup if the share folder is selected.

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
