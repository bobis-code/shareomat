# Shareomat: Audit der Live-Energie-Erweiterung

**Datum:** 08.11.2026  
**Geprüfter Stand:** `93daa3e` (Add-on 0.6.9), Arbeitsbaum ohne Änderungen an versionierten Dateien  
**Vergleichsstand:** `23bf860` (Add-on 0.6.6)  
**Art:** nur lesend. Keine Code-, Dokumentations- oder Datenbankänderung, kein Commit. Einzige neue Datei ist dieser Bericht.

> **Nachtrag:** Dieser Bericht beschreibt den Stand `93daa3e`. AUD-01, AUD-05, AUD-06, AUD-13, AUD-14 und AUD-15 sind inzwischen behoben; der aktuelle Stand steht in `docs/Live-Energie-Arbeitsliste.md`.

## 1. Ergebnis

| Frage | Antwort | Begründung in einem Satz |
|---|---|---|
| **A. Ist die bisherige Abrechnungslogik gegenüber dem freigegebenen Stand unverändert?** | **JA** | Alle Abrechnungs-, Import-, Verwaltungs- und Rechnungsdateien sind byte-identisch; in ihrer Import-Hülle weichen nur vier Dateien ab, und diese Änderungen sind rein ergänzend (Abschnitt 4). |
| **B. Sind Abrechnung und Live-Energie ausreichend voneinander isoliert?** | **EINGESCHRÄNKT** | Im Code ja, im Betrieb nein: Das automatische Sparkplug ab 0.6.8 schaltet im Abrechnungszyklus einen zuvor inaktiven Zweig ein (AUD-01), und eine Add-on-Option kann mit WAN den Abrechnungslauf stoppen (AUD-05). |
| **C. Ist die Live-Energie-Implementierung für einen lokalen Pilot bereit?** | **NUR MIT EINSCHRÄNKUNGEN** | Für die Anzeige der eigenen Werte ja, sobald AUD-01 entschieden und AUD-02 eingerichtet ist; WAN erst nach AUD-03 und AUD-04, ein Nachbar-Pilot erst nach O-3. |

**D. Zwingend vor der Inbetriebnahme:**

| Priorität | Befund | Gilt für |
|---|---|---|
| 1 | **AUD-01** Entscheiden, ob Sparkplug automatisch Prognosen berechnen und LEG-Daten an Emsomat senden darf. Bis dahin `sparkplug.mode: off`, wenn das nicht gewollt ist. | jeden Betrieb ab 0.6.8 |
| 2 | **AUD-02** Eigenes Broker-Login mit ACL für HA Second, bevor es verbunden wird | Pilot mit HA Second |
| 3 | **AUD-06** Veraltete Sensorwerte in HA verhindern | Pilot mit HA Second |
| 4 | **DOC-06** und O-3 klären: Was der Nachbar sehen darf | Nachbar-Pilot |
| 5 | **AUD-03, AUD-04, AUD-05, AUD-07** | vor dem Einschalten von WAN |

Es wurde **kein Befund der Stufe KRITISCH** gefunden. Insgesamt: 3 HOCH, 4 MITTEL und 9 NIEDRIG im Code, dazu 11 Dokumentationsbefunde.

**Der Zustand «Live-Messung geplant» ist bereits vollständig entfernt.** Er ist weder im Code noch in den Vorlagen noch in der Datenbank vorhanden (`live_meter_states` erlaubt per `CHECK` nur `no_measurement`) und wurde nie veröffentlicht. Die Treffer für «geplant» in ADR-0007 betreffen anderes (siehe DOC-01). Der Zustand «Noch nicht festgelegt» ist kein gespeicherter Zustand, sondern die Anzeige ohne Eintrag und ohne Quelle.

## 2. Umfang und Grenzen dieses Audits

**Ausgangscommit.** `23bf860` «Bump add-on version to 0.6.6» (07.11.2026). Herleitung: Es ist der letzte Commit vor `5164d57`, dem ersten mit ADR-0007, und `13a0f8c` ist der erste Commit mit Live-Code. Die beiden Commits dazwischen ändern nur Dokumente.

**Unsicherheit:** Git enthält keine Tags und keine Markierung einer Freigabe. Dass `23bf860` der von dir freigegebene Stand ist, ist eine Ableitung aus der Historie, kein Nachweis. Bitte bestätigen. Stammt deine Abnahme von einem älteren Stand, müsste die Differenz zu diesem zusätzlich geprüft werden (`4e6fb55` und `ec16f24` liegen direkt davor).

**Unabhängigkeit.** Der Prüfer hat den Code selbst geschrieben. Das Audit ist darum methodisch auf Byte-Vergleiche, ausgeführte Fehlerfälle und den tatsächlichen Diff gestützt, ersetzt aber kein Review durch eine zweite Person.

**Methode.**
* Export von Ausgangs- und Prüfstand in getrennte temporäre Ordner.
* Byte-Vergleich pro Bereich, Berechnung der Import-Hülle über den Syntaxbaum.
* Prüfung der SQL- und Migrationspfade auf temporären Datenbanken.
* Fehlerfälle mit Fake-Clients und temporären Datenbanken ausgeführt (Anhang).

**Nicht geprüft:** kein echter Broker, kein Home Assistant, keine realen Zähler, keine produktive Datenbank. Die installierte Add-on-Version und die Einstellungen von Emsomat sind nicht bekannt. Die `.drawio`-Dateien wurden nur auf Stichworte durchsucht.

## 3. Änderungsübersicht

76 Dateien, +11'556/−91 Zeilen gegenüber `23bf860`.

### Ausschliesslich neue Live-Module

