# Live-Energie – zentrale Arbeitsliste

Verbindliche Übersicht für die LEG-Liveplattform und die damit verbundenen
Anpassungen in Emsomat und der HA Entity Bridge. Architektur:
[ADR-0007](decisions/ADR-0007-live-energie-ebene-und-teilnehmerportal.md).
Zielbild: [Zukunft.md](Zukunft.md) §12.

Angelegt am 07.11.2026. Es gibt keine zweite Liste für diese Themen. Die
lokale `TODO.md` verweist für die Liveplattform hierher.

## Arbeitsweise

* Es wird immer nur **eine** Aufgabe gleichzeitig bearbeitet, danach wird
  pausiert, bis der Projektverantwortliche die nächste freigibt.
* Ablauf pro Aufgabe: Istzustand prüfen → Fehler bzw. Lücke nachweisen →
  gezielt korrigieren → Regressionstests → vollständige Testsuite →
  Ergebnis hier eintragen → Pause.
* Eine Aufgabe ist erst `ERLEDIGT`, wenn alle Prüfkriterien nachgewiesen
  sind. Ist der Code fertig, fehlt aber ein realer Test (echtes HA, echter
  Broker, echtes Gerät), ist der Status `TEST AUSSTEHEND`.
* Neue Befunde bekommen eine neue ID (ab LE-14) mit Priorität und
  Abhängigkeiten. Ein neuer Befund löst nicht automatisch eine neue
  Entwicklungsrunde aus.
* Grenzen: keine Commits, kein Push, keine Änderung der Git-History, keine
  Installationen, keine Änderung an produktivem HA, MQTT oder Proxmox,
  keine Lizenzänderung, Abrechnungslogik nur mit ausdrücklicher Freigabe.
  Shareomat, Emsomat und Entity Bridge nicht gleichzeitig umbauen.
  Bestehende uncommittete Änderungen bleiben erhalten.

Status: `OFFEN`, `IN ARBEIT`, `BLOCKIERT`, `TEST AUSSTEHEND`, `ERLEDIGT`.

## Aktueller Schwerpunkt (ab 08.11.2026)

Es wird ausschliesslich an Shareomat gearbeitet. Ziel: die Verwaltung um
Live-Energie erweitern und danach einen ersten Pilot über die HA Entity Bridge
und HA Second ermöglichen. Reihenfolge:

1. LE-04: ungültige Sparkplug-Messwerte und `is_null`
2. LE-05: Sparkplug-Sessions, Quellenidentität, Broker-Ausfälle (LE-14 dabei
   prüfen)
3. LE-06: Messquellen ohne separaten Einspeisesensor
4. LE-07: Live-Datenmodell, Qualitätsprüfung, LEG-Bilanz
5. LE-08: Verwaltung mit Live-Messquellen-Zuordnung
6. LE-09: HA-Live-Status-Sensoren
7. LE-12: Pilot über HA Second und Entity Bridge

Für später vorgemerkt: LE-19 (EV-PID-Regelung, enthält LE-16) und LE-20
(Sensorprüfer). Alle übrigen Aufgaben bleiben unverändert bestehen.

## Übersicht

| ID | Projekt | Aufgabe | Priorität | Status | Abhängig von |
|---|---|---|---|---|---|
| LE-01 | Emsomat | EV-Anschlussschutz bei ungültiger Netzleistung | HOCH | TEST AUSSTEHEND | – |
| LE-02 | Emsomat | PID-Test und ungültige Messwerte | MITTEL | ERLEDIGT | – |
| LE-03 | Emsomat | Sparkplug EA-1 abschliessen | HOCH | TEST AUSSTEHEND | – |
| LE-04 | Shareomat | Ungültige Werte über `bridge.py` und `WanDownlink` erhalten (K-3) | HOCH | ERLEDIGT | LE-03 |
| LE-05 | Shareomat | Sparkplug-Sitzungen und Quellenidentität (S-L4, S-L5, K-1, K-2) | HOCH | TEST AUSSTEHEND | LE-04 |
| LE-06 | Shareomat | Anschlüsse ohne separaten Einspeisesensor | MITTEL | ERLEDIGT | – (fachlich vor LE-07) |
| LE-07 | Shareomat | Live-Modul (Datenmodell, Prüfung, Bilanz) | HOCH | ERLEDIGT | LE-04, LE-05, LE-06 |
| LE-08 | Shareomat | Verwaltung «Live-Energie», Live-Messquelle zuordnen | HOCH | TEST AUSSTEHEND | LE-07 |
| LE-09 | Shareomat | HA-Sensoren LEG-Status und Integrationsprüfung | MITTEL | TEST AUSSTEHEND | LE-07, LE-08 |
| LE-10 | Shareomat (+ HA-Blueprint) | Home Assistant als direkte Quelle (`ha_mqtt`) | MITTEL | OFFEN | LE-07, LE-08 |
| LE-11 | Shareomat | Externes Gateway / AMS Reader (`gateway_mqtt`) | MITTEL | OFFEN | LE-07, LE-08, Broker-Entscheid |
| LE-12 | alle | Nachbar-Pilot | MITTEL | OFFEN | LE-09, LE-10 oder LE-11, O-3 |
| LE-13 | Shareomat | Teilnehmerportal | NIEDRIG | OFFEN | LE-12, Security-Review |
| LE-14 | Shareomat | Vendor-Code: NDEATH-Zeitstempel beim Lesen vertauscht | NIEDRIG | ERLEDIGT | – |
| LE-15 | Emsomat | Eingehende Teilnehmerwerte ohne Zeitstempel bekommen «jetzt» | NIEDRIG | OFFEN | – |
| LE-16 | Emsomat | EV `fast`/`pv_min` erzwingen `min_charge_a` auch ohne Anschlussreserve (Lösung in LE-19) | HOCH | OFFEN | LE-19 |
| LE-17 | Emsomat | Kein Bezugssensor konfiguriert = EV ohne Anschlussschutz | MITTEL | OFFEN | Entscheid |
| LE-18 | Emsomat | `grid_power_max_age_s` nicht konfigurierbar | NIEDRIG | OFFEN | LE-20 |
| LE-19 | Emsomat | EV-Regelung mit PID, Phasen aus OUT, ROUT-Stufen ohne Messung | HOCH | OFFEN | – |
| LE-20 | Emsomat | Sensorprüfer als Library-Objekt mit Ausgang IO BAD | MITTEL | OFFEN | – |

