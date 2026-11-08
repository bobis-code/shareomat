# Shareomat – Zukunft: vernetzte LEG-Liveplattform

**Status:** Konzept / Ideenstand, noch keine Implementierungsfreigabe  
**Datum:** 07.11.2026  
**Projekt:** Shareomat (LEG-Verwaltung und -Abrechnung)  
**Verwandtes Projekt:** Emsomat (lokales Energiemanagement)

> **Dieses Dokument ist ein Zukunftskonzept, kein Auftrag zur unmittelbaren
> Implementierung oder Internetfreigabe.** Es beschreibt, wohin sich
> Shareomat langfristig entwickeln soll. Verbindlich für den heutigen Code
> bleiben [`ARCHITECTURE.md`](ARCHITECTURE.md) und die Dokumente unter
> [`Architektur/`](Architektur/) sowie die ADRs unter
> [`decisions/`](decisions/). Abschnitt 9 zeigt, wo dieses Konzept auf dem
> Bestand aufbaut und wo es davon abweicht.

## 1. Grundidee

Shareomat soll langfristig neben der heutigen LEG-Verwaltung und Abrechnung als **online erreichbare Live-Energieplattform** für die Teilnehmer einer lokalen Elektrizitätsgemeinschaft (LEG) dienen.

Bei jedem teilnehmenden Haushalt kann ein kleines Gerät beziehungsweise Gateway an der Kundenschnittstelle des Stromzählers installiert werden. Dieses Gerät erfasst den aktuellen **Netzbezug und die Netzeinspeisung am Netzanschlusspunkt** und übermittelt die Messwerte über eine gesicherte Verbindung an Shareomat.

Shareomat sammelt die Daten der teilnehmenden Messpunkte, berechnet daraus laufend die **aktuelle LEG-Bilanz** und macht die für den jeweiligen Teilnehmer freigegebenen Informationen über eine Online-App und MQTT verfügbar.

**Die zentrale Trennung bleibt verbindlich:**

- **Shareomat misst nicht selbst und steuert keine fremden Haushaltsgeräte:** Es sammelt Messdaten, berechnet den LEG-Zustand, zeigt ihn an und verteilt freigegebene Informationen.
- **Emsomat steuert lokal:** Ein Teilnehmer kann die LEG-Livedaten an sein eigenes Emsomat übergeben und damit beispielsweise Boiler, Wärmepumpe oder Wallbox optimieren.
- **Teilnahme ohne Emsomat ist möglich:** Wer möchte, sieht die Werte in der App und schaltet Geräte von Hand oder nutzt eine andere lokale Automatisierung.
- **Die Messdaten sollen bidirektional fliessen:** Ein Teilnehmer sendet seine eigenen Zählerwerte an Shareomat und kann freigegebene LEG-Daten sowie seine eigenen Daten wieder abrufen, insbesondere via MQTT.

## 2. Beispiel aus der Praxis

Ein Nachbar erhält ein Zähler-Gateway. Es liest laufend, ob sein Haushalt Strom aus dem öffentlichen Netz bezieht oder einspeist. Diese aktuellen Messwerte werden an Shareomat übertragen.

Shareomat erhält gleichzeitig die Netto-Messwerte des PV-Produzenten und weiterer LEG-Teilnehmer. In der App sieht der Nachbar beispielhaft:

- **LEG-Export:** 6,0 kW
- **LEG-Import:** 2,0 kW
- **Aktueller rechnerischer Überschuss:** +4,0 kW

Er kann daraufhin seinen Boiler von Hand einschalten. Alternativ erhält sein lokales Emsomat den LEG-Überschuss über MQTT und entscheidet anhand eigener Regeln, ob und wie es den Boiler ansteuert.

Wenn andere Haushalte ebenfalls Verbraucher zuschalten, verändert sich die gemessene Bilanz. Shareomat aktualisiert die Anzeige und die abonnierten MQTT-Werte.

