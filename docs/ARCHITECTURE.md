# Shareomat — Architektur

Architekturdokumentation nach dem [arc42](https://arc42.de)-Schema (12
Standardabschnitte). Ergänzt `AGENTS.md` (Regeln, *wie* gearbeitet wird)
um die Frage, *was* Shareomat ist und *warum* es so gebaut ist.

Stand: 2026-08-13. Bei grösseren architektonischen Änderungen
mitpflegen (siehe `AGENTS.md`, Grundregel 5).

---

## 1. Einführung und Ziele

### Aufgabenstellung

Shareomat ist eine Abrechnungs-Engine für Schweizer **LEG**- und
**ZEV**-Gemeinschaften (siehe Glossar, §12): Messdaten des Netzbetreibers
importieren, lokal geteilte Energie korrekt von Netzbezug trennen, daraus
nachvollziehbare Abrechnungen erzeugen — mit möglichst wenig manuellem
Aufwand für kleinere Gemeinschaften, ohne teure Spezialsoftware.

### Qualitätsziele (Top 5, priorisiert)

1. **Korrektheit** der Abrechnungsbeträge — Fehler hier kosten echtes Geld
   echter Teilnehmer. Deshalb: `Decimal` statt `float` in der
   Geldrechnung, harte Matching-Invarianten (README, "Matching-Algorithmus"),
   unveränderliche freigegebene Abrechnungsläufe.
2. **Nachvollziehbarkeit** — jede Abrechnung muss im Nachhinein erklärbar
   sein: welche Quelldateien, welcher Tarif, welcher Algorithmus-Schritt.
3. **Betreibbarkeit ohne Spezialwissen** — Zielgruppe sind kleine
   Gemeinschaften, nicht IT-Abteilungen. Setup-Assistent statt Absturz bei
   fehlender Konfiguration, verständliche deutsche Fehlermeldungen im
   Web-UI.
4. **Portabilität** — dieselbe Kernanwendung läuft als Home-Assistant-Add-on
   oder komplett eigenständig (Docker/nativ), ohne Verzweigung der
   Business-Logik.
5. **Robustheit gegenüber unsicheren externen Daten** — Netzbetreiber-
   Formate und -Schnittstellen sind teils unvollständig dokumentiert oder
   noch nicht final. Lieber ein klarer Fehler als eine geratene, falsche
   Abrechnung (siehe §8, "Raise, don't guess").

### Stakeholder

| Rolle | Interesse |
|---|---|
| LEG-/ZEV-Betreiber (Admin) | Bedient die Web-Oberfläche, richtet Gemeinschaft/Teilnehmer/Vertrag ein, löst Abrechnungen aus |
| Teilnehmer der Gemeinschaft | Bekommen eine nachvollziehbare Abrechnung, nutzen Shareomat selbst nicht direkt |
| Entwickler (menschlich oder KI) | Erweitert/wartet den Code — siehe `AGENTS.md` für Regeln |
| Home Assistant (optional) | Add-on-Host, Ingress-Reverse-Proxy, MQTT-Gegenstelle |
| Emsomat (optional, separates Projekt) | Konsumiert Shareomats MQTT-Feed für Batterie-/Verbrauchssteuerung |

---

## 2. Randbedingungen

* **Technisch:** Python (Standardbibliothek-lastig, wenige Abhängigkeiten —
  siehe `requirements.txt`), SQLite als einzige Datenbank, kein
  Web-Framework (eigener `BaseHTTPRequestHandler`-basierter Server plus
  Jinja2-Templates), kein Build-Schritt/Frontend-Framework.
* **Organisatorisch:** Solo-Projekt, in der Freizeit entwickelt (siehe
  README) — Entscheidungen müssen auch nach Wochen ohne Kontext
  nachvollziehbar sein (→ `AGENTS.md`, Code-Stil).
  Der Standalone-Betrieb ohne Home Assistant ist ein real existierender,
  aber **noch nicht live verifizierter** Code-Pfad (README, Abschnitt
  "Standalone").
* **Regulatorisch:** Schweizer Energierecht (StromVG/StromVV) definiert
  LEG/ZEV, HT/NT-Konzepte, Verteilnetzbetreiber-Pflichten. Shareomat bildet
  das ab, erfindet aber keine eigenen Interpretationen davon, wo eine
  offizielle Quelle fehlt (`AGENTS.md`, Grundregel 2).
