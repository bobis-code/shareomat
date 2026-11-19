# Shareomat — SDAT-CH-Import für Schweizer LEG

> **Stand 12.11.2026 (Swisseldex-Auftrag SX-1 bis SX-3):**
>
> * Empfang, Parser, Import und offizieller Abrechnungsweg sind umgesetzt und
>   mit den offiziellen VSE-Unterlagen (SDAT-CH 2025, Stand 04.12.2025) sowie
>   synthetischen Testdokumenten geprüft. Mit echten EBL-Dateien ist das noch
>   **nicht verifiziert**.
> * Empfang: §16 «Umsetzung des Empfangs».
> * Parser: `shareomat/core/pipeline/raw/sdat_ch.py`.
> * Import und Versionen: `shareomat/core/collector/sdat_import.py`,
>   `shareomat/database/sdat_store.py`.
> * Abrechnung mit der offiziellen Zuteilung: ADR-0008.
> * Was jetzt verifiziert ist und was offen bleibt: §32.
> * Der folgende Text ist das ursprüngliche Planungsdokument und bleibt als
>   Begründung erhalten.
>
> **Status (2026-08-13):** Planungsdokument. Beschreibt den Zielzustand für
> einen echten VSE-SDAT-CH-2025-Import (E31/E66). Abgleich mit dem
> bestehenden Code:
>
> - Es existiert bereits ein Datei-Routing für `.sdat`/`.xml` in
>   [`leg_import.py`](../shareomat/core/collector/leg_import.py) und ein
>   **experimenteller, stark vereinfachter** `parse_sdat()` in
>   [`leg_parser.py`](../shareomat/core/pipeline/leg_parser.py)
>   (`MeteringPoint`/`Observation`-Struktur, keine Namespaces, kein E31/E66,
>   keine Product-IDs, kein EIC/CEM/Business-Reason). Das ist **nicht** das
>   hier beschriebene SDAT-CH-2025-Format, sondern ein eigenes
>   Platzhalterschema.
> - Der reale SDAT-CH-Import lebt jetzt getrennt davon in
>   [`shareomat/core/pipeline/raw/sdat_ch.py`](../shareomat/core/pipeline/raw/sdat_ch.py)
>   (Phase 3 unten) — bewusst als eigenes Modul, damit der bestehende
>   Platzhalter-Parser nicht angefasst werden muss und nichts stillschweigend
>   auf ungeprüfte Annahmen umgestellt wird.
> - Die Datenquelle ist pro Community umschaltbar
>   (`Einstellungen → Messdaten-Quelle`, `email_csv` | `sdat_leg`,
>   siehe `shareomat.models.settings.OperationSettings.meter_data_source`).
>   Solange `sdat_leg` gewählt ist, aber die exakte XML-Struktur nicht anhand
>   der offiziellen XSDs verifiziert ist, wirft der Parser bewusst einen
>   klaren Fehler statt falsche Werte zu erzeugen (siehe §32, gleiches Muster
>   wie `shareomat.external_data.ebl`).
> - **Phase 1 (offizielles Referenzmaterial) ist nicht abgeschlossen**: es
>   liegen keine offiziellen SDAT-CH-2025-XSDs oder Beispieldateien im Repo
>   vor. Bis diese vorliegen, bleibt Phase 3 ein Gerüst mit klar markierten
>   Lücken statt eines funktionsfähigen Parsers für echte EBL-Daten.

## Ziel

Shareomat soll den standardisierten Messdatenaustausch für Schweizer
**Lokale Elektrizitätsgemeinschaften (LEG)** unterstützen.

Für die konkrete EBL-Anbindung ist vorgesehen:

```text
EBL → Datahub swisseldex → SDAT E31/E66 → Shareomat → Messpunkt-Zuordnung → Validierung → Abrechnung
```

EBL kündigt für die LEG monatlich die SDAT-Nachrichten **E31 und E66**
am **5. Werktag des Folgemonats** an. Für diesen Versand verlangt EBL
eine EIC-Codenummer.

Die Implementierung soll trotzdem nicht EBL-spezifisch aufgebaut werden.
Ziel ist ein allgemeiner **SDAT-CH-2025-Importer**, der auch Daten
anderer Schweizer Verteilnetzbetreiber verarbeiten kann.

---

## 1. Grundprinzip der Architektur

SDAT-Daten sollen **nicht direkt in die Abrechnungslogik** geschrieben
werden.

Stattdessen wird eine neutrale Zwischenschicht verwendet:

```text
EBL / anderer VNB
       │
       ▼
Datahub swisseldex
       │
       ▼
   SDAT E31/E66
       │
       ▼
   SDAT Parser
       │
       ▼
NormalizedMeteringData
       │
       ├────────────────► Billing
       └────────────────► Validation
```

Langfristig sollen alle Importformate auf dasselbe interne Datenmodell
führen:

```text
EBL CSV ───────────┐
                    │
SDAT E66 ───────────┼───► NormalizedMeteringData ────► Billing
                    │
andere VNB ─────────┘
```