---

## LE-01 – Emsomat: EV-Anschlussschutz

* **Projekt:** Emsomat · **Priorität:** HOCH · **Status:** TEST AUSSTEHEND
* **Ursache:** Im EA-1-Bericht festgestellt: `manager.get_market_import_now_w()`
  liefert bei einem ungültigen Netzleistungssensor 0 W. Die EV-Logik
  könnte damit den Anschlussschutz aushebeln und zusätzliche Ladeleistung
  freigeben.
* **Ziel:** Bei fehlender, ungültiger oder veralteter Netzleistung verhält
  sich die EV-Logik sicher. Die normale Ladeoptimierung bleibt unverändert.
* **Abhängigkeiten:** keine.
* **Prüfkriterien:** Fehler mit Test reproduziert; sicheres Verhalten
  definiert und umgesetzt; gültige 0 W unverändert; bestehende Schutz- und
  Verriegelungslogik berücksichtigt; vollständige Emsomat-Testsuite grün.
  Keine realen Ladeversuche.
* **Ergebnis (08.11.2026, Commit `5c72b08`):**
  * **Ursache bestätigt:** `ev_logic.py` las `get_market_import_now_w()`.
    Bei `unavailable`, `unknown`, leerem Zustand, NaN oder keiner Zahl gab
    der Getter 0.0 zurück. Die Hauslast galt damit als 0 A, und FAST gab
    16 A frei statt 6 A (Reproduktion am unveränderten Code: gültige
    4600 W → 6 A; `unavailable`/`unknown`/`nan` → 16 A). Veraltete Werte
    wurden gar nicht erkannt.
  * **Sicheres Verhalten:** Ist der Bezugssensor konfiguriert, aber
    ungültig oder veraltet (HA-Zeitstempel älter als 120 s), ist der
    Anschlussschutz nicht prüfbar. Dann gilt **EV aus (0 A)**, sofort und
    auch gegen die Anti-Cycling-Sperre. Das Wiedereinschalten ist danach
    normal durch Anti-Cycling gedämpft. Ist die Einspeisung ungültig, wird
    kein PV-Überschuss angenommen. Ein negativer Bezugswert gilt wie bisher
    als 0 W. Ohne konfigurierten Bezugssensor bleibt das Verhalten wie
    bisher, wird aber als `inactive` ausgewiesen (Entscheid LE-17).
  * **Geänderte Dateien (Emsomat):** `Emsomat/addon/ev_logic.py`
    (`assess_grid_power()`, Schutz-Stopp, Advice-Feld
    `ev_grid_protection`), `Emsomat/sparkplug/power_sample.py` (negativer
    Wert behält den Zeitstempel, für Sparkplug weiterhin ungültig),
    `Emsomat/manager.py` (nur Docstring), `docs/technik_de.md`. Neu:
    `tests/test_ev_grid_protection.py` (23 Tests); ergänzt:
    `tests/test_sparkplug_power_sample.py` (1 Test).
  * **Testnachweis:** neue Tests rot vor der Korrektur (Reproduktion),
    danach grün. Vollständige Emsomat-Suite: **1347 bestanden** (vorher
    1323). `manager.py` nur syntaktisch geprüft (ohne HA nicht
    importierbar).
  * **Unverändert:** Ladeoptimierung bei gültigen Sensoren (PV, PV_MIN,
    FAST, Phasen, kW-Einheiten durch Tests abgesichert),
    `async_apply_ev_outputs()`, PID-Test (LE-02).
  * **Ausstehend:** Realtest in einer HA-Testinstanz mit simuliertem
    Sensorausfall, ohne reale Ladung. Erst danach `ERLEDIGT`.
* **Neue Befunde:** LE-16, LE-17, LE-18.
* **ADR:** ADR-0007 §10 (Stand EA-1, «Unverändert, eigener Auftrag»).

## LE-02 – Emsomat: PID-Test und ungültige Messwerte