* **Deployment-Randbedingung Home Assistant:** Läuft die Anwendung hinter
  HA Ingress, muss jede URL mit dem `X-Ingress-Path`-Header präfixiert
  werden (`shareomat/web/navigation.py:url_for()`) — ein einzelner
  Codepfad bedient beide Betriebsarten.

---

## 3. Kontextabgrenzung

### Fachlicher Kontext

```text
Netzbetreiber (VNB)
   │  Messdaten: CSV, S-DAT-XML, EBL-XLSX
   │  per E-Mail-Anhang, Share-Ordner, oder manueller Upload
   ▼
┌─────────────────────────────────────────────┐
│                  Shareomat                   │
│                                               │
│  Import → Normalisierung → Matching →        │
│  Abrechnung → Berichte                       │
└─────────────────────────────────────────────┘
   │                              │
   │ Web-UI (Browser)             │ optional: MQTT
   ▼                              ▼
LEG-/ZEV-Betreiber          Home Assistant / Emsomat
(Gemeinschaft, Vertrag,     (Status, Referenzpreise,
 Teilnehmer, Abrechnung)     lokale Historie, Prognose)
```

Zusätzlich, auf Abruf statt automatisch (Seite "Externe Daten"):

```text
Shareomat ──► ElCom (LINDAS SPARQL)   Tarifvergleich pro Gemeinde
Shareomat ──► BFE (opendata.swiss)    amtlicher PV-Referenzmarktpreis (fliesst in die Abrechnung)
Shareomat ──► SNB (data.snb.ch)       EUR/CHF-Wechselkurs
Shareomat ──► ENTSO-E                 Day-Ahead-Preise (Prognose/Dashboard, nicht Abrechnung)
Shareomat ──► EBL (geplant, blockiert) LEG-Service-Tarife / SDAT-CH-Messdaten
```