| Datei | Tatsächliche Änderung | Begründung | Einfluss auf Bestand | Risiko |
|---|---|---|---|---|
| `shareomat/live/` (`__init__`, `model`, `quality`, `balance`, `hysteresis`, `store`, `masterdata`, `service`, `adapters/__init__`, `adapters/sparkplug`) | neu | Live-Ebene | keiner direkt, wird nur von `main.py` importiert | niedrig, siehe AUD-08, AUD-09, AUD-13 |
| `shareomat/ha/live_sensors.py` | neu | HA-Sensoren | zusätzliche Topics auf dem bestehenden MQTT-Client | mittel, AUD-06, AUD-10 |
| `shareomat/web/pages/live_energy.py`, `templates/live_energy/` (5 Dateien) | neu | Verwaltung | keiner | niedrig, AUD-12 |
| `shareomat/models/live_source.py` | neu | Datenmodell | keiner | niedrig |
| `tools/live_energy_demo.py` | neu | lokale Vorführung | nicht im Add-on | keines |

### Neue Live-Tabellen und Datenbankanbindung

| Datei | Tatsächliche Änderung | Begründung | Einfluss auf Bestand | Risiko |
|---|---|---|---|---|
| `shareomat/database/sqlite.py` | `_SCHEMA_VERSION` 14 → 16; 3 Tabellen und 2 Indizes ergänzt | Quellen und Messpunkt-Zustand | rein ergänzend (Abschnitt 5) | niedrig |
| `shareomat/database/live_sources.py` | neu | Schreiblogik der Quellen | schreibt nur in die drei Live-Tabellen und ins Audit-Protokoll | niedrig |

### Änderungen an bestehenden Sparkplug-Komponenten

Keine dieser Dateien gehört zur Abrechnung, aber alle ändern bestehendes Verhalten.

| Datei | Tatsächliche Änderung | Begründung | Einfluss auf Bestand | Risiko |
|---|---|---|---|---|
| `sparkplug/wan_downlink.py` | `allowed_source` Pflicht, Grössen- und Retain-Grenzen, `is_null` bleibt erhalten, Verbindungsverlust entfernt Teilnehmer | S-L1, S-L4, S-L5 | **Verhalten geändert:** nicht zugeordnete Standorte erreichen Emsomat nicht mehr | hoch, AUD-03, AUD-04, AUD-07 |
| `sparkplug/host.py` | Retain- und Grössenfilter, NBIRTH löscht alte Metrics, `seq`-Prüfung, Rebirth, Verbindungsverlust, Live-Weitergabe | S-L5 | **Verhalten geändert:** nach Verbindungsverlust gelten Werte als weg; «Apps & Integrationen» zeigt dann offline | mittel |
| `sparkplug/participant_relay.py` | ungültig bleibt `is_null`, Zeitquelle, WAN-Births alle 5 Minuten | S-L1, K-1, K-3 | **Verhalten geändert:** Emsomat erhält `is_null` statt 0 W | mittel |
| `sparkplug/bridge.py` | kein `float(value or 0.0)` mehr, Teilnehmer wird bei Ausfall entfernt | S-L1, K-3 | **Verhalten geändert** | mittel |
| `sparkplug/vendor/payload.py` | vertauschte Bedingung beim NDEATH-Zeitstempel korrigiert | LE-14 | Zeitstempel ist nun gesetzt statt `None`; wird nirgends zur Entscheidung genutzt | niedrig |

### Änderungen an zentralen Diensten und Programmstart

| Datei | Tatsächliche Änderung | Begründung | Einfluss auf Bestand | Risiko |
|---|---|---|---|---|
| `main.py`, `ha_addon/main.py` | Imports, `_setup_live`, Verdrahtung, Stopp. **`_run_safe_cycle` selbst ist unverändert.** | Start der Live-Ebene | Fehler zur Laufzeit abgefangen, Importfehler nicht (AUD-13) | mittel |
| `ha_addon/generate_runtime_config.py` | schreibt ab 0.6.8 einen Abschnitt `sparkplug` | automatische Emsomat-Verbindung | **schaltet bestehenden, zuvor inaktiven Code ein** (AUD-01, AUD-05) | hoch |
| `ha_addon/config.yaml` | Options-Abschnitt `sparkplug`, Version 0.6.9 | wie oben | `homeassistant_api` bleibt `false` | hoch, zusammen mit AUD-01 |
| `ha_addon/translations/de.yaml`, `en.yaml` | neu | Feldbeschreibungen | keiner | keines |
| `ha_addon/README.md` | drei Tabellenzeilen | Dokumentation | keiner | keines |
| `ha_addon/shareomat/**` (Kopien) | Kopie der Quelle | Add-on-Build | byte-gleich mit `shareomat/**` geprüft | keines |

### Navigation, Routen, Templates, Berechtigungen

| Datei | Tatsächliche Änderung | Begründung | Einfluss auf Bestand | Risiko |
|---|---|---|---|---|
| `web/navigation.py` | 3 Routen, 1 Menügruppe am Ende | Live-Energie | bestehende Einträge unverändert | niedrig |
| `web/server.py` | 1 Import, 1 Seiteneintrag | Routing | keiner | niedrig |
| `web/state.py` | Feld und Accessor für den Live-Dienst | Verdrahtung | keiner | niedrig |
| `security/permissions.py` | `"live_energy": "masterdata.edit"` | Recht für POST | keiner | niedrig |

### Bestehende Abrechnungs- oder Verwaltungsmodule

**Keine Änderung.** `shareomat/core/`, `external_data/`, alle bestehenden `web/pages/`, alle bestehenden Templates und alle `database/`-Module ausser `sqlite.py` sind byte-identisch.

### Dokumentation, Tests und Unversioniertes

| Datei | Tatsächliche Änderung | Risiko |
|---|---|---|
| `docs/decisions/ADR-0007-…`, `docs/Zukunft.md`, `docs/Live-Energie-Arbeitsliste.md` | neu | Abschnitt 10 |
| `tests/test_live_core.py`, `test_live_transport.py`, `test_live_web_ha.py`, `test_main_live_wiring.py`, `test_addon_runtime_config.py` | neu | keines |
| `tests/test_sparkplug_wan.py`, `test_sparkplug_cross_house_wiring.py` | je **eine** Zeile: `allowed_source=lambda edge, device: True`. Keine Prüfung abgeschwächt, wegen der neuen Pflicht nötig. | niedrig |
| unversioniert: `docs/Architektur/Diagramm/` (v3, v4, V3-PNG, `Export/`), `docs/Architektur/Ziel/` | keine Implementierung, seit Beginn lokal | DOC-11 |