* **Projekt:** Emsomat · **Priorität:** MITTEL · **Status:** ERLEDIGT
* **Ursache:** `_read_pid_shadow_grid_power_w()` nimmt bei der Standardquelle
  `get_market_import_now_w() − get_market_export_now_w()`. Ein ungültiger
  Sensor ergibt dort 0 W. Die eigene Beschreibung verlangt «ungültige PV
  (HOLD), nicht 0 W Netzbezug».
* **Ziel:** Ungültige Werte führen zu `pv_valid = False`. Eine gültige
  0-W-Messung bleibt gültig.
* **Abhängigkeiten:** keine (kann die Hilfsfunktion aus LE-01 nutzen).
* **Prüfkriterien:** Beschreibung und Verhalten stimmen überein;
  Regressionstests; keine Änderung an der PID-Regelung selbst;
  vollständige Testsuite grün.
* **Ergebnis (08.11.2026, Commit `cbb4da1`):**
  * **Ursache bestätigt:** Die Standardquelle «Import − Export» las die
    Getter, die bei einem ungültigen Sensor 0.0 liefern. Mit `unavailable`,
    `unknown`, leer, keiner Zahl, NaN, Inf, fehlender Entity oder ohne
    konfigurierten Sensor bekam der Regler eine Zahl statt «ungültig».
  * **Korrektur:** neu `net_grid_power_w()` in
    `Emsomat/sparkplug/power_sample.py`. `manager.py` nutzt sie für die
    Standardquelle. Ungültig → `None` → `pv_valid = False` → HOLD. Der direkt
    gewählte Sensor und `lib/pid` sind unverändert.
  * **Verhalten:** Gültige 0 W bleiben eine Messung. Ein negativer Wert zählt
    wie bisher als 0 W. Neu: Fehlt einer der beiden Sensoren in der
    Konfiguration, ist die Standardquelle ungültig. Dann muss im Test ein
    Sensor direkt gewählt werden.
  * **Dateien (Emsomat):** `Emsomat/manager.py`,
    `Emsomat/sparkplug/power_sample.py`, `docs/technik_de.md`, neu
    `tests/test_pid_shadow_input.py` (34 Tests).
  * **Testnachweis:** 19 Tests schlugen vor der Korrektur fehl, 16 davon
    gegen den echten Code aus `manager.py` (per `ast` geladen, weil das Modul
    ohne HA nicht importierbar ist). Danach alle grün. Vollständige
    Emsomat-Suite: **1381 bestanden** (vorher 1347).
  * **Nicht Teil von LE-02:** `tick_battery_pid_shadow()` wurde nicht als Ganzes ausgeführt, die
    Wirkung ist auf Ebene Eingang und Regler getestet. Der PID-Test steuert
    keine Hardware.
* **ADR:** ADR-0007 §10.

## LE-03 – Emsomat: Sparkplug EA-1 abschliessen

* **Projekt:** Emsomat · **Priorität:** HOCH · **Status:** TEST AUSSTEHEND
* **Ursache:** S-L2, S-L3 (ADR-0007 Kontext).
* **Bereits umgesetzt (07.11.2026, Commit `e6be1c7`, nicht neu schreiben):**
  `Emsomat/sparkplug/power_sample.py` (neu); `edge_node.py`: `is_null`
  statt 0 W, auch im NBIRTH vor dem ersten Wert; beide Werte immer in einer
  Nachricht; Metric-Zeitstempel = `last_reported` (Fallback
  `last_updated`), der ältere von beiden; `Market/PowerTimeSource`;
  neues NBIRTH nach MQTT-Unterbruch; eingehende `is_null`/NaN/negative
  Werte nicht als 0 W. `mqtt/adapter.py`: Verbindungs-Listener.
  `market/adapter.py`: `clear_local_state()`. Testnachweis: 1323 Tests
  bestanden (vorher 1278), Gegenprobe mit unverändertem Shareomat-Decoder.
* **Noch zu prüfen:**
  * `bdSeq` je Verbindung: Heute bleibt die `bdSeq` beim Wiederverbinden
    gleich (passt zum Will, aber nicht streng nach Sparkplug-Spezifikation).
    Entscheiden, ob das reicht oder ob das Will vor dem Reconnect neu gesetzt
    werden muss.
  * Real-Test in einer echten HA-Testumgebung: `unknown`/`unavailable`,
    Reconnect, NBIRTH, Zeitstempel. `manager.py` ist in der Testumgebung
    ohne HA nicht importierbar und darum nur syntaktisch geprüft.
  * Kompatibilität mit Shareomat nach LE-04 erneut prüfen.
* **Abhängigkeiten:** echte HA-Testumgebung (nicht produktiv).
* **Prüfkriterien:** Realtest dokumentiert; `bdSeq`-Entscheid festgehalten.
* **ADR:** ADR-0007 §10 «Stand EA-1».

## LE-04 – Shareomat: Ungültige Werte über die Bridge erhalten

* **Projekt:** Shareomat · **Priorität:** HOCH · **Status:** ERLEDIGT
* **Ursache:** `sparkplug/bridge.py` (`mirror_host_to_uplink`) und
  `WanDownlink._extract_power()` machen mit `float(value or 0.0)` aus
  einem ungültigen Wert 0 W (S-L1). `ParticipantRelay` gibt nur zwei
  Zahlen weiter, keine Zeitquelle (K-3).