---

## 2. E66 — Messdaten der einzelnen LEG-Teilnehmer

Für die eigentliche Teilnehmerabrechnung ist **E66** die zentrale
Nachricht.

E66 enthält Zeitreihen auf Ebene der einzelnen Messpunkte. Für eine LEG
werden insbesondere drei Energiearten unterschieden:

1. **LEG Total Energie** — tatsächlich am Messpunkt gemessene Energie.
2. **LEG Energie** — innerhalb der LEG ausgetauschter Energieanteil.
3. **LEG Restenergie** — verbleibender Energieanteil ausserhalb des
   LEG-Austauschs.

EBL beschreibt dies ebenfalls als drei Zeitreihen pro Messpunkt und
Richtung.

---

## 3. SDAT Product IDs

Shareomat sollte Zeitreihen anhand der standardisierten Product IDs
identifizieren und nicht anhand von Dateinamen oder EBL-spezifischen
Texten.

| Product ID      | Bedeutung         | Shareomat intern |
|-----------------|-------------------|-------------------|
| `8716867000030` | LEG Total Energy  | `total`           |
| `2404050010123` | LEG Energy        | `local`           |
| `2404050010124` | Residual Energy   | `residual`        |

Die Energieeinheit ist **kWh**.

```text
energy_type:
    total
    local
    residual
```

Die ursprüngliche Product ID soll zusätzlich erhalten bleiben.

---

## 4. Verbrauch und Produktion

Verbrauch und Produktion dürfen nicht über Vorzeichen hergeleitet
werden.

SDAT unterscheidet explizit zwischen:

```text
ConsumptionMeteringPoint
ProductionMeteringPoint
```

Bei Aggregaten werden zudem Richtungen unterschieden:

```text
E17 = Consumption
E18 = Production
```

Shareomat sollte daher explizit speichern:

```text
direction:
    consumption
    production
```

---

## 5. Messpunkt-Zuordnung

Die nationale Messpunktnummer verbindet Netzbetreiber, SDAT, Shareomat,
Teilnehmer und Abrechnung.

```text
VSENationalID
      │
      ▼
Shareomat MeteringPoint
      │
      ▼
Participant
```

Unbekannte Messpunkte dürfen nicht stillschweigend importiert werden:

```text
UNKNOWN_METERING_POINT
```

Sie müssen administrativ zugeordnet bzw. geprüft werden.

---

## 6. Zeitliche Auflösung

Die LEG-Messdaten werden in **15-Minuten-Intervallen** verarbeitet.

```text
2026-11-01 00:00
2026-11-01 00:15
2026-11-01 00:30
2026-11-01 00:45
...
```

Ein `MeteringData`-Block enthält unter anderem:

```text
Interval
Resolution
Product.ID
MeasureUnit
ConsumptionMeteringPoint / ProductionMeteringPoint

Observation[]
    Position
    Volume
    Condition
```

Aus `Interval`, `Resolution` und `Position` wird der Zeitstempel
bestimmt.

Zeitzone sowie Sommer-/Winterzeit müssen ausdrücklich korrekt behandelt
werden und dürfen nicht von der Server-Zeitzone abhängen.

---

## 7. E31 — aggregierte LEG-Daten

E31 enthält aggregierte Zeitreihen der gesamten LEG.

```text
Verbrauch:
    Total
    Rest
    LEG

Produktion:
    Total
    Rest
    LEG
```

Relevante Bezeichnungen aus den VSE-Unterlagen:

```text
LGS-LEGT   Verbrauch Total
LGS-LEGR   Verbrauch Rest
LGS-LEGE   Verbrauch LEG

EGS-LEGT   Produktion Total
EGS-LEGR   Produktion Rest
EGS-LEGE   Produktion LEG
```

E31 kann zur Kontrolle der individuellen E66-Daten verwendet werden.

---

## 8. Automatische E31/E66-Validierung

Beispiel:

```text
SUM(E66 Teilnehmer — LEG Verbrauch)
                │
                ▼
        Vergleich mit
                │
                ▼
E31 — LEG Verbrauch gesamt
```

Mögliche Zustände:

```text
VALIDATED
VALIDATION_FAILED
```

Bei Abweichungen sollten mindestens gespeichert werden:

```text
expected_kwh
actual_kwh
difference_kwh
difference_percent
```

So können Probleme erkannt werden, bevor eine Abrechnung erzeugt wird.

---

## 9. EIC und Empfängeridentifikation

Für den SDAT-Versand benötigt der LEG-Vertreter einen **EIC**.

Shareomat sollte auf Community-Ebene mindestens speichern:

```text
eic_code
```

Beim Import sollte geprüft werden:

```text
receiver.eic == community.eic_code
```

Nachrichten für einen anderen Empfänger dürfen nicht automatisch
verarbeitet werden.