## 4. Schutz der bestehenden Abrechnung

Nachweis ist der Byte-Vergleich (Zeilenenden normalisiert), nicht die Testsuite.

| Bereich | Dateien | Nachweis | Ergebnis |
|---|---|---|---|
| Offizielle Messdatenimporte und Validierungen | `core/collector`, `core/pipeline`, Parser | 26 Dateien unter `core/` identisch | unverändert |
| Teilnehmer- und Messpunktverwaltung | `database/participants`, `meters`, `web/pages/...` | identisch | unverändert |
| LEG-Zuordnung, Teilnahmeberechtigung | `database/billing_eligibility.py` | identisch; Live ruft nur `for_day()` auf | unverändert |
| Tarifberechnungen | `database/tariffs`, `core`, `external_data` | identisch | unverändert |
| Matching und Energieberechnung | `core/` | identisch | unverändert |
| Rechnungsentwürfe, Freigaben, Stornierungen | `database/billing*`, `core/` | identisch | unverändert |
| Rechnungen, Gutschriften, Dokumente | `database/invoicing*`, `web/pages/invoices` | identisch | unverändert |
| Zahlungen, Ledger, Mahnwesen | `database/`, `web/pages/invoices` | identisch | unverändert |
| Vertragsverwaltung | `database/contract*`, `web/pages/contract` | identisch | unverändert |
| Hintergrundjobs, automatische Abrechnung | `main.py`, `core/` | `_run_safe_cycle` und Scheduler ohne Diff | **Code unverändert, aber AUD-01 und AUD-05** |
| Berechtigungen, Login, Web-Routen | `web/login.py`, `security/`, `web/server.py` | `login.py` identisch; sonst nur die Ergänzungen aus Abschnitt 3 | bestehende Routen und Rechte unverändert |

**Import-Hülle.** Ausgehend von `core/` und allen bestehenden Seiten sind es 112 Module. Kein einziges ist `shareomat.live`, `live_sources` oder `live_energy`. Abweichend sind nur `database/sqlite.py`, `security/permissions.py`, `web/navigation.py` und `web/state.py`, alle rein ergänzend. `shareomat.live` wird ausserhalb des eigenen Pakets ausschliesslich von `main.py` importiert. Weder `core/` noch `database/` importieren Sparkplug.

**Ergänzend:** Die Alt-Tests aus `23bf860`, unverändert gegen den aktuellen Code ausgeführt: 982 bestanden, 4 übersprungen, 17 fehlgeschlagen. 15 davon sind die gewollte neue Pflicht `allowed_source` in WAN-Tests. 2 fehlen wegen einer von Git ausgeschlossenen Testdatei (AUD-15). **Kein Abrechnungstest schlägt fehl.**

## 5. Datenbank-Audit

| Frage | Ergebnis | Nachweis |
|---|---|---|
| Nur zusätzliche Tabellen und Indizes? | **Ja:** `live_sources`, `live_source_channels`, `live_meter_states`, `ux_live_sources_meter_active`, `ux_live_sources_key_open` | Diff `sqlite.py`; Vergleich von `sqlite_master` |
| Bestehende Tabellen, Spalten, Constraints, Trigger, Migrationen verändert? | **Nein.** Alle 39 bisherigen Objekte haben denselben SQL-Text. `_COLUMN_MIGRATIONS` ohne Diff. | Migration 14 → 16 auf Testdatenbank |
| Bleiben Daten erhalten? | **Ja.** Inhalt aller Alt-Tabellen hash-gleich, nur `schema_migrations` erhält die Zeile 16. `integrity_check` ok, keine Fremdschlüssel-Verletzung. | ausgeführt |
| Kann die Migration eine Datenbank beschädigen? | Kein solcher Pfad gefunden. Nur `CREATE … IF NOT EXISTS`, zweimal ausgeführt ohne Änderung. Der alte Code öffnet die migrierte Datenbank problemlos. Nicht geprüft: volle Platte. | ausgeführt |
| Live-Konfiguration und Stammdaten getrennt? | **Ja.** Kein `INSERT`/`UPDATE`/`DELETE` auf `meters` oder `participants` in Live-Pfaden. | Suche über alle SQL-Anweisungen |
| Live-Messwerte nur in `live.db`? | **Ja.** Im Audit-Protokoll stehen Kennungen, keine Leistungswerte. | `store.py`, `live_sources.py` |
| Unzulässige Schreibzugriffe auf offizielle Mess- oder Abrechnungsdaten? | **Keine aus Live-Code.** Indirekt schreibt der durch AUD-01 aktivierte Zweig in Prognose- und Preistabellen, nicht in Mess- oder Abrechnungsdaten. | `main.py:259-265` |
| Quellenwechsel und Aktivierungen transaktionssicher? | **Ja.** `BEGIN IMMEDIATE` und partielle Unique-Indizes. 8 parallele Aktivierungen: genau 1 aktiv, 7 abgelöst. 30 parallele Läufe Aktivieren gegen Zustand setzen: 0 Widersprüche. | ausgeführt |
| Kann ein Fehler in `live.db` die Abrechnung blockieren? | **Nein.** Mit zerstörter `live.db` läuft der Dienst weiter und `shareomat.db` bleibt intakt. Die Abrechnung kennt die Datei nicht. | ausgeführt |
| Live-Fremdschlüssel gegen `meters` | `live_sources` hat `REFERENCES meters(id)` ohne `ON DELETE`, würde also das Löschen sperren. Heute **ohne Wirkung**, weil der Code nirgends Messpunkte löscht. Bei einer künftigen Löschfunktion zu beachten. | Suche nach `DELETE FROM meters`, `REPLACE INTO` |

**Nebenläufigkeit mit der Abrechnung (Analyse, nicht gemessen).** Die Live-Ebene liest `shareomat.db` nur kurz im WAL-Modus, das blockiert Schreiber nicht. Geschrieben wird nur bei Aktionen der Verwaltung in einer kurzen Transaktion.

