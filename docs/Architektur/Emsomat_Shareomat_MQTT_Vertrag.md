# EMSOMAT ↔ SHAREOMAT

## Finale lokale Kommunikation

**Status:** Verbindliche Architekturentscheidung — fachliche Spezifikation
abgeschlossen (Abschnitt 1-29). **Implementiert und per echtem lokalen
Broker-Test verifiziert (2026-08-22):** Emsomat-Seite (`Emsomat/sparkplug/`,
Emsomat-Repo) und Shareomat-Seite (`shareomat/sparkplug/`, dieses Repo,
Primary Host Application + eigene Edge-Node-Identität für Marktteilnehmer).
**Cross-House** (Shareomat↔Internet/Relay↔fremder Shareomat) hat eine eigene,
inzwischen verbindliche Spezifikation:
`docs/Architektur/Shareomat_CrossHouse_Sparkplug_Vertrag.md` — implementiert
und per echtem Broker-Test verifiziert (2026-08-22).

**Hinweis (2026-08-22):** Zwischen der ursprünglichen Implementierung und
diesem Commit wurde Shareomat auf einem anderen Rechner auf eine
SQLite-gestützte Verwaltungsanwendung umgebaut (`shareomat/core/leg_config.py`
entfernt, ersetzt durch `shareomat/config.py::RuntimeConfig` (technische
YAML-Settings) + `shareomat/database/config_builder.py::build_leg_config()`
(kombiniert RuntimeConfig mit SQLite-Stammdaten zu `LegConfig`)). Die
Sparkplug-Anbindung wurde unverändert fachlich, aber mit angepassten Imports
gegen diese neue Struktur neu eingebaut — `SparkplugConfig`/`WanConfig` leben
jetzt in `shareomat/config.py`, `main.py` startet Sparkplug-Host/-Relay/WAN
einmalig beim Prozessstart (analog zum bestehenden `mqtt_client`), nicht pro
Abrechnungslauf.
**Version:** 1.3 (Abschnitt 27: finales Market-Participant-Domainmodell ergänzt,
`Emsomat/docs/market_shareomat_migration.md` dadurch obsolet und entfernt. Abschnitt
28: vollständiger Sparkplug-Lifecycle gegen den Normativtext geprüft, inkl. Korrektur
NDEATH-Will QoS 1→0. Abschnitt 29: Implementierungs-Leitplanken, inkl. konkreter
Refactoring-Konsequenz für `MarketAdapter`)

**Ersetzt:** die frühere Entwurfsversion dieses Dokuments (JSON-über-MQTT mit Topics
`emsomat/market/<node_id>/state`, `emsomat/leg/state`). Diese Version ist verbindlich,
der alte Entwurf ist obsolet — siehe Abschnitt 5 unten.

---

# 1. Entscheidung

Die lokale Kommunikation zwischen EMSOMAT und SHAREOMAT wird ausschließlich über

**MQTT 5.0 + Eclipse Sparkplug Specification 3.0**

realisiert.

Es gibt danach **keine zweite MQTT-Kommunikation parallel dazu**.

Alte proprietäre Kommunikations-Topics zwischen EMSOMAT und SHAREOMAT werden vollständig entfernt.

---

# 2. Was bedeutet MQTT + Sparkplug?

MQTT ist nur der Nachrichtentransport.

```text
MQTT
=
Nachrichten zuverlässig zwischen
EMSOMAT und SHAREOMAT transportieren
```

Sparkplug definiert darüber:

```text
Topic-Struktur
Payload-Format
Datentypen
Startzustand
Online/Offline-Erkennung
Reconnect
Zustandssynchronisation
Schreibzugriffe
```

Damit entwickeln wir **kein eigenes MQTT-Protokoll**.

---

# 3. Lokale Struktur

EMSOMAT und SHAREOMAT benutzen denselben vorhandenen lokalen MQTT-Broker.

```text
                 MQTT Broker
              MQTT 5 + Sparkplug
                      │
          ┌───────────┴───────────┐
          │                       │
          ▼                       ▼
      EMSOMAT                 SHAREOMAT
```

Es wird lokal benötigt:

```text
1 MQTT Broker
1 EMSOMAT MQTT Client
1 SHAREOMAT MQTT Client
```

Kein zweiter Broker.

Keine Cloud.

Keine zusätzliche Kommunikationsschicht.

---

# 4. Rollen

## EMSOMAT

EMSOMAT ist der **Sparkplug Edge Node**.

EMSOMAT besitzt den lokalen Energie-/Marktzustand.

Er veröffentlicht seine für SHAREOMAT relevanten Daten.

---

## SHAREOMAT

SHAREOMAT ist die **Sparkplug Host Application**.

SHAREOMAT:

* empfängt EMSOMAT-Daten,
* erkennt automatisch den gültigen EMSOMAT-Zustand,
* liefert die für EMSOMAT bestimmten LEG-Informationen zurück.