* **Ziel:** `is_null` bleibt erhalten, Quellenzeitstempel und
  `PowerTimeSource` werden weitergegeben, Import und Export konsistent.
  Bestehende Sparkplug-/WAN-Funktionen bleiben erhalten.
* **Abhängigkeiten:** LE-03 (Emsomat-Format).
* **Prüfkriterien:** Regressionstests; vollständige Shareomat-Testsuite
  grün.
* **Ergebnis (08.11.2026, Commit `13a0f8c`; vollständige Suite: 1174 bestanden, 4 übersprungen):** `is_null`, NaN, Inf und negative Werte werden in
  `bridge.py`, `WanDownlink` und `ParticipantRelay` nie zu 0 W. Zeitstempel und
  `Market/PowerTimeSource` gehen mit. Ein gültiger Wert ohne Zeitstempel wird
  nicht mit «jetzt» versehen. Geht Emsomat weg, wird der Teilnehmer auf dem WAN
  entfernt. Tests: `tests/test_live_transport.py`.
* **Hinweis Inbetriebnahme:** Eine Emsomat-Installation ohne den Stand aus LE-03
  liefert bei Sensorausfall weiterhin 0 W. Emsomat muss dafür aktualisiert sein.
* **ADR:** ADR-0007 Kontext S-L1, §4 Härtung, §10 K-3.

## LE-05 – Shareomat: Sparkplug-Sitzungen und Quellenidentität

* **Projekt:** Shareomat · **Priorität:** HOCH · **Status:** TEST AUSSTEHEND
* **Ursache:** S-L4, S-L5, K-1, K-2.
* **Ziel:** Verbindungsabbruch erkennen, alte Werte ungültig machen,
  Sitzung und `seq` prüfen, Rebirth und Wiederanlauf behandeln,
  Quellenidentität über den berechtigten Kanal, nicht registrierte Quellen
  zurückweisen. Keine standortübergreifenden Steuerbefehle.
* **Abhängigkeiten:** LE-04.
* **Grenzen:** Die produktive Mosquitto-Konfiguration wird nicht geändert.
  Broker-ACLs werden zuerst isoliert geprüft. Änderungen am produktiven
  Broker brauchen eine Freigabe.
* **Prüfkriterien:** Tests nach ADR-0007 §8 Nr. 5 und 6; vollständige
  Testsuite grün; ACL-Nachweis isoliert dokumentiert.
* **Ergebnis (08.11.2026, Commit `13a0f8c`; vollständige Suite: 1174 bestanden, 4 übersprungen):** Verbindungsverlust macht Werte sofort ungültig, gültig
  erst wieder mit neuem BIRTH. Neues BIRTH löscht alte Metrics, `seq`-Lücken
  fordern lokal höchstens einmal pro Minute einen Rebirth an. Retained, zu
  grosse und zu lange Nachrichten werden verworfen. Der `WanDownlink` nimmt nur
  in «Live-Energie» registrierte Paare an (K-1: Uplink wiederholt Births alle
  5 Minuten, keine Befehle zwischen Standorten).
* **Ausstehend:** ACL-Negativtest auf einem isolierten Test-Broker (K-2). Bis
  dahin bleibt «Identität nachgewiesen» bewusst leer.
* **ADR:** ADR-0007 §4.1, §4.2, §10 K-1, K-2.

## LE-06 – Anschlüsse ohne separaten Einspeisesensor

* **Projekt:** Shareomat (Modell), später auch Emsomat · **Priorität:** MITTEL
* **Status:** ERLEDIGT
* **Ursache:** Nicht jeder Abnehmer hat PV. Es gibt drei Fälle: Zähler mit
  Bezug und Einspeisung; ein Nettosensor mit Vorzeichen; ein reiner
  Bezugssensor bei einem Anschluss, an dem Einspeisung nachweislich
  ausgeschlossen ist.
* **Ziel:** fachlich saubere Abbildung im einheitlichen Messwertmodell. Ein
  fehlender Sensor wird nie ohne ausdrückliche und geprüfte Konfiguration
  als 0 W gelesen.
* **Abhängigkeiten:** fachlich vor LE-07 festzulegen (Datenmodell).
* **Prüfkriterien:** Ergänzung in ADR-0007 (§2 `measurement_mode`, §3);
  Tests in LE-07.
* **Ergebnis (08.11.2026, Commit `13a0f8c`; vollständige Suite: 1174 bestanden, 4 übersprungen):** Messarten `import_export`, `signed` und `import_only`.
  `import_only` nur für Verbraucher-Messpunkte und mit Begründung. Eine fehlende
  Live-Quelle oder Einspeisung wird nie als 0 W gewertet (ADR-0007 §3.7).
* **ADR:** ADR-0007 §2, §3.

## LE-07 – Shareomat: Live-Modul

* **Projekt:** Shareomat · **Priorität:** HOCH · **Status:** ERLEDIGT
* **Ziel:** `live_sources` und `live_source_channels` (vollständiges
  Modell), `live.db`, Rohproben, zentrale Qualitätsprüfung, Aktualität und
  Identität, LEG-Bilanz, Hysterese, `LiveStatusService`. Unvollständige,
  ungültige und veraltete Messungen nach ADR-0007 §3.