> **Umgesetzt (2026-10-17):** `Community.eic_code` (Gemeinschaft → „EIC
> (Swissgrid)“), beim Speichern auf Länge und ENTSO-E-Prüfziffer geprüft.
> Die Empfängerprüfung `validate_receiver()` in
> [`sdat_ch.py`](../shareomat/core/pipeline/raw/sdat_ch.py) deckt §9–11 ab
> (EIC, Rolle CEM, Business Reason C40; ohne hinterlegten EIC wird alles
> abgewiesen). Sie wird aufgerufen, sobald der Parser die Empfängerfelder aus
> dem XML liest — deren Elementpfade sind weiterhin offen (§32).

---

## 10. Rolle CEM

Für den LEG-Datenaustausch ist die Rolle:

```text
CEM
```

relevant.

CEM steht für **Community Energy Manager**.

Validierung:

```text
receiver.role == CEM
```

---

## 11. Business Reason

Für den betrachteten LEG-Datenaustausch ist insbesondere:

```text
C40
```

relevant.

Konzeptionelle Importprüfung:

```text
receiver.eic == configured_eic
receiver.role == CEM
business_reason == C40
```

Diese Regeln sollten versionierbar bleiben.

---

## 12. CommunityID

Neben dem EIC existiert eine Identifikation der Elektrizitätsgemeinschaft
selbst.

Die **CommunityID** wird vom zuständigen Netzbetreiber vergeben und muss
getrennt vom EIC behandelt werden.

```text
community:
    name
    eic_code
    community_id
```

Der EIC identifiziert den Akteur/Empfänger im Datenaustausch; die
CommunityID identifiziert die konkrete Elektrizitätsgemeinschaft.

---

## 13. Community Type CT01 / CT02

Für LEG werden unterschiedliche Community-Typen unterschieden:

```text
CT01 = Basis LEG
CT02 = Zusammenfassende Rechnung LEG
```

Das Datenmodell sollte dies bereits vorsehen:

```text
community_type:
    CT01
    CT02
```

Die konkrete Verwendung für eine Community muss anhand der Einrichtung
durch den Netzbetreiber und der gewählten Abrechnungsvariante festgelegt
werden.

---

## 14. Original-, Ersatz- und Stornonachrichten

SDAT kann Originaldaten, Ersatzlieferungen oder Stornierungen enthalten.

Relevante Statuswerte sind unter anderem:

```text
9 = Original
5 = Replace
1 = Cancellation
```

Deshalb ist folgende Logik ungeeignet:

```text
if timestamp_exists:
    ignore
```

Korrekturen müssen nachvollziehbar verarbeitet werden.

---

## 15. Messwertqualität

Messwerte können Zustands-/Qualitätsinformationen enthalten,
beispielsweise:

```text
21 = Temporary
56 = Estimated
```

Diese Information darf nicht verworfen werden.

```text
energy_kwh
condition_code
quality
```

Damit kann später nachvollzogen werden, ob eine Abrechnung auf
geschätzten, temporären oder endgültigen Werten basiert.

---

## 16. Raw-Archivierung

Jede eingehende SDAT-Nachricht sollte **unverändert archiviert** werden.

```text
SDAT XML
   │
   ├──► Raw Archive
   │
   ▼
Parser
   │
   ▼
NormalizedMeteringData
```

Das Raw-Archiv dient:

- Nachvollziehbarkeit
- Fehleranalyse
- Re-Import
- Parser-Updates
- Abrechnungsnachweisen
- Korrekturen des Netzbetreibers
- Audits

Eine empfangene Originaldatei sollte niemals überschrieben werden.

### Umsetzung des Empfangs (SX-1, Stand 12.11.2026)

Umgesetzt sind der Empfang und das Raw-Archiv, die Verarbeitung noch nicht.
Module: `shareomat/core/collector/swisseldex_ftpes.py` (FTPES-Client) und
`shareomat/core/collector/swisseldex_receiver.py` (Empfang, Archiv, Protokoll).

* **Konfiguration:** Add-on-Optionen, Abschnitt `swisseldex` (im
  Standalone-Betrieb der Abschnitt `swisseldex` in `config.yaml`). Das Passwort
  steht nie in `shareomat.db`. Alles ist standardmässig aus, auch die
  automatische Synchronisation (Intervall 1/3/6/12/24 h, Standard 6 h).
* **Bedienung:** Seite «Externe Daten», Karte «Swisseldex Datahub» mit Status,
  «Verbindung testen», «Jetzt synchronisieren», Protokoll und empfangenen
  Dateien.
* **Archiv:** `<state>/swisseldex/received/<SHA-256-Präfix>_<Dateiname>`.
  * Die Datei wird byte-genau so gespeichert, wie sie von Swisseldex kam.
  * Ablauf: Download in eine `.part`-Datei, Prüfung der Grösse, `fsync`, dann
    Umbenennen.
  * Das Archiv liegt bewusst **nicht** in Inbox oder Archiv der
    Abrechnungsdateien. Die Freigabe eines Abrechnungslaufs prüft diese beiden
    Ordner auf Veränderungen, und der Datei-Watcher würde dort Dateien sofort
    verarbeiten.
