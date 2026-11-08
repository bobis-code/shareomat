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

## Übersicht

| ID | Projekt | Aufgabe | Priorität | Status | Abhängig von |
|---|---|---|---|---|---|
| LE-01 | Emsomat | EV-Anschlussschutz bei ungültiger Netzleistung | HOCH | TEST AUSSTEHEND | – |
| LE-02 | Emsomat | PID-Test und ungültige Messwerte | MITTEL | OFFEN | – |
| LE-03 | Emsomat | Sparkplug EA-1 abschliessen | HOCH | TEST AUSSTEHEND | – |
| LE-04 | Shareomat | Ungültige Werte über `bridge.py` und `WanDownlink` erhalten (K-3) | HOCH | OFFEN | LE-03 |
| LE-05 | Shareomat | Sparkplug-Sitzungen und Quellenidentität (S-L4, S-L5, K-1, K-2) | HOCH | OFFEN | LE-04 |
| LE-06 | Shareomat | Anschlüsse ohne separaten Einspeisesensor | MITTEL | OFFEN | – (fachlich vor LE-07) |
| LE-07 | Shareomat | Live-Modul (Datenmodell, Prüfung, Bilanz) | HOCH | OFFEN | LE-04, LE-05, LE-06 |
| LE-08 | Shareomat | Verwaltung «Live-Energie», Live-Messquelle zuordnen | HOCH | OFFEN | LE-07 |
| LE-09 | Shareomat | HA-Sensoren LEG-Status und Integrationsprüfung | MITTEL | OFFEN | LE-07, LE-08 |
| LE-10 | Shareomat (+ HA-Blueprint) | Home Assistant als direkte Quelle (`ha_mqtt`) | MITTEL | OFFEN | LE-07, LE-08 |
| LE-11 | Shareomat | Externes Gateway / AMS Reader (`gateway_mqtt`) | MITTEL | OFFEN | LE-07, LE-08, Broker-Entscheid |
| LE-12 | alle | Nachbar-Pilot | MITTEL | OFFEN | LE-09, LE-10 oder LE-11, O-3 |
| LE-13 | Shareomat | Teilnehmerportal | NIEDRIG | OFFEN | LE-12, Security-Review |
| LE-14 | Shareomat | Vendor-Code: NDEATH-Zeitstempel beim Lesen vertauscht | NIEDRIG | OFFEN | – |
| LE-15 | Emsomat | Eingehende Teilnehmerwerte ohne Zeitstempel bekommen «jetzt» | NIEDRIG | OFFEN | – |
| LE-16 | Emsomat | EV `fast`/`pv_min` erzwingen `min_charge_a` auch ohne Anschlussreserve (vor LE-02) | HOCH | BLOCKIERT | Entscheid nach Istanalyse |
| LE-17 | Emsomat | Kein Bezugssensor konfiguriert = EV ohne Anschlussschutz | MITTEL | OFFEN | Entscheid |
| LE-18 | Emsomat | `grid_power_max_age_s` nicht konfigurierbar | NIEDRIG | OFFEN | – |

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
* **Ergebnis (08.11.2026, nicht committet):**
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

* **Projekt:** Emsomat · **Priorität:** MITTEL · **Status:** OFFEN
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
* **Ergebnis und Testnachweis:** –
* **ADR:** ADR-0007 §10.

## LE-03 – Emsomat: Sparkplug EA-1 abschliessen

* **Projekt:** Emsomat · **Priorität:** HOCH · **Status:** TEST AUSSTEHEND
* **Ursache:** S-L2, S-L3 (ADR-0007 Kontext).
* **Bereits umgesetzt (07.11.2026, nicht committet, nicht neu schreiben):**
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

* **Projekt:** Shareomat · **Priorität:** HOCH · **Status:** OFFEN
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
* **ADR:** ADR-0007 Kontext S-L1, §4 Härtung, §10 K-3.

## LE-05 – Shareomat: Sparkplug-Sitzungen und Quellenidentität

* **Projekt:** Shareomat · **Priorität:** HOCH · **Status:** OFFEN
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
* **ADR:** ADR-0007 §4.1, §4.2, §10 K-1, K-2.

## LE-06 – Anschlüsse ohne separaten Einspeisesensor

* **Projekt:** Shareomat (Modell), später auch Emsomat · **Priorität:** MITTEL
* **Status:** OFFEN
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
* **ADR:** ADR-0007 §2, §3.

## LE-07 – Shareomat: Live-Modul

* **Projekt:** Shareomat · **Priorität:** HOCH · **Status:** OFFEN
* **Ziel:** `live_sources` und `live_source_channels` (vollständiges
  Modell), `live.db`, Rohproben, zentrale Qualitätsprüfung, Aktualität und
  Identität, LEG-Bilanz, Hysterese, `LiveStatusService`. Unvollständige,
  ungültige und veraltete Messungen nach ADR-0007 §3.