* **Abhängigkeiten:** LE-04, LE-05, LE-06.
* **Grenzen:** keine Änderung an der Abrechnungsberechnung.
* **Prüfkriterien:** Tests ADR-0007 §8 Nr. 1–9, insbesondere die Trennung
  von Live-Daten und Abrechnung; vollständige Testsuite grün.
* **Ergebnis (08.11.2026, Commit `13a0f8c`; vollständige Suite: 1174 bestanden, 4 übersprungen):** Paket `shareomat/live/`, Schema-Version 15, `live.db`
  neben `shareomat.db`. Architekturtests sichern, dass Abrechnung und
  Datenbankcode das Live-Paket nicht importieren. Abrechnungscode wurde nicht
  geändert. Tests: `tests/test_live_core.py`, `tests/test_main_live_wiring.py`.
  `max_age` ist neu 90 s, weil Emsomat alle 30 s sendet.
* **ADR:** ADR-0007 §2, §3, §4.

## LE-08 – Verwaltung: Live-Messquelle zuordnen

* **Projekt:** Shareomat · **Priorität:** HOCH · **Status:** TEST AUSSTEHEND
* **Ziel:** Eigener Menübereich LIVE-ENERGIE nach den bestehenden Gruppen
  (Übersicht, Messquellen, Diagnose): Übersicht aller offiziellen
  LEG-Messpunkte, aktuelle Live-Messquelle, Kommunikation, Qualität,
  letzte gültige Messung; «Live-Messquelle zuordnen» mit
  `testing → active → retired`, kontrollierter Wechsel. Die drei Arten sind
  in der Oberfläche vorgesehen. In Schritt 1 sind `emsomat_local` und
  `wan_sparkplug` zuordenbar, die anderen Arten folgen mit LE-10 und LE-11.
* **Abhängigkeiten:** LE-07.
* **Grenzen:** Die übrige Verwaltung bleibt unverändert.
* **Prüfkriterien:** Tests ADR-0007 §8 Nr. 8; CSRF, Admin-Recht, Audit;
  vollständige Testsuite grün; Sichtprüfung in HA (Ingress).
* **Ergebnis (08.11.2026, Commit `13a0f8c`; vollständige Suite: 1174 bestanden, 4 übersprungen):** Übersicht, Zuordnen, Prüfen, Aktivieren, Ändern,
  Deaktivieren und Identitätsnachweis in der Oberfläche. Über den echten
  Webserver geprüft (ohne Login Umleitung, ohne CSRF keine Änderung). Ohne Broker
  nutzbar mit `tools/live_energy_demo.py`.
* **Korrektur (08.11.2026, nicht committet; vollständige Suite: 1227 bestanden,
  4 übersprungen):** Eigener Menübereich statt Eintrag unter BETRIEB, bestehende
  Menüpunkte unverändert. Ansichten Übersicht, Messquellen und Diagnose. Neue
  Auswahl «Keine Live-Messung vorhanden», gespeichert in `live_meter_states`
  (Schema 16, `shareomat.db`) und jederzeit änderbar. Eine fehlende Live-Messung
  bleibt `MISSING`, nie 0 W. «Externer Standort / WAN-Sparkplug» ist eine eigene
  Auswahl. Alle Menüseiten über den echten Webserver geprüft.
* **Ausstehend:** Sichtprüfung in HA hinter Ingress.
* **ADR:** ADR-0007 §2, §5.1.

## LE-09 – HA-Sensoren und Integrationsprüfung

* **Projekt:** Shareomat · **Priorität:** MITTEL · **Status:** TEST AUSSTEHEND
* **Ziel:** `sensor.shareomat_leg_ueberschuss` und
  `sensor.shareomat_leg_live_status`. Keine falsche Überschusszahl bei
  unsicherer Messung. Tests für `unknown`/`unavailable`, Wiederanlauf,
  gültige Nullwerte, mehrere Messpunkte und Quellen. Hauptprojekt und
  `ha_addon/` synchron (`tools/prepare_addon.py --check`).
* **Abhängigkeiten:** LE-07, LE-08.
* **Prüfkriterien:** vollständige Testsuite grün. Reale Tests ohne
  autorisierte Testumgebung werden hier als ausstehend aufgeführt.
* **Ergebnis (08.11.2026, Commit `13a0f8c`; vollständige Suite: 1174 bestanden, 4 übersprungen):** 9 Sensoren per MQTT Discovery (`ha/live_sensors.py`),
  ohne Befehlskanal. `ha_addon/` synchron. Die eigene gültige Einzelmessung ist
  auch bei unvollständiger Bilanz sichtbar, der LEG-Überschuss nur bei
  `VOLLSTÄNDIG`.
* **Ausstehend:** Realtest mit Mosquitto und HA: Entities erscheinen, «None» wird
  zu `unknown`, Wiederanlauf, Übernahme durch die Entity Bridge. Ausserdem an
  realen Daten prüfen, ob 90 s zu den Meldeintervallen der Sensoren passen.
* **ADR:** ADR-0007 §5 I2.

## LE-10 – Home Assistant als direkte Quelle

