# ADR-0007: Live-Energie-Ebene und Teilnehmerportal

## Status

Angenommen, 6. Fassung (08.11.2026). Schritt 1 ist freigegeben, im Code
umgesetzt und mit automatischen Tests geprüft. **Realtests mit echtem Broker,
Home Assistant, WAN und Add-on stehen aus** (Arbeitsliste LE-05, LE-08, LE-09).
Die 4. Fassung ergänzt die Unterscheidung von Einzelmessung und
LEG-Bilanz (3.7), Anschlüsse ohne Einspeisemessung (LE-06) und den umgesetzten
Stand zu K-1 und K-3. Die 5. Fassung korrigiert die Navigation: Live-Energie
ist ein eigener Menübereich und steht nicht mehr unter BETRIEB. Sie ergänzt die
Ansichten Übersicht, Messquellen und Diagnose sowie den gespeicherten Zustand
«Keine Live-Messung vorhanden» (Abschnitt 2 und 5.1). Die 6. Fassung bildet
den Stand nach dem Audit vom 08.11.2026 ab. Die folgenden Abschnitte beschreiben
die Entscheidungen; Abweichungen und offene Punkte stehen unter
«Umsetzungsstand» am Ende.

Der Projektverantwortliche hat die Grundarchitektur angenommen: ein
Shareomat-System mit unveränderter Verwaltung, einem zusätzlichen Live-Modul
und einem später separat betreibbaren Teilnehmerportal. In der 2. Fassung
sind seine Präzisierungen eingearbeitet: Messwertqualität (O-1), Hysterese,
Sparkplug-Konsistenz, Quellenidentität und aktive Messpunkte (O-5). Dazu
kommen die Entscheide zu O-2 bis O-4. Die 3. Fassung ergänzt die drei
Arten von Live-Messquellen (Emsomat, Home Assistant, externe Quelle), die
Begriffe Teilnehmer / offizieller Messpunkt / Live-Messquelle, die
Durchsetzung «höchstens eine aktive Live-Messquelle pro Messpunkt» und die
Bedienung «Live-Messquelle zuordnen». H-1, H-2 und der Umfang von
Schritt 1 (Zuordnen in der Oberfläche) sind am 07.11.2026 entschieden
(Abschnitt 10). Die Aufgaben stehen in der zentralen Arbeitsliste
[`docs/Live-Energie-Arbeitsliste.md`](../Live-Energie-Arbeitsliste.md).

Bezug: [`docs/Zukunft.md`](../Zukunft.md) (Zielbild, §12 Entscheide vom
07.11.2026), Architekturzeichnung
`docs/Architektur/Diagramm/Shareomat_Architecture_Whitepaper_light_v4_LEG_Live_App.drawio`
(Entwurf, nur lokal, nicht im Repository), [ADR-0004](ADR-0004-sparkplug-b-lokale-emsomat-shareomat-kommunikation.md),
[ADR-0005](ADR-0005-cross-house-sparkplug-b-zentraler-relay.md),
[ADR-0006](ADR-0006-mqtt-3-1-1-fuer-sparkplug.md).

## Kontext

Shareomat ist heute eine Verwaltungs- und Abrechnungsanwendung für **einen**
Verwalter. Sie läuft als Home-Assistant-Add-on hinter HA Ingress oder
standalone auf `127.0.0.1:8099`. Ihr eigener Webserver spricht nur HTTP. Er
ist als Admin-Oberfläche gebaut, nicht als öffentliche Webseite.

Neu sollen die Teilnehmer der LEG live sehen, ob lokal Strom übrig ist. Dazu
gehören auch Teilnehmer ohne eigenes Home Assistant und ohne Emsomat. Emsomat
und andere EMS sollen den Status optional nutzen können. Die Abrechnung
bleibt die finanzielle Wahrheit und unverändert.

Bestand, auf dem diese Entscheidung aufbaut (geprüft am 07.11.2026):

| Baustein | Stand |
|---|---|
| Rollen und Rechte | `security/permissions.py`: nur Rolle `admin`. Jede GET-Anfrage braucht nur `view`. |
| Login | `web/login.py`, `database/auth.py` (`auth_users` mit Feld `role`), serverseitige Sessions, CSRF |
| Stammdaten | `participants` (Geschäfts-ID `participant_id`), `meters` (`meter_id`, `role`, Gültigkeit, FK auf `participants`) in `shareomat.db` |
| Eigener Live-Wert | Emsomat publiziert `Market/ExportPowerNow` und `Market/ImportPowerNow` [W] per Sparkplug-NDATA an `SparkplugHost` (ADR-0004) |
| Fremde Live-Werte | `WanDownlink` → `ParticipantRelay` (ADR-0005). Sparkplug-Device-ID = `participant_id` des fremden Standorts |
| HA-Sensoren | `ha/mqtt_runtime.py`, `ha/mqtt_discovery.py` (Discovery, Availability, retained) |
| Rücksignal an Emsomat | `SparkplugHost.publish_coordinator_ncmd()` mit `ValidUntil` (`sparkplug/coordinator_mapping.py`) |
| Zugriff auf Home Assistant | keiner: `ha_addon/config.yaml` hat `homeassistant_api: false` und `hassio_api: false`. Shareomat liest keine HA-Zustände. |
| HA Entity Bridge | überträgt Zustände retained und ohne Zeitstempel, nur bei Änderung (`ha-entity-bridge/docs/MQTT_PROTOCOL.md`) |
| Audit-Protokoll | `database/audit.py` |

Bei der Prüfung gefundene Schwächen, die eine Live-Bilanz verfälschen würden:

* **S-L1** `WanDownlink._extract_power()` macht aus einem fehlenden Wert
  `0.0` (`float(value or 0.0)`). Werte werden nicht auf Endlichkeit oder
  Vorzeichen geprüft. Dasselbe macht `sparkplug/bridge.py`
  (`mirror_host_to_uplink`), das den lokalen Emsomat-Wert ins WAN
  weiterreicht (gefunden am 07.11.2026 bei der Prüfung von EA-1).
* **S-L2** Fällt in Emsomat der Sensor aus, meldet
  `get_market_export_now_w()`/`get_market_import_now_w()` `0.0`. Shareomat
  kann «0 W» nicht von «unbekannt» unterscheiden.
* **S-L3** Emsomat setzt als Metric-Zeitstempel den Zeitpunkt der
  Publikation, nicht den Zeitpunkt der Sensormessung. Ein eingefrorener
  HA-Sensor sieht deshalb immer frisch aus.

  S-L2 und S-L3 sind in Emsomat mit dem Auftrag EA-1 behoben (07.11.2026,
  Commit `e6be1c7`, siehe Abschnitt 10). Emsomat bleibt danach Stufe C.
* **S-L4** Der Downlink nimmt für jede `device_id` unter jedem fremden
  `edge_node_id` der Gruppe Werte an. Welcher Standort für welchen
  Teilnehmer sprechen darf, ist nirgends hinterlegt. Die Broker-ACL ist
  geplant, aber nicht nachgewiesen. Der `ParticipantRelay` führt fremde
  Werte nur unter der `device_id`. Zwei Standorte mit derselben
  `device_id` überschreiben sich gegenseitig.
* **S-L5** Weder `SparkplugHost` noch `WanDownlink` reagieren auf den
  Verlust ihrer eigenen Broker-Verbindung. Werte bleiben danach stehen,
  bis zufällig eine neue Nachricht kommt. `SparkplugHost` legt Metrics
  einzeln nach Namen ab und löscht sie bei einem neuen NBIRTH nicht. Die
  `seq`-Nummern prüft keiner der beiden. Verspätete oder doppelte
  Nachrichten werden darum nicht erkannt.

## Problem

1. Ohne klare Trennung entsteht eine von zwei Fehlentwicklungen. Entweder
   bekommen Teilnehmer eine Rolle in der Verwaltung, dann sehen sie mit
   `view` alles: Rechnungen anderer, Bankkonten, Verträge, Messdaten. Oder
   der Admin-Server wird später für das Internet geöffnet.
2. Ohne eine eindeutige Bilanzdefinition zeigt die App bei fehlenden,
   veralteten oder falsch vorzeichenbehafteten Werten einen Überschuss, den
   es nicht gibt (S-L1 bis S-L3, S-L5). Teilnehmer schalten dann
   Verbraucher ein und beziehen Netzstrom.
3. Ohne eine definierte Verbindung zwischen Live-Messquellen und Stammdaten
   entsteht eine zweite Teilnehmerverwaltung, oder ein Standort kann Werte
   für fremde Teilnehmer einspeisen (S-L4).
4. Ohne Vorgabe vermischen sich Live-Werte und offizielle Messwerte.

## Entscheidung

### 1. Ein System, zwei Oberflächen, getrennte Sicherheitszonen

```text
┌───────────────────────────── Shareomat Core (ein Prozess) ─────────────────────────────┐
│                                                                                        │
│  Abrechnung (unverändert)            Live-Modul (neu, shareomat/live/)                 │
│  core/, database/  → shareomat.db    Ingest-Adapter → Prüfung → Bilanz → live.db       │
│        ▲                                   │  liest Stammdaten nur über                │
│        │ Stammdaten (führend)  ◄───────────┘  live/masterdata.py (nur lesen)           │
│                                                                                        │
│  Admin-Webserver (heute)          HA-Sensoren / Emsomat-Signal        Portal-API (neu)  │
│  :8099, Ingress / 127.0.0.1       ha/mqtt_runtime, SparkplugHost      eigener Listener, │
│  Admin-Login, Menü + Live-Energie                                     Schritt 4         │
└──────────────┬───────────────────────────────┬──────────────────────────────┬──────────┘
               │                               │                              │ schmal,
          Verwalter                    HA, Emsomat (lokal)                    │ versioniert,
                                                                              │ authentisiert
                                                             ┌────────────────┴─────────┐
                                                             │ Teilnehmerportal         │
                                                             │ eigener Prozess, später  │
                                                             │ eigener Container        │
                                                             │ keine DB, kein MQTT      │
                                                             └────────────────┬─────────┘
                                                                              │ HTTPS (Tunnel/Proxy)
                                                                         Teilnehmer
```

**Sicherheitszonen:**

| Zone | Vertrauen | Darf | Darf nie |
|---|---|---|---|
| Z1 Verwaltung (Admin-Webserver) | hoch, nur Verwalter | alles wie heute, plus Menübereich LIVE-ENERGIE | aus dem Internet direkt erreichbar sein |
| Z2 Live-Ingest (im Core) | Eingaben **nicht vertrauenswürdig** | Live-Werte prüfen und in `live.db` schreiben, Stammdaten lesen | in `shareomat.db` schreiben, Abrechnungscode aufrufen |
| Z3 Portal-API (im Core, eigener Listener) | prüft jede Anfrage | fest definierte, schreibgeschützte Antworten pro Teilnehmer | allgemeine Abfragen, Admin-Funktionen, fremde Teilnehmer |
| Z4 Teilnehmerportal (eigener Prozess/Container) | niedrig, internetseitig | Seiten ausliefern, Anfragen an die Portal-API weiterreichen | Datenbankdateien, MQTT-Zugang, Admin-Geheimnisse, Netzwerkzugriff ausser zur Portal-API |
| Z5 Extern | nicht vertrauenswürdig | Gateways, Broker, fremde Standorte, Browser | Befehle an Geräte senden (siehe Abschnitt 7) |

Regeln:

* **Teilnehmer bekommen nie eine Rolle im Admin-Rechtesystem**, auch keine
  eingeschränkte. Teilnehmerkonten liegen in einer eigenen Tabelle
  `portal_accounts`. Sie haben **kein Rollenfeld** und sind fest mit genau
  einem `participants.id` verbunden. Ein Portal-Konto kann sich am
  Admin-Webserver nicht anmelden. Ein Test stellt das sicher.
* **Das Teilnehmerportal ist ein eigenes Programm.** Es nutzt gemeinsamen
  Quellcode (Templates, DTOs), läuft aber als eigener Prozess mit eigener
  Dienstidentität. Es bekommt keine Datenbankdatei und keinen Zugang zu
  MQTT oder E-Mail. Es kennt nur die Adresse und den Dienstschlüssel der
  Portal-API.
* **Öffentlicher Internetbetrieb nur als eigener Container** mit eigenem
  Dateisystem, ohne Volume auf `/data`, und nur mit Netzwerkzugriff auf die
  Portal-API. Ein zusätzlicher Port oder Prozess im Add-on-Container ist
  höchstens für Tests im LAN zulässig.
* **Die Portal-API ist die einzige Verbindung** zwischen Portal und Core
  (Abschnitt 5). Der Admin-Webserver bleibt davon getrennt. Er bekommt keine
  Portal-Routen, und die Portal-API bekommt keine Admin-Routen.
* Vor Schritt 4 werden **keine** Portal-API und **kein** Portal gebaut.
  Schritt 1 legt nur die Struktur so an, dass sie später ohne Umbau möglich
  ist.

### 2. Datenhaltung: Stammdaten führen, Live-Daten getrennt

* **Führend bleiben `participants` und `meters` in `shareomat.db`.** Es gibt
  keine zweite Teilnehmer- oder Messpunktliste.