Von diesen ist nur der **BFE**-Wert tatsächlich abrechnungsrelevant; die
anderen sind Vergleichs- bzw. Prognosedaten (siehe README, "External data
sources").

### Technischer Kontext

* **Eingang:** `data/inbox/` — befüllt durch manuellen Upload, den
  IMAP-E-Mail-Importer, den Share-Ordner-Importer, oder direkt vom
  Betreiber kopiert.
* **Ausgang:** `data/reports/` (CSV/JSON), Web-UI, optional MQTT-Topics
  unter `{prefix}/...` (siehe `docs/emsomat-integration.md`).
* **Persistenz:** `data/shareomat.db` (SQLite, Stammdaten + Betriebs­daten),
  `config/leg_config.yaml` (technische Laufzeit-Konfiguration, nicht in
  Git).

---

## 4. Lösungsstrategie

* **Vier Schichten** (`web → database → core → models`, siehe §5) mit
  klarer Abhängigkeitsrichtung — keine Schicht greift "nach oben".
* **Zwei getrennte Konfigurationsquellen**: technische Laufzeit-Einstellungen
  (`RuntimeConfig`, aus YAML/Add-on-Optionen — Pfade, MQTT, E-Mail,
  Web-Port) versus fachliche Stammdaten/Einstellungen (SQLite — Gemeinschaft,
  Teilnehmer, Messpunkte, Vertrag, Betriebs-Settings). Zusammengeführt pro
  Lauf durch `config_builder.build_leg_config()`, damit Änderungen im Web-UI
  ohne Neustart wirken.
* **Ein gemeinsames internes Datenmodell für alle Rohformate**
  (`IntervalReading`/`EnergySlot`) — CSV, S-DAT, EBL-XLSX und künftig
  SDAT-CH-2025 (siehe `docs/sdat_leg_import.md`) landen alle darauf, bevor
  Matching/Abrechnung sie sehen. Ein neues Format bekommt ein neues Modul
  unter `core/pipeline/raw/`, keinen Sonderfall in der Abrechnungslogik.
* **"Raise, don't guess"** bei jedem extern vorgegebenen, aber nicht
  gesichert bekannten Format oder Regelwerk (SDAT-CH-XML-Struktur,
  EBL-API) — ein klarer, erklärender Fehler statt eine stillschweigend
  falsche Abrechnung.
* **Additive-only SQL-Migrationen** — eine bestehende Installation muss
  beim Update ohne manuellen Eingriff weiterlaufen.
* **Unveränderliche freigegebene Abrechnungsläufe** — Korrektur nur über
  einen neuen Status-Übergang (`cancelled`), nie Überschreiben.
* **Degradation statt Absturz** — fehlende/kaputte Konfiguration zeigt eine
  Fehlermeldung im Web-UI (`_serve_degraded`) statt den Container sterben
  zu lassen; eine leere Datenbank zeigt den Setup-Assistenten statt eines
  Crashes (`IncompleteConfigError`).

---

## 5. Bausteinsicht

### Ebene 1 — Schichten

```text
shareomat/web/          Admin-Weboberfläche (dünne Handler, rendert Templates)
        │  liest/schreibt über
        ▼
shareomat/database/     SQLite-Persistenz, ein Modul pro Entität
        │  liefert Domänen-Objekte an
        ▼
shareomat/core/         Business-Logik: collector → pipeline → report
        │  arbeitet auf
        ▼
shareomat/models/       Reine Dataclasses, keine I/O, keine Business-Logik
```

Dazu, quer zu den vier Schichten:

* `shareomat/config.py` — `RuntimeConfig`/`LegConfig`, siehe §4.
* `shareomat/leg_const.py` — projektweite Konstanten (einzige Quelle für
  String-Literale, die in mehr als einem Modul vorkommen).
* `shareomat/external_data/` — On-Demand-Abrufe externer Referenzdaten
  (ElCom, BFE, SNB/via `market_forecast.py`, EBL-Platzhalter).
* `shareomat/ha/` — MQTT-Client-Lifecycle, Home-Assistant-Discovery,
  Entity-Definitionen.

### Ebene 2 — Module je Schicht

```text
core/
  leg_runner.py            Pipeline-Orchestrierung — run(config: LegConfig)
  collector/                Dateneingang
    leg_import.py             Inbox-Scan & Archivierung
    leg_scheduler.py          Cron-basierter Lauf-Scheduler
    leg_watcher.py            Inbox-Datei-Watcher
    leg_share_importer.py     Share-Ordner → Inbox
    leg_email_importer.py     IMAP-Postfach → Inbox
    leg_storage.py            Verarbeitet-Status (SHA-256-Dedup)
  pipeline/                  Abrechnungsverarbeitung
    leg_parser.py              CSV / S-DAT (experimentell) / Aufruf von raw/
    leg_normalizer.py          Reading-Normalisierung
    leg_matcher.py             Proportionale Energieteilung
    leg_billing.py             Perioden-Aggregation & Kostenberechnung
    raw/                        Ein Modul pro externem Rohformat
      ebl_xlsx.py                 EBL-Excel-Export
      sdat_ch.py                  SDAT-CH-2025 E31/E66 (Gerüst, siehe docs/sdat_leg_import.md)
  report/
    leg_report.py             CSV-/JSON-Berichte

database/                   SQLite, Quelle der Wahrheit für Stammdaten
  sqlite.py                    Verbindung, Schema, additive Migrationen
  community.py / participants.py / meters.py / tariffs.py / settings.py
  contract_versions.py         Versionierte Vertragskonditionen (draft/publish/withdraw/end)
  contract_settings.py         Vorbelegung für einen neuen Vertragsentwurf
  participant_contract.py      Welche Vertragsversion ein Teilnehmer akzeptiert hat
  config_builder.py            RuntimeConfig + SQLite → LegConfig
  billing.py                   Interaktiver Abrechnungs-Workflow (Ausnahme: enthält Kernlogik, siehe Docstring dort)
  yaml_import.py               Einmaliger Import aus einer vorbestehenden leg_config.yaml

models/                     Domänen-Dataclasses
  community.py / participant.py / meter.py / tariff.py / settings.py
  contract.py                  ContractVersion / ContractSettings
  meter_data.py                IntervalReading / EnergySlot / ImportFile
  billing.py                    MatchResult / BillingRecord
  billing_workflow.py           BillingRun / BillingPreview / BillingSourceFile
  external_data.py              DataImportRecord

web/                         Admin-Weboberfläche
  server.py                     HTTP-Server, Routing, Upload/Download, CSRF
  state.py                      Geteilter Engine-/Web-Zustand
  navigation.py                 Sidebar-Struktur + URL-Generierung (Ingress-fest)
  pages/                        Ein Modul pro Seite (dashboard, participants, ...)
  templates/                    Jinja2-Templates
```

Vollständige, laufend aktuelle Liste siehe README, Abschnitt "Project
layout".

### Seiten der Web-Oberfläche

```text
Übersicht     — Dashboard
VERWALTUNG    — Gemeinschaft, Teilnehmer, Messpunkte, Tarife, Vertrag
BETRIEB       — Messdaten, Analyse, Abrechnungen, Rechnungen*, Automatisierung, Berichte
SYSTEM        — Einstellungen, Externe Daten
```

\* Rechnungen ist aktuell ein Platzhalter (`PLACEHOLDER_ROUTES` in
`navigation.py`) — siehe §11.

---

## 6. Laufzeitsicht

### Szenario: automatischer/manueller Abrechnungslauf

Ausgelöst durch Programmstart, Cron, Datei-Watcher, Share-/E-Mail-Importer,
MQTT-Kommando, oder den "Jetzt ausführen"-Knopf im Web-UI — alle über
`main._run_safe_cycle()`, geschützt durch einen Lock (`_RUN_LOCK`), damit
nie zwei Läufe gleichzeitig auf Inbox/Archiv/State schreiben.

```text
1. LegConfig frisch aus SQLite + RuntimeConfig bauen (config_builder)
2. Inbox scannen (neue Dateien, per SHA-256 von bereits Verarbeitetem unterschieden)
3. Parsen — Format-Router wählt csv / sdat (legacy oder sdat_ch, je nach
   meter_data_source) / xlsx
4. Unbekannte/inaktive Messpunkte herausfiltern (unknown_meter_policy)
5. Matching — proportionale Energieteilung pro 15-Minuten-Slot
6. Abrechnung — Perioden-Aggregation mit dem gültigen Tarif
7. Berichte schreiben (CSV/JSON) — muss erfolgreich sein, bevor irgendwas
   als verarbeitet markiert wird
8. Quelldateien als verarbeitet markieren, ins Archiv verschieben
9. MQTT publizieren (best effort — ein Fehler hier invalidiert den
   Abrechnungslauf nicht mehr, er ist zu diesem Zeitpunkt schon
   abgeschlossen)
```

Details und die exakte Reihenfolgebegründung: `shareomat/core/leg_runner.py`
(Docstring "Notes").

### Szenario: Vertragsänderung wirkt auf den nächsten Lauf

```text
Admin ändert/publiziert einen Vertrag im Web-UI
        │
        ▼
contract_versions.publish_version() — neue Tarif-Version, draft → published
        │
        ▼
Nächster Abrechnungslauf: config_builder.build_leg_config() liest den
zu diesem Zeitpunkt gültigen Tarif frisch aus SQLite
```

Kein Neustart nötig — mit einer dokumentierten Ausnahme: Änderungen an
Cron-Zeitplan/Automatik-Scan wirken erst nach einem Neustart, weil die
zugehörigen Threads nur beim Programmstart erzeugt werden (siehe `TODO.md`,
"Betrieb").

### Szenario: interaktive Abrechnung für eine frei gewählte Periode

`shareomat/database/billing.py` parst dafür bewusst **auch bereits
archivierte** Dateien erneut (nicht nur "was ist neu") — ein Betreiber kann
so eine beliebige historische Periode abrechnen, nicht nur "was seit dem
letzten Lauf reinkam".

---

## 7. Verteilungssicht

### Variante A — Home-Assistant-Add-on (primär getestet)

```text
Home Assistant Host
  └── Docker-Container (aus ha_addon/, Multi-Arch-Image)
        ├── main.py (Python-Prozess)
        ├── data/ (Volume: inbox, archive, reports, state, shareomat.db)
        └── Web-UI erreichbar über HA Ingress (Pfad-Präfix aus X-Ingress-Path)
  MQTT-Broker (z. B. Home Assistant Mosquitto Add-on) ── optional
```

`ha_addon/` ist **keine separate Codebasis** — ein Git-Pre-Commit-Hook hält
es als Sync-Kopie von `shareomat/` + `main.py` aktuell (Docker-Build-Kontext
für Home Assistant). Verifizieren: `python tools/prepare_addon.py --check`.

### Variante B — Standalone (Docker Compose oder nativ)

```text
Beliebiger Host
  └── docker compose (docker-compose.yml) oder nativer Python-Prozess
        ├── main.py
        ├── config/leg_config.yaml (lokal, nicht in Git)
        └── data/ (lokales Verzeichnis statt Named Volume)
  Web-UI erreichbar direkt unter http://localhost:8099 (kein Ingress-Präfix)
```

Gleicher Kern-Code wie Variante A — der einzige Unterschied ist das Fehlen
des `X-Ingress-Path`-Headers, was `navigation.py:url_for()` auf den
Kein-Präfix-Zweig umschalten lässt. Laut README noch nicht live
end-to-end verifiziert (siehe §11).

---

## 8. Querschnittliche Konzepte

* **Konfigurationstrennung** RuntimeConfig (YAML/Add-on-Optionen) vs.
  SQLite (Stammdaten/Settings) — siehe §4.
* **Rohformat-Normalisierung** über `core/pipeline/raw/` — siehe §4.
* **"Raise, don't guess"** bei unsicheren externen Formaten/Regeln — siehe
  §4, Beispiele: `external_data/ebl.py`, `core/pipeline/raw/sdat_ch.py`.
* **Additive SQL-Migrationen** (`database/sqlite.py:_COLUMN_MIGRATIONS`) —
  siehe §4.
* **Unveränderliche freigegebene Abrechnungsläufe** (`database/billing.py`)
  — siehe §4.
* **Fehlerbehandlung / Degradation:**
  * Fehlende/kaputte technische Konfiguration → `_serve_degraded()` zeigt
    eine Warnung im Web-UI statt den Prozess abstürzen zu lassen.
  * Fehlende Stammdaten (frische Installation) → `IncompleteConfigError`
    → Setup-Assistent, kein harter Fehler.
  * `do_GET`/`do_POST` in `web/server.py` fangen grundsätzlich jede
    unerwartete Exception ab und liefern immer eine Antwort — ein
    konkreter früherer Vorfall (unbehandelte `KeyError` → 502 ohne
    Antwort) hat das erzwungen (siehe `TODO.md`).
  * Nebenläufigkeitsschutz: `_RUN_LOCK` verhindert parallele
    Abrechnungsläufe, egal welcher Trigger sie ausgelöst hat.
* **Geldbeträge:** immer `Decimal`, nie direkt aus `float` konstruiert
  (`Decimal(str(round(value, 4)))`-Muster, siehe `database/billing.py`).
* **Audit-Trail externer Abrufe:** jeder Abruf von ElCom/BFE/SNB/ENTSO-E/EBL
  wird in `data_imports` protokolliert (was, wann, Erfolg/Misserfolg) —
  siehe `database/data_imports.py`.
* **Sprachkonvention:** Code/Kommentare/Logs Englisch, Doku/UI Deutsch —
  siehe `AGENTS.md`.

---

## 9. Architekturentscheidungen

Siehe `docs/decisions/`. Noch nicht als nummerierte ADRs nachgetragen, aber
bereits gelebte Entscheidungen (Kandidaten für ADR-0001 ff.):

* Freigegebene Abrechnungsläufe sind unveränderlich.
* Rohformat-Parser normalisieren auf ein gemeinsames internes Modell.
* "Raise, don't guess" bei unverifizierten externen Datenformaten.

---

## 10. Qualitätsanforderungen

Kurzer Qualitätsbaum (Details/Priorisierung siehe §1):

```text
Korrektheit
  ├── Decimal statt float in der Geldrechnung
  ├── Matching-Invarianten (exporter ≠ importer im selben Slot, README)
  └── Unveränderliche freigegebene Abrechnungsläufe

Nachvollziehbarkeit
  ├── Audit-Trail externer Abrufe (data_imports)
  ├── Quelldateien pro Abrechnungslauf referenziert (source_files)
  └── Geplant: Raw-Archiv für SDAT-Importe (docs/sdat_leg_import.md §16)

Robustheit
  ├── Degradation statt Absturz (_serve_degraded, IncompleteConfigError)
  ├── Lauf-Lock gegen Nebenläufigkeit
  └── Web-Server fängt jede unerwartete Exception ab

Portabilität
  └── Ein Kern-Code für HA-Add-on und Standalone
```

---

## 11. Risiken und technische Schulden

Laufend gepflegt in `TODO.md` (nicht in Git, lokal) — hier die
architektur-relevanten Punkte, Stand 2026-08-13:

* **Standalone-Betrieb** ist ein real existierender, aber **noch nicht
  live verifizierter** Code-Pfad (kein Ingress-Präfix-Zweig getestet).
* **HA-Add-on-Docker-Build** bisher nur über Datei-Sync-Check verifiziert,
  kein echter Container-Start getestet.
* **Kein Browser-Klicktest** der Weboberfläche — bisher nur `pytest` +
  direkte Handler-Aufrufe.
* **SDAT-CH-2025-Import** (`core/pipeline/raw/sdat_ch.py`) ist ein Gerüst
  ohne funktionierendes Parsing — blockiert auf offizielle VSE-XSDs (siehe
  `docs/sdat_leg_import.md` §32).
* **EBL-Anbindung** (LEG-Service-Tarife, Folgetag-Preise) blockiert extern
  auf eine BFE-Entscheidung, nicht auf Shareomat-Code.
* **ENTSO-E** strukturell fertig, aber ungetestet ohne echten API-Token.
* **`supplier_tariffs`-Tabelle** existiert im Schema, wird von nichts
  befüllt (wartet auf EBL) und hat keine eigene Verwaltungsseite.
* **Rechnungen** (`invoices`) sind bewusst ein Platzhalter — PDF-Erzeugung,
  Versand, Zahlungsstatus, Mahnungen fehlen komplett.
* **Cron-/Watcher-Änderungen** wirken erst nach einem Neustart (Threads nur
  bei Programmstart erzeugt) — bekannte Einschränkung, keine Regression.
* **Tarifwechsel mitten in einer Abrechnungsperiode** wird nicht anteilig
  aufgeteilt — der zu `period_start` gültige Tarif gilt für die ganze
  Periode (bewusste Vereinfachung, siehe `TODO.md`).

---

## 12. Glossar

| Begriff | Bedeutung |
|---|---|
| **LEG** | Lokale Elektrizitätsgemeinschaft (Schweizer Recht) |
| **ZEV** | Zusammenschluss zum Eigenverbrauch (Schweizer Recht, historisch vor LEG) |
| **VNB** | Verteilnetzbetreiber (z. B. EBL) |
| **EIC** | Energy Identification Code — eindeutige Akteurs-/Objekt-ID im europäischen Energiedatenaustausch |
| **CEM** | Community Energy Manager — SDAT-Rolle für den LEG-Vertreter im Datenaustausch |
| **HT/NT** | Hochtarif/Niedertarif — zeitabhängige Tarifzonen |
| **Prosumer** | Teilnehmer mit gleichzeitigem Export (Produktion) und Import (Verbrauch) |
| **Slot** | 15-Minuten-Messintervall — die kleinste Zeiteinheit im gesamten Modell |
| **Mangel / Überschuss / Ausgeglichen** | Verhältnis von lokaler Produktion zu Verbrauch in einem Slot (README, Matching-Beispiele) |
| **IntervalReading** | Ein roher Messwert (ein Meter, ein Slot, eine Richtung) — gemeinsames internes Modell aller Rohformate |
| **EnergySlot** | Alle Messwerte eines Slots, nach Meter gruppiert — Eingabe für das Matching |
| **BillingRun** | Ein Abrechnungslauf für eine Periode — draft/released/cancelled, released ist unveränderlich |
| **SDAT** | Standardisierter Schweizer Messdatenaustausch (VSE) — siehe `docs/sdat_leg_import.md` |
| **E31 / E66** | SDAT-Nachrichtentypen: E31 = aggregierte LEG-Daten, E66 = Messdaten je Teilnehmer |
| **Vertrag** | Der öffentliche LEG-Mustervertrag inkl. Preisen — Quelle der Wahrheit für Tarife, nicht die Tarife-Seite (die ist nur Fallback) |