**Wichtig:** Der angezeigte Überschuss ist eine momentane, gemeinsam verfügbare rechnerische Grösse, **keine reservierte Leistung** für einen einzelnen Teilnehmer. Auch negative Bilanzwerte (LEG-Defizit) müssen sichtbar sein.

## 3. Vorgeschlagene Systemarchitektur

```text
  HAUSHALT A – PRODUZENT                       HAUSHALT B – NACHBAR
  Stromzähler / Netzanschlusspunkt            Stromzähler / Netzanschlusspunkt
              |                                           |
      lokales Zähler-Gateway                       lokales Zähler-Gateway
              |                                           |
      lokale Messdaten (MQTT)                      lokale Messdaten (MQTT)
              |                                           |
       Emsomat (optional)                          Emsomat (optional)
              |                                           |
              +----------- gesicherte Datenübertragung ---+
                                      |
                                      v
                       SHAREOMAT – ONLINE-SERVER
                  Geräteidentitäten / Teilnehmerrechte
                         Empfang der Live-Messwerte
                    Plausibilität / Qualität / Zeitstempel
                         Berechnung der LEG-Bilanz
                      Zeitreihe und Teilnehmer-Ansicht
                       Verteilung über API und MQTT
                                      |
                       +--------------+--------------+
                       |                             |
                 Web-/Mobil-App                Freigegebene MQTT-Daten
                       |                             |
                 manuelle Entscheidung         lokales Emsomat / anderes EMS
                                                     |
                                              Steuerung vor Ort
```

**Wichtig zur Architektur:** Das Nachbar-Gateway soll selbst eine **ausgehende Verbindung** aufbauen. Ein offener eingehender Port am Heimnetz des Nachbarn ist nicht Voraussetzung. Lokale Zählerwerte sollen auch lokal verfügbar bleiben, damit sein EMS nicht von der Cloud-Verbindung abhängt.

## 4. Datenmodell und Berechnung der Live-Bilanz

Für jeden autorisierten Netzanschlusspunkt erfasst Shareomat mindestens:

| Feld | Bedeutung |
|---|---|
| Teilnehmer-/Messpunkt-ID | Eindeutige, mandantenbezogene Zuordnung |
| Netzbezug (kW) | Momentane positive Leistungsaufnahme aus dem öffentlichen Netz |
| Netzeinspeisung (kW) | Momentane positive Abgabe an das öffentliche Netz |
| Zeitstempel | Wann am Zähler erfasst bzw. gemessen |
| Qualitäts- und Aktualitätsstatus | Gültigkeit, Kommunikationsstatus, Messwertalter |
| Quelle/Gerät | Identität und Typ des liefernden Gateways |

Für ein vereinfachtes Live-Anzeigemodell:

- **Gesamtexport:** Summe der momentanen Einspeiseleistung aller gültigen, erfassten LEG-Anschlusspunkte.
- **Gesamtimport:** Summe ihrer momentanen Bezugsleistung.
- **LEG-Nettobilanz:** `Gesamtexport – Gesamtimport` in kW.
- **Überschuss:** `max(LEG-Nettobilanz, 0)`.
- **Defizit:** `max(-LEG-Nettobilanz, 0)`.
- **Rechnerisch zeitgleich deckbarer LEG-Verbrauch:** `min(Gesamtexport, Gesamtimport)` als zusätzliche Live-Kennzahl.

Die Vorzeichenkonvention muss in API, MQTT und App einheitlich sein: **positiv = Überschuss**, **negativ = Defizit**.

**Fachliche Grenze:** Diese Kennzahlen sind zunächst Live-Indikatoren. Sie ersetzen **nicht** die verbindliche LEG-Abrechnung mit den dafür massgebenden Viertelstundenwerten, Teilnehmerberechtigungen, Tarifen und Vorgaben des Netzbetreibers. Die Details der tatsächlichen energierechtlichen LEG-Zuordnung werden vor Umsetzung fachlich validiert.