* **Begriffe**, verbindlich in Code, Oberfläche und Dokumentation:
  * **LEG-Teilnehmer:** Person oder Organisation, die Mitglied der LEG ist
    (`participants`).
  * **Offizieller Messpunkt:** der beim Verteilnetzbetreiber registrierte
    Messpunkt (`meters`). Seine offiziellen Messdaten sind die Grundlage der
    Abrechnung. Ein Teilnehmer kann mehrere offizielle Messpunkte haben.
  * **Live-Messquelle:** eine zusätzlich zugeordnete technische
    Datenquelle, die die aktuelle Bezugs- und Einspeiseleistung **eines**
    offiziellen Messpunkts liefert (`live_sources`). Bezug und Einspeisung
    sind zwei Messgrössen derselben Live-Messquelle, nicht zwei Quellen und
    nicht zwei Messpunkte.

  In der Oberfläche heisst es **«Live-Messquelle zuordnen»**, nie
  «Live-Messpunkt hinzufügen». Der offizielle Messpunkt ist bereits
  vorhanden, ergänzt wird nur seine Live-Datenanbindung.
* **Regel für die erste Version:** Pro offiziellem Messpunkt ist höchstens
  **eine** Live-Messquelle aktiv. Zugeordnet wird immer dem Messpunkt, nie
  pauschal dem Teilnehmer. Eine Live-Messquelle ist optional. Ohne sie
  bleibt der Messpunkt für die Abrechnung vollständig nutzbar, verhindert
  aber den Status `VOLLSTÄNDIG` (O-2).
* **Messpunkt ohne Live-Messung.** Der Verwalter kann festhalten: «Keine
  Live-Messung vorhanden». Das steht in `live_meter_states` in `shareomat.db`
  und referenziert nur `meters.id`, die Tabelle `meters` bleibt unverändert.
  Ohne Eintrag und ohne Quelle gilt «Noch nicht festgelegt». Der Zustand ist
  jederzeit änderbar: Eine zugeordnete Quelle ersetzt ihn, und wird er gesetzt,
  werden offene Quellen des Messpunkts in derselben Transaktion deaktiviert.
  Er wirkt nur auf die Anzeige: Der Messpunkt bleibt in *E(t)*, seine fehlende
  Messung ist `MISSING`, nie 0 W, und `VOLLSTÄNDIG` ist nicht möglich.
* **Konfiguration der Live-Messquellen in `shareomat.db`**, in einer neuen
  Tabelle `live_sources`, die der Verwalter pflegt. Ein Fremdschlüssel auf
  `meters.id` sichert die Zuordnung referenziell ab. Weil es
  Verwaltungsdaten sind, liegen sie dort, wo auch Messpunkte, Tarife und
  Verträge liegen.

  | Feld | Bedeutung |
  |---|---|
  | `id` | interne ID |
  | `meter_id` (FK `meters.id`) | **genau ein** offizieller Messpunkt. Daraus folgt der Teilnehmer über `meters.participant_id`. Es gibt kein eigenes Teilnehmerfeld. |
  | `source_type` | fachliche Art, wie sie der Verwalter wählt: `emsomat`, `home_assistant`, `external` (Abschnitt 4) |
  | `adapter` | technischer Weg darunter: `emsomat_local`, `ha_mqtt`, `wan_sparkplug`, `gateway_mqtt`. Kein `ha_api` (H-1). Die erlaubten Paare aus Art und Adapter sind fest im Code und per `CHECK` abgesichert. |
  | `source_key` | kanonische technische Identität, eindeutig je Adapter, z. B. `emsomat_local:<edge_node_id>`, `wan_sparkplug:<edge_node_id>/<device_id>`, `gateway_mqtt:<geräte-id>`, `ha_mqtt:<publisher-id>` |
  | `measurement_mode` | `import_export` (zwei Werte), `signed` (ein vorzeichenbehafteter Wert) oder `import_only` (nur Bezug) |
  | `no_export_note` | Pflicht bei `import_only`: Begründung, warum Einspeisung ausgeschlossen ist. Nur für Messpunkte mit der Rolle Verbraucher zulässig. |
  | `sign_convention` | Pflicht bei `signed`: `positive_is_import` oder `positive_is_export`. **Keine Vorgabe.** Bei `import_export` leer. |
  | `adapter_config` | adapterspezifische Einstellungen als JSON, z. B. bei `home_assistant` die Entity-IDs für Bezug und Einspeisung. Wird beim Speichern gegen ein festes Schema des Adapters geprüft. Enthält keine Geheimnisse. |
  | `max_power_w` | Plausibilitätsgrenze, Pflicht (aus der Anschlussleistung) |
  | `freshness_evidence` | Nachweisstufe der Messaktualität `A`, `B` oder `C` (Abschnitt 3.2). Wird vom Verwalter gesetzt, nie von der Quelle. |
  | `identity_basis`, `identity_note`, `identity_verified_at` | Womit die Identität der Quelle durchgesetzt wird (z. B. `broker_acl`) und wann das zuletzt nachgewiesen wurde (Abschnitt 4.2) |
  | `state` | `testing`, `active` oder `retired` (siehe unten). Ersetzt das Feld `enabled` der 2. Fassung. |
  | `activated_at`, `retired_at`, `created_at`, `updated_at` | |
  | später: `credential_hash`, `revoked_at` | Gerätezugang für `gateway_mqtt` und `ha_mqtt` (Schritt 2) |

  Dazu kommt die Tabelle **`live_source_channels`**: Sie hält pro
  Live-Messquelle die gelesenen Kanäle fest. `quantity` ist `import`,
  `export` oder `signed`, `channel_ref` der Metric-Name, die Entity-ID oder
  das Feld im Gateway-Vertrag. Damit lässt sich prüfen, dass derselbe Kanal,
  etwa dieselbe HA-Entity, nie in zwei aktiven Quellen steckt.
* **«Höchstens eine aktive Live-Messquelle pro Messpunkt» wird an drei
  Stellen durchgesetzt:**
  1. **Datenbank:** partielle Unique-Indizes `UNIQUE (meter_id) WHERE
     state = 'active'` und `UNIQUE (source_key) WHERE state IN ('testing',
     'active')`. So ist die Regel auch bei einem Programmfehler nicht
     verletzbar.
  2. **Schreiblogik** in `database/live_sources.py`: Zuordnen, Aktivieren,
     Wechseln und Zurückziehen laufen nur in einer Transaktion
     (`BEGIN IMMEDIATE`). Dabei wird geprüft, dass kein Kanal schon in einer
     anderen aktiven Quelle verwendet wird und dass der Messpunkt existiert
     und nicht die Rolle `grid` hat.
  3. **Bilanz:** Sie nimmt pro Messpunkt nur den Wert der aktiven Quelle.
     Findet sie trotzdem mehr als eine, wird der Messpunkt `INVALID`
     («mehrere aktive Quellen»), nie werden Werte summiert.

  Emsomat und HA-Sensoren desselben Anschlusses können damit nie addiert
  werden: Es wären zwei Quellen desselben Messpunkts, und nur eine davon
  kann aktiv sein.
* **Lebenszyklus und Quellenwechsel:**
  * `testing`: Die Quelle ist zugeordnet. Ihre Werte werden empfangen,
    geprüft und angezeigt, gehen aber **nie** in die Bilanz. Eine Quelle
    in `testing` darf neben der aktiven Quelle desselben Messpunkts
    bestehen. So lässt sich ein Wechsel vorher prüfen.
  * `active`: höchstens eine pro Messpunkt. Nur ihre Werte zählen.
  * `retired`: abgelöst oder entfernt. Eintreffende Werte werden
    verworfen. Die Zeile bleibt für die Nachvollziehbarkeit.
  * Ein Wechsel ist eine einzige Transaktion: Die alte Quelle wird
    `retired`, die neue `active`. Es gibt keinen Zeitpunkt mit zwei aktiven
    Quellen. Der letzte Wert der alten Quelle wird nicht übernommen. Bis zur
    ersten gültigen Probe der neuen Quelle ist der Messpunkt `MISSING`.
  * Bevorzugte und alternative Quellen (Ausweichen bei Ausfall) gehören
    **nicht** zur ersten Version. Falls sie später kommen, zählt immer genau
    eine Quelle, nie beide.
  * Jede Änderung kommt ins bestehende Audit-Protokoll
    (`database/audit.py`): wer, wann, alt → neu.
* **Live-Messwerte in `live.db`**, einer eigenen SQLite-Datei mit kurzer
  Aufbewahrung. In Schritt 1 nur der letzte Wert pro Quelle. Ein Verlauf mit
  Aufbewahrungsfrist kommt später.
* **Drei Datenbanken im selben Datenverzeichnis:**

  | Datei | Inhalt | Geschrieben von |
  |---|---|---|
  | `shareomat.db` | Stammdaten, offizielle Messdaten, Abrechnung, Live-Konfiguration (`live_sources`, `live_source_channels`, `live_meter_states`) | Verwaltung und Abrechnung |
  | `live.db` | letzter Live-Wert pro Quelle, Statuswechsel | Live-Ebene |
  | `coordinator.db` | Lastprognose und Day-Ahead-Preise für Emsomat | Coordinator-Aufgaben (ADR-0004) |

  `coordinator.db` ist nötig, weil die Rechnungsfreigabe bei jeder fremden
  Änderung an `shareomat.db` abbricht (`PRAGMA data_version`), auch bei einer
  fachlich unbeteiligten Tabelle. Die Inhalte lassen sich jederzeit neu
  erzeugen, darum gibt es keine Übernahme aus `shareomat.db`. Alte Zeilen dort
  bleiben unberührt und ungenutzt.
* **Die Verbindung ist einseitig und eng:**
  * Das Live-Modul liest Stammdaten nur über ein schmales Lesemodul
    `live/masterdata.py`: erwartete Messpunkte, Live-Messquellen,
    Plausibilitätsgrenzen. Es schreibt nie in `shareomat.db`, ausser der
    Verwalter pflegt `live_sources` über die Admin-Seite.
  * `live/masterdata.py` ist das **einzige** Modul unter `shareomat/live/`,
    das etwas aus `shareomat.database` importiert. Erlaubt sind nur:
    `meters`, `participants`, `community`, `live_sources` und
    `billing_eligibility` (nur die Teilnahmeregel, Abschnitt 3.1). Alles
    andere ist verboten, insbesondere `billing`, `meter_readings`,
    `invoicing`, `settlement_history` und alles unter `core/`.
  * Abrechnungscode (`core/`, `database/billing*`, `database/meter_readings`,
    `database/invoicing`) importiert **nichts** aus `shareomat.live` und kennt
    den Pfad von `live.db` nicht.
  * Werte in `live.db` verweisen auf `live_sources.id`, auch die von Quellen in `testing`. Die Zuordnung zu
    einem Teilnehmer wird beim Lesen über die Stammdaten aufgelöst und nicht
    kopiert.
* `live.db` ist eine **zusätzliche** Schutzmassnahme gegen Verwechslung,
  nicht die einzige Sicherheitsgrenze. Die Grenzen liegen in Abschnitt 1
  (Zonen), Abschnitt 5 (API-Scope) und Abschnitt 8 (Architekturtests).
* **Mehrere LEG später:** Jeder Live-Schlüssel und jede Portal-Antwort
  trägt von Anfang an die `community_id`. Aktiv ist in einer Installation
  vorerst genau eine LEG. Mandantenverwaltung wird jetzt **nicht** gebaut.

### 3. Fachliche Definition der Live-Bilanz

**Kanonischer Live-Wert** (eine *Messprobe*) pro Live-Messquelle, nach dem
Ingest-Adapter:

| Feld | Regel |
|---|---|
| `import_w` | Bezug aus dem Netz am Messpunkt, in W, `≥ 0`, endlich |
| `export_w` | Einspeisung ins Netz am Messpunkt, in W, `≥ 0`, endlich |
| `measured_at` | Zeitpunkt laut Quelle (UTC). Was er belegt, hängt von der Nachweisstufe der Quelle ab (3.2). Wird auf Plausibilität geprüft. |
| `sample_seq` | Zähler der Quelle, der nur bei einer neuen erfolgreichen Erfassung steigt (Stufen A und B). Sonst leer. |
| `session` | Sparkplug-Sitzung, aus der die Probe stammt (4.1) |
| `received_at` | Empfangszeit bei Shareomat (UTC, Serveruhr) |
| `quality` | Ergebnis der Prüfung (3.3) |

* **Vorzeichen:** Intern gibt es nur die zwei nicht-negativen Grössen. Das
  entspricht den bestehenden Metrics `ExportPowerNow`/`ImportPowerNow` und den
  OBIS-Registern 1.7.0/2.7.0. Liefert eine Quelle **einen** vorzeichenbehafteten
  Wert, rechnet der Adapter ausschliesslich mit dem für diese Quelle
  hinterlegten `sign_convention` um. Fehlt er, wird nicht geraten, sondern
  der Wert abgelehnt (ADR-0003).