* **Projekt:** Shareomat, HA-Blueprint · **Priorität:** MITTEL · **Status:** OFFEN
* **Ziel:** HA sendet ausgewählte Sensoren selbst per MQTT (H-1),
  Shareomat bekommt keinen HA-Zugriff. Zuerst prüfen, was HA tatsächlich
  kann: Blueprint/Automation, Zugriff auf `last_reported` in Templates,
  MQTT-Login und TLS. Danach: Senderidentität, einheitliche Struktur
  (Gateway-Vertrag), Aktualität, Zuordnung von Import und Export,
  Gültigkeits- und Ausfallsignale. Muss auch beim Teilnehmer mit eigenem HA
  funktionieren.
* **Abhängigkeiten:** LE-07, LE-08.
* **Prüfkriterien:** Machbarkeitsprüfung dokumentiert; Tests ADR-0007 §8
  Nr. 10; Realtest in einer HA-Testumgebung.
* **ADR:** ADR-0007 §4.3, §10 H-1.

## LE-11 – Externes Gateway / AMS Reader

* **Projekt:** Shareomat · **Priorität:** MITTEL · **Status:** OFFEN
* **Ziel:** Anbindung von AMS Reader und anderen Gateways über einen
  festen Vertrag; MQTT/TLS oder eine andere sichere Übertragung;
  individuelle Gerätezugänge; Server- und Geräteauthentisierung; Rechte nur
  für eigene Messwerte; kein Zugriff auf die Verwaltung. Kosten und
  Abhängigkeit externer Dienste prüfen. Keine Broker-Lösung vorschnell fest
  einbauen.
* **Abhängigkeiten:** LE-07, LE-08, Entscheid zu Broker/Hosting.
* **Prüfkriterien:** Gateway-Vertrag als Dokument; ACL-Negativtests;
  Realtest mit einem Testgerät.
* **ADR:** ADR-0007 §4.4.

## LE-12 – Nachbar-Pilot

* **Projekt:** alle · **Priorität:** MITTEL · **Status:** OFFEN
* **Ziel:** eigene Live-Messquelle beim Nachbarn, sichere Übertragung,
  eindeutige Zuordnung zum Messpunkt, korrekte LEG-Bilanz; optional HA
  Second mit Entity Bridge für die Anzeige (H-2).
* **Abhängigkeiten:** LE-09, LE-10 oder LE-11; Datenschutz und Freigabe
  der Gemeinschaftswerte (O-3) **vorher**.
* **Grenzen:** kein eigenständiger Eingriff in reale Nachbarsysteme.
* **ADR:** ADR-0007 §9 Schritt 3, §10 O-3, H-2.

## LE-13 – Teilnehmerportal

* **Projekt:** Shareomat · **Priorität:** NIEDRIG · **Status:** OFFEN
* **Ziel:** separater Portal-Dienst, eigene Teilnehmerkonten, strikt
  begrenzte API, nur eigene Daten und freigegebene LEG-Werte, HTTPS,
  unabhängiger Security-Review vor der Internetfreigabe. Die Verwaltung
  bleibt geschützt.
* **Abhängigkeiten:** LE-12, Security-Review.
* **ADR:** ADR-0007 §1, §5.2, §9 Schritte 4–5.

## LE-14 – Shareomat: NDEATH-Zeitstempel im Vendor-Code

* **Projekt:** Shareomat · **Priorität:** NIEDRIG · **Status:** ERLEDIGT
* **Ursache:** `shareomat/sparkplug/vendor/payload.py:176`: Die Bedingung
  ist vertauscht (`… if not payload.HasField("timestamp") else None`). Der
  Zeitstempel einer NDEATH geht beim Lesen verloren. Heute wird er nicht
  verwendet.
* **Ergebnis (08.11.2026, Commit `13a0f8c`; vollständige Suite: 1174 bestanden, 4 übersprungen):** Bedingung korrigiert, Test in
  `tests/test_live_transport.py`.

## LE-15 – Emsomat: Teilnehmerwerte ohne Zeitstempel

* **Projekt:** Emsomat · **Priorität:** NIEDRIG · **Status:** OFFEN
* **Ursache:** `EdgeNodeAdapter._metrics_to_power()` setzt bei eingehenden
  Teilnehmerwerten ohne Metric-Zeitstempel die aktuelle Zeit ein. Ein
  solcher Wert wirkt damit frisch, obwohl sein Alter unbekannt ist. Heute
  sendet Shareomat immer einen Zeitstempel.
* **Abhängigkeiten:** sinnvoll nach LE-04.

---

## LE-16 – Emsomat: EV-Mindeststrom ohne Anschlussreserve

* **Projekt:** Emsomat · **Priorität:** HOCH · **Status:** OFFEN
* **Stand 08.11.2026:** Istanalyse erstellt:
  Emsomat `docs/development/EV_Ladelogik_Istanalyse_LE-16.md`. Wichtigster
  Befund: Der Bezugssensor enthält den eigenen Ladestrom, die
  Reserveformel zählt ihn doppelt (bei 3 Phasen dreifach). Der Iststrom der
  Wallbox ist unbekannt. **Entschieden:** Die Reserve rechnet mit einer neuen,
  optionalen Ist-Leistung der Wallbox. Die Umsetzung gehört zu LE-19.