Wenn Teilnehmerdaten fehlen, zu alt oder widersprüchlich sind, darf Shareomat keinen unqualifizierten „vollständigen LEG-Überschuss“ behaupten. Die App muss dann beispielsweise **„teilweise Daten / unvollständige Bilanz“** zeigen; MQTT muss einen Datenqualitätsstatus mitliefern.

## 5. Funktionen der Teilnehmer-App

**MVP / erste Ausbaustufe:**

- Anmeldung für jeden Teilnehmer mit individuellen Berechtigungen.
- Eigener aktueller Netzbezug beziehungsweise eigene Einspeisung.
- LEG-Gesamtexport, Gesamtimport, Nettobilanz, Überschuss und Defizit.
- Verständliche visuelle Anzeige mit Messwertalter und Datenqualität.
- Verlauf der Live-Werte, soweit freigegeben.
- Anzeige bei Verbindungsverlust und veralteten Messwerten.
- Möglichkeit, die Daten **manuell** für Entscheidungen über eigene Verbraucher zu verwenden.

**Später denkbar:**

- Prognosen und Benachrichtigungen (z. B. „LEG-Überschuss vorhanden“).
- Verknüpfung mit individuellen Stromkosten und bereits freigegebenen Abrechnungsdaten.
- Mobile App/PWA und ein vereinfachtes Dashboard für Teilnehmer ohne eigenes Home Assistant.
- Einbindung weiterer lokaler Energiemanagementsysteme neben Emsomat.

Die App erhält nicht automatisch Einblick in die Einzelmesswerte anderer Haushalte. **Aggregierte LEG-Werte und eigene Messwerte** sind der Normalfall; zusätzliche Daten brauchen eine explizite Berechtigung.

## 6. MQTT und Emsomat – Rollen und Grenzen

Ein Teilnehmer-Gateway muss seine **eigenen** Messwerte an Shareomat liefern können. Gleichzeitig soll es möglich sein, die **eigenen Messwerte und die erlaubten aggregierten LEG-Werte** per MQTT zu beziehen.

Illustratives Topic-Schema, **noch keine verbindliche API** (zum Verhältnis zur bestehenden Sparkplug-B-Topic-Struktur siehe Abschnitt 9.3):

```text
shareomat/v1/<leg-id>/meter/<meter-id>/telemetry     # Gateway veröffentlicht eigene Daten
shareomat/v1/<leg-id>/live/balance                     # Shareomat veröffentlicht LEG-Bilanz
shareomat/v1/<leg-id>/live/quality                     # Aktualität / Qualität / Vollständigkeit
shareomat/v1/<leg-id>/participant/<id>/own              # nur eigene, freigegebene Werte
```

Zugriffsrechte sind **nicht** allein aus der Topic-Schreibweise abzuleiten, sondern auf dem Broker beziehungsweise im API-Backend zu erzwingen. Jede Teilnehmer- und Geräteidentität benötigt eigene Berechtigungen.

**Emsomat bleibt die lokale Steuerinstanz.** Ein MQTT-LEG-Signal ist nur eine Eingangsgrösse für dessen Optimierung. Emsomat berücksichtigt weiterhin eigene Sensoren, Gerätegrenzen, Schutzverriegelungen und manuelle Vorgaben. Bei Kommunikationsverlust muss das lokale Energiemanagement sinnvoll und sicher weiterarbeiten.

Für die vorhandene Sparkplug-/WAN- und ParticipantRelay-Implementierung wird später geprüft, welche Teile wir wiederverwenden können. **Die bestehende Lösung ist ein Ausgangspunkt, aber noch keine nachgewiesene internetfähige Mehrteilnehmerplattform.**

## 7. Sicherheit und Datenschutz vor Internetfreigabe

Sobald Shareomat öffentlich erreichbar ist, gelten andere Anforderungen als für ein lokales Add-on:

1. **HTTPS und gesicherte MQTT-Verbindungen** mit sauberer Zertifikatsprüfung; keine ungeschützten Zugangsdaten über das Internet.
2. **Eigene Geräteidentität** pro Gateway, gesondert von Benutzerkonten; Zugang sperren und erneuern können.
3. **Broker-ACLs und serverseitige Autorisierung:** Nur eigene Messwerte publizieren, nur erlaubte Daten abonnieren. Keine Steuerung anderer Haushalte.
4. **Trennung der LEG-Gemeinschaften:** Wenn später mehrere LEG betrieben werden, echte Mandantentrennung in Datenbank, API, MQTT und Hintergrunddiensten.
5. **Robuste Annahme der Messwerte:** Zeitstempel, Plausibilität, veraltete Daten, Wiederholungen, Identität und Ratenbegrenzung.
6. **Keine zentrale Pflichtsteuerung:** Ein Ausfall von Shareomat darf lokale Boiler-, Wärmepumpen- oder Wallbox-Regelungen nicht blockieren.
7. **Datensparsamkeit:** Teilnehmer sehen grundsätzlich nur eigene und aggregierte Werte; geregelte Aufbewahrungsfristen sowie Lösch- und Exportfunktionen.
8. **Update- und Recovery-Konzept** für Gateways sowie sicherer Umgang mit verlorenen Geräten und kompromittierten Zugangsdaten.
9. **Keine Freigabe allein aufgrund grüner Unit-Tests:** zusätzliche Integrationstests, echte ACL-Negativtests, Netztrennung, Ausfall- und Lasttests und unabhängiger Security-Review.

## 8. Abgrenzung zur heutigen Abrechnung

Die jetzige Shareomat-Abrechnung bleibt als eigener, fachlich verbindlicher Ablauf bestehen. Eine Live-MQTT-Messung wird **nicht automatisch** zu einer abrechnungsrelevanten Viertelstundenmessung.

| Live-Plattform | Abrechnung |
|---|---|
| Sekundennahe Leistungswerte (kW) | Anerkannte Energie-/Viertelstundenwerte (kWh) |
| Schnelle Visualisierung und Optimierung | Nachvollziehbare, freigebbare Rechnungsdaten |
| Teilweise verfügbare Daten mit Status erlaubt | Vollständigkeits- und Konfliktprüfung vor Freigabe |
| MQTT / App / lokale EMS | Messdatenimport / Entwurf / Freigabe / Rechnung |

Eine spätere Integration dieser Datenpfade muss explizit geplant und überprüft werden; keine stillschweigende Vermischung.

Das entspricht der bereits bestehenden Regel: Shareomat rechnet ausschliesslich mit offiziellen Messdaten des Netzbetreibers ab (README, Abschnitt „Architektur“), und Live-Telemetrie und Abrechnungsdaten sind getrennte Datenklassen ([`Kommunikations_und_Sicherheitsarchitektur.md`](Architektur/Kommunikations_und_Sicherheitsarchitektur.md) §39.1 vs. §39.3).

## 9. Bezug zur bestehenden Architektur

Das Konzept fängt nicht bei null an. Ein grosser Teil der Leitplanken ist
bereits in [`Kommunikations_und_Sicherheitsarchitektur.md`](Architektur/Kommunikations_und_Sicherheitsarchitektur.md)
(v0.2), im [Cross-House-Sparkplug-Vertrag](Architektur/Shareomat_CrossHouse_Sparkplug_Vertrag.md),
im [WAN-Broker-Deployment-Vorschlag](Architektur/Zentraler_WAN_Broker_Deployment_Vorschlag.md)
und in [ADR-0004](decisions/ADR-0004-sparkplug-b-lokale-emsomat-shareomat-kommunikation.md)
bis [ADR-0006](decisions/ADR-0006-mqtt-3-1-1-fuer-sparkplug.md) festgelegt.
Wenn die Live-Plattform konkret wird, gehört ihre Entscheidung in ein eigenes ADR.

### 9.1 Was bereits passt