## 6. Architektur-Audit

| Anforderung | Ergebnis | Nachweis |
|---|---|---|
| Live-Modul verändert keine Abrechnungsfunktion | erfüllt | Abschnitt 4 |
| Abrechnung unabhängig von `shareomat.live` | erfüllt | Import-Hülle |
| Live liest nur erlaubte Stammdaten und Regeln | erfüllt: nur `masterdata.py` greift auf die Datenbankmodule `meters`, `community`, `live_sources`, `billing_eligibility` zu; `EligibilityResolver` führt nur Lesevorgänge aus | Code, `test_live_core.py` |
| `billing_eligibility.py` nicht verändert | erfüllt | byte-identisch |
| Live-Fehler beeinträchtigen den Betrieb möglichst nicht | überwiegend: Start und Laufzeit gekapselt, Importfehler nicht (AUD-13) | `main.py:270-281`; AUD-13 |
| Keine unkontrollierten Rückwirkungen über `main.py` und Dienste | **nicht erfüllt** | AUD-01, AUD-05 |
| Bestehende Menüs, Routen, Abrechnungsseiten unverändert | erfüllt | Diff, 18 Menüseiten am echten Server: Status 200 |
| Live-Energie als eigener Bereich | erfüllt, nach SYSTEM, vor dem Benutzerblock KONTO | `navigation.py`, Sidebar |

## 7. Sicherheits-Audit

| Thema | Ergebnis | Status |
|---|---|---|
| Authentisierung, Autorisierung | Alle Routen hinter dem bestehenden Login. POST braucht `masterdata.edit`, GET `view`; es gibt nur die Rolle `admin`. Ohne Login 303. | geprüft am echten Server |
| CSRF | zentral für jeden POST. Ohne Token keine Änderung. | geprüft am echten Server |
| Fremde oder manipulierte Quellen | Unbekannte Paare werden vor dem Dekodieren verworfen. **Mit gültigem Broker-Zugang ist Fälschen möglich.** | AUD-02, **Realtest offen** |
| Zuordnung zu offiziellen Messpunkten | Quelle hängt am Fremdschlüssel; pro Messpunkt höchstens eine aktive; Schlüssel und WAN-`device_id` eindeutig. Eine falsche Zuordnung ist technisch nicht erkennbar (K-4). | geprüft |
| Sessions, `seq`, Rebirth, Disconnect | Lokal geprüft: neue Sitzung löscht alte Werte, `bdSeq` bei NDEATH, Lücken lösen begrenzten Rebirth aus. **WAN ohne `bdSeq` und ohne `seq`.** | AUD-07, **Realtest offen** |
| MQTT-ACL | **Keine Annahme getroffen.** Es gibt keinen Nachweis einer Bindung. `identity_verified` bleibt leer, die Bilanz kann darum nicht `VOLLSTÄNDIG` werden. | **Realtest offen** |
| `is_null`, NaN, Inf, Einheiten | werden nie zu 0 W; falscher Typ, negativ, unbekannte Einheit ergeben `INVALID` | geprüft, Tests |
| Zeitstempel, verspätete und wiederholte Nachrichten | Zukunft, Ausreisser, ältere, doppelte und widersprüchliche Proben behandelt | geprüft, Tests |
| Veraltete Daten, Ausfälle | Verbindungsverlust macht Werte sofort ungültig; `max_age` beim Lesen. **In HA nicht abgesichert, wenn die Live-Ebene selbst steht.** | AUD-06 |
| Doppelzählung | eine aktive Quelle pro Messpunkt, in Datenbank und Bilanz; gleicher Kanal blockiert | geprüft |
| Gleichzeitige Aktivierung | keine Verletzung in parallelen Läufen | ausgeführt, ohne automatisierten Test (AUD-16) |
| Belastungsgrenzen | Queue 2048, 40 Proben pro 10 s, Grössen- und Metric-Limits. **Sitzungsereignisse sind nicht ratenbegrenzt.** | AUD-08, AUD-10 |
| Broker- und DB-Ausfall | Live fällt auf «keine Daten», die Abrechnung läuft weiter | ausgeführt |
| Informationsabfluss Web | nur Admin, keine Geheimnisse, Ausgaben escaped | Tests |
| Informationsabfluss HA und MQTT | HA-Sensoren ohne Daten anderer Teilnehmer. **Auf dem lokalen Broker liegen die Werte fremder Standorte (Relay) offen.** | AUD-02, **Realtest offen** |
| Informationsabfluss Logs | nur Kennungen; kein Passwort. Häufige Warnungen für verworfene Nachrichten sind nicht gedrosselt. | NIEDRIG, in AUD-08 |
| Admin und künftiges Portal | Es gibt kein Portal und keine Teilnehmerrolle. GET braucht nur `view`, ein künftiges Leserecht sähe also alles (in ADR-0007 bekannt). | Vorgabe für Schritt 4 |

## 8. GUI- und Navigations-Audit

| Prüfpunkt | Ergebnis |
|---|---|
| Bestehende Menügruppen und -punkte unverändert | erfüllt; automatischer Vergleich mit dem alten Stand in `test_live_web_ha.py` |
| Live-Energie als eigener Bereich | erfüllt |
| Übersicht, Messquellen, Diagnose getrennt | erfüllt, je eigene Route und Vorlage |
| Zuordnung über die Oberfläche | erfüllt; fünf Auswahlen |
| «Keine Live-Messung vorhanden» | erfüllt, gespeichert und jederzeit änderbar |
| `testing`, `active`, `retired` | erfüllt; abgelöste Quellen bleiben in der Datenbank, die Oberfläche zeigt sie nicht |
| Sichere Aktivierung, Deaktivierung, Wechsel | Technik erfüllt; **Bestätigung fehlt** (AUD-12) |
| Einzelmessung unabhängig von der Bilanz | erfüllt |
| Keine 0-W-Annahme ohne Quelle | erfüllt: «–» in der Übersicht, `MISSING` in der Bilanz |
| Keine unvollständige Bilanz als vollständig | erfüllt |
| Keine unnötigen Änderungen an bestehenden Templates | erfüllt, alle identisch |
| Fachlicher Zustand und technischer Lebenszyklus getrennt | erfüllt: `live_meter_states` und `live_sources.state` sind getrennte Felder |
| Darstellung hinter HA Ingress | **Realtest offen** |