* **Einheiten:** Gerechnet wird in W. Ein Wert in kW wird nur umgerechnet,
  wenn die Quelle die Einheit ausdrücklich mitliefert.
* **Bezugsgrösse ist der Netzanschlusspunkt:** Eine Live-Messquelle beschreibt
  den Messpunkt am Netzanschluss, nicht eine PV-Produktion oder einen
  Einzelverbraucher. Produktionszähler hinter dem Anschluss dürfen nicht
  zusätzlich gezählt werden. Das wird durch die 1:1-Zuordnung zu einem
  offiziellen LEG-Messpunkt erzwungen.

**Nettoleistung pro Messpunkt** *i*:
`P_net,i = import_w,i − export_w,i`, positiv = Bezug, negativ = Einspeisung.

**LEG-Bilanz:**
`S = Σ export_w,i − Σ import_w,i = −Σ P_net,i` über alle *i* in *E(t)*
(3.1). Positiv = Überschuss.

#### 3.1 Erwartete Messpunkte *E(t)* (präzisiert O-5)

*E(t)* sind alle Messpunkte, die nach der **Teilnahmeregel der Abrechnung**
am lokalen Kalendertag von *t* an der LEG teilnehmen.

* Die Teilnahmeregel steht heute in `shareomat/database/billing_eligibility.py`.
  `EligibilityResolver(db_path).for_day(day)` liefert `DayEligibility.meters`
  (Messpunkt → Teilnehmer) und `DayEligibility.excluded` (Messpunkt → Grund).
  Die Regel prüft nur Stammdaten: Messpunkt aktiv und am Tag gültig, Rolle
  nicht `grid`, Teilnehmer aktiv. Im Vertragsmodus prüft sie zusätzlich,
  ob der Teilnehmer wirksames Mitglied der gültigen Vertragsversion ist und
  ob der Tag nach dem vom Netzbetreiber bestätigten LEG-Start liegt. Sie
  rechnet weder Energie noch Preise noch Beträge.
* Die Live-Ebene ruft **genau diese Regel** auf, ausschliesslich über
  `live/masterdata.py`. Sie kopiert sie nicht und baut keine eigene. Ändert
  sich die Teilnahmeregel, folgt die Live-Ebene automatisch.
* **Die Abrechnung wird dafür nicht geändert.** Verwendet wird nur der
  Konstruktor, `for_day()` und die Felder `meters`, `excluded` und
  `unconfirmed_participants`. Die Live-Ebene ruft keine Energie-, Matching-
  oder Preisfunktion auf. Ein Architekturtest prüft das (Abschnitt 8).
  Sollte die Teilnahmeregel später in ein neutrales Modul verschoben
  werden, ist das ein eigener Entscheid mit eigenen Tests der Abrechnung.
* **Zeitpunkt → Tag:** *t* wird mit derselben Zeitzone wie in der
  Abrechnung (`SHAREOMAT_TZ`, Vorgabe `Europe/Zurich`) in den lokalen
  Kalendertag umgerechnet. Die Teilnahme gilt wie in der Abrechnung für den
  ganzen Tag.
* **Aktualität der Stammdaten:** `EligibilityResolver` hält Stammdaten im
  Speicher. Die Live-Ebene baut ihn darum beim Tageswechsel und
  spätestens alle 5 Minuten neu auf. Schlägt der Aufbau fehl, wird keine
  Bilanz berechnet (Status `KEINE_DATEN`), bis er wieder gelingt. Mit einer
  veralteten Liste wird nie weitergerechnet.
* Messpunkte unter `excluded` gehören nicht zu *E(t)*. Die Admin-Seite zeigt
  sie mit Grund an. Teilnehmer unter `unconfirmed_participants` werden wie in
  der Abrechnung gezählt, die Admin-Seite zeigt einen Hinweis.
* **Entscheid O-2:** Ein Messpunkt in *E(t)* ohne aktive, gültige
  Live-Messquelle bleibt erwartet. Er gilt als `MISSING` und verhindert den
  Status `VOLLSTÄNDIG`. Eine Ausnahme oder ein «von der Live-Bilanz
  ausnehmen» gibt es nicht.

#### 3.2 Messzeitpunkt und Nachweis einer neuen Messung (präzisiert O-1)

Home Assistant führt pro Zustand drei Zeitstempel. **Keiner belegt für sich
allein eine neue physische Messung:**

* `last_changed` und `last_updated` ändern sich nur, wenn sich der Wert
  (bzw. ein Attribut) ändert. Bei einem gleichbleibenden Wert, etwa 0 W
  Einspeisung in der Nacht, bleiben sie stehen, obwohl gemessen wird.
* `last_reported` ändert sich, wenn die Integration den Zustand schreibt,
  auch mit unverändertem Wert. Das geschieht auch, wenn sie einen
  zwischengespeicherten oder eingefrorenen Wert erneut schreibt, zum
  Beispiel beim Abfragen eines Geräts, das keine neuen Zählerdaten hat.
* Der Zeitstempel einer Sparkplug-Nachricht belegt nur, wann sie publiziert
  wurde (S-L3).

Darum gibt es drei **Nachweisstufen**. Die Stufe ist eine Eigenschaft der
Quelle. Der Verwalter hinterlegt sie in `live_sources.freshness_evidence`.
Sie wird nie aus der Nachricht übernommen. Liefert eine Quelle die Felder
ihrer Stufe nicht, ist die Probe `INVALID`.

| Stufe | Die Quelle liefert | Das ist belegt | `VOLLSTÄNDIG` möglich |
|---|---|---|---|
| **A** Messzeitpunkt | Zeitstempel des Messgeräts selbst, z. B. die Zeit im Zählertelegramm, als `measured_at`, dazu `sample_seq` | eine Messung zu diesem Zeitpunkt | ja |
| **B** Erfassungsnachweis | Zeitpunkt einer erfolgreichen Auslesung durch das erfassende Gerät (Gateway, Leseadapter, Integration) als `measured_at`, dazu `sample_seq`, der nur bei einer erfolgreichen neuen Auslesung steigt | eine erfolgreiche Erfassung zu diesem Zeitpunkt | ja |
| **C** nur Übertragung | nur Publikationszeit oder HA-Zeitstempel (`last_updated`, `last_reported`) | nur, dass etwas gesendet wurde | **nein**, höchstens `UNSICHER` |

Regeln:

* **Frische** wird ab `measured_at` gerechnet, nie allein ab `received_at`.
* **Neue Messung:** Eine Probe gilt nur als neu, wenn `sample_seq` grösser
  ist als bei der zuletzt angenommenen Probe derselben Sitzung (4.1) und
  `measured_at` neuer ist. Eine Wiederholung frischt nichts auf. Das Alter
  läuft weiter, bis die Probe nach `max_age` als `STALE` gilt.
* **Eingefrorene Werte:** Ein gleichbleibender Wert allein ist kein Fehler,
  etwa 0 W Einspeisung in der Nacht. Als eingefroren gilt eine Quelle, wenn
  kein neuer Nachweis kommt: `sample_seq` steigt nicht oder `measured_at`
  bleibt stehen. Das ergibt nach `max_age` `STALE`. Ein zusätzlicher
  Abgleich mit Zählerständen (passen die Zählerzuwächse zur Leistung?) ist
  möglich, sobald eine Quelle Zählerstände liefert. Er ist nicht Teil von
  Schritt 1.
* **Unbekannte Zustände und Sensorfehler:** HA-Zustand `unknown` oder
  `unavailable`, ein leerer oder nicht numerischer Zustand, Lesefehler,
  Prüfsummenfehler, Zeitüberschreitung. In all diesen Fällen sendet die
  Quelle `is_null`, wo möglich mit Fehlergrund, und die Probe ist `MISSING`.
  Ein Ersatzwert (0 W oder der letzte Wert) wird nie gesendet und nie
  angenommen. Das gehört zu den Stufen A und B. Eine Quelle, die bei einem
  Fehler einen Zahlenwert sendet, ist höchstens Stufe C.
* **Heutiger Stand:** Emsomat ist Stufe C (S-L2, S-L3). Ein WAN-Standort, der
  Emsomat-Werte weiterreicht, ist ebenfalls Stufe C. Ob eine Quelle Stufe A
  oder B erreichen kann, hängt davon ab, was das Messgerät oder die
  HA-Integration hergibt (Abschnitt 10, O-1).

#### 3.3 Qualität pro Wert

Die Prüfungen laufen in dieser Reihenfolge, die erste zutreffende gewinnt:

| Qualität | Bedingung |
|---|---|
| `MISSING` | kein Wert, `is_null`, Quelle deaktiviert, Quelle offline (NDEATH/DDEATH, Will), Sitzung beendet oder durch neues BIRTH ersetzt und noch keine neue Probe, eigene Broker-Verbindung unterbrochen (4.1) |
| `INVALID` | nicht endlich, negativ, über `max_power_w`, `measured_at` mehr als `max_skew` in der Zukunft, unbekannte Einheit, fehlende Vorzeichenregel, Felder der Nachweisstufe fehlen, Paarregel verletzt (4.1), gleicher `measured_at` mit anderen Werten |
| `STALE` | `now − measured_at > max_age` **oder** `now − received_at > max_age`, jeweils ab der letzten *neuen* Probe (3.2). Der erste Teil fängt Rebirths und verspätete Nachrichten mit alten Werten. |
| `CONFLICT` | `import_w > conflict_w` **und** `export_w > conflict_w` gleichzeitig |
| `OK` | sonst |

#### 3.4 LEG-Status

| Status | Bedingung | Was angezeigt wird |
|---|---|---|
| `VOLLSTÄNDIG` | *E(t)* nicht leer, für jeden Messpunkt genau eine aktive Live-Messquelle, alle Werte `OK`, alle Quellen Stufe A oder B (3.2), Identität aller Quellen nachgewiesen (4.2) | Überschuss bzw. «kein Überschuss» nach 3.5 |
| `UNSICHER` | mindestens ein erwarteter Wert nicht `OK`, **oder** eine Quelle Stufe C, **oder** die Identität einer Quelle nicht nachgewiesen | **keine Zahl**: App/HA zeigen «Status unsicher». Die Verwaltung sieht die Teilsumme, deutlich als «unvollständig, nicht verwenden» markiert, mit Liste der Gründe pro Messpunkt |
| `KEINE_DATEN` | *E(t)* leer, kein einziger `OK`-Wert oder Teilnahmeregel nicht ermittelbar (3.1) | «keine Live-Daten» |

Dazu kommen immer `coverage = OK-Werte / |E(t)|`, `updated_at` und der
älteste verwendete `measured_at`.

#### 3.5 Anzeige: Hysterese mit zeitlicher Stabilisierung

Die Anzeige darf nicht flattern, und «Überschuss» soll nur erscheinen, wenn
er stabil ist. Darum wechselt sie **schnell zu «kein Überschuss» und
langsam zu «Überschuss»**. Im Zweifel wird lieber einmal zu wenig
empfohlen, als dass jemand Netzstrom bezieht. Die Hysterese ersetzt das
feste Totband der 1. Fassung.

Es gibt zwei Anzeigezustände, `UEBERSCHUSS` und `KEIN_UEBERSCHUSS`. Sie
werden nur im `LiveStatusService` berechnet, einmal für alle Ausgaben (I2,
I3, I5). App, HA und Verwaltung sehen darum denselben Zustand.

| Parameter | Bedeutung | Startwert |
|---|---|---|
| `on_threshold_w` | Einschaltschwelle | 300 W |
| `on_hold_s` | so lange muss `S ≥ on_threshold_w` ununterbrochen gelten | 120 s |
| `off_threshold_w` | Ausschaltschwelle, kleiner als die Einschaltschwelle | 100 W |
| `off_hold_s` | so lange muss `S < off_threshold_w` ununterbrochen gelten | 20 s |
| `display_step_kw` | Anzeigeschritt | 0,1 kW |

Regeln:

1. `KEIN_UEBERSCHUSS → UEBERSCHUSS`: Jede Bilanz während `on_hold_s` ist
   `VOLLSTÄNDIG` und hat `S ≥ on_threshold_w`. Eine Lücke, ein anderer
   Status oder ein kleineres `S` setzt die Wartezeit zurück.
2. `UEBERSCHUSS → KEIN_UEBERSCHUSS` nach `off_hold_s` mit `S < off_threshold_w`.
3. `UEBERSCHUSS → KEIN_UEBERSCHUSS` **sofort**, wenn `S ≤ 0` (Defizit).
4. Ist der Status nicht `VOLLSTÄNDIG`, wird sofort «Status unsicher» bzw.
   «keine Live-Daten» angezeigt, und der Anzeigezustand fällt auf
   `KEIN_UEBERSCHUSS` zurück. Danach beginnt Regel 1 von vorn. Ein
   Überschuss wird also nie über eine Datenlücke hinweg weiter angezeigt.
5. Angezeigte Zahl im Zustand `UEBERSCHUSS`: das aktuelle `S`, auf
   `display_step_kw` **abgerundet**. Ergibt das 0, steht «< 0,1 kW».
6. Nach einem Neustart beginnt der Zustand bei `KEIN_UEBERSCHUSS`. Er wird
   nicht gespeichert.