* **Zustand und Protokoll:** `<state>/swisseldex/state.json`.
  * Der Zustand liegt in einer JSON-Datei wie beim E-Mail-Importer, nicht in
    `shareomat.db`. Eine Synchronisation schreibt deshalb nie in die
    Geschäftsdatenbank.
  * Die Datei wird atomar geschrieben. Ist sie beschädigt, wird sie nicht
    zurückgesetzt, und der Empfang hält an.
* **Duplikate:**
  * Eine Datei mit gleichem Namen, gleicher Grösse und gleichem Zeitstempel
    gilt als bekannt und wird nicht erneut geladen.
  * Gleicher Inhalt unter neuem Namen wird erkannt (SHA-256) und nicht doppelt
    abgelegt.
  * Gleicher Name mit neuem Inhalt wird als zweite Datei behalten und
    gekennzeichnet.
* **Prüfbedürftig, nicht still ignoriert:**
  * Nicht zulässige Dateinamen und andere Endungen als `.xml`, `.txt` und
    `.csv` (jeweils auch `.gz`) werden nicht heruntergeladen.
  * Ebenso Dateien über 50 MB.
  * Beschädigte `.gz`-Dateien und solche, die entpackt über 512 MB gross sind,
    werden archiviert, aber gekennzeichnet.
* **Swisseldex-Regeln:**
  * Zwischen zwei Anmeldungen liegen mindestens 61 s, gespeichert über einen
    Neustart hinweg. Eine zweite Anfrage wird eingeplant statt abgewiesen.
  * Nach einem Abbruch beginnt der Download neu, weil der Server kein `REST`
    kennt.
  * Ein TLS-Schliessfehler nach vollständigem Download (Grösse belegt) ist nur
    ein Hinweis.
  * Der Datenkanal verwendet die TLS-Sitzung des Steuerkanals wieder.
* **Nie:**
  * kein Löschen, Umbenennen oder Hochladen auf Swisseldex,
  * keine Quittungen,
  * kein Import in die Messdaten,
  * keine Abrechnung.
* **Warnung:** Nach 7 Tagen ohne erfolgreiche Synchronisation erscheint ein
  Hinweis auf jeder Seite. Swisseldex löscht nicht abgeholte Dateien nach
  30 Tagen.

---

## 17. Idempotenz und Duplikaterkennung

Der gleiche Datensatz kann mehrfach eintreffen.

Shareomat muss idempotent importieren.

Mögliche Identifikatoren:

```text
document_id
message_id
metering_point_id
product_id
direction
interval
position
document_status
```

Die endgültige Schlüsselbildung ist anhand der offiziellen XSDs und
Beispielnachrichten festzulegen.

Ziel:

```text
gleiche Nachricht erneut
        →
kein doppelter Messwert
```

aber:

```text
Replace-Nachricht
        →
neue Version des Messwerts
```

---

## 18. Versionierung der Messwerte

Empfohlenes Konzept:

```text
MeteringValueVersion
    metering_point_id
    timestamp
    direction
    energy_type
    value_kwh
    condition
    source_document_id
    status
    received_at
    supersedes
```

Damit bleibt nachvollziehbar:

```text
Original
   →
Replace
   →
Replace
```

Für die Abrechnung wird jeweils die aktuell gültige Version verwendet.

---

## 19. Monatliche und tägliche Lieferungen

Für die konkrete EBL-LEG ist laut Formular vorgesehen:

```text
monatlich
E31 + E66
am 5. Werktag des Folgemonats
```

Der Parser sollte trotzdem nicht technisch auf monatliche Lieferungen
beschränkt werden. Wiederholte oder häufigere zulässige Lieferungen
sollen verarbeitet werden können.

---

## 20. Vorgeschlagenes normalisiertes Datenmodell

```text
NormalizedMeteringData

community_id
metering_point_id

timestamp
interval_minutes

direction
    consumption
    production

energy_type
    total
    local
    residual

energy_kwh

product_id
measure_unit

condition_code
quality

source
    sdat_e66
    sdat_e31
    csv

document_id
document_status
received_at
```

Nicht zwingend alle Felder müssen in einer einzelnen Tabelle liegen.
Entscheidend ist, dass diese Informationen erhalten bleiben.

---

## 21. Trennung von Raw-, Import- und Billing-Daten

```text
1. RawDocument
       │
       ▼
2. Import / Parsed Data
       │
       ▼
3. NormalizedMeteringData
       │
       ▼
4. Billing Data
       │
       ▼
5. Invoice / Billing Version
```

Damit bleibt getrennt:

- Was hat der Netzbetreiber geliefert?
- Wie hat Shareomat die Daten interpretiert?
- Welche Werte wurden für eine konkrete Abrechnung verwendet?

---

## 22. Fehlerbehandlung

Sinnvolle Fehler-/Statusklassen:

```text
INVALID_XML
INVALID_SCHEMA
UNSUPPORTED_SDAT_VERSION
UNKNOWN_MESSAGE_TYPE
WRONG_RECEIVER
WRONG_ROLE
WRONG_BUSINESS_REASON
UNKNOWN_COMMUNITY
UNKNOWN_METERING_POINT
UNKNOWN_PRODUCT
UNKNOWN_UNIT
INVALID_INTERVAL
MISSING_OBSERVATION
DUPLICATE_DOCUMENT
VALIDATION_FAILED
```