| Konzept (dieses Dokument) | Bestand |
|---|---|
| Zentrale Berechnung der LEG-Bilanz | Rolle **Shareomat Coordinator**: genau ein logischer Coordinator pro LEG, bildet den LEG-Gesamtzustand und verteilt ihn zurück (Kommunikationsarchitektur §7.2, §34, §35). **Noch nicht implementiert**: Coordinator-Aggregate sind laut Cross-House-Vertrag ausdrücklich nicht Teil der heutigen Strecke. |
| Zentraler MQTT-Endpunkt im Internet | Rolle **Zentraler Communication Relay** (`mqtt.shareomat.ch`, §7.3, §23). Pilot über EMQX Cloud Serverless, eigener VPS erst später (WAN-Broker-Vorschlag, Abschnitt 0). |
| Teilnehmer-App | Webportal `my.shareomat.ch`, Browser-Livewerte über WebSocket/SSE statt MQTT, Browser braucht keine MQTT-Zugangsdaten (§37, §38). |
| Ausgehende Verbindungen, kein offener Port | Keine Portfreigabe, keine direkten IP-Verbindungen zwischen Häusern (§12, §20). |
| Eigene Identität pro Gerät, LEG-Isolation am Broker | §28–§31; ACL-Regel im Cross-House-Vertrag Abschnitt 6. |
| Messwertalter, Qualitätsstatus, Fallback | `timestamp`/`sequence`/`source`, `max_age` → `STALE`, Emsomat-Fallback (§42, §43, §44–§46). |
| Keine Fernsteuerung fremder Häuser | NCMD/DCMD auf der WAN-Seite vollständig verboten (ADR-0005); keine generischen Fernsteuerbefehle (§51). |
| Mehrere LEG auf derselben Infrastruktur | §54, §55 (Beispiel 100 Häuser in 20 LEG). |
| Export/Import getrennt übertragen | Bestehende Metriken `Market/ExportPowerNow` und `Market/ImportPowerNow` (lokaler und Cross-House-Vertrag). Die Nettobilanz lässt sich daraus ableiten. |

### 9.2 Wo das Konzept vom Bestand abweicht

Diese Punkte sind keine Fehler im Konzept. Sie verändern aber bewusst
getroffene Entscheidungen und müssen vor einer Umsetzung entschieden werden
(siehe Abschnitt 11):

1. **Topologie: zentraler Server statt Shareomat pro Haus.** Heute gilt „pro
   Haus ein Shareomat Edge“ (§9). Jeder Standort betreibt einen eigenen
   Shareomat mit WAN-Uplink und -Downlink (ADR-0005). Im Konzept braucht der
   Nachbar nur noch ein Zähler-Gateway, und Shareomat läuft zentral als
   Online-Server.
2. **Datenquelle: Zähler-Gateway statt Emsomat.** Der heutige WAN-Uplink hat
   als *einzige* Datenquelle den lokalen Emsomat-Zustand (`sparkplug/bridge.py`,
   Cross-House-Vertrag Abschnitt 3). Teilnehmer ohne Emsomat können heute also
   gar nichts liefern. Das Konzept macht das Gateway zur primären Quelle,
   Emsomat wird optional.
3. **Emsomat und Internet.** Heute gilt verbindlich, dass Emsomat nie direkt
   mit dem Internet spricht (§6) und dass Cloud-MQTT und lokales MQTT nicht
   transparent gebridged werden (§26). Shareomat ist die kontrollierte
   Sicherheitsgrenze. Wenn Emsomat die LEG-Bilanz „über MQTT“ vom
   Online-Server bezieht, braucht es vor Ort weiterhin eine filternde
   Instanz: das Gateway selbst, einen HA-Adapter oder einen schlanken
   Shareomat Edge. Sonst wird §6/§26 aufgegeben.
4. **Sichtbarkeit: aggregiert statt Einzelwerte.** Der heutige WAN-Downlink
   sieht *alle* Teilnehmerdaten der eigenen LEG-Gruppe einzeln (ADR-0005:
   „breite Sicht innerhalb der eigenen LEG-Gruppe“). Das Konzept verlangt
   standardmässig nur eigene und aggregierte Werte (Abschnitt 5, Abschnitt 7
   Punkt 7). Die bestehenden ACLs wären dafür zu offen.

