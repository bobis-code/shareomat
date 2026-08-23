# ADR-0005: Cross-House-Kommunikation (Shareomat↔Shareomat) über zentralen Sparkplug-B-Relay

## Status

Angenommen (Anwendungslogik implementiert und per Fake-Broker- sowie echtem
Broker-Test verifiziert). Zentraler Relay-Broker selbst noch nicht
produktiv betrieben — siehe
`docs/Architektur/Zentraler_WAN_Broker_Deployment_Vorschlag.md`.

## Kontext

Eine LEG kann mehrere Standorte (Häuser) mit je eigenem Shareomat umfassen
(`Kommunikations_und_Sicherheitsarchitektur.md` Abschnitt 9-11). Diese Häuser
haben keine öffentliche IP zueinander und sollen sich nicht direkt verbinden
(Abschnitt 12, "keine direkten öffentlichen IP-Verbindungen zwischen
Häusern"). ADR-0004 klärt nur die Kommunikation innerhalb eines Hauses
(Emsomat↔Shareomat); für die Sichtbarkeit zwischen Häusern fehlte bisher ein
Mechanismus.

## Problem

Ohne einen definierten Cross-House-Kanal kann ein Shareomat nicht wissen, was
andere Teilnehmer derselben LEG an einem anderen Standort gerade
produzieren/verbrauchen — eine Voraussetzung für jede LEG-weite
Optimierung. Gleichzeitig muss verhindert werden, dass (a) Häuser
verschiedener LEGs sich gegenseitig sehen, (b) ein Haus fremde Daten unter
eigenem Namen weiterverbreitet (Loop/Fälschung), und (c) ein Haus
Fernsteuerbefehle an ein fremdes Haus senden kann.

## Entscheidung

Jeder Shareomat-Standort erhält zwei zusätzliche, vom lokalen Kanal (ADR-0004)
getrennte WAN-Sparkplug-Rollen gegen einen gemeinsamen zentralen Broker:

* **WAN Uplink** (`shareomat/sparkplug/wan_uplink.py::build_wan_uplink`) —
  Sparkplug-Edge-Node, publiziert **ausschliesslich** die eigenen,
  lokal bekannten Teilnehmerdaten. Kein Primary Host auf WAN-Seite.
* **WAN Downlink** (`shareomat/sparkplug/wan_downlink.py::WanDownlink`) —
  Sparkplug-Host-Application-artige, breite Sicht innerhalb der eigenen
  LEG-Gruppe; schreibt empfangene Fremddaten in den lokalen
  `ParticipantRelay` (nie zurück in den eigenen Uplink — siehe
  Origin/Loop-Prevention unten).

Strikte Regeln, durchgesetzt sowohl anwendungsseitig als auch (geplant) über
Broker-ACL:

* Datenfluss streng gerichtet: `lokales Emsomat → ParticipantRelay →
  WAN Uplink → Broker` und `Broker → WAN Downlink → ParticipantRelay →
  lokales Emsomat` — niemals `WAN Downlink → WAN Uplink`.
* `group_id` = stabile interne LEG-ID (kein Klarname), `edge_node_id` = pro
  Standort eindeutig — Teilnehmer-Eigentümerschaft folgt strukturell aus der
  Topic-Struktur, nicht aus einer separaten Registrierung.
* NCMD/DCMD sind auf der WAN-Seite vollständig verboten (weder senden noch
  empfangen) — reine Sichtbarkeit, keine Fernsteuerung fremder Häuser.
* Remote-NDEATH entfernt alle zuletzt bekannten Devices dieses Edge-Node
  lokal (spec-konform, auch ohne individuelle DDEATH-Nachrichten).

Vollständige Spezifikation:
`docs/Architektur/Shareomat_CrossHouse_Sparkplug_Vertrag.md`.

## Begründung

Wiederverwendet dieselbe Sparkplug-B-Schicht wie ADR-0004 (gleicher
Vendor-Layer, gleiche Metric-Namen, gleiche Lifecycle-Mechanik) statt ein
zweites, eigenes WAN-Protokoll zu entwerfen. Die Uplink/Downlink-Trennung
mit strikt einseitigem Datenfluss ist eine strukturelle (nicht nur
disziplinarische) Garantie gegen Re-Broadcast fremder Daten als eigene: der
Uplink hat schlicht keine Objektreferenz auf vom Downlink empfangene Daten.

## Konsequenzen

* Pro Shareomat-Standort zwei zusätzliche MQTT-Sessions (WAN Uplink,
  WAN Downlink) neben den zwei lokalen aus ADR-0004 — vier insgesamt, sobald
  WAN aktiviert ist.
* Neues Config-Feld `wan.own_participant_id` nötig, weil eine `LegConfig`
  mehrere Teilnehmer einer ganzen LEG beschreibt, die lokale Sparkplug-
  Strecke aber nur den einen, hier angeschlossenen Standort.
* Coordinator-Aggregate (LEG-Gesamtüberschuss/-bedarf) und
  LEG/DemandForecast über WAN sind bewusst **nicht** Teil dieser
  Entscheidung — eigene, spätere Erweiterung.
* Der zentrale Broker selbst (Standort, Betreiber, ACL-Umsetzung) ist noch
  offen — siehe `Zentraler_WAN_Broker_Deployment_Vorschlag.md`. Ein Ausfall
  oder Fehlen dieses Brokers darf die lokale Emsomat↔Shareomat-Funktion
  (ADR-0004) nicht beeinträchtigen (bereits in `main.py` so verdrahtet:
  WAN-Start-Fehler setzen nur `wan_uplink`/`wan_downlink` auf `None`).

## Betroffene Dateien

* `shareomat/sparkplug/wan_uplink.py`, `wan_downlink.py`, `bridge.py`
* `shareomat/config.py` (`WanConfig`)
* `main.py` (`_setup_sparkplug`, `_stop_sparkplug`)

## Überprüfung

Neu bewerten, sobald ein echter zentraler Broker produktiv läuft und reale
Multi-Haus-Daten vorliegen — insbesondere ob die Broker-ACL-Umsetzung
(Abschnitt 6.3 des Cross-House-Vertrags) 1:1 wie hier beschrieben trägt, oder
ob die konkrete Plattform (Managed-Broker oder eigener VPS,
siehe Deployment-Vorschlag) Anpassungen erzwingt.