---

# 5. Alte Topics werden entfernt

Die bisherige Struktur wie:

```text
emsomat/market/<node_id>/state
shareomat/...
emsomat/leg/state
```

wird **nicht weitergeführt**.

Es gibt:

* keine Legacy-Topics,
* keine Spiegelung alter Topics,
* keine doppelte Veröffentlichung,
* keinen Compatibility Layer,
* keine alte und neue Kommunikation gleichzeitig.

Das neue System verwendet ausschließlich den Sparkplug-Namespace.

---

# 6. Sparkplug Topics

Die Topic-Struktur wird nicht von uns erfunden.

Sparkplug verwendet:

```text
spBv1.0/<group_id>/<message_type>/<edge_node_id>
```

Relevante Message Types sind insbesondere:

```text
NBIRTH
NDATA
NDEATH
NCMD
```

Die genaue Bedeutung dieser Nachrichten folgt der Sparkplug-Spezifikation.

---

# 7. Start eines EMSOMAT

Wenn EMSOMAT startet und sich mit dem Broker verbindet:

```text
EMSOMAT
   │
   ▼
MQTT CONNECT
   │
   ▼
NBIRTH
```

`NBIRTH` enthält den vollständigen Zustand und die von EMSOMAT angebotenen Metrics.

SHAREOMAT muss deshalb nicht selbst versuchen herauszufinden:

```text
Welche Werte existieren?
Ist EMSOMAT neu gestartet?
Sind alte Werte noch gültig?
```

Sparkplug übernimmt diesen Lifecycle.

---

# 8. EMSOMAT → SHAREOMAT

Ändert sich ein relevanter EMSOMAT-Wert:

```text
EMSOMAT
   │
   ▼
NDATA
   │
   ▼
SHAREOMAT
```

Die Daten werden als typisierte Sparkplug-Metrics übertragen.

Beispielsweise bestehende fachliche Werte wie:

```text
Market/ExportPrice
Market/ExportPriceLevel
```

Weitere Metrics werden nur eingeführt, wenn SHAREOMAT sie tatsächlich benötigt.

Es werden **keine neuen Werte nur wegen MQTT erfunden**.

---

# 9. SHAREOMAT → EMSOMAT

SHAREOMAT besitzt LEG-Informationen, die EMSOMAT benötigt.

Diese werden als klar definierte EMSOMAT-Input-Metrics übertragen.

Beispielsweise:

```text
LEG/Price
LEG/SurplusPower
LEG/State
LEG/SourceTimestamp
LEG/ValidUntil
```

Die Übertragung erfolgt über den Sparkplug-Mechanismus:

```text
SHAREOMAT
    │
    ▼
NCMD
    │
    ▼
EMSOMAT
```

Sparkplug definiert `NCMD` als standardisierten Weg, um Metrics eines Edge Nodes zu schreiben. Nach Übernahme meldet der Edge Node den tatsächlichen Wert wieder über DATA zurück.

---

# 10. Rückmeldung

Es gilt niemals:

```text
SHAREOMAT hat gesendet
=
EMSOMAT hat übernommen
```

Sondern:

```text
SHAREOMAT
    │
   NCMD
    ▼
EMSOMAT
    │
prüfen
übernehmen
    │
   NDATA
    ▼
SHAREOMAT
```

Erst der von EMSOMAT gemeldete Zustand gilt als tatsächlich übernommen.

---

# 11. Datentypen

Sparkplug verwendet typisierte Metrics.

Damit werden keine eigenen JSON-Blobs mehr für diese Kommunikation verwendet.

Beispiele:

```text
Power       → numerisch
Price       → numerisch
Boolean     → Boolean
Timestamp   → Timestamp
State       → definierter Zustand
```

Es darf beispielsweise nicht gleichzeitig geben:

```text
"2500"
```

und:

```text
2500
```

für denselben fachlichen Wert.

---

# 12. Einheiten

Für jede Metric wird genau eine Einheit festgelegt.

Beispielsweise:

```text
Power  = W
Energy = Wh
```

Für Preise wird ebenfalls genau eine interne Einheit definiert.

Beispielsweise muss vor Implementierung entschieden werden:

```text
Rp/kWh
oder
CHF/kWh
```

Danach wird diese Einheit überall gleich verwendet.

---

# 13. price_level

Vor Implementierung werden die heute vorhandenen Begriffe:

```text
price_level
export_price_level
```

geprüft.

Danach gibt es entweder:

```text
einen gemeinsamen Begriff
```

wenn sie dieselbe Bedeutung haben,

oder zwei eindeutig unterschiedliche Begriffe, wenn ihre Semantik tatsächlich verschieden ist.

Eine dritte Enumeration wird nicht eingeführt.

---

# 14. Online / Offline

Wir bauen keine eigene:

```text
online
heartbeat
availability
last_seen
```

MQTT-Logik.

Sparkplug verwendet dafür sein Birth-/Death- und Session-State-Modell. Genau dafür wurde Sparkplug entwickelt.

Damit kann SHAREOMAT zuverlässig unterscheiden:

```text
EMSOMAT gültig erreichbar
```

oder:

```text
EMSOMAT nicht mehr gültig erreichbar
```

---

# 15. LEG-Daten besitzen zusätzlich fachliche Gültigkeit

Die Netzwerkverbindung allein sagt nicht, ob ein LEG-Wert noch aktuell ist.

Deshalb besitzt ein LEG-State:

```text
LEG/SourceTimestamp
LEG/ValidUntil
```

Beispiel:

```text
LEG/ValidUntil = 12:03:00
Jetzt           = 12:03:10
```

Dann gilt:

```text
LEG DATA = STALE
```

EMSOMAT verwendet anschließend seinen definierten lokalen Fallback.

---

# 16. Neustart und Reconnect

Folgende Fälle müssen automatisch funktionieren:

```text
EMSOMAT Neustart
SHAREOMAT Neustart
MQTT Broker Neustart
Netzwerkunterbruch
MQTT Reconnect
```

Danach muss sich das System automatisch wieder synchronisieren.

Kein manueller Neustart.

Keine manuelle Topic-Bereinigung.

Keine manuelle Wiederherstellung.

---

# 17. Abrechnung bleibt vollständig getrennt

MQTT/Sparkplug dient für:

```text
Live-Daten
Zustände
Market
LEG-Zustand
Optimierungsinformationen
```

Die Abrechnung bleibt weiterhin:

```text
Meter-Dateien
SDAT
offizielle 15-Minuten-Daten
        │
        ▼
SHAREOMAT Billing
```

MQTT-Livewerte werden nicht zur abrechnungsführenden Quelle.

---

# 18. Keine interne MQTT-Zerlegung

Sparkplug wird ausschließlich zwischen EMSOMAT und SHAREOMAT verwendet.

Nicht innerhalb von EMSOMAT:

```text
Optimizer → MQTT → Battery
```

sondern:

```text
Optimizer → Battery
```

direkt über interne Klassen.

Dasselbe gilt für SHAREOMAT.

Damit bleiben beide Systeme modulare Monolithen.

---

# 19. Keine generische Fernsteuerung

Über Sparkplug werden ausschließlich vorher definierte fachliche Metrics übertragen.

Nicht erlaubt:

```text
Shell
ExecuteCommand
RawModbusWrite
WriteAnything
SetArbitraryValue
```

Nur klar spezifizierte EMSOMAT-/SHAREOMAT-Daten dürfen die Schnittstelle passieren.

---

# 20. Harte Umstellung

Es gibt **keinen produktiven Parallelbetrieb**.

Vorgehen:

```text
1. Neue Sparkplug-Schnittstelle vollständig implementieren.
2. Im Testsystem verifizieren.
3. Alle alten EMSOMAT↔SHAREOMAT MQTT-Pfade entfernen.
4. EMSOMAT und SHAREOMAT gemeinsam aktualisieren.
5. Nur Sparkplug aktivieren.
6. Alte Topics dürfen danach nicht mehr existieren.
```

Das ist ein **Hard Cut**.

---

# 21. Nach der Umstellung muss gelten

Auf dem MQTT-Broker darf für EMSOMAT↔SHAREOMAT nur noch die standardisierte Kommunikation sichtbar sein.

Nicht mehr:

```text
emsomat/market/...
emsomat/leg/...
shareomat/...
```

für diese Kommunikationsstrecke.

Sondern ausschließlich:

```text
spBv1.0/...
```

---

# 22. Was wir selbst noch definieren müssen

Sparkplug standardisiert die Kommunikation.

Sparkplug weiß aber nicht, was eine Schweizer LEG ist.

Deshalb müssen wir nur noch die **fachlichen Metrics** definieren.

Beispielsweise:

```text
Market/ExportPrice
Market/ExportPriceLevel

LEG/Price
LEG/SurplusPower
LEG/State
LEG/SourceTimestamp
LEG/ValidUntil
```

Das ist unser fachliches Datenmodell.

Nicht unser eigenes Kommunikationsprotokoll.

---

# 23. Was wir ausdrücklich nicht selbst entwickeln

Wir entwickeln nicht neu:

```text
MQTT Topic Convention
Payload Encoding
Birth Message
Death Message
Online Detection
Reconnect Protocol
Discovery Protocol
Metric Typing
Command Transport
```

Dafür wird Sparkplug verwendet.

Sparkplug 3.0 ist eine offene Eclipse-Spezifikation und besitzt mit Eclipse Tahu eine Open-Source-Referenzimplementierung einschließlich Python-Code und einen TCK zur Kompatibilitätsprüfung.