7. Ungültige Parameter (negativ, `off_threshold_w ≥ on_threshold_w`) lassen
   das Live-Modul nicht starten. Die Verwaltung zeigt eine Meldung. Der Rest
   von Shareomat läuft weiter.
8. Die Verwaltung sieht `S` immer ungefiltert und mit Vorzeichen, dazu den
   Anzeigezustand und die laufende Wartezeit.

Die Startwerte sind Annahmen. Die endgültigen Werte werden anhand realer
Messreihen festgelegt (3.6).

#### 3.6 Parameter (Entscheid O-4)

Alle Parameter sind **überprüfbare Startwerte**: `max_age` 90 s (mindestens
das Dreifache des Publikationsintervalls), `max_skew` 30 s, `conflict_w`
50 W, dazu die Hysterese aus 3.5.

* Sie stehen an einer einzigen Stelle in der Konfiguration, nicht im Code
  verstreut. Beim Start werden sie geprüft und protokolliert. Die Admin-Seite
  zeigt sie an.
* Eine Quelle kann sie nie ändern. Eine Änderung ist eine
  Konfigurationsänderung des Verwalters.
* Festgelegt werden sie anhand realer Messreihen. Dafür protokolliert die
  Admin-Seite in Schritt 1 Statuswechsel, Anzeigewechsel und Alter der
  Proben, mit kurzer Aufbewahrung in `live.db`.

**Umsetzungsstand:** Die Parameter stehen zentral in `LiveParams`, aber fest im
Code, und werden beim Start nicht protokolliert. Der Wert `max_age` 90 s ist
eine Annahme aus der Umsetzung (Publikationsintervall von Emsomat), kein Entscheid.
Anzeige von `S` und Wartezeit sowie das Protokoll des Probenalters fehlen
(Audit AUD-11).

#### 3.7 Einzelmessung und LEG-Bilanz

Zwei getrennte Aussagen mit verschiedenen Bedingungen:

| | Einzelmessung | Vollständige LEG-Bilanz |
|---|---|---|
| Inhalt | Bezug und Einspeisung **eines** Messpunkts, Qualität, Zeitstempel, Verbindung, Herkunft | Summe über alle erwarteten Messpunkte *E(t)* |
| Anzeigbar, wenn | der Wert dieser Quelle `OK` ist | alle Bedingungen von `VOLLSTÄNDIG` erfüllt sind (3.4) |
| Nachweisstufe und Identität | werden angezeigt, sind aber keine Bedingung | Stufe A/B und nachgewiesene Identität nötig |
| Fehlende andere Messpunkte | ohne Einfluss | verhindern `VOLLSTÄNDIG` |

* Eine gültige Einzelmessung wird angezeigt, auch wenn andere Teilnehmer noch
  keine Live-Messquelle haben. Auf HA Second darf die eigene gültige
  Netzeinspeisung sichtbar sein.
* Eine Einzelmessung ist **keine Empfehlung**: Sie sagt nichts darüber, ob in
  der LEG Überschuss vorhanden ist.
* Messpunkte ohne Live-Messung werden nie als 0 W gerechnet. Eine Teilsumme
  gibt es nur in der Verwaltung, eindeutig als unvollständig markiert.
* Nur die aktive Live-Messquelle zählt. Eine Quelle in `testing` wird
  angezeigt, erscheint aber weder in der Bilanz noch in den HA-Sensoren.

**Anschlüsse ohne separate Einspeisemessung (LE-06):** `import_export` verlangt
beide Werte, eine fehlende Einspeisung ist `MISSING`. Ein reiner Bezugssensor
ist nur als `import_only` zulässig: ausdrücklich gewählt, begründet und nur bei
einem Messpunkt mit der Rolle Verbraucher. Die Einspeisung gilt dann als 0 W.
Meldet die Quelle trotzdem Einspeisung über `conflict_w`, ist die Probe
`INVALID`. Ein Wert mit Vorzeichen braucht die Vorzeichenregel.

### 4. Live-Messquellen: drei Arten, austauschbare Adapter, eine zentrale Prüfung

Fachlich wählt der Verwalter eine von drei Arten. Darunter liegen
technische Adapter. Die Oberfläche zeigt nur die Art und die dafür nötigen
Felder, nicht die Adapter.

| Art (`source_type`) | Für wen | Adapter | Weg | Identität (`source_key`) | Schritt |
|---|---|---|---|---|---|
| **A Emsomat / Sparkplug** (`emsomat`) | Teilnehmer mit Emsomat am eigenen Shareomat | `emsomat_local` | bestehender `SparkplugHost` (ADR-0004) | `edge_node_id` des Emsomat | 1 |
| **B Home Assistant / HA-Sensoren** (`home_assistant`) | Teilnehmer mit HA, ohne Emsomat | `ha_mqtt` | HA sendet die zwei Sensoren selbst nach einem festen Vertrag per MQTT (4.3): lokal über den HA-Broker, ein fremdes HA über den gesicherten Transport | Publisher-ID | 2 |
| | | ~~`ha_api`~~ | direkter Lesezugriff über `homeassistant_api`: **nicht vorgesehen** (H-1) | – | – |
| **C Externe Quelle / Gateway** (`external`) | Teilnehmer ohne HA und ohne Emsomat | `gateway_mqtt` | Smart-Meter-Reader (z. B. AMS Reader), eigenständiges MQTT-Gateway, künftige Geräte, nach Gateway-Vertrag (4.4) | registrierte Geräte-ID | 2 |
| | | `wan_sparkplug` | bestehende WAN-Sparkplug-Anbindung eines anderen Standorts (`WanDownlink`, ADR-0005) | (`edge_node_id`, `device_id`) | 1 |

**Zentrale Verarbeitung, für alle Adapter gleich:**

```text
Adapter ──► Rohprobe ──► zentrale Prüfung ──► live.db ──► Bilanz ──► Anzeige
(übersetzt nur)          (3.2, 3.3, 4.1–4.4)              (3.4)      (3.5)
```

* **Ein Adapter übersetzt nur:** Er macht aus dem Transportformat eine
  *Rohprobe*. Sie enthält `import_w` und `export_w` oder `signed_w`,
  `measured_at` mit Herkunft, Nachweisangaben, Sitzung, `received_at` und
  die Transportidentität. Ein Adapter entscheidet **nie** über die
  Qualität, rechnet keine Vorzeichen um und kennt weder Messpunkt noch
  Teilnehmer. Die Rohprobe hat kein Qualitätsfeld.
* **Die zentrale Prüfung** (`live/quality.py`) löst die Transportidentität
  über `live_sources` zur Quelle auf und wendet `measurement_mode` und
  `sign_convention` an. Sie prüft Plausibilität, Nachweisstufe, Alter und
  Paarregel und setzt die Qualität. Unbekannte Identitäten, Quellen mit
  `retired` und fremde Messpunkte werden hier verworfen.
* **Die Bilanz kennt keine Adapter.** Ein weiteres Messgerät braucht nur
  einen neuen Übersetzer und Tests, keine Änderung an Prüfung oder Bilanz.
* **Kein Adapter hängt an einem bestimmten Cloud-Broker oder Hersteller.**
  Die Verträge (4.3, 4.4) legen Inhalt und Identität fest. MQTT mit TLS ist
  die erste Transportbindung. Welcher Broker es wird, ist ein
  Betriebsentscheid (TODO «Hosting»).

Härtung in Schritt 1, ohne die bestehende Emsomat-Strecke zu verändern:

* Ein fehlender Wert oder `is_null` ergibt `MISSING`, nicht `0` (S-L1).
  Werte werden auf Endlichkeit und Vorzeichen geprüft. Das gilt auch für
  `sparkplug/bridge.py`, das den Emsomat-Wert ins WAN weiterreicht.
* Der Downlink nimmt nur Paare (`edge_node_id`, `device_id`) an, die als
  `live_sources` hinterlegt sind. Unbekannte Paare werden verworfen und
  begrenzt geloggt (S-L4). Siehe 4.2.
* Die Regeln zur Messprobe aus 4.1 gelten für `emsomat_local` und `wan_sparkplug` (S-L5).
* Bei der Weitergabe ins WAN gehen `is_null` und die Herkunft des
  Zeitstempels (`Market/PowerTimeSource`) mit (K-3). Eine WAN-Quelle ist
  nie besser belegt als ihre Herkunft. Ohne diese Angabe gilt Stufe C.
* Ingest-Grenzen: maximale Payload-Grösse, maximale Anzahl Metrics pro
  Nachricht, Ratenbegrenzung pro Quelle. Bei Überschreitung wird verworfen,
  ohne die Verbindung zu blockieren.
* Der Live-Ingest blockiert den Sparkplug-Callback nicht und wirft dort
  keine Ausnahme. Er arbeitet über eine begrenzte Queue. Läuft sie über,
  werden die betroffenen Quellen `MISSING`, nicht stillschweigend veraltet.

#### 4.1 Messprobe aus Sparkplug-Metrics (Adapter `emsomat_local` und `wan_sparkplug`)

Grundsatz: **Eine Messprobe entsteht aus genau einer Sparkplug-Nachricht**
(NBIRTH/NDATA lokal, DBIRTH/DDATA über WAN). Bezug und Einspeisung werden nie
aus verschiedenen Nachrichten zusammengesetzt. Emsomat sendet beide Werte
heute schon gemeinsam in einer Nachricht mit gleichem Zeitstempel
(`publish_own_state`).

1. **Paar:** Enthält eine Nachricht weder `Market/ImportPowerNow` noch
   `Market/ExportPowerNow`, ist sie für die Live-Ebene ohne Bedeutung, zum
   Beispiel eine Prognose-NDATA. Sie ändert nichts. Enthält sie nur einen
   der beiden Werte, entsteht keine Probe, und die Quelle wird `INVALID`.
   Der vorhandene Wert wird nie mit einem älteren Gegenwert kombiniert.
   Quellen, die nur geänderte Werte senden (Report by Exception), sind
   darum für die Live-Bilanz nicht zulässig.
2. **Zeitstempel:** Beide Metrics müssen denselben Metric-Zeitstempel
   haben. Fehlt er bei beiden, gilt der Payload-Zeitstempel. Weichen sie ab
   oder fehlt jeder Zeitstempel, ist die Probe `INVALID`.
3. **Sitzung:** Proben werden nur innerhalb einer gültigen Sitzung
   angenommen: nach NBIRTH (lokal) bzw. DBIRTH (WAN) und vor dem passenden
   NDEATH/DDEATH. Mit jedem neuen BIRTH beginnt eine neue Sitzung. Proben der
   alten Sitzung verfallen sofort (`MISSING`). Werte im BIRTH sind eine neue
   Probe und werden wie jede andere geprüft, insbesondere auf ihr Alter. Ein
   NDEATH mit veraltetem `bdSeq` wird lokal ignoriert; über WAN fehlt diese Prüfung noch (Audit AUD-07). DDATA ohne
   bekanntes DBIRTH wird verworfen, ebenfalls wie heute. Die Prüfung von
   `sample_seq` beginnt in jeder Sitzung neu, `measured_at` muss trotzdem
   frisch sein.
4. **Reihenfolge und Verspätung:** Pro Quelle wird nur eine Probe
   angenommen, deren `measured_at` neuer ist als die der zuletzt
   angenommenen. Ältere oder gleich alte Proben werden verworfen und
   gezählt: verspätet, doppelt oder nach einem Wiederverbinden nachgeliefert.
   Sie ändern den aktuellen Wert nicht. Gleicher `measured_at` mit anderen
   Werten macht die Quelle `INVALID`, bis eine neuere Probe kommt. Eine Probe
   mit `received_at − measured_at > max_age` ist schon beim Eintreffen
   `STALE`.
5. **`seq`:** Die Sparkplug-`seq` (0–255) wird pro Sitzung geprüft. Eine
   Lücke oder ein Rücksprung wird protokolliert. Lokal fordert Shareomat
   dann einen Rebirth an (bestehendes `request_rebirth()`, höchstens einmal
   pro Minute). Über die Gültigkeit einer Probe entscheidet aber Regel 4,
   nicht die `seq`, weil bei QoS 0 einzelne Verluste normal sind.
6. **Verbindungsunterbrechung:** Verliert Shareomat die eigene Verbindung
   zum Broker, lokal oder WAN, werden alle Quellen über diese Verbindung
   **sofort** `MISSING`, nicht erst nach `max_age` (S-L5). Nach dem
   Wiederverbinden wird eine Quelle erst mit einem neuen BIRTH wieder
   gültig, weil ein DEATH oder BIRTH während der Unterbrechung verpasst
   worden sein kann. Lokal fordert Shareomat dazu einen Rebirth an. Eine
   WAN-Quelle bleibt nach einem Unterbruch `MISSING`, bis ein neues DBIRTH
   kommt. Der Uplink der Gegenseite wiederholt seine Births alle 5 Minuten
   (Abschnitt 10, K-1).
7. **Retained:** Sparkplug-Daten werden nicht retained publiziert. Eine
   BIRTH-, DATA- oder DEATH-Nachricht mit Retain-Flag wird verworfen. Sonst
   könnte nach einem Neustart ein alter Wert als neu erscheinen. Nur `STATE`
   ist retained.