Fehlerhafte Dokumente bleiben trotzdem im Raw-Archiv erhalten.

---

## 23. Import-Protokoll

```text
ImportJob

id
received_at
source
filename
document_id
message_type
status

records_found
records_imported
records_replaced
records_cancelled
records_skipped
errors
warnings
```

Beispiel für die UI:

```text
E66 — November 2026
96'000 Werte gefunden
96'000 importiert
0 Fehler
2 Warnungen

Status: IMPORTED
```

---

## 24. Billing-Freigabe

Eine Periode sollte nicht automatisch abrechnungsbereit sein, nur weil
Messwerte vorhanden sind.

Mögliche Zustände:

```text
WAITING_FOR_DATA
DATA_RECEIVED
VALIDATING
VALIDATION_FAILED
READY_FOR_BILLING
BILLED
```

Beispiel:

```text
E66 vollständig
+
E31 vorhanden
+
E31/E66-Validierung erfolgreich
=
READY_FOR_BILLING
```

---

## 25. E31 als Kontrollinstanz

Mögliche Prüfungen:

```text
SUM(E66 local consumption)
↔
E31 local consumption
```

```text
SUM(E66 local production)
↔
E31 local production
```

Zusätzliche Plausibilitäten:

```text
local <= total
residual <= total
```

und möglicherweise:

```text
total ≈ local + residual
```

Solche Gleichungen dürfen erst als harte Regeln implementiert werden,
wenn ihre genaue Semantik anhand der offiziellen Spezifikation und
Beispieldaten bestätigt wurde.

---

## 26. Sicherheit

SDAT-Dateien sind externe Eingaben.

Für XML insbesondere:

- keine externen Entities
- kein unkontrolliertes DTD-Processing
- Grössenlimits
- sichere XML-Bibliothek
- Schema-Validierung
- keine Ausführung von Daten aus XML
- Dateinamen nicht als vertrauenswürdig behandeln

---

## 27. Parser-Struktur

> Umgesetzt als ein Modul statt eines Unterpakets, konsistent mit dem
> bestehenden `shareomat/core/pipeline/raw/`-Muster (siehe `ebl_xlsx.py`):
> [`raw/sdat_ch.py`](../shareomat/core/pipeline/raw/sdat_ch.py) enthält
> Konstanten, Exceptions und den Parser-Einstiegspunkt in einer Datei,
> solange der Umfang das erlaubt. Eine Aufteilung wie unten skizziert
> lohnt sich erst, sobald der Parser tatsächlich gegen echte
> Beispieldateien implementiert wird.

Empfohlene Python-Struktur:

```text
shareomat/
└── sdat/
    ├── parser.py
    ├── models.py
    ├── constants.py
    ├── validation.py
    ├── normalizer.py
    ├── importer.py
    ├── exceptions.py
    └── schemas/
```

### `constants.py`

```text
Product IDs
Role Codes
Business Reason Codes
Status Codes
Condition Codes
Community Types
```

### `parser.py`

XML → SDAT-interne Python-Objekte.

### `normalizer.py`

SDAT-interne Objekte → Shareomat `NormalizedMeteringData`.

### `validation.py`

Prüft unter anderem:

```text
EIC
Rolle
Business Reason
Messpunkt
Product ID
Einheit
Zeitintervall
E31/E66-Konsistenz
```

### `importer.py`

Verantwortlich für:

```text
Raw speichern
Parser starten
Duplikate erkennen
Versionen erzeugen
Transaktion durchführen
ImportJob aktualisieren
```

---

## 28. XML nicht hart an Prefixes koppeln

Namespaces korrekt anhand von **Namespace URI + Local Name**
verarbeiten.

Nicht:

```text
suche exakt nach "ns2:MeteringData"
```

Namespace-Prefixes können sich ändern, obwohl die Nachricht semantisch
identisch bleibt.

---

## 29. SDAT-Version speichern

Jede Nachricht sollte ihre erkannte Version speichern:

```text
schema_version
sdat_version
```

Dadurch können später mehrere SDAT-Versionen parallel unterstützt
werden.

---

## 30. CSV und SDAT auf denselben Kern führen

```text
CSV Parser ──────┐
                  ▼
       NormalizedMeteringData
                  ▲
SDAT Parser ──────┘
```

Die Abrechnung kennt danach nur noch:

```text
Messpunkt
Zeit
Richtung
Energieart
kWh
Qualität
```

und muss das ursprüngliche Lieferformat nicht kennen.

---

# 31. Konkreter Implementierungsplan

## Phase 1 — Referenzmaterial sichern

Benötigt werden:

- aktuelle SDAT-CH-2025-Spezifikation
- E31-Spezifikation
- E66-Spezifikation
- offizielle XSD-Dateien
- Core Components
- offizielle Beispiel-XML-Dateien
- LEG-spezifische Beispieldaten