---

# 24. Finale Architektur

```text
                LOKALER MQTT BROKER
                  MQTT 5.0
               Sparkplug 3.0
                       │
         ┌─────────────┴─────────────┐
         │                           │
         ▼                           ▼

      EMSOMAT                    SHAREOMAT
   Sparkplug Edge              Sparkplug Host
       Node                    Application

         │                           │
         │ NBIRTH                    │
         ├──────────────────────────►│
         │                           │
         │ NDATA                     │
         ├──────────────────────────►│
         │                           │
         │             NCMD          │
         │◄──────────────────────────┤
         │                           │
         │ NDATA / State            │
         ├──────────────────────────►│
```

---

# 25. Verbindliche Entscheidung

**Eine Kommunikation.**

```text
EMSOMAT ↔ SHAREOMAT
=
MQTT 5.0
+
Eclipse Sparkplug Specification 3.0
```

Keine Legacy-Schnittstelle.

Keine parallelen Topics.

Keine eigenen JSON-MQTT-Protokolle.

Keine zweite Kommunikation zwischen denselben Systemen.

**EEBUS bleibt unabhängig davon eine mögliche Geräteschnittstelle von EMSOMAT zu externen Energy Devices und gehört nicht in die EMSOMAT↔SHAREOMAT-Kommunikation.**

---

# 26. Prüfergebnis / offene Punkte (Stand 2026-08-22)

Gegen die tatsächliche Sparkplug-3.0-Spezifikation und den aktuellen Python-Bibliotheks-
Stand geprüft, bevor dieses Dokument als verbindlich übernommen wurde:

**Bestätigt:**
- `pysparkplug` (PyPI, aktiv dokumentiert unter pysparkplug.mattefay.com) ist eine
  echte, typisierte Python-Implementierung von Sparkplug B.
  **Korrektur (2026-08-22, vor Implementierungsbeginn geprüft):** `pysparkplug`
  ist NICHT direkt neben Emsomats bestehendem `paho-mqtt==2.1.0` installierbar —
  es verlangt `paho-mqtt<2` und `_client.py` crasht mit paho-mqtt 2.x hart
  (`Client()`-Konstruktor ohne `callback_api_version`, `on_connect`-Callback im
  alten 4-Parameter-Format). Umsetzung: nur die MQTT-freien Teile von
  `pysparkplug` (`_protobuf/`, `_payload.py`, `_metric.py`, `_datatype.py`,
  `_topic.py` — reines Protobuf/Python, keine paho-mqtt-Kopplung, Apache-2.0
  lizenziert) werden vendored/adaptiert; der eigentliche Transport läuft über
  Emsomats bestehenden `MqttAdapter` (paho-mqtt 2.x), nicht über `pysparkplug`s
  eigenen Client.
- NCMD als Schreibmechanismus für Edge-Node-Metrics (Abschnitt 9/10) ist
  spec-konform: Payload trägt Metric-Name + neuen Wert im selben Protobuf-Format wie
  Datennachrichten, Edge Node bestätigt den tatsächlich übernommenen Wert per NDATA.

**Zwei Lücken — beide jetzt in Abschnitt 28 abschliessend geklärt (2026-08-22):**
1. Retain-Flag pro Message-Typ — siehe Abschnitt 28.1 (mit einer Korrektur:
   NDEATH-Will ist QoS 0, nicht QoS 1, siehe dort).
2. Primary-Host-STATE-Message (Shareomat) — siehe Abschnitt 28.2.

**Offen, nicht Teil dieser Entscheidung:** `group_id`/`edge_node_id`-Benennung für
die Sparkplug-Topics (`spBv1.0/<group_id>/.../<edge_node_id>`) — relevant, sobald
diese Struktur später auch für die Haus-zu-Haus-Strecke (Coordinator/Relay) infrage
kommt, aber für die rein lokale Emsomat↔Shareomat-Verbindung nicht zwingend zu
entscheiden.