8. **Schlüssel:** Proben werden unter `live_sources.id` geführt. Diese wird
   aus dem Paar (`edge_node_id`, `device_id`) bzw. lokal aus der
   Emsomat-Node ermittelt, nie nur aus der `device_id` (S-L4).

#### 4.2 Quellenidentität (präzisiert S-L4)

* **Kennungen sind Zuordnung, keine Authentisierung.** `group_id`,
  `edge_node_id` und `device_id` im Topic ordnen eine Nachricht einer
  hinterlegten Quelle zu. Wer auf das Topic publizieren darf, kann jede
  Kennung hineinschreiben. Kennungen im Payload, etwa eine Teilnehmer-ID,
  werden für die Zuordnung nie verwendet und sind keine Vertrauensgrundlage.
* **Die Identität wird durch Transport und Broker-Berechtigungen
  durchgesetzt:**
  * Pro Standort bzw. Gerät gibt es ein eigenes Broker-Login (Benutzer mit
    starkem Passwort oder Client-Zertifikat). Es wird nie geteilt.
  * Eine Broker-ACL bindet dieses Login fest an seine Kennung: Publizieren
    nur unter `spBv1.0/<group_id>/<Typ>/<eigene edge_node_id>/#`, bei
    Gateways nur unter dem eigenen Geräte-Topic. Die Broker bieten dafür
    Platzhalter für Benutzername oder Client-ID, die Wahl gehört zur
    Broker-Spezifikation.
  * Ausserhalb des Hauses wird nur über TLS mit Prüfung des
    Server-Zertifikats verbunden.
* **Überprüfbar heisst nachgewiesen:** Bevor eine Quelle den Status
  `VOLLSTÄNDIG` mittragen kann, wird die Bindung mit Negativtests
  nachgewiesen. Das Login von Standort X darf nicht unter der Kennung von
  Standort Y publizieren können. Ergebnis und Datum stehen in
  `identity_basis` und `identity_verified_at`. Ohne Nachweis werden die
  Werte angenommen und in der Verwaltung gezeigt, der Status bleibt aber
  höchstens `UNSICHER`. Nach jeder Änderung an Broker, ACL oder Logins wird
  der Nachweis wiederholt.
* **Warum die Broker-ACL tragend ist:** Shareomat sieht als Empfänger nicht,
  welches Login eine Nachricht publiziert hat. Die Prüfung in Shareomat
  (nur hinterlegte Paare) ist die zweite Verteidigung gegen
  Fehlkonfiguration, nicht gegen einen Angreifer mit Broker-Rechten.
* **Lokal (`emsomat_local`):** Die Vertrauensgrenze ist der HA-Broker
  (Mosquitto). Ohne eigene ACL kann jeder lokale MQTT-Client als Emsomat
  publizieren. Für `identity_basis = broker_acl` braucht Emsomat ein
  eigenes Login mit ACL auf seine Node-Topics. Das ist eine Konfiguration am
  produktiven HA-Broker und nicht Teil dieses ADR (Abschnitt 10, K-2). Ohne
  sie bleibt `emsomat_local` bei `UNSICHER`, zusätzlich zu O-1.
* **Gateways und HA-Sender (`gateway_mqtt`, `ha_mqtt`, Schritt 2):** ein
  Login pro Gerät bzw. pro HA-Sender, `credential_hash` und `revoked_at` in
  `live_sources`. Gesperrt wird an beiden Stellen: am Broker (Login
  löschen) und in Shareomat (Quelle `retired`).
* **Home Assistant:** Shareomat erhält kein HA-Token (4.3). Die Identität
  eines HA-Senders ist sein Broker-Login, nicht die Entity-ID in der
  Nachricht.
* **Weitergabe an Emsomat:** Der `WanDownlink` gibt nur hinterlegte Paare
  (`edge_node_id`, `device_id`) weiter. Eine `device_id` kann nur einem
  Standort zugeordnet sein (`database/live_sources.py`), weil Emsomat einen
  fremden Teilnehmer nur unter der `device_id` kennt. So überschreibt ein
  zweiter Standort den ersten nicht. **Offen vor produktiver WAN-Nutzung:**
  Eine Quelle in `testing` erreicht Emsomat bereits (AUD-04), und nach dem
  Deaktivieren bleibt ihr letzter Wert dort stehen (AUD-03).

#### 4.3 Art B: Home-Assistant-Sensoren (Schritt 2, noch nicht umgesetzt)

Ein Teilnehmer mit Home Assistant, aber ohne Emsomat, bestimmt zwei
Entities: Netzbezug (z. B. `sensor.grid_import_power`) und Netzeinspeisung
(z. B. `sensor.grid_export_power`). Alternativ geht eine einzige Entity mit
Vorzeichen (`measurement_mode = signed`, `sign_convention` Pflicht). Die
Entities bilden zusammen **eine** Live-Messquelle.

**Zugriffsweg, die wichtigste Sicherheitsfrage dieser Art:**

* Shareomat hat heute **keinen** Zugriff auf Home Assistant
  (`ha_addon/config.yaml`: `homeassistant_api: false`, `hassio_api: false`).
  Das bleibt so.
* Die einzige HA-Schnittstelle, die ein Add-on bekommen kann
  (`homeassistant_api: true` mit Supervisor-Token), gibt **vollen Zugriff
  auf die HA-Core-API**: alle Zustände lesen und alle Dienste aufrufen, also
  auch Geräte schalten. Ein reines Leserecht gibt es dort nicht. Ein
  Langzeit-Token eines HA-Benutzers hat dasselbe Problem. Ein Fehler in
  Shareomat oder seine Übernahme würde damit zur Steuerung des ganzen
  Hauses. Das widerspricht «Shareomat steuert keine Geräte» und dem
  Grundsatz der geringsten Rechte.
* **Entschieden (H-1): Senden statt Abholen (`ha_mqtt`).** HA liefert die Werte
  selbst per MQTT an Shareomat. Shareomat bekommt **kein** HA-Token und
  greift **nie** auf eine HA-Instanz zu, weder auf die eigene noch auf eine
  fremde. Auf der HA-Seite übernimmt das ein von Shareomat bereitgestellter
  Blueprint (eine Automation), später eventuell eine kleine Integration.
  Er liest in festem Takt beide Entities und publiziert **eine** Nachricht
  nach dem Gateway-Vertrag (4.4).
  * Eigenes HA: über den HA-Broker, mit eigenem MQTT-Login und ACL nur auf
    das eigene Topic.
  * Fremdes HA: über den gesicherten Transport, genau wie ein Gateway.
* Direkt lesen über die API (`ha_api`) ist nicht vorgesehen (H-1). Für ein
  fremdes HA ist es in jedem Fall ausgeschlossen.
* **Fremdes HA nie direkt.** Erlaubt sind nur das Senden wie oben oder die
  HA Entity Bridge. Die Bridge ist heute aber keine prüfbare
  Live-Messquelle, siehe H-2.

**Inhalt der Nachricht:** Sie trägt die **Rohzustände** beider Entities, so
wie HA sie hat: Zustand als Text, Einheit, `last_reported` und
`last_updated`, dazu `sent_at` und optional eine Nachweis-Entity.
Ausgewertet wird zentral in Shareomat, nicht im Blueprint. So gelten
dieselben Regeln wie bei EA-1 und bei allen anderen Quellen.

**Prüfung:**

* `unknown`, `unavailable`, ein leerer oder nicht numerischer Zustand,
  NaN oder Inf, ein negativer Wert oder eine unbekannte Einheit ergeben
  `MISSING` bzw. `INVALID`, nie 0 W. Eine echte 0 bleibt 0 W.
* **Zeitliche Zusammengehörigkeit:** Beide Werte stammen aus derselben
  Nachricht, der Grundsatz aus 4.1 gilt sinngemäss. Ihre `last_reported`
  dürfen höchstens `max_pair_skew` auseinanderliegen (Startwert 10 s,
  O-4). Sonst ist die Probe `INVALID`. Für das Alter zählt der ältere Wert.
* **Aktualität:** `measured_at` ist das ältere `last_reported`. Das ist
  Stufe C (3.2): Es belegt, dass die Integration schreibt, nicht dass
  gemessen wurde. Eingefrorene Werte sind so nicht sicher erkennbar.
* **Stufe B ist möglich**, wenn der Verwalter eine dritte
  **Nachweis-Entity** zuordnet, die nur bei einem neuen Zählertelegramm
  wechselt, etwa die Telegrammzeit oder einen Telegrammzähler der
  Zähler-Integration. Shareomat prüft, dass sie steigt. Bleibt sie stehen,
  ist die Quelle nach `max_age` `STALE`. Welche Integrationen eine solche
  Entity liefern, wird an den realen Zählern geprüft.
* Nachrichten mit Retain-Flag werden verworfen. Ältere oder doppelte
  Nachrichten (`sent_at` bzw. `last_reported` nicht neuer) werden
  verworfen (4.1 Regeln 4 und 7 sinngemäss).
* Ohne HA-Zugriff kann Shareomat keine Entity-Liste anbieten. Die
  Entity-IDs werden darum eingetippt und streng geprüft (Muster
  `^[a-z_]+\.[a-z0-9_]+$`). Ein Auswahlfeld gibt es darum nicht (H-1).

#### 4.4 Art C: externes Gateway (Schritt 2, eigene Spezifikation)

* **Ein Vertrag für alle externen Geräte:** AMS Reader, andere
  Smart-Meter-Reader, eigenständige Gateways und HA nach 4.3. Er ist JSON
  und versioniert (`v`). Eine Nachricht ist eine Probe mit beiden Werten
  oder einem Wert mit Vorzeichen.
  * Der Messzeitpunkt hat eine Herkunft: `meter` (Telegrammzeit),
    `acquisition` (Zeitpunkt der Auslesung) oder `transport`.
    Optional kommt ein Telegrammzähler dazu.
  * Ein ungültiger Wert ist `null` mit Grund, nie 0.
* **Pro Gerät ein eigenes Login**, ACL nur auf das eigene Topic, TLS
  (4.2). Die Geräte-ID im Topic ist über die ACL an das Login gebunden.
  Felder im Payload sind keine Identität.
* **Herstellerformate** (z. B. das JSON eines AMS Readers) kommen nicht in
  die zentrale Prüfung. Sie werden als eigener kleiner Übersetzer auf den
  Vertrag abgebildet. Ein Gerät, das die Zeit aus dem Zählertelegramm
  liefert, kann Stufe A erreichen.
* **Nur ausgehende Verbindungen** der Geräte. Bei Teilnehmern gibt es keinen
  offenen Port, bei Shareomat vor Schritt 5 keinen HTTP-Eingang.

### 5. Schnittstellen

| ID | Schnittstelle | Richtung | Inhalt | Schritt |
|---|---|---|---|---|
| I1 | `live.service.LiveStatusService` (Python, im Core) | lesend | `leg_status()`, `own_reading()`, `source_reading()` und `meter_rows()`. Einzige Quelle für alle Ausgaben. | 1 |
| I2 | HA-Sensoren über `ha/live_sensors.py` | Core → HA | `sensor.shareomat_leg_ueberschuss` (kW: im Anzeigezustand `UEBERSCHUSS` die angezeigte Zahl nach 3.5, bei `KEIN_UEBERSCHUSS` 0, bei Status ≠ `VOLLSTÄNDIG` der Zustand `unknown`), `sensor.shareomat_leg_live_status` (Enum `ueberschuss`, `kein_ueberschuss`, `unsicher`, `keine_daten`), `sensor.shareomat_leg_vollstaendigkeit` (%), dazu für die eigene aktive Emsomat-Quelle `sensor.shareomat_live_netzbezug`, `_netzeinspeisung`, `_messwert_qualitaet`, `_verbindung`, `_messzeitpunkt`, `_zeitquelle`. Nur die Discovery ist retained, die Werte nicht. Mit Availability und `expire_after` (180 s), ohne Befehlskanal und ohne Daten anderer Teilnehmer. | 1 |
| I3 | Admin-Bereich «LIVE-ENERGIE» | Verwalter ↔ Core | Übersicht, Messquellen mit «Live-Messquelle zuordnen» und Diagnose (5.1) | 1 (Übersicht, Messquellen, Diagnose; Empfang für `emsomat_local` und `wan_sparkplug`; `ha_mqtt` und `gateway_mqtt` lassen sich eintragen, haben aber noch keinen Eingang), 2 (Eingänge der Arten B und C-Gateway) |
| I4 | LEG-Status an Emsomat | Core → Emsomat | über den Coordinator-NCMD-Mechanismus mit `ValidUntil`, nur bei `VOLLSTÄNDIG`. Noch nicht umgesetzt. Davon zu unterscheiden sind die bestehenden Coordinator-Daten aus ADR-0004 (Abschnitt 9). | später |
| I5 | Portal-API v1 | Portal → Core | siehe unten | 4 |
| I6 | Gateway-Ingest | Gateway bzw. HA-Sender → Broker → Core | ein gemeinsamer Vertrag für Art C und Art B (4.3, 4.4) | 2 |

