# MQTT-Sicherheitskonzept (Audit-Befund AUD-02)

**Stand:** 08.11.2026, 2. Fassung nach der Gegenprüfung. Entwurf zur späteren Freigabe. **Nichts davon ist umgesetzt.** Es wurden keine Benutzer angelegt, keine ACLs, Passwörter oder Verbindungen geändert.

## 1. Ziel und Ausgangslage

Jeder MQTT-Teilnehmer bekommt ein eigenes Login und nur die Topics, die er braucht. Nur der Broker kann die Herkunft einer Nachricht durchsetzen: Shareomat sieht als Empfänger nicht, wer publiziert hat.

**Heute:** Shareomat und Emsomat nutzen laut Einstellungen dasselbe Login. Wer dieses Login hat, kann:
* als Emsomat Messwerte einspeisen,
* über `shareomat/cmd/run_once` einen Abrechnungslauf auslösen,
* den Auto-Scan über `shareomat/auto_scan/set` umschalten,
* die Werte fremder Standorte auf `spBv1.0/emsomat/DDATA/#` lesen.

Welches Login HA Second tatsächlich nutzt und ob heute eine ACL aktiv ist, ist nicht bekannt und vor der Umsetzung zu prüfen.

## 2. Architektur und Zuständigkeiten

```text
Shareomat → (MQTT Discovery) → HA Main → HA Entity Bridge → HA Second
```

* **Shareomat** stellt nur seine Sensoren bereit. Es kennt weder die Topics noch die Profile der Bridge und braucht sie nicht zu kennen.
* **HA Main** erhält die Sensoren über seine MQTT-Integration.
* **Die HA Entity Bridge ist ein eigenständiges System.** Sie verwaltet ihre Topics, Discovery-Namensräume, Profile und ACL-Vorlagen selbst, einschliesslich der Rückbefehle.
* **HA Second** stellt die übertragenen Entities dar.

Dieses Konzept legt für die Bridge **keine eigenen Topics oder Regeln** fest (Abschnitt 5).

## 3. Logins und Vertrauensgrenzen

| Teilnehmer | Login (Vorschlag) | Broker | Grundsatz |
|---|---|---|---|
| Shareomat (3 Verbindungen, gleiches Login) | `shareomat_main` | lokal | nur die Topics aus Abschnitt 4 |
| Emsomat | `emsomat_local` | lokal | nur die Topics aus Abschnitt 4 (K-2) |
| HA Entity Bridge, HA Second | je Profil, von der Bridge vorgegeben | lokal | Vorlage der Bridge (Abschnitt 5) |
| Nachbar | **kein Login** | – | Abschnitt 6 |
| Spätere externe Quelle | ein Login pro Gerät | noch festzulegen | Abschnitt 7 |
| Fremder Standort | ein Login pro Standort | zentraler WAN-Broker | Cross-House-Vertrag, Abschnitt 6.3 |

**Bestehende Vertrauensgrenze: `homeassistant` und `addons`.** Das sind die internen Konten der Mosquitto-App. Laut der Dokumentation der Entity Bridge (`docs/SECURITY.md`, Abschnitt 4) brauchen sie Vollzugriff (`topic readwrite #`). Das bedeutet ausdrücklich:

* HA Main und jedes Add-on, das diese internen Zugangsdaten bekommt, können alle Topics lesen und schreiben, auch `shareomat/cmd/run_once`.
* Die ACL schützt also **nicht** gegen ein kompromittiertes HA Main oder Add-on. Sie trennt die Teilnehmer mit eigenem Login.
* Shareomat und Emsomat dürfen deshalb **nicht** mit einem dieser beiden Konten laufen.

## 4. Rechte von Shareomat und Emsomat

Grundlage sind die tatsächlichen Publish-, Subscribe- und Will-Aufrufe im Code. Werte: Topic-Präfix `shareomat`, Discovery `homeassistant`, Gruppe `emsomat`, Knoten `emsomat-node`, Relay `shareomat-relay`, Host `shareomat`.

### `shareomat_main`

| Recht | Topic | Wofür |
|---|---|---|
| write | `shareomat/status` | Status, auch als Will |
| write | `shareomat/last_run`, `shareomat/inbox_count`, `shareomat/report_count`, `shareomat/last_error` | Systemsensoren |
| write | `shareomat/billing/#` | Abrechnungssensoren |
| write | `shareomat/live/#` | Live-Energie-Sensoren |
| write | `shareomat/auto_scan/state` | Zustand des Schalters |
| read | `shareomat/cmd/run_once`, `shareomat/auto_scan/set` | Befehle von HA Main |
| write | `homeassistant/button/shareomat_engine_run_now/config` | Discovery Schaltfläche |
| write | `homeassistant/switch/shareomat_engine_auto_scan/config` | Discovery Schalter |
| write | `homeassistant/sensor/+/config` | Discovery Sensoren (Restlücke, Abschnitt 10) |
| write | `spBv1.0/STATE/shareomat` | Primary-Host-Status, auch als Will |
| read | `spBv1.0/emsomat/NBIRTH/emsomat-node`, `.../NDATA/emsomat-node`, `.../NDEATH/emsomat-node` | Emsomat empfangen |
| write | `spBv1.0/emsomat/NCMD/emsomat-node` | Rebirth und LEG-Daten |
| write | `spBv1.0/emsomat/NBIRTH/shareomat-relay`, `.../NDEATH/shareomat-relay` | Relay, auch als Will |
| write | `spBv1.0/emsomat/DBIRTH/shareomat-relay/#`, `.../DDATA/shareomat-relay/#`, `.../DDEATH/shareomat-relay/#` | Teilnehmerwerte an Emsomat |
| read | `spBv1.0/emsomat/NCMD/shareomat-relay` | Rebirth an den Relay |