## 9. Tests

**Ausführung** (isolierte Exporte, Python des Systems, nichts installiert):

| Lauf | Ergebnis |
|---|---|
| Aktueller Stand im Export | 1224 bestanden, 4 übersprungen, 3 fehlgeschlagen |
| Davon 2 | `test_vnb_tariff_fetch_security`: fehlende, von Git ausgeschlossene Datei (AUD-15), kein Live-Bezug |
| Davon 1 | `test_auth::test_public_forms_are_size_limited`: Verbindungsabbruch, während zwei Suiten parallel liefen; einzeln 5 von 5 bestanden; Test und `login.py` seit `23bf860` unverändert (AUD-14) |
| Vollständige Suite im Arbeitsbaum | 1227 bestanden, 4 übersprungen (vor den letzten Änderungen an Dokumenten und an `tools/live_energy_demo.py`) |
| Alt-Tests gegen aktuellen Code | Abschnitt 4 |

Eine Zeilenabdeckung liess sich nicht messen: `coverage` ist nicht installiert und durfte nicht installiert werden. Die Bewertung beruht auf den Testinhalten.

| Bereich | Tests (Dateien, Anzahl) | Bewertung |
|---|---|---|
| Abrechnungs-Regression | `leg_billing` 17, `leg_matcher` 17, `pipeline_integration` 16, `leg_runner_*` 23, `billing_atomicity` 28, `billing_quality` 22, `database_billing` 13, `invoicing*` 38, `swiss_qr` 33, `database_contract` 52, `contract_billing_integration` 2, `web_pages_contract` 39, `web_pages_billing` 5, `f03_validation` 153 | gut, Code unverändert |
| Live-Datenmodell und Datenbank | `live_core` 103 (Anteil) | gut |
| Quellenzuordnung | `live_core`, `live_web_ha` | gut, **Nebenläufigkeit nicht getestet** |
| Sparkplug und Ausfallverhalten | `live_transport` 42, `sparkplug_*` 69 | gut mit Fake-Client, **kein echter Broker** |
| Messwertqualität und LEG-Bilanz | `live_core` (Anteil) | gut |
| GUI, Navigation, Rechte, CSRF | `live_web_ha` 45, `web_server_routing` 4, `auth` 31 | gut |
| HA-Sensoren | `live_web_ha` (Anteil) | Payload geprüft, **HA selbst nicht** |
| Add-on-Kompatibilität | `addon_runtime_config` 34 | Generator gut, **Supervisor nicht** |

**Ungetestete Pfade:**
* Worker-Thread der Live-Ebene (`start`, `_run`)
* parallele Aktivierungen und Zustandswechsel (nur in diesem Audit ausgeführt)
* WAN: Verhalten nach dem Deaktivieren einer Quelle (AUD-03), `testing`-Quelle zum Relay (AUD-04), NDEATH ohne `bdSeq` (AUD-07)
* beschädigte `live.db` (nur in diesem Audit ausgeführt)
* Zusammenspiel von `sparkplug.mode` und `wan.enabled` (AUD-05)
* vollständiger Programmstart über `main()`
* Zusammenspiel von Live-Ebene und Abrechnungszyklus (AUD-01)
* alles aus der Liste **Realtests** (Abschnitt 12)

## 10. Dokumentations-Audit (nichts geändert)

