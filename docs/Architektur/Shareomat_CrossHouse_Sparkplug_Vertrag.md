# SHAREOMAT ↔ SHAREOMAT (Cross-House)

## Sparkplug B über den zentralen WAN-Relay

**Status:** Verbindliche Architekturentscheidung — **implementiert, per
Fake-Broker-Simulation UND per echtem Broker-Test vollständig verifiziert
(2026-08-22):** `shareomat/sparkplug/wan_uplink.py`, `wan_downlink.py`,
`bridge.py` (verdrahtet den lokalen Emsomat-State aus `SparkplugHost` in den
WAN Uplink — das ist die einzige Datenquelle des Uplinks, siehe Abschnitt 3),
`main.py` (Start/Stop nur wenn `sparkplug.enabled` UND `wan.enabled`, lokale
Strecke läuft unverändert weiter ohne/bei Ausfall der WAN-Strecke). Neues
Config-Feld `wan.own_participant_id` (siehe `shareomat/config.py::WanConfig`) —
verweist auf einen bestehenden `participant_id` aus `participants:`, da eine
`LegConfig` mehrere Teilnehmer einer ganzen LEG beschreibt, die lokale
Sparkplug-Strecke aber nur den einen, hier angeschlossenen Standort.
Echter Broker-Test (`tools/manual_wan_broker_test.py`, gegen die reale
Home-Assistant-Mosquitto-Instanz des Nutzers): normaler Datenfluss A→B,
Metric-Timestamp-Erhalt über den echten Wire-Transport, NDEATH-Cleanup,
Reconnect (Rebirth auf demselben Uplink-Objekt), Cross-LEG-Isolation — alle
12 Checks bestanden. Coordinator-Aggregate weiterhin nicht Teil dieser
Strecke.
**Version:** 1.0
**Voraussetzung:** Baut auf `Emsomat_Shareomat_MQTT_Vertrag.md` (lokale Strecke,
implementiert und per echtem Broker-Test verifiziert, 2026-08-22) auf — gleicher
Sparkplug-B-Vendor-Layer, gleiche Metric-Namen (`Market/ExportPowerNow`/
`Market/ImportPowerNow`), gleiche Grundprinzipien (Domain bleibt transportneutral,
ein Kommunikationsadapter pro Rolle).
**Bezug:** Konkretisiert `Kommunikations_und_Sicherheitsarchitektur.md`
Abschnitt 7 (Edge/Coordinator/Relay-Rollen), 20-24 (zentraler Endpunkt), 29-31
(Identität/LEG-Isolation) für die konkrete Sparkplug-Umsetzung.

---

# 1. Topologie

```
EMSOMAT A                                              EMSOMAT B
   ↕ Sparkplug (lokal, siehe Emsomat_Shareomat_MQTT_Vertrag.md)
SHAREOMAT A                                            SHAREOMAT B
   ↕ Sparkplug (WAN, dieses Dokument)
   ↕
mqtt.shareomat.ch (zentraler Relay-Broker)
   ↕
   ↕ Sparkplug (WAN)
SHAREOMAT B ...
```

Jede Shareomat-Instanz baut ihre WAN-Verbindung(en) selbst ausgehend auf (siehe
Abschnitt 20-21 der Kommunikationsarchitektur) — kein Haus öffnet einen
eingehenden Port, keine Haus-zu-Haus-Direktverbindung.

---

# 2. WAN-Rollen pro Shareomat (verbindlich)

Jede Shareomat-Instanz besitzt auf der WAN-Seite **zwei getrennte Rollen mit
zwei getrennten MQTT-Sessions** (analog zur bereits gebauten lokalen Strecke,
wo `SparkplugHost` und `ParticipantRelay` ebenfalls getrennte Connections
sind):

## 2.1 WAN Uplink (Sparkplug Edge Node)

- Publiziert **ausschliesslich** die eigenen, lokal bekannten
  Participant-Daten (die es von seinem lokalen `ParticipantRelay`/Emsomat
  kennt) unter der eigenen `edge_node_id`.