**`shareomat_main` darf `shareomat/cmd/run_once` und `shareomat/auto_scan/set` nicht schreiben.** Auch NCMD, NDATA und DCMD für den Relay sind nicht erlaubt, weil der Relay sie nicht sendet.

### `emsomat_local`

| Recht | Topic | Wofür |
|---|---|---|
| write | `spBv1.0/emsomat/NBIRTH/emsomat-node`, `.../NDATA/emsomat-node`, `.../NDEATH/emsomat-node` | eigene Werte, auch als Will |
| read | `spBv1.0/emsomat/NCMD/emsomat-node` | Rebirth und LEG-Daten |
| read | `spBv1.0/STATE/shareomat` | Host-Status |
| read | `spBv1.0/emsomat/DBIRTH/#`, `.../DDATA/#`, `.../DDEATH/#` | Teilnehmer aus dem Relay |

**Das frühere `readwrite emsomat/#` entfällt.** Die Funktionen für `emsomat/node/...`, `emsomat/core/...` und `emsomat/market/...` im MQTT-Adapter von Emsomat werden im Code nirgends aufgerufen. Tatsächlich genutzt werden nur die Sparkplug-Topics (`sparkplug/edge_node.py`). Vor der Umsetzung am Broker-Log bestätigen.

### Befehle an Shareomat

| Topic | Schreiben darf | Lesen darf |
|---|---|---|
| `shareomat/cmd/run_once` | nur HA Main (Schaltfläche in HA) | `shareomat_main` |
| `shareomat/auto_scan/set` | nur HA Main | `shareomat_main` |

Kein anderes eigenes Login erhält Schreibrecht. Shareomat verwirft Befehle mit Retain-Flag bereits. Wird die Schaltfläche nicht gebraucht, stellt der vorhandene Add-on-Schalter `command_topic_enabled` die Befehle ganz ab. Das ist eine eigene Entscheidung.

### Vorlage der ACL für Shareomat und Emsomat (nicht aktiv)

```text
# Interne Konten, unverändert laut Dokumentation der Mosquitto-App (Abschnitt 3)
user addons
topic readwrite #
user homeassistant
topic readwrite #

user shareomat_main
topic write shareomat/status
topic write shareomat/last_run
topic write shareomat/inbox_count
topic write shareomat/report_count
topic write shareomat/last_error
topic write shareomat/billing/#
topic write shareomat/live/#
topic write shareomat/auto_scan/state
topic read shareomat/cmd/run_once
topic read shareomat/auto_scan/set
topic write homeassistant/button/shareomat_engine_run_now/config
topic write homeassistant/switch/shareomat_engine_auto_scan/config
topic write homeassistant/sensor/+/config
topic write spBv1.0/STATE/shareomat
topic read spBv1.0/emsomat/NBIRTH/emsomat-node
topic read spBv1.0/emsomat/NDATA/emsomat-node
topic read spBv1.0/emsomat/NDEATH/emsomat-node
topic write spBv1.0/emsomat/NCMD/emsomat-node
topic write spBv1.0/emsomat/NBIRTH/shareomat-relay
topic write spBv1.0/emsomat/NDEATH/shareomat-relay
topic write spBv1.0/emsomat/DBIRTH/shareomat-relay/#
topic write spBv1.0/emsomat/DDATA/shareomat-relay/#
topic write spBv1.0/emsomat/DDEATH/shareomat-relay/#
topic read spBv1.0/emsomat/NCMD/shareomat-relay

user emsomat_local
topic write spBv1.0/emsomat/NBIRTH/emsomat-node
topic write spBv1.0/emsomat/NDATA/emsomat-node
topic write spBv1.0/emsomat/NDEATH/emsomat-node
topic read spBv1.0/emsomat/NCMD/emsomat-node
topic read spBv1.0/STATE/shareomat
topic read spBv1.0/emsomat/DBIRTH/#
topic read spBv1.0/emsomat/DDATA/#
topic read spBv1.0/emsomat/DDEATH/#

# Für die HA Entity Bridge: Vorlage aus ihrem Panel einfügen (Abschnitt 5)
```

**Wichtig:** Sobald eine ACL aktiv ist, ist alles nicht Aufgeführte verboten. Ein Login ohne Eintrag verliert ohne Fehlermeldung jeden Zugriff. Alle bestehenden Clients am Broker müssen vorher erfasst werden.