#### 5.1 Bedienung «Live-Energie»

**Navigation (Korrektur der 5. Fassung).** Live-Energie erweitert Shareomat
und ist ein eigener Menübereich **LIVE-ENERGIE**. Er steht nach den bestehenden
Gruppen VERWALTUNG, BETRIEB, INTEGRATIONEN und SYSTEM, vor dem Kontobereich.
Bestehende Menüpunkte werden nicht verschoben, umbenannt oder neu gruppiert.
Die frühere Einordnung unter BETRIEB ist aufgehoben.

| Ansicht | Inhalt |
|---|---|
| Übersicht | LEG-Bilanz, eigene Einzelmessung, eine Zeile pro Messpunkt mit Live-Zustand, Bezug, Einspeisung und Qualität |
| Messquellen | Zustand und Quellen pro Messpunkt, «Live-Messquelle zuordnen», Prüfen, Aktivieren, Ändern, Deaktivieren, Identitätsnachweis |
| Diagnose | Empfang pro Quelle, verworfene Nachrichten, nicht zugeordnete WAN-Standorte, Parameter, letzte Statuswechsel |

Die folgende Tabelle beschreibt die Angaben pro Messpunkt, verteilt auf diese
Ansichten.

**Angaben pro Messpunkt.** Eine Zeile pro offiziellem Messpunkt der LEG, also *E(t)*.
Messpunkte, die nicht in der LEG sind, stehen grau mit Grund darunter.

| Spalte | Inhalt |
|---|---|
| Messpunkt, Teilnehmer | aus den Stammdaten, nicht kopiert |
| Live-Zustand und -Messquelle | Art und Kennung, «Keine Live-Messung vorhanden» oder «Noch nicht festgelegt». Ohne aktive Quelle ist keine vollständige Bilanz möglich. Unter **Messquellen** mit der Schaltfläche **«Live-Messquelle zuordnen»** änderbar. |
| Kommunikation | `online`, `offline` oder `nie empfangen`, mit Zeitpunkt der letzten Nachricht |
| Qualität | `OK`, `MISSING`, `INVALID`, `STALE` oder `CONFLICT` mit Grund in Klartext |
| Bezug / Einspeisung | letzter Wert in W |
| Letzte gültige Messung | Zeitpunkt und Alter |
| Nachweis | Stufe A/B/C und ob die Identität nachgewiesen ist (mit Datum) |

Darüber stehen der LEG-Status, `S` mit Vorzeichen und der Anzeigezustand
(3.4, 3.5). Quellen in `testing` sind sichtbar markiert und gehen nicht in
die Summe.

**Ablauf «Live-Messquelle zuordnen»:**

1. **Offiziellen Messpunkt wählen.** Meist ist er schon gewählt, weil der
   Ablauf aus seiner Zeile startet.
2. **Auswahl treffen:** Keine Live-Messung vorhanden, Emsomat / Sparkplug,
   Home Assistant / HA-Sensoren, Externe Quelle / Gateway oder Externer
   Standort / WAN-Sparkplug. «Keine Live-Messung vorhanden» wird sofort
   gespeichert, die weiteren Schritte entfallen.
3. **Nur die Felder dieser Art ausfüllen:**
   * Emsomat: keine Eingabe. Es gilt der lokale Knoten aus der Konfiguration
     (Add-on-Optionen, Abschnitt `sparkplug`).
   * Home Assistant: die Entity-IDs für Bezug und Einspeisung (oder eine
     mit Vorzeichen und Vorzeichenregel), optional eine Nachweis-Entity.
     Shareomat soll danach die passende Blueprint-Einstellung anzeigen (Schritt 2, noch nicht umgesetzt).
   * Externe Quelle / Gateway: die Kennung eines registrierten Geräts.
   * Externer Standort / WAN-Sparkplug: Standort und Device.
   * Bei jeder Art: `max_power_w` und die Nachweisstufe.
4. **Prüfen:** Gespeichert wird zuerst als `testing`. Die Seite zeigt live,
   was ankommt: Werte, Qualität, Alter, Gründe. In die Bilanz geht nichts.
5. **Zuordnen und aktivieren:** Das geht nur, wenn in den letzten Minuten
   gültige Proben angekommen sind und kein Kanal schon belegt ist. Ist dem
   Messpunkt bereits eine aktive Quelle zugeordnet, weist die Schaltfläche
   darauf hin. Ein eigener Bestätigungsschritt fehlt noch (Audit AUD-12). Der Wechsel läuft in einer Transaktion (Abschnitt 2).

Weitere Aktionen: **«Deaktivieren»** (wird `retired`) und
**«Identität als nachgewiesen eintragen»** mit Datum und Verweis auf den
Negativtest.

Regeln: Nur der Verwalter darf das, mit CSRF-Schutz und Audit-Eintrag. Alle
Eingaben werden streng geprüft. Es werden keine Geheimnisse angezeigt. Die
übrige Verwaltung bleibt unverändert.

#### 5.2 Portal-API v1 (Schritt 4, hier nur der Rahmen)

* Eigener Listener, nie auf derselben Adresse wie der Admin-Webserver. Im
  LAN-Test über einen Unix-Socket oder `127.0.0.1`. Im Containerbetrieb nur
  im internen Netz, mit Dienstschlüssel, später mTLS.
* Endpunkte, abschliessend aufgezählt. Was hier nicht steht, gibt es nicht
  (deny by default):
  * `POST /portal/v1/session`, `DELETE /portal/v1/session`,
    `POST /portal/v1/me/password`
  * `GET /portal/v1/leg/status`: nur Überschuss bzw. Status, `coverage`,
    `updated_at`
  * `GET /portal/v1/me/live`: nur die eigenen Messpunkte
  * `GET /portal/v1/me/documents` und `GET /portal/v1/me/documents/{opaque_id}`:
    nur **freigegebene** eigene Belege
* Die Anmeldung prüft der Core. Passwort-Hashes liegen nur im Core. Der Core
  gibt einen undurchsichtigen Session-Token aus, und das Portal reicht ihn bei
  jeder Anfrage weiter. **Der Teilnehmer wird immer aus der Session
  bestimmt, nie aus Parametern der Anfrage.**
* Antworten sind feste Datenobjekte mit einer Feldliste. Es werden keine
  Datenbankzeilen weitergereicht, keine internen IDs (nur undurchsichtige
  IDs) und keine Daten anderer Teilnehmer ausser dem LEG-Status.
* Ratenbegrenzung und Login-Drosselung im Core, wie heute bei `database/auth.py`.
* Auswirkung einer Kompromittierung: Ein übernommenes Portal sieht den
  LEG-Status und die Daten der Teilnehmer, die sich während der
  Kompromittierung anmelden. Es kann keine Teilnehmer auflisten, nicht
  schreiben, nicht auf die Verwaltung zugreifen und keine Datenbank lesen.
* **Teilnehmeransicht in der Verwaltung:** Die Admin-Seite ruft dieselben
  Funktionen hinter der Portal-API im Prozess auf, mit einem ausdrücklich
  gewählten, schreibgeschützten Teilnehmer. Es wird keine Portal-Session
  erzeugt.

### 6. Datenflüsse

```text
Schritt 1 (nur lokal, kein Internetzugang neu):

Emsomat ──Sparkplug NDATA──► SparkplugHost ─┐
                                             ├─► Live-Adapter ─► Prüfung ─► live.db ─► LiveStatusService
fremder Standort ─► Relay ─► WanDownlink ───┘       ▲                                   │
                                                    │ live_sources, E(t)                ├─► HA-Sensoren (I2)
                                         shareomat.db (nur lesen)                       └─► Admin «Live-Energie» (I3)

Abrechnung (unverändert): EBL/VNB-Dateien ─► Import ─► meter_readings ─► Matching ─► Billing
                          (keine Verbindung zu live.db)

Coordinator (ADR-0004):   shareomat.db (nur lesen), ENTSO-E/SNB ─► coordinator.db ─► NCMD an Emsomat
                          (nur bei verbundenem Emsomat, ausserhalb der Abrechnungssperre)

Schritt 2: Gateway / AMS Reader ─┐
           HA-Sender (Blueprint) ┴─MQTT/TLS─► Broker ─► gateway_mqtt / ha_mqtt ─► dieselbe Prüfung
Später:    Portal ─► Portal-API (I5) ─► LiveStatusService
```

### 7. Was ausdrücklich nicht gemacht wird

* Shareomat **steuert keine Geräte**: keine Schalter in Portal oder App, keine
  Befehle an Emsomat ausser den informativen LEG-Daten (ADR-0004, I4).
* Live-Werte ersetzen, ergänzen oder korrigieren **nie** offizielle
  Messwerte, Matching, Rechnungen oder Gutschriften.
* Der Browser bekommt **keinen** MQTT-Zugang.
* **Keine** öffentliche Seite ohne Login in den ersten Schritten. Falls
  später doch: nur «Überschuss vorhanden / kein Überschuss», ohne kW-Wert.
* **Keine** Portweiterleitung, auch nicht im Pilot.
* HA Second mit der HA Entity Bridge ist eine **optionale Pilotlösung**. Das
  Portal hängt nicht davon ab.
* Shareomat bekommt **kein** HA-Token und greift auf **keine** HA-Instanz
  zu (H-1). Auf ein fremdes HA gibt es
  nie einen direkten Zugriff.
* Keine zwei gleichzeitig zählenden Live-Messquellen für denselben
  offiziellen Messpunkt. Keine Summe aus Emsomat und HA-Sensoren desselben
  Anschlusses.

### 8. Durchsetzung durch Tests

Neben den Funktionstests gelten diese Architektur- und Sicherheitstests ab
Schritt 1:

1. Kein Modul unter `shareomat/core/` oder `shareomat/database/` importiert
   `shareomat.live`.
2. Unter `shareomat/live/` importiert nur `masterdata.py` etwas aus
   `shareomat.database`, und nur aus der erlaubten Liste (Abschnitt 2). Kein
   Modul unter `shareomat/live/` importiert etwas aus `shareomat.core`.
   `shareomat.live` ruft keine schreibende Funktion von `shareomat.db` auf,
   ausser der Pflege von `live_sources` über die Admin-Seite.
3. **Erwartete Messpunkte:** Mit denselben Testdaten liefern Live-Ebene und
   Abrechnung für denselben Tag dieselbe Messpunktliste. Das gilt auch im
   Vertragsmodus, bei Austritt, fehlender Unterschrift, vor dem bestätigten
   LEG-Start und bei Rolle `grid`. Die bestehenden Abrechnungstests laufen
   unverändert.
4. **Bilanzfälle:** alle `OK`; ein Wert fehlt / ist veraltet / ungültig /
   widersprüchlich; Zeitstempel in der Zukunft; NaN und Inf; negative Werte;
   Werte über `max_power_w`; fehlende Vorzeichenregel; leere *E(t)*;
   Messpunkt ohne Live-Messquelle; Quelle der Stufe C; Identität nicht
   nachgewiesen; Teilnahmeregel nicht ermittelbar; Queue übergelaufen. In
   jedem nicht vollständigen Fall erscheint **keine** Zahl in den Ausgaben
   I2 und I5.
5. **Messprobe (4.1):** nur einer der beiden Werte; verschiedene
   Zeitstempel; Werte aus zwei Nachrichten werden nicht kombiniert;
   Prognose-NDATA ändert nichts; verspätete und doppelte Nachricht werden
   verworfen; gleicher `measured_at` mit anderen Werten; Rebirth mit altem
   `measured_at`; neues BIRTH lässt alte Proben verfallen; DDATA ohne
   DBIRTH; `seq`-Lücke fordert lokal einen Rebirth an, ändert aber nicht die
   Gültigkeit; eigene Verbindung getrennt → sofort `MISSING`, nach dem
   Wiederverbinden erst mit neuem BIRTH gültig; Nachricht mit Retain-Flag
   wird verworfen; `sample_seq` steigt nicht → nach `max_age` `STALE`.
6. **Quellenidentität (4.2):** unbekanntes Paar (`edge_node_id`,
   `device_id`) wird verworfen; dieselbe `device_id` unter zwei
   `edge_node_id` überschreibt sich nicht; eine Teilnehmer-ID im Payload hat
   keine Wirkung; zu grosse oder zu viele Nachrichten werden verworfen.
   Broker-Negativtests (Login X publiziert unter Kennung Y) gehören zum
   Nachweis vor dem Aktivieren einer Quelle, nicht zu den Unit-Tests.
7. **Hysterese (3.5):** Einschalten erst nach `on_hold_s`; eine
   Unterbrechung setzt die Wartezeit zurück; Ausschalten nach `off_hold_s`;
   sofortiges Ausschalten bei `S ≤ 0`; ein Status ≠ `VOLLSTÄNDIG` setzt
   zurück; Abrunden und «< 0,1 kW»; ungültige Parameter; I2 und I3 zeigen
   denselben Anzeigezustand.