- **Für den WAN Edge Node wird zunächst kein Primary Host konfiguriert** —
  im Unterschied zur lokalen Strecke (wo Emsomat auf Shareomats lokales
  STATE=ONLINE wartet) gibt es auf der WAN-Seite noch keine Coordinator-Rolle,
  auf die gewartet werden müsste (Coordinator-Aggregate sind ausdrücklich
  nicht Teil dieser Strecke, siehe Abschnitt 8). Der WAN Edge Node birth't
  direkt nach Verbindungsaufbau zum zentralen Broker.

## 2.2 WAN Downlink (Sparkplug Host Application)

- Abonniert breit **innerhalb der eigenen LEG-Gruppe** (alle anderen
  Häuser derselben LEG) auf dem zentralen Broker.
- Spec-konforme Host-Application-Rolle (breite Sicht über viele Edge Nodes),
  nicht ein Abo auf einen einzelnen bekannten Edge Node wie beim lokalen
  `SparkplugHost`.
- **Keine Schreibrechte:** NCMD/DCMD sind WAN-seitig zunächst verboten
  (Abschnitt 6) — reine Sichtbarkeit, keine Fernsteuerung fremder Häuser.

---

# 3. Origin/Loop Prevention (verbindlich)

Strikt einzuhalten, keine Ausnahme:

- Der WAN Uplink publiziert **ausschliesslich** Daten des eigenen, lokalen
  Emsomat.
- Über WAN empfangene (fremde) Participant-Daten dürfen **niemals** erneut
  in den eigenen WAN Uplink gelangen — kein Weiterleiten fremder Daten an
  Dritte Häuser über die eigene Uplink-Identität.
- Über WAN empfangene Daten werden **ausschliesslich lokal** an das eigene
  Emsomat weitergegeben (über den bereits bestehenden lokalen
  `ParticipantRelay`-Mechanismus, siehe Abschnitt 5).

Damit ist der Datenfluss pro Haus streng gerichtet:

```
lokales Emsomat → lokaler ParticipantRelay → WAN Uplink → zentraler Broker
zentraler Broker → WAN Downlink → lokaler ParticipantRelay → lokales Emsomat
```

Niemals: `WAN Downlink → WAN Uplink` (das wäre eine Schleife/Re-Broadcast
fremder Daten als eigene).

---

# 4. Participant Ownership

Jeder Participant gehört **genau einem** Shareomat/Standort. Nur dieser
Shareomat darf ihn auf dem WAN-Broker veröffentlichen. Durchgesetzt über die
Topic-Struktur selbst, nicht über eine separate Registrierung: ein Participant
erscheint nur unter der `edge_node_id` seines eigenen Hauses
(`spBv1.0/<group_id>/DBIRTH/<eigene edge_node_id>/<participant_id>`) — die
Broker-ACL (Abschnitt 6) verhindert, dass ein anderes Haus unter dieser
`edge_node_id` publizieren kann.

---

# 5. Lifecycle (verbindlich)

- **Remote NDEATH:** Sparkplug-spezifikationskonform gilt eine NDEATH für
  einen fremden Edge Node als Tod **aller** unter ihm bekannten Participant-
  Devices — auch ohne individuelle DDEATH-Nachrichten (die im Fehlerfall,
  z.B. Netzwerkabbruch beim Remote-Haus, ohnehin nicht mehr gesendet werden
  können, siehe Will-Mechanismus). Der WAN Downlink muss bei Empfang einer
  NDEATH für einen fremden Edge Node **alle** zuletzt bekannten Participants
  dieses Edge Nodes lokal entfernen (`ParticipantRelay.deregister_participant()`
  für jeden).
- **Remote DDEATH:** einzelner Participant wird lokal entsprechend entfernt.
- **Wiederverbindung:** neues DBIRTH/DDATA baut den lokalen Zustand sauber
  neu auf (kein Merge mit veraltetem Zwischenstand).