| ID | Stufe | Fundstelle | Problem | Nachweis | Auswirkung | Minimale Korrektur | Tests |
|---|---|---|---|---|---|---|---|
| DOC-01 | MITTEL | ADR-0007 «Betroffene Dateien» (Z. 1231-1254, 1260) | Überschrift «Schritt 1 (geplant)» obwohl umgesetzt. `mqtt_discovery.py` und `mqtt_runtime.py` als geändert genannt, tatsächlich unverändert. Adapter heissen anders. «Nicht betroffen: Emsomat». | Diff | Falsches Bild vom Änderungsumfang | Liste auf den Diff anpassen | keine |
| DOC-02 | MITTEL | ADR-0007 I1 (Z. 784) | `meter_values()` gibt es nicht | `service.py` | Vorgabe entspricht nicht dem Code | durch `meter_rows()`, `own_reading()` ersetzen | keine |
| DOC-03 | MITTEL | ADR-0007 §7, §9, I4 (Z. 787, 910, 1003) | «keine neue Verbindung nach aussen», «I4 später, nur bei VOLLSTÄNDIG». Tatsächlich laufen seit 0.6.8 automatisch Coordinator-Daten. | AUD-01 | Planung und Betrieb widersprechen sich | nach Entscheid AUD-01 anpassen | keine |
| DOC-04 | MITTEL | ADR-0005 Z. 51, Cross-House-Vertrag Abschnitt 2.2 und 4; `Emsomat_Shareomat_MQTT_Vertrag.md` | «nicht über eine separate Registrierung» widerspricht `allowed_source`. `is_null`, `Market/PowerTimeSource` und der 5-Minuten-Birth fehlen in den Verträgen. | Suche, Code | Verträge beschreiben nicht das Verhalten | Verträge nachführen, ADR-0005 ergänzen | keine |
| DOC-05 | MITTEL | `docs/ARCHITECTURE.md` Z. 208 ff., 241 ff., 388 ff. | Kein `live/`, keine Gruppe LIVE-ENERGIE, ADR-0007 fehlt. Vorbestehend: «Rechnungen» als Platzhalter, obwohl `PLACEHOLDER_ROUTES` leer ist. | Text gegen Code | Massgebliches Dokument veraltet | Abschnitte ergänzen | keine |
| DOC-06 | MITTEL | `Zukunft.md` §12.1 gegen ADR-0007 §3.7 | «nur eine Zahl, keine Einzelwerte anderer Haushalte» gegen «auf HA Second darf die eigene Netzeinspeisung sichtbar sein». Für den Nachbarn ist das ein fremder Einzelwert. O-3 ist offen. | Text, `live_sensors.py` | Datenschutzfrage vor dem Pilot ungeklärt | Entscheid festhalten | keine |
| DOC-07 | MITTEL | `Zukunft.md` §12.2, ADR-0007 §9 Schritt 3, Arbeitsliste LE-12 | «HA Second des Nachbarn über VPN oder Cloud-Broker», LE-12 hängt von LE-10/11 ab. Tatsächlich steht HA Second an deinem Broker, der Nachbar kommt nur per Tailscale auf dessen Oberfläche, und zunächst ohne eigene Quelle. | dein Aufbau laut Gespräch | Pilot ist anders dokumentiert, als er läuft | Pilotweg neu beschreiben | keine |
| DOC-08 | MITTEL | ADR-0007 §3.6 unter «Entscheid O-4»; `live/model.py:52` | **Annahme als Entscheid:** `max_age` 90 s statt der von dir geprüften 60 s wurde bei der Umsetzung festgelegt, ohne Rückfrage. | ADR-Fassungen, Code | Scheinbar entschiedener Wert | als Annahme markieren oder entscheiden | `test_live_core` |
| DOC-09 | MITTEL | ADR-0007 Status und K-1, K-3; Arbeitsliste | **Planung als umgesetzt:** «Schritt 1 umgesetzt», «K-1 umgesetzt» ohne Lauf an einem echten Broker. Eine automatische Sparkplug-Aktivierung steht in keinem Entscheid, nur als Dateiliste. | Abschnitt 12 | Überschätzter Reifegrad | Status «umgesetzt, Realtest offen» | keine |
| DOC-10 | NIEDRIG | Arbeitsliste Z. 13, 24; LE-08; ADR I3 | «keine Commits», «eine Aufgabe gleichzeitig» entspricht nicht dem Ablauf. «In Schritt 1 nur zwei Arten zuordenbar», die Oberfläche bietet alle Arten an. | Text gegen Code | Verwirrend | nachführen | keine |
| DOC-11 | NIEDRIG | ADR-0007 Z. 30, 838, 846, 1121; Diagramme | v4-Zeichnung ist verlinkt, aber nicht im Repository. Blueprint-Hinweis und Bestätigung sind beschrieben, nicht vorhanden. «Ursprüngliche Zeitstempel» meint den letzten bekannten Wert. Versioniert ist nur Diagramm v2 ohne Live-Teil. | Git, Code | Tote Verweise, ungenaue Aussagen | Verweise und Texte korrigieren | keine |

**Abweichungen der Implementierung von festgelegten Anforderungen:** AUD-01 (keine Auswirkung auf Bestehendes), AUD-03 (kein stehen gebliebener Wert), AUD-07 (§4.1 Regel 3 behauptet `bdSeq`-Prüfung), AUD-12 (Bestätigung), AUD-11 (§3.5 Regel 8, §3.6).

## 11. Befunde

### HOCH

#### AUD-01: Automatisches Sparkplug aktiviert den Coordinator-Zweig im Abrechnungszyklus
* **Position:** `ha_addon/generate_runtime_config.py:57-97`, `ha_addon/config.yaml:38`, `main.py:259-265`
* **Problem:** Bis 0.6.7 schrieb das Add-on nie einen Abschnitt `sparkplug`, der Zweig `if sparkplug_host is not None` war im Add-on inaktiv. Seit 0.6.8 ist `mode: auto` Standard. Damit läuft bei jedem Zyklus: Prognose berechnen und in `shareomat.db` speichern; mit hinterlegtem ENTSO-E-Token HTTP-Abrufe an ENTSO-E und SNB, und zwar unter `_RUN_LOCK`; dazu ein NCMD mit `LEG/DemandForecast`, `LEG/ExportPrice`, `LEG/FeedInPrice` an Emsomat.
* **Nachweis:** Diff des Generators; `main.py` ohne Diff in diesem Bereich; auf der Zuordnungsseite unter 0.6.7 war gemeldet, dass kein Knoten konfiguriert ist; Test `test_emsomat_on_same_home_assistant_connects_without_input`.
* **Auswirkung:** Die Berechnung der Abrechnung ändert sich nicht, Fehler werden abgefangen. Aber der Live-Wunsch «Werte anzeigen» setzt ungefragt Funktionen in Gang, die vorher aus waren. Emsomat erhält neue Eingangsdaten; die Preise wirken laut `Emsomat/shareomat/adapter.py` nur bei `leg.price_source == 'shareomat'` (deine Einstellung ist hier nicht bekannt). Bei der Einführung von 0.6.8 wurde nur der Versand an Emsomat erwähnt, nicht das Speichern und die HTTP-Abrufe. Widerspricht ADR-0007 §7, §9 und I4.
* **Minimale Korrektur:** Lesen für Live vom Coordinator-Versand trennen, zum Beispiel eigene Option mit Standard aus. Oder Standard `mode: off`.
* **Tests:** `test_addon_runtime_config.py`, `test_main_coordinator_publish.py`; neu: Live-Empfang ohne Coordinator-Versand.

#### AUD-02: Quellenidentität am lokalen Broker nicht gegeben; HA Second am selben Broker
* **Position:** Betrieb; `ha/mqtt_runtime.py:271-277` (`shareomat/cmd/run_once`), `sparkplug/host.py`
* **Problem:** Shareomat und Emsomat nutzen laut den Einstellungen dasselbe Login. Wer dieses Login hat, kann als Emsomat senden, die Werte fremder Standorte lesen und einen Abrechnungslauf über `run_once` auslösen. Welches Login HA Second nutzt, ist nicht bekannt. Mit Tailscale-Zugang zu HA Second und Adminrechten dort wären die Zugangsdaten für den Nachbarn erreichbar.
* **Nachweis:** Screenshots; Code liest den Absender nicht und kann ihn nicht prüfen. **Realtest offen.**
* **Auswirkung:** Gefälschte Anzeige, unerlaubter Abrechnungslauf, Daten anderer Teilnehmer.
* **Minimale Korrektur:** Eigenes Login für HA Second, das nur die Topics der Entity Bridge lesen darf. Eigenes Login für Emsomat (K-2). Nachbar ohne Adminrechte auf HA Second. Tailscale nur auf den Web-Port.
* **Tests:** Broker-Negativtests (LE-05); keine Unit-Tests möglich.

