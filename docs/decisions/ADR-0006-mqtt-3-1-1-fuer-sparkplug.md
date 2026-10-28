# ADR-0006: MQTT 3.1.1 statt 5.0 für die Sparkplug-B-Kommunikation

## Status

Angenommen. Ersetzt die MQTT-Version in ADR-0004 (5.0 → 3.1.1); alle anderen
Punkte von ADR-0004 gelten unverändert.

## Kontext

ADR-0004 und `docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md` legen für
die lokale Emsomat↔Shareomat-Kommunikation **MQTT 5.0** + Eclipse Sparkplug
B 3.0 fest. Bei der Prüfung des Architekturdiagramms (28.10.2026) zeigte
sich, dass beide Programme tatsächlich **MQTT 3.1.1** sprechen:

* **Emsomat** setzt ausdrücklich `protocol=paho_mqtt.MQTTv311` und
  `clean_session=True` (`Emsomat/mqtt/adapter.py`).
* **Shareomat** erzeugt `mqtt.Client(client_id=…)` ohne Protokollangabe;
  paho verwendet dann MQTT 3.1.1 (`shareomat/sparkplug/host.py`,
  `participant_relay.py`, `wan_downlink.py`, `shareomat/ha/mqtt_runtime.py`).

Beide Seiten arbeiten damit zusammen. Die Dokumentation wich vom Code ab,
nicht umgekehrt.

## Problem

Code und angenommenes ADR widersprechen sich. Entweder werden beide
Programme auf MQTT 5.0 umgebaut, oder die Entscheidung wird an den Code
angepasst.

## Entscheidung

Emsomat und Shareomat verwenden für Sparkplug B (lokal und Cross-House)
**MQTT 3.1.1** mit `clean_session=True`. MQTT 5.0 wird nicht eingeführt,
solange keine konkrete MQTT-5-Funktion gebraucht wird.

## Begründung

* Beide Programme sprechen bereits übereinstimmend 3.1.1 und sind so
  zusammen getestet. Ein Wechsel hätte beide gleichzeitig verändert, ohne
  fachlichen Nutzen.
* Keine Funktion, die nur MQTT 5 bietet (Session Expiry, User Properties,
  Reason Codes, Topic Aliases), wird verwendet. Sparkplug verlangt ohnehin
  eine saubere Session ("clean session MUST always be set to true") und
  formuliert durchgehend in MQTT-3.1.1-Begriffen (siehe Vertrag
  Abschnitt 28.3).
* Assumption: Sparkplug B 3.0 lässt MQTT 3.1.1 und 5.0 gleichermassen zu.
  Needs verification: gegen die vollständige Sparkplug-3.0-Spezifikation
  (PDF) gegenprüfen.
* Mosquitto (HA-Add-on) unterstützt 3.1.1 vollständig; die Version ist auch
  für die Broker-Seite unkritisch.

Verworfen: Umbau beider Programme auf MQTT 5.0. Kein Nutzen heute, aber
Aufwand und Testbedarf auf beiden Seiten.

## Konsequenzen

* ADR-0004, der Vertrag und die Kommunikationsarchitektur nennen MQTT 3.1.1
  (bzw. verweisen auf dieses ADR).
* Das Architekturdiagramm nennt keine MQTT-Version, nur Sparkplug B.
* Shareomat setzt die Version bisher nur implizit (paho-Standard). Wechselt
  paho später seinen Standard, müsste Shareomat `protocol=MQTTv311`
  ausdrücklich setzen, wie Emsomat es tut.
* Die Kopie des Vertragsdokuments im Emsomat-Repo ist entsprechend
  nachzuziehen.

## Betroffene Dateien

* Shareomat: `shareomat/sparkplug/host.py`, `participant_relay.py`,
  `wan_downlink.py`, `wan_uplink.py`, `shareomat/ha/mqtt_runtime.py`
* Emsomat: `Emsomat/mqtt/adapter.py`
* Doku: `docs/decisions/ADR-0004-…`,
  `docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md`,
  `docs/Architektur/Kommunikations_und_Sicherheitsarchitektur.md`

## Überprüfung

Neu bewerten, sobald eine Funktion gebraucht wird, die nur MQTT 5 bietet,
oder wenn ein Broker bzw. eine Gegenstelle nur noch MQTT 5 anbietet.