8. **Live-Messquellen (Abschnitt 2):** Eine zweite aktive Quelle für
   denselben Messpunkt wird schon von der Datenbank abgelehnt, auch am
   Programmcode vorbei. Derselbe Kanal in zwei aktiven Quellen wird
   abgelehnt. Werte einer Quelle in `testing` erscheinen nie in der Bilanz.
   Ein Wechsel ist atomar: Es gibt keinen Zeitpunkt mit zwei aktiven
   Quellen, und der alte Wert wird nicht übernommen. Werte einer Quelle mit
   `retired` werden verworfen. Ein unerlaubtes Paar aus Art und Adapter
   wird abgelehnt. Jede Änderung erzeugt einen Audit-Eintrag.
9. **Zentrale Prüfung (Abschnitt 4):** Eine Rohprobe hat kein
   Qualitätsfeld. Ein Adapter kann die Prüfung nicht umgehen. Dieselben
   Fehlerfälle (fehlt, NaN, negativ, veraltet …) ergeben bei jedem Adapter
   dieselbe Qualität. Die Bilanz importiert keinen Adapter.
10. **HA-Sensoren (4.3, ab Schritt 2):** `unknown` und `unavailable` werden
    nie zu 0 W; eine echte 0 bleibt 0 W; Werte aus zwei Nachrichten werden
    nicht kombiniert; `last_reported` weiter auseinander als
    `max_pair_skew` → `INVALID`; eine Nachweis-Entity, die nicht steigt →
    `STALE`. Architekturtest: Shareomat enthält keinen HA-API-Client und
    keinen Aufruf von HA-Diensten (H-1).
11. Ab Schritt 4: Ein Portal-Konto kann sich nicht am Admin-Webserver
   anmelden. Jeder Portal-Endpunkt liefert bei fremder Teilnehmer-ID
   denselben Fehler wie bei nicht existierender. Es gibt keine Admin-Route
   auf dem Portal-Listener und keine Portal-Route auf dem Admin-Webserver.

### 9. Umsetzungsschritte

| Schritt | Inhalt | Neu aus dem Internet erreichbar |
|---|---|---|
| **1** | `shareomat/live/` (Modell, Rohprobe, zentrale Prüfung, Bilanz, Hysterese, `live.db`, `LiveStatusService`). Tabellen `live_sources` und `live_source_channels` gleich im **vollständigen** Modell mit Arten, Adaptern, Lebenszyklus und Unique-Indizes, damit später keine Migration nötig ist. Adapter `emsomat_local` und `wan_sparkplug` mit Härtung und Regeln aus 4.1, inklusive `bridge.py` und Weitergabe von `is_null`/Zeitquelle (K-3). HA-Sensoren I2. Admin-Seite «Live-Energie»: Übersicht und **«Live-Messquelle zuordnen»** für diese zwei Adapter (5.1). Tests 1–9 aus Abschnitt 8. Keine Änderung an Emsomat, kein HA-Zugriff. | nein |
| 2 | Gateway-Vertrag (gemeinsam für Art C und Art B), Adapter `gateway_mqtt` und `ha_mqtt`, HA-Blueprint, Gerätezugänge anlegen und sperren, Zuordnen der Arten B und C in der Oberfläche. Vorher Prüfung der HA-Möglichkeiten (H-1, Aufgabe 10). | nein (nur ausgehend) |
| 3 | Pilot beim Nachbarn: Sensor I2 über HA Entity Bridge → HA Second (über VPN oder Cloud-Broker). **Beginnt erst, wenn Datenschutz und Freigabe geklärt sind (O-3).** | nein |
| 4 | Portal-API I5 und Teilnehmerportal als eigener Prozess, Test im LAN, Teilnehmerkonten, Teilnehmeransicht in der Verwaltung | nein |
| 5 | Portal als eigener Container, HTTPS-Tunnel oder Reverse Proxy, unabhängiger Security-Review, Last- und Ausfalltests | ja, erst nach Review |

**Schritt 1 bleibt vollständig lokal.** Es gibt keinen zusätzlichen
Internetzugang und keine neue Verbindung nach aussen. Eine bereits
konfigurierte WAN-Verbindung (ADR-0005) wird nur gehärtet, nicht erweitert.

**Automatik (Entscheid vom 08.11.2026):** Seit Add-on 0.6.8 verbindet sich
Shareomat automatisch mit einem Emsomat auf demselben Home Assistant
(Add-on-Optionen, Abschnitt `sparkplug`). Solange Emsomat online ist, laufen
damit auch die bestehenden Funktionen aus ADR-0004: Lastprognose, LEG-Daten an
Emsomat und, nur mit hinterlegtem Token, der Preisabruf bei ENTSO-E und SNB.
Diese Arbeiten laufen ausserhalb der Abrechnungssperre, schreiben nur in
`coordinator.db` und können einen Abrechnungslauf nicht aufhalten.

Die Verwaltung bleibt unverändert, abgesehen vom neuen, abgetrennten
Menübereich LIVE-ENERGIE nach den bestehenden Gruppen.

**Änderung gegenüber der 2. Fassung:** Das Zuordnen einer Live-Messquelle
war in Schritt 2 vorgesehen, Schritt 1 war nur lesend. Neu gehört das
Zuordnen für die zwei schon vorhandenen Wege (`emsomat_local`,
`wan_sparkplug`) zu Schritt 1. Sonst müssten Quellen über die Einrichtung
oder von Hand in der Datenbank angelegt werden, und genau dort liessen sich
die Regeln aus Abschnitt 2 am leichtesten umgehen. Das Datenmodell wird
gleich vollständig angelegt, die Adapter für Art B und Art C-Gateway
kommen erst in Schritt 2.

Jeder Schritt wird einzeln geprüft, getestet und freigegeben.

### 10. Offene und entschiedene Punkte

**Entschieden (07.11.2026, Projektverantwortlicher):**

* **O-2:** Ein LEG-relevanter Messpunkt ohne gültige Live-Messquelle verhindert
  den Status `VOLLSTÄNDIG`. Es gibt keine Ausnahme (Abschnitt 3.1).
* **O-3:** Datenschutz und Freigabe der Live-Daten werden **vor dem
  Nachbar-Pilot** (Schritt 3) geklärt. Hintergrund: Bei zwei Haushalten
  kann jeder aus dem Überschuss und dem eigenen Wert den Saldo des anderen
  ausrechnen. Zu klären sind Einwilligung im Teilnehmervertrag, was wer
  sieht und wie lange Live-Daten aufbewahrt werden.
* **O-4:** Die Parameter sind überprüfbare Startwerte (Abschnitt 3.6).
* **O-5:** präzisiert in Abschnitt 3.1. Die Live-Ebene nutzt die
  bestehende Teilnahmeregel `EligibilityResolver.for_day()` nur lesend. Die
  Abrechnung bleibt unverändert, Test 3 in Abschnitt 8 sichert die
  Übereinstimmung.

**O-1: separater Auftrag an Emsomat (EA-1). Nicht Teil dieses ADR und
nicht Teil von Schritt 1. Emsomat wird jetzt nicht verändert.**

Ziel: Emsomat-Proben erreichen Stufe A oder B (Abschnitt 3.2). Inhalt des
Auftrags, als Vorschlag für eine neue Version von
`Emsomat_Shareomat_MQTT_Vertrag.md`:

1. Ist der HA-Leistungssensor `unknown` oder `unavailable`, leer oder nicht
   numerisch, sendet Emsomat beide Metrics mit `is_null`. Nie 0 W, nie den
   letzten Wert (S-L2).
2. Beide Leistungswerte stehen wie heute immer zusammen in derselben
   Nachricht mit identischem Zeitstempel. Report by Exception ist dafür nicht
   zulässig.
3. Der Metric-Zeitstempel ist der Messzeitpunkt (Stufe A) oder der Zeitpunkt
   der erfolgreichen Erfassung (Stufe B). Den Publikationszeitpunkt meldet
   Emsomat nicht als Messzeit (S-L3).
4. Neue Metrics `Market/PowerSampleSeq` (steigt nur bei einer neuen
   erfolgreichen Erfassung) und `Market/PowerTimeSource` (`meter`,
   `acquisition` oder `publish`). Shareomat prüft beide gegen die
   hinterlegte Stufe. Meldet die Quelle eine tiefere Stufe als hinterlegt,
   ist die Probe `INVALID`.
5. Woher Emsomat den Nachweis bekommt: `last_updated` und `last_reported`
   genügen nicht (3.2). Emsomat braucht pro Leistungssensor eine
   konfigurierbare Nachweisquelle aus der Zähler-Integration, zum Beispiel
   eine Entität mit der Telegrammzeit oder einem Telegrammzähler. Ohne sie
   meldet Emsomat `PowerTimeSource = publish`, und die Quelle bleibt Stufe C.
6. Rückwärtskompatibel: Shareomat behandelt Nachrichten ohne die neuen
   Metrics als Stufe C.
7. Tests auf der Emsomat-Seite für alle Punkte. Freigabe des Vertrags durch
   beide Projekte.

Ob Stufe A oder B erreichbar ist, hängt von den Zählern und Lesegeräten der
Haushalte ab (TODO «Zähler-Schnittstellen prüfen»). Bis EA-1 umgesetzt ist,
bleibt jede Emsomat-Quelle bei `UNSICHER`.

**Stand EA-1 (07.11.2026, in Emsomat umgesetzt, Commit `e6be1c7`):**

* Punkte 1–3 umgesetzt: `is_null` statt 0 W (auch im NBIRTH vor dem ersten
  Messwert), beide Werte immer gemeinsam, Metric-Zeitstempel =
  `last_reported` des HA-Sensors (Fallback `last_updated`), bei zwei
  Sensoren der neuere (seit 09.11.2026, die Seite ohne Leistungsfluss meldet sich sonst nicht mehr). Das ist eine Obergrenze für das Alter, kein Nachweis
  einer Messung.
* Punkt 4 angepasst: `Market/PowerTimeSource` mit den Werten
  `ha_last_reported`, `ha_last_updated`, `none` (statt `meter`,
  `acquisition`, `publish`). `Market/PowerSampleSeq` wird bewusst **nicht**
  gesendet: HA liefert dafür keinen ehrlichen Nachweis, ein solcher Zähler
  würde eine Messung nur vortäuschen. Emsomat bleibt damit Stufe C.
* Punkt 5 (konfigurierbare Nachweis-Entity in Emsomat) ist offen,
  sinngemäss wie die Nachweis-Entity in 4.3.
* Zusätzlich behoben: neues NBIRTH nach einem MQTT-Unterbruch; eingehende
  Teilnehmerwerte mit `is_null`, NaN oder negativ werden nicht mehr zu 0 W.
* Unverändert, eigener Auftrag: EV-Logik und PID-Test in Emsomat bekommen
  bei einem ungültigen Sensor weiterhin 0 W.

**Entschieden am 07.11.2026 (Projektverantwortlicher):**

* **H-1 Home Assistant sendet selbst.** HA sendet seine Bezugs- und
  Einspeisesensoren über eine gesicherte, ausgehende Verbindung an
  Shareomat (`ha_mqtt`, 4.3). Shareomat bekommt **keinen** umfassenden
  HA-Zugriff und kein HA-Token. Ein Blueprint oder eine vergleichbare
  HA-Automation ist der Kandidat. Was HA dafür tatsächlich bietet und
  voraussetzt, wird vor der Umsetzung geprüft (Arbeitsliste, Aufgabe 10).
  Gültigkeit und Aktualitätsnachweis bleiben Pflicht: Eine
  MQTT-Veröffentlichung allein ist kein Nachweis einer neuen Zählermessung
  (3.2). Die Alternative `ha_api` (direktes Lesen über
  `homeassistant_api`, voller HA-Zugriff bis zur Gerätesteuerung) ist damit
  nicht vorgesehen.
* **H-2 HA Entity Bridge nur für Pilot und Anzeige.** Die Bridge darf im
  Nachbar-Pilot und zur Anzeige verwendet werden. Solange sie keinen
  ausreichenden Messzeitstempel oder Erfassungsnachweis liefert, ist sie
  keine qualitätsgesicherte Live-Messquelle für eine vollständige
  LEG-Bilanz. Hintergrund: Ihr Protokoll überträgt Zustände retained, ohne
  Zeitstempel und nur bei Änderung. Die Bridge wird jetzt nicht verändert.
* **Schritt 1 enthält das Zuordnen.** Bestehende Emsomat- und
  WAN-Sparkplug-Quellen werden schon in Schritt 1 über die Oberfläche
  zugeordnet. Eine manuelle Datenbankbearbeitung ist im normalen Betrieb
  nicht nötig. Der Lebenszyklus `testing → active → retired` und die Regel
  «höchstens eine aktive Live-Messquelle pro offiziellem Messpunkt»
  (Abschnitt 2) sind verbindlich.

**Zu klären in Schritt 1, ohne Änderung an fremden Systemen:**

* **K-1 Neues DBIRTH über WAN nach einem Wiederverbinden (umgesetzt, Realtest offen).**
  ADR-0005 sieht keine Befehle zwischen Standorten vor. Darum wiederholt der
  WAN-Uplink NBIRTH und DBIRTHs alle 5 Minuten, mit dem Zeitstempel des letzten bekannten
  Werts. Ein Standort, der die Verbindung verlor, hat damit nach
  höchstens 5 Minuten wieder eine gültige Sitzung.