**Quellen:** [Sparkplug Specification 3.0.0 (PDF)](https://sparkplug.eclipse.org/specification/version/3.0/documents/sparkplug-specification-3.0.0.pdf),
[PySparkplug Dokumentation](https://pysparkplug.mattefay.com/),
[pysparkplug auf GitHub](https://github.com/matteosox/pysparkplug)

**Korrektur zu diesem Prüfergebnis, siehe Abschnitt 27:** die dort ursprünglich
vorgeschlagene eigene `SourceTimestamp`-Metric war ein Fehler — Sparkplugs
Metric-Timestamp (pro Metric, unabhängig vom Payload-Timestamp) reicht aus und
bleibt beim Weiterreichen durch Shareomat erhalten. Retain-Flag- und
STATE-Message-Punkte oben bleiben unverändert gültig.

---

# 27. Finales Market-Participant-Domainmodell (Stand 2026-08-22, Version 1.1)

Ersetzt `Emsomat/docs/market_shareomat_migration.md` vollständig (Datei entfernt) —
dies ist jetzt die einzige gültige Dokumentation für die Emsomat↔Shareomat-Strecke,
inklusive der Market-Teilnehmer-Kommunikation.

## Ausgangslage

`Emsomat/market/` (siehe `market/README.md`) bleibt als **fachliche Domäne**
bestehen — Sichtbarkeit, welche anderen Teilnehmer gerade exportieren/importieren.
Nur ihr bisheriger Transport (direktes Haus-zu-Haus-MQTT,
`emsomat/market/<node_id>/state`) wird vollständig durch Sparkplug ersetzt, wie in
Abschnitt 1-6 oben entschieden.

## Was aus dem Code übernommen wird

Gegen den Code geprüft (`market/models.py`, `market/store.py`, `market/adapter.py`,
`market/README.md`, `manager.py`, `slotEnergyService.py`):

- **`export_now_w`/`import_now_w`** — einzige echten, produktiv genutzten
  Live-Werte (real sensorgespeist, fliessen in `slotEnergyService.py` in die
  Kostenrechnung ein). Bleiben.
- **`score`/`confidence`** — laut `market/README.md` selbst "noch bewusst
  einfach, später verfeinerbar", keine echte Berechnung dahinter. **Entfernt.**
- **`window_from`/`window_to`** — laut README ebenfalls Platzhalter (fix
  "jetzt bis jetzt+Slot", kein echtes Forecast-Fenster). **Entfernt.**
- **`state_id`** — `manager.py:3084`: `f"{node_id}_{int(utcnow().timestamp())}"`,
  ändert sich jeden Publish-Zyklus (~30s). In `store.py:40`
  (`current.state_id != state.state_id or current.to_dict() != state.to_dict()`)
  macht das den Kurzschluss-Vergleich faktisch wirkungslos — die Dedup-Funktion,
  die `state_id` haben sollte, greift heute nie. **Vollständig entfernt, kein
  Ersatz** — der neue Vergleich fällt auf die echten Wertfelder zurück, das ist
  eine Verbesserung gegenüber heute, kein Verlust.
- **Offer/Availability** (zusätzlich anbietbare/aufnehmbare Leistung) — geprüft,
  existiert nirgends im Code. `mqtt/topics.py::node_surplus()`/`node_shortage()`/
  `node_slots()` sind reine Topic-Builder mit Publish/Subscribe-Wrappern, aber
  **null Call-Sites** im ganzen Repo (`market/README.md`: "vorbereitet, aber
  nicht verdrahtet"). `CoreEnergyRegistry.get_surplus_kwh()` ist ein reiner
  Selbst-Forecast, nie broadcastet. Wird jetzt **nicht** nachgebaut — keine
  Metrics dafür anlegen, aber die Struktur unten lässt sich später verlustfrei
  um z.B. `AvailableExportPower`/`AvailableImportPower` erweitern.

## Sparkplug-Modellierung

Fremde LEG-Teilnehmer werden als **Sparkplug Devices** unter dem lokalen
Shareomat-Edge-Node geführt (kein eigener Topic-Pfad, kein erfundenes
Metric-Naming):

```
SHAREOMAT_A (Edge Node)
├── PARTICIPANT_B (Device)
│   ├── ExportPower [W]
│   └── ImportPower [W]
└── PARTICIPANT_C (Device)
    ├── ExportPower [W]
    └── ImportPower [W]
```

Übertragung via `DBIRTH`/`DDATA` (Device-Ebene), nicht `NBIRTH`/`NDATA`
(Node-Ebene) — letztere bleiben für Shareomats eigenen Node-Status reserviert.

## Timestamp — Korrektur gegenüber der Vorversion dieses Dokuments

Frühere Annahme (jetzt korrigiert): Sparkplug könne den ursprünglichen
Messzeitpunkt beim Multi-Hop-Relay nicht erhalten, deshalb brauche es eine eigene
`SourceTimestamp`-Metric. **Das war falsch.** Sparkplug trennt:

- **Payload-Timestamp** — wann die Sparkplug-Nachricht publiziert wurde,
- **Metric-Timestamp** (pro einzelner Metric) — wann der Wert erfasst wurde.

Der Metric-Timestamp bleibt beim Weiterreichen durch Shareomat unverändert
erhalten — Shareomat setzt beim Republish also `ExportPower.timestamp = 12:00:01`
(Originalmessung bei Emsomat A), auch wenn der Payload selbst erst um `12:00:05`
publiziert wird. Keine zusätzliche `SourceTimestamp`-Metric nötig. Bei
gepuffertem/verzögertem Store-and-Forward (z.B. Coordinator kurzzeitig
unerreichbar) wird der Sparkplug-Standard-Mechanismus `is_historical` am Metric
verwendet — keine eigene Historical-Protokollschicht.

## Mapping im Emsomat-Adapter

```
Sparkplug Device ID   → NodeMarketState.node_id
ExportPower            → NodeMarketState.export_now_w
ImportPower             → NodeMarketState.import_now_w
Metric.timestamp        → NodeMarketState.updated_at_utc
```

## Was unverändert bleibt (reine Domain-Logik, kennt kein MQTT)

`Store` (`NodeMarketStore`), `TrendAnalyzer`, `NeighborDayPattern`,
`RampRateLimiter`, `MarketLedger`, die `slotEnergyService`-Anbindung. Trend und
Tagesmuster werden weiterhin **lokal** aus der empfangenen Zeitreihe berechnet —
nicht übertragen, nicht Teil des Domain Contracts.

## Zielstruktur

```
ALT:  Emsomat A ↕ direktes MQTT ↕ Emsomat B
NEU:  Emsomat A ↕ lokaler Shareomat A ↕ Coordinator/Relay ↕ Shareomat B ↕ Emsomat B
```

Kein Emsomat adressiert oder abonniert je einen fremden Emsomat direkt.

## Vollständig entfernt

`state_id`, `score`, `confidence`, `window_from`, `window_to`, alte MQTT-Topic-
Builder (`market_state`/`market_state_prefix`/`market_state_wildcard` in
`mqtt/topics.py`), direkte Emsomat↔Emsomat-Subscribe/Publish-Logik in
`market/adapter.py`, jede Spiegelung alter `emsomat/market/<node_id>/state`-
Topics. Kein Compatibility Layer, keine parallele Kommunikation.

---

# 28. Sparkplug Lifecycle — verbindlich, gegen den Sparkplug-3.0-Normativtext geprüft

Quelle für alle Normativ-Aussagen unten: `eclipse-sparkplug/sparkplug/docs/normative_statements.md`
und die operative Verhaltensbeschreibung (`Sparkplug_5_Operational_Behavior.adoc`) im
offiziellen Eclipse-Sparkplug-Repo, plus [Sparkplug Specification 3.0.0 (PDF)](https://sparkplug.eclipse.org/specification/version/3.0/documents/sparkplug-specification-3.0.0.pdf).
Wo der Text unten keine proprietäre Wahl trifft, ist es der zitierte Standard, nicht
unsere Entscheidung.

## 28.1 Retain / QoS — vollständige, korrigierte Tabelle

| Nachricht | QoS | Retain |
|---|---|---|
| NBIRTH | 0 | false |
| NDATA | 0 | false |
| NDEATH (MQTT Will) | **0** | false |
| DBIRTH | 0 | false |
| DDATA | 0 | false |
| DDEATH | 0 | false |
| NCMD | 0 | false |
| DCMD | 0 | false |
| STATE (Host Birth, `ONLINE`) | 1 | true |
| STATE (Host Death/Will, `OFFLINE`) | 1 | true |

**Korrektur gegenüber der Vorgabe:** NDEATH-Will ist **QoS 0**, nicht QoS 1 — der
Normativtext ist eindeutig: *"Edge clients must register an MQTT Will with the topic
'.../NDEATH/...', MQTT retain=false, and MQTT QoS=0."* Alle Nicht-STATE-Nachrichten
sind ausnahmslos QoS 0 + retain=false; ausschliesslich STATE (Host) ist QoS 1 +
retain=true. Keine eigenen Abweichungen von dieser Tabelle.

## 28.2 Shareomat als Primary Host Application

Bestätigt und übernommen:

- Topic: `spBv1.0/STATE/<sparkplug_host_id>`.
- Host Birth: Payload `ONLINE` (UTF-8 String), QoS 1, retain=true.
- Host Death (MQTT Will): Payload `OFFLINE` (UTF-8 String), QoS 1, retain=true.
- **Edge Node wartet mit NBIRTH/DBIRTH, bis der konfigurierte Primary Host
  `ONLINE` meldet** — spec-bestätigt: *"the Edge Node waits to publish its NBIRTH
  and DBIRTH messages until the Host Application that the Edge Node has designated
  as its Primary Host Application has come online."* Begründung laut Spec: sinnlos,
  Daten zu publizieren, wenn niemand verbunden ist/mitliest.
- **Offener Punkt in der Sparkplug-Community selbst, nicht nur bei uns:** das exakte
  Verhalten, sobald ein Primary Host NACH einer Offline-Phase wieder online kommt
  (muss der Edge Node seine MQTT-Session komplett neu aufbauen, oder reicht ein
  erneutes Publish von NBIRTH/DBIRTH innerhalb derselben Session?), ist selbst im
  offiziellen Sparkplug-Repo als offene Klärung geführt (GitHub Issue #328,
  "Ensure behavior of Edge Nodes after a Primary Host comes back online is well
  defined"). **Für Emsomat verbindlich festgelegt (unsere Wahl, da Spec hier
  unterspezifiziert ist):** Emsomat behandelt "Primary Host wird wieder online" wie
  einen Neustart der Birth-Sequenz — erneutes NBIRTH + DBIRTH aller aktuell
  bekannten Devices, ohne die MQTT-Session selbst neu aufzubauen. Einfachste,
  konservativste Variante; kann revidiert werden, falls sich in der Sparkplug-Community
  eine Standardpraxis etabliert.
- **Emsomat Core bleibt unabhängig:** wartet Emsomat auf den Primary Host, betrifft
  das ausschliesslich die Kommunikationsschicht — die lokale Regelung
  (`battery_logic.py` etc.) läuft unverändert weiter, siehe Abschnitt 17/18.

## 28.3 MQTT-5-Session-Parameter

- **Clean Session/Clean Start = true** — normativ bestätigt: *"The MQTT clean
  session flag MUST always be set to true for all Sparkplug clients."*
- **Session Expiry Interval = 0** — folgt direkt aus "clean session muss immer true
  sein": ein Session Expiry Interval > 0 würde in MQTT 5 eine persistente Session
  erlauben, was der "always clean"-Vorgabe widerspricht. **Hinweis zur Quellenlage:**
  dieser exakte MQTT-5-Parametername wird im geprüften Normativtext-Auszug nicht
  wörtlich genannt (der Standard nutzt durchgehend die ältere MQTT-3.1.1-Terminologie
  "clean session") — Session Expiry Interval=0 ist die korrekte MQTT-5-Übersetzung
  dieser Vorgabe, keine eigene Erfindung, aber vor Implementierung gegen die volle
  PDF-Spezifikation gegenprüfen, falls dort ein expliziterer MQTT-5-Abschnitt existiert.
- **Will Message:** wie in 28.1/28.2 — NDEATH (Edge Node) und STATE-OFFLINE (Host)
  werden je als MQTT Will beim Connect registriert, nicht aktiv gesendet.

## 28.4 bdSeq — Session-Korrelation

Separates Konzept von `seq` (siehe 28.5), verwechslungsanfällig:

- `bdSeq` ist eine Metric (Name `bdSeq`), die **im Will-Payload des NDEATH**
  registriert wird — bevor die eigentliche NBIRTH-Nachricht gesendet wird.
- Die anschliessend gesendete NBIRTH-Nachricht **muss denselben `bdSeq`-Wert** als
  eigene Metric enthalten.
- Zweck: der Host kann dadurch eine verzögert eintreffende NDEATH aus einer
  **alten** Session von der aktuellen unterscheiden (verzögerte alte NDEATH trägt
  einen älteren `bdSeq`-Wert als die aktuelle Session) — verhindert, dass ein
  verspätetes "Death" einer neuen, bereits wieder lebenden Session fälschlich
  zugeordnet wird.
- Inkrementiert bei jedem neuen MQTT-Connect (jede neue Session), nicht bei jedem
  einzelnen Birth-Zyklus innerhalb einer Session.

## 28.5 seq — Nachrichtenreihenfolge innerhalb einer Session

- Payload-weites (nicht Metric-weites) Zählfeld, Bereich 0–255, mit Wraparound
  zurück auf 0.
- **Wird bei jeder NBIRTH auf 0 zurückgesetzt**, danach bei jeder NDATA/DBIRTH/DDATA
  dieses Edge Nodes hochgezählt.
- Host erkennt daran Nachrichtenverlust/Umsortierung. Bricht die erwartete Reihenfolge
  und lässt sich nicht innerhalb eines Reordering-Timeouts reparieren, fordert der
  Host per `Node Control/Rebirth`-NCMD (siehe 28.6) eine komplette Neu-Synchronisation.

## 28.6 Rebirth-Mechanismus

- Jeder Sparkplug Edge Node **muss** die Standard-Metric `Node Control/Rebirth`
  (boolean, schreibbar) implementieren — normativ vorgeschrieben, keine Kür.
- Host publiziert NCMD mit `Node Control/Rebirth=true` an den Edge Node.
- Edge Node antwortet mit einer kompletten neuen NBIRTH + DBIRTH aller aktuell
  bekannten Devices (Market-Teilnehmer, siehe Abschnitt 27) — vollständige
  Re-Synchronisation, kein Delta.

## 28.7 NBIRTH/DBIRTH-Reihenfolge

Innerhalb einer Session: **NBIRTH immer zuerst**, danach DBIRTH je Device (hier: je
Market-Teilnehmer). NDATA/DDATA dürfen erst nach der jeweiligen Birth-Nachricht
gesendet werden — kein Emsomat-Live-Wert vor der eigenen NBIRTH, keine
Participant-Metric vor der DBIRTH dieses Participants.

## 28.8 Test-Checkliste (bestätigt, keine Ergänzung nötig)

Die vom User vorgegebene Liste deckt die relevanten Fälle korrekt ab:

```text
[ ] SHAREOMAT startet zuerst
[ ] EMSOMAT startet zuerst
[ ] SHAREOMAT STATE retained online=true
[ ] SHAREOMAT ungeplant beendet -> STATE online=false ueber Will
[ ] EMSOMAT ungeplant beendet -> NDEATH
[ ] EMSOMAT reconnect -> korrekte NBIRTH/DBIRTH-Sequenz
[ ] Broker-Neustart
[ ] SHAREOMAT-Neustart
[ ] EMSOMAT-Neustart
[ ] keine NBIRTH/NDATA/DDATA-Nachricht ist retained
[ ] nur Host STATE verwendet Retain=true
[ ] EMSOMAT Core laeuft bei SHAREOMAT-Ausfall weiter
```

Zusätzlich sinnvoll (aus 28.4-28.7 abgeleitet, nicht im Original enthalten):

```text
[ ] bdSeq in Will-Payload und nachfolgender NBIRTH stimmen ueberein
[ ] seq wird bei NBIRTH auf 0 zurueckgesetzt und zaehlt danach korrekt hoch
[ ] Node Control/Rebirth-NCMD loest vollstaendige NBIRTH+DBIRTH-Neusendung aus
[ ] Primary Host kommt nach Offline-Phase zurueck -> Edge Node re-birth't (Abschnitt 28.2)
```

Erst wenn diese lokale Sparkplug-Strecke vollständig funktioniert, beginnt die
Cross-House-Spezifikation.

**Quellen:** [Sparkplug Specification 3.0.0 (PDF)](https://sparkplug.eclipse.org/specification/version/3.0/documents/sparkplug-specification-3.0.0.pdf),
[normative_statements.md](https://github.com/eclipse-sparkplug/sparkplug/blob/master/docs/normative_statements.md),
[Sparkplug_5_Operational_Behavior.adoc](https://github.com/eclipse-sparkplug/sparkplug/blob/master/specification/src/main/asciidoc/chapters/Sparkplug_5_Operational_Behavior.adoc),
[GitHub Issue #328](https://github.com/eclipse-sparkplug/sparkplug/issues/328)

---

# 29. Implementierungs-Leitplanken (verbindlich, Abschluss der fachlichen Spezifikation)

Die fachliche Sparkplug-Spezifikation ist mit Abschnitt 1-28 abgeschlossen. Für die
Implementierung gelten nur noch diese Architekturregeln:

1. **Domain bleibt transportneutral.** Market-/LEG-/Energy-Logik kennt weder MQTT
   noch Sparkplug. Sparkplug-Code sitzt ausschliesslich im Communication Adapter.
2. **Emsomat:** bestehende Market-Logik (`Store`, `TrendAnalyzer`,
   `NeighborDayPattern`, `RampRateLimiter`, `MarketLedger`, `NodeMarketState`)
   bleibt erhalten. Der neue Sparkplug-Adapter ersetzt nur die alte
   MQTT-Transportlogik.
3. **Shareomat:** LEG-/Coordinator-/Participant-Logik bleibt ebenfalls unabhängig
   von MQTT. Der Sparkplug-Adapter übernimmt nur Primary Host, Empfang von Emsomat,
   Mapping Domain↔Sparkplug, Veröffentlichung der Participant-Devices.
4. **Keine neuen Services.** Emsomat und Shareomat bleiben modulare Monolithen —
   kein Service pro Market/Grid/Participant usw.
5. **Ein Kommunikationsadapter pro Anwendung:**

```text
EMSOMAT Domain
      |
Sparkplug Adapter
      |
   MQTT Broker
      |
Sparkplug Adapter
      |
SHAREOMAT Domain
```

**Konkrete Konsequenz für den bestehenden Emsomat-Code (aus Regel 1+2 abgeleitet,
gegen den Code geprüft):** `market/adapter.py::MarketAdapter` vermischt heute Domain
(hält `Store`/`Ledger`/`TrendAnalyzer`/`DayPattern`) und Transport (`self._mqtt.
subscribe_market(...)`, `publish_market_state(...)`, `_on_market_message()`'s
MQTT-Topic-Parsing) in einer Klasse. Um Regel 1 einzuhalten, muss das bei der
Umsetzung getrennt werden: `MarketAdapter` bleibt reine Domain-Fassade (Store-Zugriff,
Trend/DayPattern-Abfragen), die MQTT/Sparkplug-Aufrufe wandern in einen neuen,
eigenständigen Sparkplug-Adapter, der `MarketAdapter` nur noch über die bestehenden
Domain-Methoden (`get_registry_snapshot()`, `get_neighbor_trend(s)`, ...) anspricht —
nicht umgekehrt, und nicht vermischt in derselben Klasse wie heute.