**Noch nicht im Repo vorhanden.** Bis diese Unterlagen vorliegen, bleibt
alles unter §32 unten Annahme statt Fakt.

## Phase 2 — Test Fixtures

```text
tests/fixtures/sdat_ch/
    e66_consumption.xml
    e66_production.xml
    e31_leg.xml
    replace.xml
    cancellation.xml
    estimated_values.xml
```

Falls die Originalbeispiele lizenzrechtlich nicht ins Repository dürfen,
werden minimale eigene Fixtures daraus abgeleitet — aber erst, sobald die
echte Struktur (Namespaces, Elementpfade) aus offiziellem Material bekannt
ist. Ein Platzhalter-Ordner mit Erklärung liegt bereits vor.

## Phase 3 — Parser

Zunächst E66 und E31 mit folgenden Feldern:

```text
Document ID
Sender
Receiver
Role
Business Reason
Community ID
Metering Point
Product ID
Direction
Interval
Resolution
Unit
Observations
Condition
Status
```

Noch keine Billing-Logik.

**Umgesetzt als Gerüst** in `raw/sdat_ch.py`: bekannte Konstanten (Product
IDs, Rollen, Business Reason, Status-/Condition-Codes, Community-Typen)
sind bereits eingetragen; das eigentliche XML-Parsing wirft aktuell
`SdatChNotImplementedError`, weil die exakten Namespaces/Elementpfade
laut §32 nicht geraten werden dürfen.

## Phase 4 — Normalizer

```text
SDAT Product ID → energy_type
ConsumptionMeteringPoint → consumption
ProductionMeteringPoint → production
```

## Phase 5 — Datenbank

Einführen bzw. an bestehende Modelle anbinden:

```text
RawDocument
ImportJob
NormalizedMeteringData
MeteringValueVersion
```

## Phase 6 — E66-Import

```text
E66
 │
Messpunkt finden
 │
15-Minuten-Werte normalisieren
 │
speichern
```

## Phase 7 — E31-Import

```text
E31
 │
LEG-Aggregate speichern
 │
mit E66 vergleichen
```

## Phase 8 — Billing Gate

Abrechnung erst freigeben, wenn die erforderlichen Daten vollständig und
plausibel sind.

## Phase 9 — UI

Bereich:

```text
Datenimporte
```

Beispiel:

| Datum      | Typ | Zeitraum | Status    | Werte | Fehler |
|------------|-----|----------|-----------|-------|--------|
| 07.12.2026 | E66 | 11/2026  | Imported  | ...   | 0      |
| 07.12.2026 | E31 | 11/2026  | Validated | ...   | 0      |

Detailansicht:

```text
Sender
Receiver
Document ID
Community ID
Messpunkte
Produkte
Zeitraum
Qualität
Warnungen
Fehler
Raw Document
```

---

# 32. Vor Implementierung noch anhand der Originalunterlagen verifizieren

Folgende Punkte dürfen **nicht geraten** werden:

1. exakte XML-Namespaces
2. exakte Elementpfade von E31 und E66
3. Dokument-ID-/Message-ID-Struktur
4. genaue Semantik aller Statuscodes
5. genaue Semantik aller Condition-Codes
6. Zeitstempelbildung aus `Interval`, `Resolution` und `Position`
7. DST-Verhalten bei Sommer-/Winterzeit
8. CommunityID im konkreten E31/E66-XML
9. CT01/CT02 im konkreten Datenaustausch
10. E31-Aggregatstruktur
11. Ersatz- und Stornoverarbeitung
12. zulässige Einheiten
13. Vollständigkeitsregeln pro Abrechnungsmonat
14. technischer Empfang vom Swisseldex Datahub
15. Envelope-/Dateinamenskonventionen

Diese Punkte werden aus den offiziellen XSDs und Beispielnachrichten
abgeleitet.

### Stand der Verifikation (12.11.2026)

Quelle: VSE-ZIP «Beispieldaten (SDAT-CH 2025)»
`https://www.strom.ch/system/files/media/documents/20251204-be-standardisierter-datenaustausch-ch-beispieldateien.zip`
mit XSDs und Beispielen, dazu «Messdatenaustauschprozesse» Kapitel 1.12/1.13 und
Anhang 1. Die PDF-Links `media/13631` und `media/13636` sind Beschreibungen,
keine XSD-Dateien.

