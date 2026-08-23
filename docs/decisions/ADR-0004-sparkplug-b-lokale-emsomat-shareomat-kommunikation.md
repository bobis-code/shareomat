# ADR-0004: MQTT 5.0 + Eclipse Sparkplug B 3.0 für die lokale Emsomat↔Shareomat-Kommunikation

## Status

Angenommen

## Kontext

Emsomat und Shareomat sind getrennte Anwendungen (siehe
`docs/Architektur/Kommunikations_und_Sicherheitsarchitektur.md` Abschnitt
65.1-65.2: getrennte, intern modular-monolithische Anwendungen). Sie
kommunizieren lokal über MQTT als einzige Schnittstelle — vorher über
projekteigene JSON-Topics (`emsomat/market/<node_id>/state`,
`emsomat/leg/state`, siehe `market/`-Modul-Historie in Emsomat) plus
zusätzlich lose gekoppelte, teils direkte Emsomat-zu-Emsomat-Kommunikation
über dasselbe `market/`-Modul — ein Widerspruch zur eigentlich verbindlichen
Regel, dass ein Shareomat-Edge die alleinige externe Sicherheitsgrenze eines
Standortes bildet (Abschnitt 65.6).

## Problem

Ein selbst erfundenes JSON-über-MQTT-Protokoll ohne Lifecycle-Semantik
(Birth/Death, Sequenznummern, Store-and-Forward-Kennzeichnung) macht es
schwer, zuverlässig zu erkennen: Ist der Kommunikationspartner online? Ist
eine Nachricht verloren gegangen? Ist ein Wert aktuell oder veraltet
gepuffert? Diese Fragen werden für die spätere Cross-House-Erweiterung
(ADR-0005) noch wichtiger, wenn mehrere unabhängige Häuser über einen
gemeinsamen Broker kommunizieren.

## Entscheidung

Die lokale Kommunikation zwischen Emsomat und Shareomat läuft ausschliesslich
über **MQTT 5.0 + Eclipse Sparkplug Specification 3.0**. Shareomat nimmt
zwei getrennte Sparkplug-Rollen mit zwei getrennten MQTT-Sessions ein:

* **Primary Host Application** (`shareomat/sparkplug/host.py::SparkplugHost`)
  — beobachtet Emsomats Edge-Node-Lifecycle (NBIRTH/NDATA/NDEATH), publiziert
  STATE.
* **Eigene Edge-Node-Identität** (`shareomat/sparkplug/participant_relay.py::ParticipantRelay`)
  — publiziert LEG-Teilnehmer als Sparkplug-Devices, die Emsomat als Host
  konsumieren kann.

Das bestehende `market/`-Modul (Emsomat-seitig) wird auf reine Domain-Logik
reduziert (keine eigene MQTT-Anbindung mehr) — Transport läuft ausschliesslich
über die Sparkplug-Schicht.

Vollständige Spezifikation (Topic-Struktur, QoS/Retain pro Message-Type,
bdSeq/seq, Rebirth-Mechanismus): `docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md`.

## Begründung

Sparkplug B ist ein etablierter, normativer IIoT-Standard mit genau den
fehlenden Eigenschaften: definiertes Birth/Death-Lifecycle, MQTT-Will für
unsauberes Trennen, Sequenznummern gegen Nachrichtenverlust, ein
`is_historical`-Flag für gepufferte Daten. Das deckt sich mit dem
Leitprinzip "keinen neuen Standard erfinden, wenn bereits ein etablierter
existiert" (`AGENTS.md`, "Standards-Referenzen"). Alternative (EEBUS/SPINE)
wurde geprüft und verworfen — EEBUS ist für die Geräte-Ebene innerhalb eines
Hauses (Wärmepumpe, Wallbox) sinnvoll, nicht für die Haus-zu-Haus- bzw.
Emsomat-zu-Shareomat-Ebene, für die es nicht entworfen ist.

## Konsequenzen

* Neue Abhängigkeit: `protobuf` (Sparkplug-B-Payload-Encoding) in beiden
  Projekten (`manifest.json`/`requirements.txt`).
* `pysparkplug` (dritte Bibliothek) konnte wegen eines Versionskonflikts
  mit dem bereits gepinnten `paho-mqtt==2.1.0` nicht direkt verwendet
  werden — nur der MQTT-Client-freie Teil wurde vendored
  (`sparkplug/vendor/`, Apache-2.0, siehe `NOTICE.md` dort).
* `NodeMarketState` (Domain-Modell) verliert `score`/`confidence`/
  `window_from`/`window_to`/`state_id` — Felder, die nur im Rahmen der alten,
  jetzt ersetzten MQTT-Anbindung existierten und nie tatsächlich
  funktional genutzt wurden (`state_id` erzeugte sich pro Zyklus neu und
  machte den beabsichtigten Dedup-Check wirkungslos).
* Zwei getrennte MQTT-Sessions pro Shareomat-Standort (Host + eigene
  Edge-Node) statt einer — mehr Verbindungs-Overhead, aber
  spezifikationskonform (ein Sparkplug-STATE-Will kann keine zweite Rolle
  auf derselben Session teilen).

## Betroffene Dateien

* Shareomat: `shareomat/sparkplug/`, `shareomat/config.py`
  (`SparkplugConfig`), `main.py`
* Emsomat: `Emsomat/sparkplug/`, `Emsomat/market/adapter.py`,
  `Emsomat/market/models.py`, `Emsomat/manager.py`

## Überprüfung

Neu bewerten, falls Sparkplug B durch eine neue Major-Version ersetzt wird
oder falls sich herausstellt, dass die zwei-Sessions-pro-Standort-Kosten bei
sehr vielen gleichzeitigen Installationen zum echten Engpass werden.