## 5. HA Entity Bridge

* **Massgebend ist die Bridge selbst.** Ihre ACL-Vorlage erzeugt sie pro Profil aus ihren eigenen Topics: Panel → Status → ACL-Vorlage (`custom_components/ha_entity_bridge/acl.py`, Beschreibung in `docs/SECURITY.md`, Abschnitt 4). Das Repository wurde für diese Fassung geprüft.
* Dieses Konzept schreibt weder Topics noch Discovery-Namensräume der Bridge vor und schränkt ihre Funktionen nicht ein. Die bidirektionale Funktion bleibt erhalten.
* **Ob Rückbefehle erlaubt sind, richtet sich nach dem Bridge-Profil.** Für ein reines Anzeigeprofil können sie entfallen. Andere Profile mit Steuerungsfunktionen bleiben davon unberührt.
* Für den aktuellen Nachbar-Pilot werden nur freigegebene Live-Energie-Sensoren zur Anzeige übertragen. Dafür ist ein reines Anzeigeprofil vorgesehen.
* Aus Sicht von Shareomat gilt nur: Das Login von HA Second bekommt keine Rechte auf `shareomat/#`, `homeassistant/#`, `spBv1.0/#`.

## 6. Nachbarzugriff

Der Nachbar erhält später ausschliesslich Zugriff auf die freigegebene Oberfläche von HA Second:

* keinen Zugang zur Shareomat-Administration,
* keinen Zugang zu HA Main,
* keinen allgemeinen MQTT-Broker-Zugang,
* keine Administratorrechte auf HA Second, weil ein Administrator an die dort gespeicherten MQTT-Zugangsdaten käme.

Die Netzwerkfreigabe über Tailscale wird separat eingerichtet und geprüft. Vorher zu klären: O-3 und DOC-06 aus dem Audit, also ob der Nachbar deine Einzelwerte sehen darf oder nur den LEG-Status.

## 7. Spätere externe Quellen und WAN

* Ein Login pro Gerät, TLS, Schreibrecht nur auf das eigene Geräte-Topic, kein Leserecht auf fremde Daten.
* Das Topic-Schema des Gateway-Eingangs ist noch nicht festgelegt (LE-11). Regeln werden mit dem Gateway-Vertrag ergänzt.
* Gesperrt wird an beiden Stellen: Login am Broker löschen und Quelle in Shareomat deaktivieren.
* WAN: Vor produktiver Nutzung müssen AUD-03, AUD-04 und AUD-07 behoben sein. Regeln: Cross-House-Vertrag, Abschnitt 6.3.

## 8. Nachweis vor der Freigabe (isolierter Test-Broker)

| Nr. | Test | Erwartung |
|---|---|---|
| 1 | `shareomat_main` publiziert auf `shareomat/cmd/run_once` und auf Emsomats NBIRTH-Topic | kommt nicht an |
| 2 | `shareomat_main` publiziert NCMD unter `shareomat-relay` | kommt nicht an |
| 3 | `emsomat_local` publiziert auf `shareomat/cmd/run_once` und abonniert `shareomat/#` | kein Lauf, keine Nachrichten |
| 4 | Login von HA Second abonniert `shareomat/#`, `spBv1.0/#`, `homeassistant/#` | keine Nachrichten |
| 5 | Login von HA Second publiziert auf `shareomat/cmd/run_once` und Emsomats NBIRTH-Topic | kommt nicht an |
| 6 | Shareomat und Emsomat verbinden sich, Werte, Rebirth, LEG-Daten und alle Will-Nachrichten funktionieren | funktioniert |
| 7 | Schaltfläche «Run Now» in HA Main löst einen Lauf aus | funktioniert |
| 8 | Bridge und HA Second zeigen die freigegebenen Entities, Profile mit Rückbefehlen bleiben nutzbar | funktioniert |
| 9 | Broker-Log zeigt keine verweigerten, aber benötigten Topics | keine |

Erst danach darf in Live-Energie der Identitätsnachweis eingetragen werden.

## 9. Reihenfolge der Umsetzung (nach deiner Freigabe)

1. Bestehende Clients am Broker erfassen (Log der Mosquitto-App).
2. Tests aus Abschnitt 8 auf einem isolierten Broker.
3. Backup der Mosquitto-Konfiguration, Rückweg: `customize.active: false`.
4. Logins anlegen, ACL aktivieren, zuerst nur Shareomat und Emsomat umstellen.
5. Bridge und HA Second mit der Vorlage der Bridge verbinden.
6. Tests am echten Broker wiederholen.

## 10. Offene Punkte

* Die Emsomat-Topics stammen aus dem Code und müssen am Broker-Log bestätigt werden.
* `homeassistant/sensor/+/config` erlaubt Shareomat auch fremde Sensor-Discovery, weil die Kennungen der Abrechnungssensoren dynamisch sind und `+` nur ganze Topic-Ebenen abdeckt.
* Ändern sich Knoten-ID, Gruppe, Topic-Präfix oder Discovery-Präfix, muss die ACL angepasst werden.