* **Abhängigkeiten:** LE-04, LE-05, LE-06.
* **Grenzen:** keine Änderung an der Abrechnungsberechnung.
* **Prüfkriterien:** Tests ADR-0007 §8 Nr. 1–9, insbesondere die Trennung
  von Live-Daten und Abrechnung; vollständige Testsuite grün.
* **ADR:** ADR-0007 §2, §3, §4.

## LE-08 – Verwaltung: Live-Messquelle zuordnen

* **Projekt:** Shareomat · **Priorität:** HOCH · **Status:** OFFEN
* **Ziel:** Menü BETRIEB → «Live-Energie»: Übersicht aller offiziellen
  LEG-Messpunkte, aktuelle Live-Messquelle, Kommunikation, Qualität,
  letzte gültige Messung; «Live-Messquelle zuordnen» mit
  `testing → active → retired`, kontrollierter Wechsel. Die drei Arten sind
  in der Oberfläche vorgesehen. In Schritt 1 sind `emsomat_local` und
  `wan_sparkplug` zuordenbar, die anderen Arten folgen mit LE-10 und LE-11.
* **Abhängigkeiten:** LE-07.
* **Grenzen:** Die übrige Verwaltung bleibt unverändert.
* **Prüfkriterien:** Tests ADR-0007 §8 Nr. 8; CSRF, Admin-Recht, Audit;
  vollständige Testsuite grün; Sichtprüfung in HA (Ingress).
* **ADR:** ADR-0007 §2, §5.1.

## LE-09 – HA-Sensoren und Integrationsprüfung

* **Projekt:** Shareomat · **Priorität:** MITTEL · **Status:** OFFEN
* **Ziel:** `sensor.shareomat_leg_ueberschuss` und
  `sensor.shareomat_leg_live_status`. Keine falsche Überschusszahl bei
  unsicherer Messung. Tests für `unknown`/`unavailable`, Wiederanlauf,
  gültige Nullwerte, mehrere Messpunkte und Quellen. Hauptprojekt und
  `ha_addon/` synchron (`tools/prepare_addon.py --check`).
* **Abhängigkeiten:** LE-07, LE-08.
* **Prüfkriterien:** vollständige Testsuite grün. Reale Tests ohne
  autorisierte Testumgebung werden hier als ausstehend aufgeführt.
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

* **Projekt:** Shareomat · **Priorität:** NIEDRIG · **Status:** OFFEN
* **Ursache:** `shareomat/sparkplug/vendor/payload.py:176`: Die Bedingung
  ist vertauscht (`… if not payload.HasField("timestamp") else None`). Der
  Zeitstempel einer NDEATH geht beim Lesen verloren. Heute wird er nicht
  verwendet.
* **Abhängigkeiten:** sinnvoll zusammen mit LE-05.

## LE-15 – Emsomat: Teilnehmerwerte ohne Zeitstempel

* **Projekt:** Emsomat · **Priorität:** NIEDRIG · **Status:** OFFEN
* **Ursache:** `EdgeNodeAdapter._metrics_to_power()` setzt bei eingehenden
  Teilnehmerwerten ohne Metric-Zeitstempel die aktuelle Zeit ein. Ein
  solcher Wert wirkt damit frisch, obwohl sein Alter unbekannt ist. Heute
  sendet Shareomat immer einen Zeitstempel.
* **Abhängigkeiten:** sinnvoll nach LE-04.

---

## LE-16 – Emsomat: EV-Mindeststrom ohne Anschlussreserve

* **Projekt:** Emsomat · **Priorität:** HOCH (vor LE-02) · **Status:** BLOCKIERT
* **Stand 08.11.2026:** Istanalyse erstellt, noch keine Codeänderung:
  Emsomat `docs/development/EV_Ladelogik_Istanalyse_LE-16.md`. Wichtigster
  Befund: Der Bezugssensor enthält den eigenen Ladestrom, die
  Reserveformel zählt ihn doppelt (bei 3 Phasen dreifach). Der Iststrom der
  Wallbox ist unbekannt. Wie die Reserve den eigenen Ladestrom
  berücksichtigen soll, entscheidet der Projektverantwortliche, bevor
  LE-16 umgesetzt wird.
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
* **Abhängigkeiten:** Realtest LE-01 (zeigt, ob 120 s passen).

## Ausstehende reale Tests (ohne autorisierte Testumgebung nicht möglich)

| Bezug | Test | Voraussetzung |
|---|---|---|
| LE-03 | EA-1 in echtem HA: `unknown`/`unavailable`, Reconnect, NBIRTH, Zeitstempel | HA-Testinstanz mit Test-Broker |
| LE-01 | EV-Schutz mit simuliertem Sensorausfall in echtem HA, ohne reale Ladung | HA-Testinstanz, Wallbox simuliert |
| LE-05 | Broker-ACL-Negativtests | isolierter Test-Broker |
| LE-09 | HA-Sensoren des Add-ons, Ingress | HA-Testinstanz |
| LE-10, LE-11 | Blueprint bzw. Gateway gegen Test-Broker | HA-Testinstanz, Testgerät |