### 9.3 Topic-Schema und Protokoll

Für die LEG-/WAN-Ebene ist heute **Sparkplug B über MQTT 3.1.1** verbindlich
entschieden (Kommunikationsarchitektur §66b, ADR-0005, ADR-0006). Die Topics
lauten `spBv1.0/<group_id>/…` mit `group_id` = LEG und `edge_node_id` =
Standort. Das illustrative Schema in Abschnitt 6 (`shareomat/v1/…`, sinngemäss
JSON) ist davon verschieden.

Wahrscheinlich sind beide Ebenen nötig: Handelsübliche Zähler-Gateways
sprechen typischerweise einfaches MQTT/JSON und kein Sparkplug-B-Protobuf.
Möglich wäre also ein schlanker JSON-Eingang für Gateways, während
Sparkplug zwischen Shareomat-Instanzen bleibt. Das ist offen und Teil von
Etappe 1.

## 10. Vorgeschlagene Entwicklungsetappen

Die Etappen sind bewusst **Etappe 0–4** benannt. So gibt es keine
Verwechslung mit den „Phasen“ im
[WAN-Broker-Vorschlag](Architektur/Zentraler_WAN_Broker_Deployment_Vorschlag.md)
(Phase 1 Pilot-Broker, Phase 2 eigener VPS) und in der
[vZEV-Spezifikation](Spezification/Shareomat_vZEV_Abrechnung_Spezifikation.md) §23.

**Etappe 0 – Aktuellen Stand absichern (zuerst)**

- Offene Sicherheitsbefunde aus dem laufenden Shareomat-Audit abschliessen.
- Vorhandene Korrekturen testen, unabhängig reviewen und einen stabilen Stand freigeben.

**Etappe 1 – Live-Konzept technisch spezifizieren**

- Reale verfügbare Stromzähler-Schnittstellen und Gateway-Optionen prüfen.
- Datenmodell, Vorzeichen, Messwertalter, Qualitätsstatus und Bilanzberechnung definieren.
- MQTT-Topic- und Rechtekonzept mit Emsomat/Sparkplug-Wiederverwendung entwerfen (siehe Abschnitt 9.3).
- Die Abweichungen aus Abschnitt 9.2 entscheiden und als ADR festhalten.
- Klären, ob zunächst nur **eine LEG pro Installation** oder schon mehrere LEG unterstützt werden sollen.

**Etappe 2 – Geschlossener Pilot**

- Eigener Anschluss und ein Nachbar mit Test-Gateway.
- Bidirektionaler Messdaten-/LEG-Daten-Austausch über gesichertes MQTT. Als Broker bietet sich der bereits vorgeschlagene Pilot-Broker an (EMQX Cloud Serverless, WAN-Broker-Vorschlag Abschnitt 0a).
- Live-App für den Teilnehmer, Emsomat als optionaler Konsument der LEG-Bilanz.
- Ausfallverhalten, Gleichzeitigkeit, Datenqualität, Rechte und Energiebilanz prüfen.

**Etappe 3 – Online-Betrieb**

- Härtung für öffentliche Erreichbarkeit, Nutzer- und Geräteverwaltung, Monitoring, Backups, Betrieb und Updates.
- Datenschutz, Einwilligungen, Rollen, Aufbewahrung sowie reale Security- und Belastungstests.

**Etappe 4 – Skalierung**

- Weitere Teilnehmer, Gateways und EMS-Integrationen.
- Bei Bedarf mehrere unabhängig voneinander getrennte LEG auf derselben Serverinfrastruktur.
- Prognosen, Benachrichtigungen und optionale fortgeschrittene Koordinierung, ohne die dezentrale Steuerverantwortung aufzugeben.

## 11. Noch zu treffende Entscheidungen

**Aus dem Konzept:**