- **Metric-Timestamps:** der ursprüngliche Metric-Timestamp (vom Herkunfts-
  Emsomat) bleibt über die gesamte Kette (Emsomat → lokaler Shareomat → WAN
  Uplink → zentraler Broker → WAN Downlink → lokaler ParticipantRelay →
  fremdes Emsomat) unverändert erhalten — siehe
  `Emsomat_Shareomat_MQTT_Vertrag.md` Abschnitt 27 (Metric-Timestamp getrennt
  vom Payload-Timestamp), gilt hier über alle Hops hinweg, nicht nur einen.
  Bei gepuffertem/verzögertem Store-and-Forward: `is_historical`-Flag am
  Metric verwenden (bereits im Vendor-Layer vorhanden), keine eigene
  Historical-Protokollschicht.

---

# 6. group_id, edge_node_id, ACL

## 6.1 group_id

- Stabile, **interne** LEG-ID — kein sprechender Name, keine
  Messpunktnummer, keine personenbezogene Information.
- Kandidat: `LegConfig.community_id` (bereits vorhanden, z.B. `"ZEV-001"` in
  bestehenden Testfixtures) — vor produktivem Rollout gegen echte
  `leg_config.yaml`-Werte prüfen, ob dort tatsächlich nur interne Codes
  verwendet werden, nicht Klarnamen.
- Die `group_id` strukturiert Sparkplug (wer gehört zu welcher LEG); die
  eigentliche Durchsetzung der Isolation passiert **zusätzlich und
  unabhängig davon** über die Broker-ACL (Abschnitt 6.3) — die `group_id`
  allein ist kein Sicherheitsmechanismus.

## 6.2 edge_node_id (WAN)

- Pro Shareomat-Standort eindeutig — Kandidat: die bereits in
  `docs/Architektur/Kommunikations_und_Sicherheitsarchitektur.md` Abschnitt 28
  skizzierte Shareomat-ID (`SH-000017` o.ä.), noch nicht real vergeben/
  provisioniert (Abschnitt 66 dort weiterhin offen: Credential-Provisioning).

## 6.3 Broker-ACL (WAN)

Verbindlich, unabhängig vom konkreten Broker-Produkt (Mosquitto o.ä.,
Abschnitt 66 der Kommunikationsarchitektur bleibt hier offen):

```text
Publish:    nur unter spBv1.0/<eigene group_id>/+/<eigene edge_node_id>/#
Subscribe:  nur innerhalb spBv1.0/<eigene group_id>/#
NCMD/DCMD:  WAN-seitig zunächst vollständig verboten (weder senden noch empfangen)
Cross-LEG:  jeder Zugriff (Publish UND Subscribe) auf eine fremde group_id verboten
```

Damit ist Abschnitt 29 der Kommunikationsarchitektur ("Identität aus
Authentifizierung, nicht Payload") konkret umgesetzt: ein Credential kann
technisch nicht unter einer fremden `edge_node_id` publizieren, unabhängig
davon, was im Payload steht.

---

# 7. Zentraler Endpunkt

Keine neue Entscheidung gegenüber `Kommunikations_und_Sicherheitsarchitektur.md`
Abschnitt 20-24: `mqtt.shareomat.ch`, Standard-MQTT (3.1.1 oder 5, kein
Sparkplug-spezifisches Broker-Feature nötig), TLS oder WSS über Cloudflare
Tunnel als erste Deployment-Option. Weiterhin bewusst offen (Abschnitt 66):
konkreter Broker, Hosting-Ort, Zertifikats-/Credential-Provisionierung.

---

# 8. Bewusst nicht Teil dieser Strecke

- **Coordinator-Aggregate** (LEG-Gesamtüberschuss/-bedarf, Abschnitt 34 der
  Kommunikationsarchitektur) — anderes Datenprodukt als reine
  Teilnehmer-Sichtbarkeit, eigener, späterer Schritt.
- **LEG/DemandForecast über die WAN-Strecke** — die Domain-Logik dafür
  existiert bislang nicht einmal lokal (siehe
  `Emsomat_Shareomat_MQTT_Vertrag.md`, "Keine Platzhalter"-Entscheidung
  2026-08-22).
- **Schreibzugriffe zwischen Häusern** (NCMD/DCMD WAN-seitig, Abschnitt 6.3) —
  reine Sichtbarkeit für den ersten Ausbauschritt.