| Nr. | Punkt | Ergebnis |
|---|---|---|
| 1 | Namespaces | `http://www.strom.ch`, Wurzel `ValidatedMeteredData_16` (E66) bzw. `AggregatedMeteredData_13` (E31). Erkannt über Namespace, nicht über das Präfix |
| 2 | Elementpfade E66/E31 | aus XSD und Beispielen 1_12_3/1_13_3 übernommen |
| 3 | Dokument-ID | `InstanceDocument/DocumentID`, je Zeitreihe zusätzlich `MeteringData/DocumentID` |
| 4 | Statuscodes | 9 Original, 5 Replace, 1 Cancellation |
| 5 | Qualitätscodes | nur 21 (vorläufig) und 56 (Ersatzwert) zulässig (XSD-Pattern), fehlend = normaler Wert |
| 6 | Zeitstempel | `Interval` in UTC, Position n = Beginn + (n−1) × 15 min, alle Positionen 1..N genau einmal |
| 7 | Sommer-/Winterzeit | ergibt sich aus der UTC-Intervalllänge (92/96/100 Viertelstunden), getestet |
| 8 | CommunityID | je Zeitreihe `Community/CommunityID` (Muster `[0-9]{6}-[0-9A-Z]{6}`). Ein Dokument kann mehrere Gemeinschaften enthalten |
| 9 | CT01/CT02 | `Community/CommunityType/VSENationalCode` |
| 10 | E31-Struktur | je Netzgebiet (`MeteringGridArea/EICID`) und Fluss (`FlowCharacteristic` E17 Verbrauch / E18 Einspeisung) |
| 11 | Ersatz/Storno | umgesetzt nach ADR-0008. Needs verification: wie EBL storniert |
| 12 | Einheiten | nur `KWH`, höchstens 3 Nachkommastellen |
| 13 | Vollständigkeit pro Monat | Gesamtenergie = LEG-Energie + Restenergie (in den Beispielen erfüllt). Fehlende Teile sperren die Abrechnung |
| 14 | Empfang | FTPES-Postfach beim Datahub (SX-1) |
| 15 | Dateinamen | nicht verwendet, die Erkennung erfolgt über den Inhalt |

Bekannte Abweichung im VSE-Beispiel 1_12_3: Die Produktionsblöcke f/g tragen die
Produkt-ID «Total» statt LEG- bzw. Restenergie. Ihre Werte sind aber LEG- und
Restenergie (900 = 700 + 200). Shareomat weist so ein Dokument als mehrdeutig ab
(`DUPLICATE_SERIES`) und rät nicht.

### Offen bis zur ersten echten Lieferung (Version 0.6.11)

Version 0.6.11 ist **nicht mit echten Monatsdaten verifiziert**. Die erste
EBL-Lieferung (November 2026) wird um den 5. Arbeitstag im Dezember 2026 erwartet.
Bis dahin bleiben offen:

| Nr. | Prüfung | Wie |
|---|---|---|
| O1 | Echte Anmeldung am Datahub mit den Zugangsdaten aus den Add-on-Optionen, TLS-Sitzung im Datenkanal | Button «Verbindung testen» auf «Externe Daten» |
| O2 | Echte E66/E31-Dateien von EBL werden empfangen, archiviert und ohne Fehler importiert | Protokoll und Dateiliste auf «Externe Daten» |
| O3 | Gesamtenergie = LEG-Energie + Restenergie auch in den echten Daten | Abrechnungsvorschau mit Datenquelle SDAT |
| O4 | Wie EBL Korrekturen und Stornierungen schickt (Status 5/1) | erste Korrekturlieferung abwarten |
| O5 | Ob EBL eine Empfangsbestätigung verlangt (`isIntelligibleCheckRequired`) | Hinweis im Import, Frage an EBL/Swisseldex |
| O6 | Ob fehlende E31-Summen und Ersatzwerte (56) im SDAT-Modus die Rechnungen sperren sollen | Entscheid des Betreibers |
| O7 | Build des Add-on-Images | erster Update-Lauf in Home Assistant (lokal kein Docker) |

Keine vollständige XSD-Validierung: Dafür bräuchte es den kompletten VSE-, UNECE-
und eCH-Schemasatz und eine zusätzliche Bibliothek. Der Parser prüft jedes
verwendete Element gegen Typ und Codeliste der XSDs und weist alles andere ab.

### Erste echte Lieferungen (Stand 18.11.2026)

Am 18.11.2026 lagen 7 Tageslieferungen E66 von EBL im Postfach (erstellt 12. bis
18.11.2026). Ausgewertet nur lesend mit einer Kopie; die Dateien sind echte
Messdaten und gehören nie ins Repository.

| Nr. | Ergebnis |
|---|---|
| O1 | ✓ Anmeldung und TLS im Datenkanal funktionieren (Verbindungstest, Synchronisation) |
| O2 | ✓ für E66: 7 Dateien empfangen, archiviert und importiert, Duplikaterkennung bestätigt. E31 kam noch keine |
| O3 | Gilt **nicht exakt**: EBL rundet Total, LEG- und Restenergie je auf 0.001 kWh. In 5 von 2112 Viertelstunden weicht Total um −0.001 kWh ab, im Energie-Clearing in 2 Viertelstunden um +0.001 kWh. Darum ab 0.6.18 Toleranz von einer Stelle (siehe unten) |
| O4 | offen, bisher nur Status 9 (Original) |
| O5 | EBL setzt `isIntelligibleCheckRequired` in jeder Datei. Format und Weg der Quittung bei EBL/Swisseldex klären; Shareomat sendet weiterhin keine |
| O6 | Tageslieferungen tragen **kein** Qualitätskennzeichen (weder 21 noch 56), obwohl sie nach VSE nicht plausibilisiert sind. Shareomat kann sie darum nicht von der Monatslieferung unterscheiden: erst nach der Monatslieferung abrechnen |
| O7 | ✓ Add-on wird in Home Assistant gebaut (0.6.17 installiert) |