#### AUD-03: Nach dem Deaktivieren einer WAN-Quelle bleibt ihr letzter Wert bei Emsomat stehen
* **Position:** `sparkplug/wan_downlink.py:316-344`
* **Problem:** `_handle_ddata` und `_handle_ddeath` brechen bei nicht registrierten Paaren ab, bevor der Teilnehmer entfernt wird.
* **Nachweis:** Ausgeführt: Quelle zugeordnet, DBIRTH, Quelle deaktiviert, danach DDATA und DDEATH. Relay enthält weiterhin `{'P2': (1500.0, 0.0)}`, einziger Eintrag im Protokoll ist `register`.
* **Auswirkung:** Emsomat sieht einen eingefrorenen Wert, bis die WAN-Verbindung abbricht. Widerspricht S-L5. Nur bei aktivem WAN.
* **Minimale Korrektur:** Beim Entfernen einer Registrierung den Teilnehmer aus dem Relay nehmen; `DDEATH` vor der Registrierungsprüfung für bekannte Teilnehmer verarbeiten.
* **Tests:** `test_live_transport.py`, neuer Fall.

### MITTEL

#### AUD-04: Eine WAN-Quelle in `testing` erreicht bereits Emsomat
* **Position:** `sparkplug/wan_downlink.py:298-314`, `live/service.py:185`
* **Problem:** `is_registered_wan` gilt auch für `testing`, und `_handle_dbirth` gibt den Wert an den Relay weiter.
* **Nachweis:** Ausgeführt: Zustand `testing`, Relay `{'P2': (1500.0, 0.0)}`.
* **Auswirkung:** Ein noch nicht geprüfter Wert wird Eingang für Emsomat. Nur bei aktivem WAN.
* **Minimale Korrektur:** Relay nur bei `active`, Live-Empfang weiterhin auch bei `testing`.
* **Tests:** `test_sparkplug_wan.py`, `test_live_transport.py`.

#### AUD-05: Sparkplug aus bei vorhandenem WAN stoppt die Abrechnung
* **Position:** `shareomat/config.py:436-440`, `ha_addon/generate_runtime_config.py:85`, `main.py:237-240`
* **Problem:** `mode: off`, oder `auto` ohne installiertes Emsomat, setzt `sparkplug.enabled` auf `false`. Ist in der Datei `wan.enabled: true`, wirft `validate_leg_config` einen Fehler, und `_run_safe_cycle` überspringt den Abrechnungslauf.
* **Nachweis:** Ausgeführt: Generator mit `off`, danach `ValueError: wan.enabled is true but sparkplug.enabled is false`.
* **Auswirkung:** Eine Option für Live-Energie kann die Abrechnung anhalten. Nur mit WAN-Block in der Datei.
* **Minimale Korrektur:** Generator verhindert die Kombination, oder WAN wird dann ebenfalls ausgeschaltet.
* **Tests:** `test_addon_runtime_config.py`, `test_config.py`.

#### AUD-06: HA zeigt alte Werte weiter, wenn die Live-Ebene steht
* **Position:** `ha/live_sensors.py:97-126`
* **Problem:** Werte sind retained, die Availability hängt nur an `shareomat/status`, `expire_after` fehlt. Steht die Live-Ebene, während Shareomat läuft, bleibt der letzte Wert als aktuell stehen.
* **Nachweis:** Discovery-Payload ausgelesen: kein `expire_after`, Availability nur `shareomat/status`.
* **Auswirkung:** Eingefrorene Netzeinspeisung auf HA Second. Genau das soll die Live-Ebene vermeiden.
* **Minimale Korrektur:** `expire_after` (etwa 180 s) setzen; die vollständige Veröffentlichung alle 60 s hält die Werte frisch.
* **Tests:** `test_live_web_ha.py`; **Realtest offen.**

#### AUD-07: WAN-NDEATH ohne `bdSeq`-Prüfung
* **Position:** `sparkplug/wan_downlink.py:184`, `346-360`
* **Problem:** NBIRTH wird nicht abonniert, ein altes NDEATH wird nicht als veraltet erkannt. ADR-0007 §4.1 Regel 3 behauptet das Gegenteil. Das Verhalten bestand schon, die Live-Ebene hängt nun davon ab.
* **Nachweis:** Ausgeführt: DBIRTH, dann NDEATH einer älteren Sitzung. Relay leer, Qualität `missing`.
* **Auswirkung:** Falscher Ausfall bis zum nächsten Birth (bis 5 Minuten).
* **Minimale Korrektur:** NBIRTH abonnieren und `bdSeq` vergleichen.
* **Tests:** `test_sparkplug_wan.py`.

### NIEDRIG