* **Ursache:** Bei LE-01 gefunden. `fast` rechnet
  `_clamp(available_a, min_a, max_a)`, `pv_min`
  `_clamp(max(min_a, …), min_a, max_a)`. Ist die freie Anschlussreserve
  kleiner als `min_charge_a`, wird trotzdem `min_charge_a` freigegeben.
  Beispiel: 4600 W Hauslast = 20 A, frei 5 A → FAST gibt 6 A frei. Das gilt
  auch bei gültigem Sensor.
* **Ziel:** Entscheiden, ob der Anschlussschutz Vorrang vor dem
  Kalender-Mindestladen hat (Vorschlag: ja, dann 0 A statt `min_a`), und
  das gezielt korrigieren.
* **Abhängigkeiten:** keine, betrifft dieselbe Datei wie LE-01.
* **Prüfkriterien:** Regressionstests; heutiger Test
  `test_fast_mode_respects_house_load` dokumentiert das alte Verhalten und
  wird dann angepasst.

## LE-17 – Emsomat: EV ohne Bezugssensor

* **Projekt:** Emsomat · **Priorität:** MITTEL · **Status:** OFFEN
* **Ursache:** Der Bezugssensor ist in der Konfiguration optional. Ohne
  ihn rechnet die EV-Logik wie bisher mit 0 A Hauslast, also ohne
  Anschlussschutz. Seit LE-01 ist das als `inactive` ausgewiesen.
* **Ziel:** Entscheid, ob das so bleibt (z. B. wenn die Wallbox selbst
  eine Lastbegrenzung hat) oder ob EV-Laden ohne Bezugssensor auf
  `min_charge_a` begrenzt bzw. eine Warnung in der Konfiguration angezeigt
  wird.
* **Abhängigkeiten:** Entscheid des Projektverantwortlichen.

## LE-18 – Emsomat: Grenzalter der Netzleistung konfigurierbar

* **Projekt:** Emsomat · **Priorität:** NIEDRIG · **Status:** OFFEN
* **Ursache:** `grid_power_max_age_s` (Standard 120 s) wird von
  `manager.get_ev_cfg()` nicht durchgereicht und ist im Betrieb fest. Für
  Sensoren, die selten melden, könnte ein anderer Wert nötig sein.
* **Abhängigkeiten:** LE-20 (die Grenze wird dort ein Parameter), Realtest
  LE-01 (zeigt, ob 120 s passen).

## LE-19 – Emsomat: EV-Regelung mit PID

* **Projekt:** Emsomat · **Priorität:** HOCH · **Status:** OFFEN
* **Ursache:** Die EV-Logik springt zwischen Strömen und Phasen (Istanalyse
  LE-16). Die Phasenregel «≥ 10 A → 3 Phasen» läuft ohne Hysterese und ohne
  Mindestzeit und schaltet bei wenig Überschuss zu früh hoch (2,5 kW
  Überschuss → 6 A×3 = 4,1 kW, 1,6 kW Netzbezug).
* **Entscheide des Projektverantwortlichen (08.11.2026):**
  * Der **Score** entscheidet, ob geladen wird. Der Netzbezug wird nicht
    vom PV-Überschuss abgezogen.
  * Der **PID** entscheidet, wie viel: Messwert = Netzleistung mit
    Vorzeichen (Bezug − Einspeisung), Sollwert 0 W, OUT = Ladeleistung.
    Eigene Instanz des Library-Objekts `lib/pid`. Der Battery-PID-Test ist
    davon unabhängig, ein Umbau am EV hat keinen Einfluss darauf.
  * **Phasen aus den OUT-Grenzen:** 1 Phase 1,4–3,7 kW, 3 Phasen 4,1–11 kW.
    Auf 3 Phasen springt OUT LO auf 4,1 kW. Gewechselt wird erst, wenn OUT
    eine Mindestzeit an der Grenze anliegt, mit Nachführung des Reglers.
  * **Ist-Leistung der Wallbox:** neues optionales Feld. Wallbox-Sensor oder
    Helfer. Ein Helfer muss auf einer echten Messung beruhen.
  * **Ohne Messung (Feld leer):** Der User wählt eine feste Stufe: 1 Phase
    10 A (2,3 kW), 3 Phasen 6 A (4,1 kW) oder 3 Phasen 16 A (11 kW). Der PID
    läuft dann in ROUT.
  * **Deadline** («um 16:00 voll»): so viel PV wie möglich, der Rest aus dem
    Netz. Ersetzt die feste Regel «2 h vor Ende FAST». Braucht den SoC.
  * **Später:** Die Core Registry teilt die verfügbare Leistung über OUT HI
    zu (Battery vor EV, Haus).
* **Enthält:** LE-16 (Anschlussreserve als OUT-HI-Grenze, kein Mindeststrom
  über der Reserve, Anti-Cycling hält keinen Strom über der Reserve). Keine
  parallele zweite EV-Regelung entwickeln.
* **Zu beachten:** Der Regler geht im ROUT bei ungültigem Messwert nicht auf
  HOLD, der LE-01-Schutz muss ausserhalb greifen (ROUT auf 0). Eine Prüfung,
  ob ein Sensor hängt, gehört nicht ins EV und nicht an den PID-Eingang: Das
  macht der Sensorprüfer (LE-20), der IO BAD meldet. Die Getter
  `get_market_import_now_w()`/`get_market_export_now_w()` haben seit LE-02
  keinen Aufrufer mehr und können dann entfallen.