Weitere Beobachtungen:

* Kopf: E66, Status 9, Geschäftsgrund C40, Absender-Rolle MDR, Empfänger-Rolle CEM,
  CommunityType CT01. Jede Tageslieferung enthält die **letzten 5 Tage**; zwischen den
  Lieferungen hat sich kein Wert geändert.
* Jeder Messpunkt hat beide Richtungen (Bezug und Einspeisung, je Total/LEG/Rest), auch ein
  reiner Bezüger (Einspeisung dann 0).
* Die ersten Lieferungen reichen bis 07.11.2026 zurück; 01. bis 06.11. fehlen. Die
  Monatslieferung für November muss den ganzen Monat bringen.

**Toleranz ab 0.6.18** (`shareomat/database/billing.py`, `_SDAT_RESOLUTION_KWH`):

* Total − (LEG + Rest) von höchstens 0.001 kWh je Viertelstunde gilt als Rundung. Sie wird als
  Hinweis gezählt und sperrt nicht. Abgerechnet wird ohnehin die LEG-Energie des
  Netzbetreibers. Mehr sperrt weiterhin.
* Energie-Clearing im SDAT-Modus je Viertelstunde: bezogene minus gelieferte LEG-Energie darf
  höchstens 0.0005 kWh je gerundetem Wert abweichen. Das ist die Rundung, nur ein Hinweis. Mehr
  sperrt.

Die offiziellen Werte stehen unter **Energie & Analyse → Analyse** (seit 0.6.20; vorher als
Monatstabelle auf «Messdaten»):

* **Summen & Auswertung:** je Messpunkt und Zeitraum (Tag, Woche, Monat, Jahr, frei) Bezug und
  Einspeisung, je Total / LEG / Netz, mit Vollständigkeit (Viertelstunden bis zur neuesten
  Lieferung) und der Prüfung Total = LEG + Netz (Rundung bis 0.001 kWh ist nur ein Hinweis).
  Darunter dieselben Summen je Tag oder Monat. Jede Zahl öffnet die Messwerte.
* **Messwerte:** ein Messpunkt je Viertelstunde, Stunde oder Tag. Jede Viertelstunde hat eine
  Zeile, auch ohne Lieferung («kein Wert geliefert» / «noch nicht geliefert»). ⓘ zeigt alle
  Lieferungen dieser Viertelstunde und welcher Wert gilt.

Beide verwenden dieselben Werte wie die Abrechnung (`load_official_values()`: neueste Lieferung
je Viertelstunde, Stornos, kein Ersatz eines plausibilisierten Werts durch einen vorläufigen).

---

# 33. Quellen / technische Referenzen

Massgeblich für die Implementierung:

- **VSE SDAT-CH 2025** — standardisierter Datenaustausch Schweiz
- Prozesse **E66 an LEG-Vertreter**
- Prozesse **E31 an LEG-Vertreter**
- SDAT-CH XML Schemas / XSD
- SDAT-CH Core Components
- SDAT-CH Beispieldaten
- VSE-Unterlagen zu Lokalen Elektrizitätsgemeinschaften
- EBL LEG-Anmeldeformular und konkrete Vorgaben zum Datenversand
- Swissgrid EIC-Unterlagen
- ENTSO-E EIC Reference Manual

Offizielle Portale:

- VSE / SDAT: `https://www.strom.ch/`
- Swissgrid / EIC: `https://www.swissgrid.ch/`
- ENTSO-E / EIC:
  `https://www.entsoe.eu/data/energy-identification-codes-eic/`

---

# 34. Zielzustand

Der fertige Shareomat-SDAT-Importer soll folgende Eigenschaften
besitzen:

```text
✓ SDAT-CH-konform
✓ E31-Unterstützung
✓ E66-Unterstützung
✓ LEG-fähig
✓ mehrere Netzbetreiber
✓ 15-Minuten-Werte
✓ Verbrauch + Produktion
✓ Total + LEG + Rest
✓ EIC-Prüfung
✓ CommunityID
✓ Messpunkt-Matching
✓ Messwertqualität
✓ Ersatzlieferungen
✓ Stornierungen
✓ Raw-Archiv
✓ Versionierung
✓ Duplikaterkennung
✓ E31/E66-Validierung
✓ CSV und SDAT mit gemeinsamem Datenmodell
✓ abrechnungssicher nachvollziehbar
```

SDAT wird damit eine standardisierte **Eingangsschnittstelle** von
Shareomat.

Die eigentliche Shareomat-Abrechnung bleibt unabhängig davon, ob die
Messdaten ursprünglich aus:

```text
EBL CSV
SDAT
oder einem anderen VNB-Format
```

stammen.