* **K-2 Lokale Broker-ACL für Emsomat.** Ein eigenes Mosquitto-Login mit
  ACL für Emsomat ist eine Änderung am produktiven HA-Broker. Sie wird nur
  beschrieben und vom Projektverantwortlichen separat entschieden, nicht in
  Schritt 1 ausgeführt. Ein Entwurf liegt vor
  (`docs/development/MQTT-Sicherheitskonzept_AUD-02.md`), umgesetzt ist nichts.
* **K-3 Nachweis über WAN weitergeben (umgesetzt, Realtest offen).** `ParticipantRelay`,
  `bridge.py` und `WanDownlink` geben `is_null`, den Quellenzeitstempel und
  `Market/PowerTimeSource` weiter. Eine WAN-Quelle ist nie besser
  belegt als ihre Herkunft. Fehlt die Angabe, gilt Stufe C.
* **K-4 Falsche Zuordnung erkennen.** Wird die Quelle von Haus X dem
  Messpunkt von Haus Y zugeordnet, ist das technisch nicht sicher
  erkennbar. Vorgesehen ist ein späterer Abgleich: Mittelwert der
  Live-Werte gegen den offiziellen Viertelstundenwert, als Warnung in der
  Verwaltung, nie als Korrektur (Etappe 2, TODO-Test «Live-Bilanz mit
  Viertelstundendaten vergleichen»).

## Begründung

* **Kein Teilnehmer in der Admin-Rolle:** Das heutige Rechtemodell gibt mit
  `view` alle Seiten frei. Eine eingeschränkte Teilnehmerrolle würde jede
  künftige Admin-Seite automatisch für Teilnehmer öffnen, sobald jemand das
  Recht falsch setzt. Eine eigene Oberfläche, bei der alles verboten ist,
  was nicht ausdrücklich erlaubt ist, macht diesen Fehler unmöglich.
* **Portal als eigener Prozess/Container statt nur als eigener Port:** Ein
  Port im selben Prozess teilt Speicher, Dateisystem, Geheimnisse und
  Datenbankzugriff. Eine Lücke im Portal wäre eine Lücke in der Abrechnung.
  Verworfen: Portal im Admin-Webserver mit eigenen Routen; zweiter Port im
  selben Prozess für den Internetbetrieb.
* **Live-Messquellen in `shareomat.db`, Messwerte in `live.db`:** Die Zuordnung
  ist Verwaltungsdatum mit Fremdschlüssel auf die führenden Messpunkte.
  Messwerte sind kurzlebig, häufig und nicht vertrauenswürdig. Verworfen:
  alles in `live.db` (die Zuordnung würde ohne referenzielle Integrität
  Messpunkte duplizieren); alles in `shareomat.db` (Live-Werte mit
  Schreiblast und kurzer Aufbewahrung neben der finanziellen Wahrheit).
* **Zwei nicht-negative Grössen statt eines Vorzeichens:** Das stimmt mit
  dem bestehenden Sparkplug-Vertrag und den Zähler-Registern überein. Damit
  gibt es keine stille Vorzeichenumkehr. Wo nur ein Wert existiert, muss die
  Vorzeichenregel ausdrücklich konfiguriert sein.
* **Keine Zahl bei unvollständigen Daten:** Ein fehlender Teilnehmer kann
  gerade viel beziehen oder einspeisen. Eine Teilsumme ist daher weder eine
  Ober- noch eine Untergrenze. Eine falsche Empfehlung kostet die Teilnehmer
  Geld und das Projekt Vertrauen.
* **Nachweisstufen statt HA-Zeitstempel:** Weder `last_updated` noch
  `last_reported` unterscheiden eine neue Messung von einem erneut
  geschriebenen alten Wert. Nur ein Zeitstempel des Messgeräts oder ein
  Erfassungszähler belegt Frische. Verworfen: Frische ab Empfangszeit oder
  ab `last_reported`; Erkennung eingefrorener Werte über «Wert ändert sich
  nicht» (falsch-positiv bei 0 W in der Nacht).
* **Eine Probe aus einer Nachricht:** Werden Bezug und Einspeisung aus
  verschiedenen Nachrichten kombiniert, entsteht ein Paar, das es nie
  gegeben hat, z. B. neuer Export mit altem Import nach einem Verlust.
  Verworfen: Kombination der jeweils letzten Einzelwerte.
* **Asymmetrische Hysterese:** Schnell aus, langsam ein. Eine verpasste
  Empfehlung kostet nichts. Eine falsche führt zu Netzbezug. Verworfen:
  festes Totband (flattert an der Schwelle), gleitender Mittelwert (zeigt
  nach einem Einbruch noch Überschuss).
* **Identität über den Broker:** Shareomat sieht nicht, wer publiziert hat.
  Nur der Broker kann ein Login an eine Kennung binden. Verworfen:
  Vertrauen in Kennungen im Topic oder Payload; signierte Payloads (eigene
  Schlüsselverwaltung pro Gerät, für den Pilot unverhältnismässig, später
  möglich).
* **Wiederverwendung von Sparkplug/WAN:** ADR-0004 und ADR-0005 liefern
  Transport, Lebenszyklus und Rebirth bereits. Es wird nur ein Adapter auf
  das kanonische Modell ergänzt, kein zweites Protokoll.

## Konsequenzen

* Mehr Struktur als eine schnelle Zusatzseite: ein neues Paket, eine neue
  Tabelle, eine zweite SQLite-Datei und später ein zweites Programm.
* **Schritt 1 wird voraussichtlich noch nie `VOLLSTÄNDIG` zeigen.** Heute
  sind alle Quellen Stufe C (Emsomat, über WAN weitergereichte
  Emsomat-Werte), und die Identität ist weder lokal (K-2) noch über WAN
  nachgewiesen. Schritt 1 liefert darum die Admin-Ansicht mit gekennzeichneter
  Teilsumme, Messreihen für die Parameter und HA-Sensoren, die korrekt
  `unknown` bzw. «unsicher» melden. Für Teilnehmer ist der Nutzen erst da,
  wenn EA-1 umgesetzt ist (oder eine Gateway-Quelle der Stufe A/B, Schritt 2)
  und die Identität nachgewiesen ist. Das ist gewollt: Eine Zahl ohne diesen
  Nachweis wäre eine Behauptung.
* Jeder LEG-relevante Messpunkt braucht eine Live-Messquelle. Sonst bleibt der
  Status `UNSICHER` (O-2).
* Quellen, die nur geänderte Werte senden (Report by Exception), sind für
  die Live-Bilanz nicht zulässig (4.1).
* Die Härtung von `WanDownlink`, `SparkplugHost` und `ParticipantRelay`
  (Abschnitt 4) ändert deren Verhalten: Unbekannte Standorte werden
  ignoriert, fehlende Werte werden nicht mehr als 0 W weitergegeben, und
  nach einem Verbindungsverlust gelten Werte erst nach einem neuen BIRTH
  wieder. Bei der Weitergabe an Emsomat über den `ParticipantRelay` wird
  geprüft, ob Emsomat damit umgehen kann.
* Die Live-Ebene hängt von der öffentlichen Schnittstelle von
  `billing_eligibility` ab. Eine Änderung dort muss Test 3 aus Abschnitt 8
  bestehen.
* Für den öffentlichen Betrieb (Schritt 5) braucht es Hosting, Betrieb und
  Zertifikate. Das ist ein eigener Entscheid.
* Jeder Teilnehmer kann unabhängig von seiner Hardware eine Live-Messquelle
  bekommen: mit Emsomat (A), mit HA ohne Emsomat (B) oder mit einem
  Smart-Meter-Reader bzw. Gateway (C). Zentral sind nur Prüfung und Bilanz,
  die Adapter bleiben klein und austauschbar.
* Art B ohne HA-Token heisst: Der Teilnehmer richtet einmal einen Blueprint
  in seinem HA ein, und Shareomat kann keine Entity-Liste anbieten. Das ist
  der Preis dafür, dass Shareomat kein Haus steuern kann.
* Mit HA-Sensoren allein bleibt eine Quelle Stufe C, wie Emsomat. Stufe A
  oder B gibt es nur mit einer Nachweis-Entity oder einem Gateway, das die
  Telegrammzeit liefert.
* Ein Quellenwechsel lässt den Messpunkt kurz `MISSING` werden, bis die
  neue Quelle die erste gültige Probe liefert. Das ist gewollt.

## Betroffene Dateien

Schritt 1 (umgesetzt):

* neu: `shareomat/live/` (`model.py`, `quality.py`, `balance.py`,
  `hysteresis.py`, `store.py` für `live.db`, `masterdata.py`, `service.py`,
  `adapters/sparkplug.py`)
* neu: Tabellen `live_sources`, `live_source_channels`, `live_meter_states` mit
  Unique-Indizes in `shareomat/database/sqlite.py` plus
  `shareomat/database/live_sources.py`
* geändert: `shareomat/sparkplug/wan_downlink.py`,
  `shareomat/sparkplug/host.py`, `shareomat/sparkplug/participant_relay.py`,
  `shareomat/sparkplug/bridge.py`, `shareomat/sparkplug/vendor/payload.py`
* neu: `shareomat/sparkplug/coordinator_store.py` (`coordinator.db`)
* neu: `shareomat/ha/live_sensors.py` (Sensoren I2);
  `ha/mqtt_discovery.py` und `ha/mqtt_runtime.py` sind unverändert
* neu: Admin-Seite `web/pages/live_energy.py` mit Templates, eigener Bereich
  in `web/navigation.py`, Recht in `security/permissions.py`
* geändert: `main.py` (Verdrahtung, Coordinator, geordneter Stopp),
  `web/server.py` (Seite, Stopp), `web/state.py`, `ha_addon/` (Kopie, Optionen,
  `timeout`, Generator)
* Tests nach Abschnitt 8
* nur gelesen, nicht geändert: `shareomat/database/billing_eligibility.py`
* `homeassistant_api` bleibt `false`

Schritt 2 (geplant): `live/adapters/gateway_mqtt.py`,
`live/adapters/ha_mqtt.py`, Gateway-Vertrag als eigenes Dokument,
HA-Blueprint, Gerätezugänge.

Unverändert gegenüber `23bf860`: Abrechnung, Matching, Tarife, Rechnungen,
Zahlungen, Login und bestehende Admin-Seiten. Der Code von Emsomat wird von
diesem ADR nicht geändert; Emsomat erhält aber bei aktivem Sparkplug die
LEG-Daten aus ADR-0004.

## Umsetzungsstand und offene Punkte (08.11.2026)

**Geprüft durch automatische Tests, nicht real:** Live-Ebene, Verwaltung,
HA-Sensoren, Sparkplug-Härtung, `coordinator.db` und der geordnete Stopp.

**Programmende:** SIGTERM und Ctrl+C lösen denselben Ablauf aus: keine neuen
Läufe, Daemon-Threads stoppen, Webserver nimmt nichts Neues an und lässt laufende
Anfragen fertig werden, laufende Abrechnung abwarten (zusammen höchstens 15 s),
Coordinator (5 s), Sparkplug, Live-Ebene, MQTT. Das Add-on hat dafür 30 s
(`timeout` in `ha_addon/config.yaml`). Was bei Fristablauf noch läuft, endet mit
dem Prozess. SQLite verwirft dann die offene Transaktion.

**Offene Audit-Befunde** (`docs/development/Live-Energie-Audit_2026-11-08.md`):

* AUD-02: Broker-Logins und ACL sind nur ein Entwurf. Solange sie fehlen, gilt
  die Identität einer Quelle als nicht nachgewiesen.
* AUD-03, AUD-04, AUD-07: vor produktiver WAN-Nutzung zu beheben.
* AUD-08 bis AUD-12, AUD-16: nachrangig.

**Realtests offen:** Broker-ACL, echtes Home Assistant mit HA-Sensoren und
`expire_after`, Entity Bridge und HA Second, Ingress, WAN zwischen zwei Standorten,
Add-on-Maske, SIGTERM und `coordinator.db` beim echten Add-on, Wirkung der
LEG-Daten auf Emsomat, 90 s gegen reale Meldeintervalle.

## Überprüfung

Neu bewerten:

* vor Schritt 4, wenn die Portal-API konkret spezifiziert wird;
* sobald mehr als eine LEG pro Installation nötig wird;
* wenn ein zentraler Betrieb (Core nicht mehr beim Verwalter zuhause)
  beschlossen wird;
* sobald reale Live-Daten zeigen, dass die Qualitätsregeln oder die
  Hysterese aus Abschnitt 3 zu streng oder zu locker sind;
* wenn der Emsomat-Auftrag EA-1 umgesetzt oder verworfen wird;
* wenn K-1 oder K-2 entschieden ist;
* vor Schritt 2, wenn die HA-Möglichkeiten für H-1 geprüft sind;
* wenn die Entity Bridge einen Messzeitstempel bekommt (H-2);
* wenn bevorzugte und alternative Live-Messquellen (Ausweichen) nötig
  werden.