* **Abhängigkeiten:** keine.
* **Prüfkriterien:** Regressionstests aus dem LE-16-Auftrag (FAST, PV_MIN,
  PV, 1/3 Phasen, Grenzfälle, keine Stopp-/Start-Zyklen, kein
  Phasenflattern); vollständige Testsuite grün; Realtest in einer
  HA-Testinstanz ohne reale Ladung.

## LE-20 – Emsomat: Sensorprüfer als Library-Objekt

* **Projekt:** Emsomat · **Priorität:** MITTEL · **Status:** OFFEN (später,
  Entscheid 08.11.2026)
* **Ursache:** Ein Sensor ist IO BAD oder in Ordnung. Ob er ungültig ist oder
  hängt, soll nicht an jeder Verwendungsstelle einzeln geprüft werden. Heute
  sitzt die Altersprüfung (120 s, aus LE-01) direkt in
  `Emsomat/addon/ev_logic.py`.
* **Ziel:** ein Sensorprüfer als eigenes Library-Objekt, aufgebaut wie
  `lib/pid`: für alle Sensoren gleich, je Verwendung eine eigene Instanz, mit
  einem Ausgang IO BAD (mit Grund). Verbraucher wie EV oder PID-Eingang werten
  nur noch IO BAD aus.
* **Umbau:** Die Altersprüfung aus `ev_logic.py` wandert in den Sensorprüfer.
  Bis dahin bleibt sie wie sie ist und wird **nicht** entfernt. Ersetzt wird
  sie erst in einer späteren, getesteten Migration. Die 120 s
  werden ein Parameter (LE-18).
* **Notizen zur Klasse (Vorschlag, noch nicht angelegt):**
  * Neues Paket neben `lib/pid` und `lib/trend`, z. B. `lib/sensor_check/` mit
    einer Klasse `SensorCheck`. Kein `homeassistant`-Import, damit sie ohne HA
    testbar ist.
  * Konfiguration pro Instanz, z. B. erlaubte Einheiten, Wertebereich, ob
    negative Werte erlaubt sind, maximales Alter.
  * Eingang: Zustand, Einheit und Zeitstempel eines Sensors.
  * Ausgang: Wert, **IO BAD** ja/nein und Grund (`unknown`, `unavailable`,
    keine Zahl, NaN/Inf, Bereich, Einheit, veraltet, fehlt). Ein gültiger Wert
    0 bleibt 0.
  * Ein Verbraucher wertet nur IO BAD aus und prüft nichts mehr selbst. Welches
    Verhalten bei IO BAD gilt, entscheidet weiterhin der Verbraucher.
  * Wie ein hängender Sensor sicher erkannt wird, wird beim Bau festgelegt. Das
    Alter nach HA-Zeitstempel allein erkennt einen weiter geschriebenen,
    eingefrorenen Wert nicht.
* **Stellen, die heute selbst prüfen (Stand 08.11.2026):**

  | Stelle | Sensor |
  |---|---|
  | `addon/ev_logic.py` | Netzleistung mit Altersprüfung, SoC des Autos, Verfügbarkeit der Wallbox |
  | `sparkplug/power_sample.py` | Bezug und Einspeisung für Sparkplug, EV und PID-Test |
  | `manager.py` (`_as_float`, `_as_bool`) | u. a. Preis, Exportpreis, Batterie-SoC, Wärmeenergie, Wärmeleistung, Aussentemperatur, PV-Leistung, direkt gewählter PID-Sensor |
  | `taskgrid_ha/device_snapshot.py` | SoC des Autos für TaskGrid |
  | `models/parameters.py` | wiederhergestellte Zustände der Parameter |
  | `adapters/tempForecastAdapter.py` | Temperatursensor |

  `get_battery_soc_pct()` und `get_live_pv_power()` geben immer eine Zahl
  zurück. Beim Umbau prüfen, was sie bei ungültigem Sensor liefern. Pro Stelle
  einzeln entscheiden, ob sie umgestellt wird.
* **Abhängigkeiten:** keine.
* **Prüfkriterien:** Tests des Library-Objekts; das Verhalten aus LE-01
  (Laden stoppt bei ungültigem oder veraltetem Bezug) bleibt nachgewiesen;
  vollständige Testsuite grün.

## Ausstehende reale Tests (ohne autorisierte Testumgebung nicht möglich)

| Bezug | Test | Voraussetzung |
|---|---|---|
| LE-03 | EA-1 in echtem HA: `unknown`/`unavailable`, Reconnect, NBIRTH, Zeitstempel | HA-Testinstanz mit Test-Broker |
| LE-01 | EV-Schutz mit simuliertem Sensorausfall in echtem HA, ohne reale Ladung | HA-Testinstanz, Wallbox simuliert |
| LE-05 | Broker-ACL-Negativtests | isolierter Test-Broker |
| LE-09 | HA-Sensoren des Add-ons, Ingress | HA-Testinstanz |
| LE-10, LE-11 | Blueprint bzw. Gateway gegen Test-Broker | HA-Testinstanz, Testgerät |