| ID | Position | Problem | Nachweis | Auswirkung | Minimale Korrektur | Tests |
|---|---|---|---|---|---|---|
| AUD-08 | `live/service.py:204-216`, `237-241` | Nur Proben sind ratenbegrenzt. Sitzungsereignisse eines registrierten Standorts können die Queue füllen, der Überlauf verwirft die Werte **aller** Quellen. Warnungen für verworfene Nachrichten sind ungedrosselt. | Ausgeführt ohne Worker: 2048 Ereignisse, Emsomat danach `missing` | Verfügbarkeit, braucht einen registrierten Standort | Sitzungsereignisse begrenzen, nur betroffene Quelle verwerfen | `test_live_core.py` |
| AUD-09 | `live/service.py:466` | Aktivieren ist auch mit dem aus `live.db` wiederhergestellten Wert möglich, ohne laufende Sitzung | Ausgeführt: `nie_empfangen`, `can_activate` = `True` | Quelle ohne aktuellen Empfang aktivierbar | wiederhergestellte Werte nicht zählen | `test_live_core.py` |
| AUD-10 | `ha/live_sensors.py:191`, `ha/mqtt_runtime.py` | Paho puffert QoS-1-Nachrichten ohne Verbindung unbegrenzt | Ausgeführt: 5000 gepuffert; rund 14'000 pro Tag | Speicher bei langem Broker-Ausfall | `max_queued_messages_set` oder QoS 0 für Live-Werte | neu |
| AUD-11 | `live/model.py:52`, `main.py:273`, `overview.html:12-15` | Parameter fest im Code, beim Start nicht protokolliert. `S` und Wartezeit nur bei vollständiger Bilanz. Alter der Proben wird nicht aufgezeichnet. | Code gegen ADR §3.5 Regel 8 und §3.6 | Festlegen an realen Daten erschwert | ergänzen oder ADR anpassen | `test_live_web_ha.py` |
| AUD-12 | `live_energy.py:350`, `assign.html`, `sources.html` | Keine Bestätigung. «Keine Live-Messung vorhanden» deaktiviert eine aktive Quelle mit einem Klick. Ein Hinweistext steht auf der Seite. | Vorlage; ADR §5.1 Schritt 5 | versehentlicher Quellenverlust | Bestätigungsschritt | `test_live_web_ha.py` |
| AUD-13 | `main.py:63-67` | Live-Importe stehen auf Modulebene. Ein Importfehler im Live-Paket verhindert den Start des ganzen Programms. `_setup_live` fängt nur Laufzeitfehler. | Code | Live-Fehler könnte Abrechnung stoppen, nur bei defektem Paket | Import in `_setup_live` verlegen | `test_main_live_wiring.py` |
| AUD-14 | `tests/test_auth.py:594` | Test bricht unter Last mit `WinError 10053` ab | 1 Fehlschlag bei Parallellauf, einzeln 5 von 5 bestanden | unsichere Testergebnisse, kein Live-Bezug | Verbindungsabbruch im Test tolerieren | selbst |
| AUD-15 | `tests/test_vnb_tariff_fetch_security.py:29`, `.gitignore:252` | Tests brauchen eine ignorierte Datei | 2 Fehlschläge in sauberem Export | sauberer Checkout nicht grün, kein Live-Bezug | Datei bereitstellen oder Test überspringen | selbst |
| AUD-16 | `tests/test_live_*.py` | Keine Tests mit Threads; Worker-Thread und Programmstart ungetestet; Discovery wird nur beim Start gesendet und fehlt, wenn der Broker dann nicht erreichbar ist | Suche nach `Thread`, `.start()`: 0 Treffer | Regressionen unbemerkt; Sensoren erst nach Neustart | Tests ergänzen, Discovery beim Wiederverbinden | neu |

## 12. Realtest offen

| Test | Umgebung | Befund, LE |
|---|---|---|
| ACL-Negativtests: Fremdes Login darf nicht unter fremder Kennung senden | isolierter Test-Broker | AUD-02, LE-05 |
| Emsomat liefert Werte, NBIRTH, `seq`, Rebirth, kein Rebirth-Dauerlauf | Testinstanz | LE-03, LE-05 |
| Emsomat und Shareomat am selben Broker (in beiden unterschiedlich eingetragen) | Testinstanz | LE-05 |
| Wirkung der LEG-Daten auf Emsomat | Testinstanz | AUD-01 |
| HA-Sensoren: Discovery, `None` wird `unknown`, `default_entity_id`, Wiederanlauf | HA-Testinstanz | LE-09 |
| Entity Bridge und HA Second | Testinstanz | LE-09, H-2 |
| Add-on-Maske: Abschnitt `sparkplug`, Übersetzungen | HA-Testinstanz | Add-on 0.6.8 |
| Ansicht hinter HA Ingress | HA-Testinstanz | LE-08 |
| 90 s gegen reale Meldeintervalle | reale Daten | DOC-08 |
| WAN zwischen zwei Standorten, 5-Minuten-Birth | Test-Broker | LE-05, K-1 |

## Anhang: Nachvollziehen der Fehlerfälle

Alle Fälle liefen auf temporären Datenbanken mit Fake-MQTT-Clients gegen einen Export von `93daa3e`.

| Fall | Vorgehen | Ergebnis |
|---|---|---|
| Migration | Datenbank mit Code `23bf860` anlegen und füllen, zweimal `init_db` des aktuellen Codes, Vergleich von `sqlite_master` und Tabelleninhalten, danach mit altem Code öffnen | Abschnitt 5 |
| AUD-03, AUD-04 | `WanDownlink` mit `LiveStatusService` und Fake-Relay, WAN-Quelle anlegen, DBIRTH, `retire_live_source`, `reload_masterdata`, DDATA, DDEATH | Relay behält den Wert |
| AUD-05 | Konfiguration mit `wan.enabled`, Generator mit `mode: off`, `validate_leg_config` | `ValueError` |
| AUD-07 | DBIRTH, dann NDEATH mit älterem Zeitstempel und anderem `bdSeq` | Teilnehmer entfernt |
| AUD-08 | ohne Worker `QUEUE_SIZE + 5` `SessionStart` eines WAN-Standorts, dann `process_pending` | alle Quellen `missing` |
| AUD-09 | Wert in `live.db` ablegen, Dienst neu starten, `can_activate` | `True` |
| AUD-10 | Paho-Client ohne Verbindung, 5000 QoS-1-Publish | 5000 gepuffert |
| Beschädigte `live.db` | Datei mit Fremdinhalt, Dienst starten | läuft, `shareomat.db` intakt |
| Nebenläufigkeit | Threads mit `Barrier`: 8 Aktivierungen; 30 Runden Aktivieren gegen Zustand setzen | keine Verletzung |