- Welche Stromzähler und lokalen Datenschnittstellen sollen zuerst unterstützt werden?
- Ist das Gateway ein eigenes Gerät, ein Home-Assistant-Adapter oder beides?
- Läuft Shareomat zuerst auf der eigenen Infrastruktur mit externem Zugang oder bei einem Hosting-Anbieter?
- Welche Aktualisierungsrate und maximal zulässige Messwertverzögerung brauchen App und Emsomat?
- Wie sollen LEG-Daten bei fehlenden Teilnehmern oder schlechten Messwerten dargestellt werden?
- Welche Messwerte dürfen andere Teilnehmer sehen (Standard: nur aggregierte und eigene Werte)?
- Welche Energiegrössen dürfen für lokale Automationen verwendet werden, ohne eine verfügbare Leistung zu garantieren?
- Wie lässt sich die bestehende Sparkplug-/MQTT-Bridge weiterverwenden, ohne die heutige Abrechnung zu beeinflussen?

**Aus dem Abgleich mit dem Bestand (Abschnitt 9.2/9.3):**

- Bleibt „pro Haus ein Shareomat Edge“ (§9) die Regel? Oder wird der zentrale Online-Server zum Normalfall, mit dem Edge nur noch für Haushalte mit Emsomat?
- Welche lokale Instanz übernimmt beim Nachbarn die Rolle der Sicherheitsgrenze nach §6/§26, wenn Emsomat die LEG-Bilanz aus der Cloud bekommt?
- Wird die „breite Sicht innerhalb der LEG-Gruppe“ (ADR-0005) auf eigene und aggregierte Werte eingeschränkt? Und was bedeutet das für die bestehenden WAN-Downlink-ACLs?
- Bekommen Gateways einen eigenen, schlanken JSON-Eingang, oder müssen sie Sparkplug B sprechen?
- Wo läuft der Coordinator: auf dem zentralen Server oder weiterhin auf einem der Edges (§7.2, §10)?

## 12. Entscheide vom 07.11.2026 und Pilotweg

> Architektur, Schnittstellen und Sicherheitsgrenzen:
> [ADR-0007](decisions/ADR-0007-live-energie-ebene-und-teilnehmerportal.md)
> (vorgeschlagen, 3. Fassung; Grundarchitektur angenommen, Schritt 1 noch
> nicht freigegeben). Darin u. a.: Nachweisstufen der Messaktualität,
> Hysterese für die Anzeige, Regeln für Sparkplug-Messproben,
> Quellenidentität über Broker-ACL, drei Arten von Live-Messquellen
> (Emsomat, Home Assistant, externes Gateway) mit höchstens einer aktiven
> Live-Messquelle pro offiziellem Messpunkt und der Bedienung
> «Live-Messquelle zuordnen». Die Emsomat-Änderung EA-1 ist ein separater
> Auftrag.

Zielbild der App: [`Architektur/Ziel/Shareomat_ Lokale Energie clever nutzen.png`](Architektur/Ziel/)
(Ideenskizze, keine Spezifikation).

### 12.1 Getroffene Entscheide

1. **Keine Empfehlung bei unsicheren Daten.** Fehlen Teilnehmer oder sind
   Werte zu alt, zeigt die App «Status unsicher» und gibt keine
   Verbrauchsempfehlung. Lieber keine Aussage als eine falsche.
2. **Die App zeigt nur eine Zahl: den LEG-Überschuss.** Keine Produktion,
   kein Verbrauch, keine Einzelwerte anderer Haushalte. Bei Defizit erscheint
   «kein Überschuss». Das ersetzt die breitere Anzeige aus Abschnitt 5 und
   beantwortet Frage E3 weitgehend.
   *Restrisiko:* In einer LEG mit nur zwei Haushalten kann der eine aus dem
   Überschuss und seinem eigenen Wert den Saldo des anderen ausrechnen. Im
   Pilot akzeptiert, im Teilnehmervertrag erwähnen.
3. **Shareomat steuert nichts.** Keine Geräteschalter in der App. Gesteuert
   wird lokal durch Emsomat oder von Hand durch den Teilnehmer. Die Beispiele
   «Geschirrspüler», «Boiler» im Zielbild sind höchstens Texthinweise.

### 12.2 Pilot-Anzeige über die HA Entity Bridge (Idee)

Für den ersten Nachbarn, statt einer eigenen App:

```text
Shareomat (HA Main) → Sensor «LEG-Überschuss» (MQTT Discovery)
  → HA Entity Bridge (nur lesen) → HA Second des Nachbarn → HA Companion App
```

Voraussetzungen und offene Punkte:

- Shareomat muss die Live-Bilanz überhaupt erst berechnen und als Sensor
  veröffentlichen. Heute gibt es nur Status-, Abrechnungs- und
  Systemsensoren (`ha/mqtt_runtime.py`).
- Die Entity Bridge ist noch nicht real abgenommen (`docs/ABNAHME_M7B.md`),
  die Mosquitto-ACL muss aktiv sein.
- **Netzweg:** HA Second verbindet sich mit dem Broker auf HA Main. Die
  Entity Bridge ist für das Haus-LAN gebaut. Ihre eigene SECURITY.md verbietet
  ausdrücklich, den Broker ins WAN zu veröffentlichen oder einen Port
  weiterzuleiten. Ein Nachbar in einem anderen Haus braucht deshalb ein VPN
  (WireGuard/Tailscale) oder einen Cloud-Broker; der Broker-Host ist in der
  Bridge frei konfigurierbar.
- Funktioniert nur für Nachbarn mit eigenem Home Assistant.
- Die Entity Bridge transportiert nur **Main → Second**. Für den
  **Rückweg** (Messwert des Nachbarn → Shareomat) braucht es einen eigenen
  Eingang, siehe 12.3.

Für viele Teilnehmer später: kein Home Assistant pro Teilnehmer, sondern ein
Teilnehmerportal (PWA) mit eigenem Login.

### 12.3 Live-Eingang (Weg B): Optionen

| Option | Bewertung |
|---|---|
| **Cloud-MQTT-Broker** (EMQX Cloud Serverless, HiveMQ Cloud), alle verbinden sich ausgehend mit TLS | Empfohlen. Kein offener Port zuhause, eigenes Login und eigene ACL pro Gerät, deckt Hin- und Rückweg ab, entspricht dem bestehenden WAN-Broker-Vorschlag. Für einen Pilot reicht voraussichtlich die kostenlose Einstiegsstufe (aktuelle Bedingungen vor dem Entscheid prüfen). Später kleiner eigener Server. |
| Eigener Mosquitto auf HA Main, über VPN erreichbar | Möglich, wenn der Reader des Nachbarn VPN kann (ESPHome hat WireGuard, viele andere Reader nicht). Ohne VPN verboten (keine Portweiterleitung). |
| E-Mail | Für Live-Werte ungeeignet: Verzögerung von Minuten, Absender fälschbar (vgl. Audit-Befund R-1), Limits der Mailanbieter. Verworfen. |

Unabhängig vom Weg gilt für jeden Live-Eingang: eigenes Login pro Gerät,
Schreibrecht nur auf den eigenen Messpunkt, Plausibilitätsgrenze
(Anschlussleistung), Zeitstempel- und Grössenprüfung, Ratenbegrenzung.
Ein manipulierter Reader kann kein Geld fälschen (Live ≠ Abrechnung),
aber falschen Überschuss vortäuschen und damit andere zum Einschalten
bewegen. Darum Grenzen pro Messpunkt und «Status unsicher» bei
Auffälligkeiten. Emsomat behandelt das LEG-Signal nur als Hinweis, fällt
bei fehlendem oder veraltetem Signal auf einen sicheren Standard zurück und
bekommt es weiterhin über den lokalen Shareomat (§6).

---

**Leitidee:** *Shareomat macht die aktuelle LEG-Energie für alle Teilnehmer sichtbar und stellt sie über App und MQTT bereit. Die Teilnehmer liefern ihre Zählerdaten selbst. Emsomat oder andere lokale Systeme steuern die eigenen Verbraucher dezentral.*
