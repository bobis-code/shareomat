# Shareomat / Emsomat
## Kommunikations- und Sicherheitsarchitektur

**Version:** 0.2
**Status:** Architekturgrundlage / Arbeitskonzept
**Zweck:** Grundlage für die weitere technische Spezifikation von Emsomat, Shareomat, LEG-Kommunikation, Internetkommunikation und Webportal.

---

# 1. Ziel des Dokuments

Dieses Dokument definiert zunächst die **Systemgrenzen und Architekturprinzipien**.

Es soll noch **keine konkrete Python-Dateistruktur**, keine endgültige MQTT-Topic-Liste und keine konkrete Cloud-Infrastruktur vorschreiben.

Zuerst soll klar sein:

- Welche Anwendung besitzt welche Verantwortung?
- Wie viele Instanzen existieren?
- Welche Komponente darf mit dem Internet kommunizieren?
- Wo wird MQTT eingesetzt?
- Welche Daten gehören Emsomat?
- Welche Daten gehören Shareomat?
- Wie kommunizieren verschiedene Häuser?
- Wie bleibt die lokale Energieverwaltung funktionsfähig, wenn Internet oder zentrale Kommunikation ausfallen?
- Wie wird verhindert, dass die öffentliche Kommunikation direkten Zugriff auf Emsomat erhält?

---

# 2. Grundprinzip

Emsomat und Shareomat sind **zwei eigenständige Anwendungen**.

Beide werden intern nach dem Prinzip eines:

> **modularen Monolithen**

aufgebaut.

Das bedeutet:

- klare Klassen,
- klar definierte Module,
- ein gemeinsames internes Objektmodell,
- direkte interne Funktions-/Klassenaufrufe,
- keine unnötigen Microservices,
- kein MQTT zwischen internen Funktionsmodulen,
- Kommunikationsprotokolle nur an echten Systemgrenzen.

```text

        EMSOMAT

  modularer Monolith

  Energy Management
  Regelung
  Geräteintegration
  Optimierung

             MQTT
             echte Systemgrenze

       SHAREOMAT

  modularer Monolith

  LEG
  Kommunikation
  Messdaten
  Abrechnung
  Web

```

Der zentrale Architekturgrundsatz lautet:

> **Intern integriert und direkt kommunizieren.
> MQTT nur dort einsetzen, wo tatsächlich unabhängige Anwendungen oder Standorte getrennt sind.**

---

# 3. Verantwortlichkeit Emsomat

Emsomat besitzt die Verantwortung für das **lokale Energiemanagement eines Standortes**.

Dazu gehören beispielsweise:

- PV
- Batterie
- Netzanschlusspunkt
- Hausverbrauch
- steuerbare Verbraucher
- Wärmepumpe
- Wallbox
- Lade-/Entladeleistung
- Leistungsbegrenzungen
- Eigenverbrauchsoptimierung
- Preisoptimierung
- Gerätezustände
- Betriebsarten
- lokale Regelalgorithmen
- EEBUS
- Matter
- SunSpec
- Modbus
- herstellerspezifische Geräteadapter

Emsomat ist damit der **Owner des lokalen Energiezustandes und der lokalen Regelung**.

---

# 4. Emsomat muss autonom funktionieren

Eine wesentliche Systemanforderung lautet:

```text
Internet ausgefallen

        X

Shareomat nicht erreichbar

        X

EMSOMAT

    PV
    Batterie
    Netz
    Verbraucher
    Regelung

lokale Funktion läuft weiter
```

Das bedeutet:

> **Cloud, Shareomat und Webportal dürfen nicht Bestandteil eines zeitkritischen lokalen Regelkreises sein.**

Shareomat kann Emsomat zusätzliche Informationen zur Verfügung stellen.

Emsomat muss aber definierte Fallback-Strategien besitzen, wenn diese Informationen nicht verfügbar oder veraltet sind.

---

# 5. Verantwortlichkeit Shareomat

Shareomat besitzt die Verantwortung für die **LEG-, Kommunikations- und Informationsseite**.

Dazu gehören beispielsweise:

- LEG
- Teilnehmer
- Messpunkte
- LEG-Gesamtzustand
- LEG-Energieverteilung
- LEG-Preise
- SDAT
- abrechnungsrelevante Daten
- Historie
- Abrechnung
- Reports
- Webportal
- Datenaustausch zwischen Standorten
- kontrollierte Kommunikation mit Emsomat

Zusätzlich erhält Shareomat eine besondere Rolle:

> **Shareomat bildet die Kommunikations- und Sicherheitsgrenze zwischen Emsomat und externen Netzen.**

---

# 6. Keine direkte Internetkommunikation von Emsomat

Die Zielarchitektur soll ausdrücklich vermeiden:

```text
Internet

EMSOMAT
```

Stattdessen:

```text
Internet

SHAREOMAT

    kontrollierte Anwendungsschnittstelle

EMSOMAT
```

Emsomat benötigt damit grundsätzlich:

- keinen öffentlichen Port,
- keine öffentliche API,
- keinen öffentlichen MQTT-Endpunkt,
- keine Portweiterleitung,
- keinen Cloudflare-Tunnel,
- keine Kenntnis anderer Häuser,
- keine Kenntnis öffentlicher IP-Adressen.

---

# 7. Drei unterschiedliche Shareomat-Rollen

Damit das Modell auch für mehrere Häuser und mehrere LEGs funktioniert, müssen drei **logische Rollen** unterschieden werden.

Diese Rollen bedeuten **nicht automatisch drei verschiedene Produkte oder Programme**.

## 7.1 Shareomat Edge

Ein Shareomat Edge läuft lokal an einem Standort.

Seine Aufgabe:

```text
Internet / externe Kommunikation

        SHAREOMAT EDGE

        private Verbindung

            EMSOMAT
```

Der Edge:

- kommuniziert mit dem lokalen Emsomat,
- kommuniziert mit der Außenwelt,
- filtert Daten,
- validiert Daten,
- übersetzt externe Kommunikation in das lokale Datenmodell,
- verhindert direkten Internetzugriff auf Emsomat.

---

## 7.2 Shareomat Coordinator

Pro LEG gibt es **genau einen logischen Coordinator**.

Der Coordinator besitzt LEG-weite Informationen und Funktionen.

Beispielsweise:

- Teilnehmerstruktur
- LEG-Gesamtzustand
- Energieverteilung
- Preise
- Abrechnung
- SDAT
- übergeordnete LEG-Informationen

Wichtig:

> Der Coordinator muss kein zusätzlicher Computer sein.

Ein vorhandener Shareomat Edge kann zusätzlich die Coordinator-Rolle übernehmen.

---

## 7.3 Zentraler Communication Relay

Zusätzlich existiert ein gemeinsamer Kommunikationspunkt im Internet.

Beispielsweise:

```text
mqtt.shareomat.ch
```

Dieser ist **kein Shareomat einer bestimmten LEG**.

Er ist Infrastruktur.

Seine Aufgabe:

- Verbindungen der Shareomat-Instanzen entgegennehmen,
- MQTT-Nachrichten anhand von Topics und Rechten verteilen,
- keine lokalen EMS-Regelungen durchführen,
- keine direkte Verbindung in private Emsomat-Netze herstellen.

---

# 8. Anzahl Shareomaten

Damit ist die Anzahl eindeutig definiert.

## Beispiel: Ein Haus, eine LEG

```text
Haus A

Emsomat A

Shareomat A
 Edge + Coordinator
```

Benötigt:

```text
1  Shareomat
```

---

## Beispiel: Zwei Häuser in einer LEG

```text
HAUS A                       HAUS B

Emsomat A                    Emsomat B

Shareomat A                  Shareomat B
Edge + Coordinator           Edge
```

Benötigt:

```text
2  lokale Shareomat-Instanzen
1  Coordinator-Rolle
```

---

## Beispiel: Fünf Häuser in einer LEG

```text
LEG 001

Haus A
Shareomat A   Coordinator

Haus B
Shareomat B

Haus C
Shareomat C

Haus D
Shareomat D

Haus E
Shareomat E
```

Benötigt:

```text
5  lokale Shareomat Edge
1  davon zusätzlich Coordinator
```

---

# 9. Warum pro Haus ein Shareomat Edge?

Diese Entscheidung folgt aus der Sicherheitsanforderung:

> **Emsomat soll niemals direkt mit dem öffentlichen Internet kommunizieren.**

Ohne lokalen Shareomat müsste beispielsweise:

```text
Emsomat Haus B

mqtt.shareomat.ch
```

direkt kommunizieren.

Mit Shareomat Edge entsteht stattdessen:

```text
Emsomat Haus B

 lokale/private Kommunikation

Shareomat Haus B

 sichere externe Verbindung

mqtt.shareomat.ch
```

Damit kann Emsomat vollständig in einer privaten Netzwerkzone verbleiben.

---

# 10. Shareomat Edge und Coordinator sind Rollen

Es soll vermieden werden, für jede Funktion einen separaten Dienst zu bauen.

Daher:

```text
shareomat --role=edge
```

und beispielsweise:

```text
shareomat --role=edge,coordinator
```

sind konzeptionell unterschiedliche Konfigurationen **derselben Anwendung**.

Die genaue technische Umsetzung wird später definiert.

Der wichtige Architekturpunkt lautet:

> **Edge und Coordinator sind logische Rollen innerhalb des Shareomat-Systems und nicht automatisch Microservices.**

---

# 11. Lokale Netzwerkstruktur

Ein Standort kann beispielsweise folgendermaßen aufgebaut werden:

```text
                   INTERNET

                SHAREOMAT-ZONE

                 SHAREOMAT

                PRIVATE NETWORK
                / Proxmox vmbr

                  EMSOMAT

```

Shareomat besitzt Außenkommunikation.

Emsomat nicht.

---

# 12. Shareomat darf kein IP-Router werden

Sehr wichtig:

Nur weil Shareomat Zugriff auf zwei Netzwerkseiten besitzt, darf daraus **kein Routing zwischen diesen Netzen** entstehen.

Nicht:

```text
Internet

Shareomat

IP Forwarding

Emsomat-Netz
```

Sondern:

```text
Internet

Shareomat Anwendung

Authentifizierung

Validierung

Berechtigungsprüfung

internes Datenmodell

lokale MQTT-Kommunikation

Emsomat
```

Shareomat arbeitet damit als:

> **Application Gateway**

und nicht als Netzwerkrouter.

---

# 13. Lokale Kommunikation Shareomat -> Emsomat

Für diese echte Anwendungsgrenze wird MQTT eingesetzt.

```text
EMSOMAT

    MQTT

SHAREOMAT
```

MQTT passt hier, weil:

- Emsomat und Shareomat unabhängig laufen,
- sie unabhängig neu gestartet werden können,
- unterschiedliche Laufzeitumgebungen möglich sind,
- Emsomat beispielsweise HA-Integration sein kann,
- Shareomat beispielsweise Docker-basiert ist,
- Daten asynchron ausgetauscht werden.

---

# 14. Kein MQTT innerhalb des Emsomat-Core

Innerhalb von Emsomat gilt:

```text
Optimizer

Battery

Control
```

über direkte interne Interfaces.

Nicht:

```text
Optimizer

 MQTT

Battery

 MQTT

Control
```

MQTT soll nicht die interne Klassenarchitektur ersetzen.

---

# 15. Kein MQTT innerhalb des Shareomat-Core

Dasselbe gilt für Shareomat.

Beispielsweise:

```text
Metering

LEG Allocation

Billing

Reporting
```

kommunizieren intern direkt.

Nicht:

```text
Metering Service

     MQTT

Billing Service

     MQTT

Report Service
```

Shareomat bleibt ein modularer Monolith.

---

# 16. Daten-Ownership

Für jeden Zustand muss eindeutig sein, welches System ihn besitzt.

## Emsomat ist Owner von:

```text
lokaler PV-Zustand
lokale Batterie
lokaler Netzanschlusspunkt
lokale Lasten
lokale Regelung
lokale Geräte
lokale Optimierung
lokale Betriebsarten
```

## Shareomat ist Owner von:

```text
LEG
Teilnehmer
Messpunkte
LEG-Zuordnung
LEG-Verteilung
LEG-Preise
Abrechnung
SDAT
LEG-Gesamtzustand
```

Dadurch wird vermieden, dass beide Systeme denselben Zustand unabhängig verwalten.

---

# 17. Gemeinsames Informationsmodell

Obwohl beide Anwendungen unabhängig bleiben, müssen sie dieselben grundlegenden Begriffe verstehen.

Beispielsweise:

```text
Power
Energy
Timestamp
Quality
MeterPoint
Device
Availability
Status
Price
PowerLimit
```

Dafür wird später ein **versionierter Kommunikationsvertrag** definiert.

Dieser Vertrag ist wichtiger als die konkrete MQTT-Topic-Struktur.

---

# 18. Kommunikation Emsomat -> Shareomat

Emsomat veröffentlicht lokale Informationen.

Beispielsweise:

```text
PV-Leistung
Batterieleistung
Batterie-SoC
Netzleistung
Hausverbrauch
Gerätestatus
verfügbare Leistung
15-Minuten-Energiewerte
```

```text
EMSOMAT

    lokale MQTT-Nachricht

SHAREOMAT EDGE
```

Der Shareomat entscheidet anschließend, welche Daten:

- lokal verarbeitet,
- gespeichert,
- an den Coordinator,
- an das Webportal,
- oder über den zentralen Kommunikationsweg weitergegeben

werden.

---

# 19. Kommunikation Shareomat -> Emsomat

Shareomat besitzt Informationen, die Emsomat lokal nicht selbst bestimmen kann.

Beispielsweise:

```text
LEG-Gesamtüberschuss
LEG-Gesamtbedarf
LEG-Preis
verfügbare LEG-Leistung
LEG-Zustand
übergeordnete Limits
```

Diese Informationen können lokal über MQTT an Emsomat veröffentlicht werden.

```text
SHAREOMAT

    lokale MQTT-Nachricht

EMSOMAT
```

Emsomat nutzt diese Daten als Eingang für seine lokale Optimierung.

---

# 20. Kommunikation zwischen verschiedenen Häusern

Häuser verbinden sich **nicht direkt miteinander**.

Nicht:

```text
Haus A

öffentliche IP

Haus B
```

Auch nicht:

```text
Shareomat A

direkte TCP-Verbindung

Shareomat B
```

Stattdessen:

```text
Shareomat A

           mqtt.shareomat.ch

Shareomat B

Shareomat C
```

Alle Shareomaten bauen ihre Verbindung **selbst ausgehend** auf.

---

# 21. Vorteil des zentralen Kommunikationspunktes

Damit spielen folgende Dinge für die Anwendung keine Rolle:

```text
öffentliche IP Haus A
öffentliche IP Haus B
NAT
Providerwechsel
Routerwechsel
dynamische IP
CGNAT
```

Jede Installation kennt nur einen stabilen Namen:

```text
mqtt.shareomat.ch
```

---

# 22. Eine Domain reicht

Benötigt wird nur:

```text
shareomat.ch
```

Darunter können beliebig viele Subdomains erzeugt werden.

Empfohlene logische Trennung:

```text
my.shareomat.ch
```

für Menschen / Webportal.

```text
mqtt.shareomat.ch
```

für Shareomat-Maschinenkommunikation.

Optional später:

```text
api.shareomat.ch
```

für explizite externe APIs.

Es braucht **keine Domain pro Haus und keine Domain pro LEG**.

---

# 23. Zentraler MQTT-Endpunkt

Alle Shareomat-Edges verbinden sich mit demselben Endpunkt:

```text
mqtt.shareomat.ch
```

Beispiel:

```text
                         mqtt.shareomat.ch

                          MQTT Broker

     Shareomat A           Shareomat B           Shareomat C
     LEG 001               LEG 001               LEG 002
```

Der Broker verteilt Nachrichten nur entsprechend der definierten Berechtigungen.

---

# 24. Standard MQTT/TLS oder MQTT über WSS

Hier muss zwischen Architektur und Deployment unterschieden werden.

## Variante A  Broker auf öffentlichem Server/VPS

Dann kann klassisches:

```text
MQTT over TLS
mqtts://mqtt.shareomat.ch:8883
```

verwendet werden.

MQTT 5 spezifiziert TLS und WebSockets als geeignete Transportmöglichkeiten; Port 8883 ist für MQTT über TLS registriert.

---

## Variante B  Broker zunächst zuhause hinter Cloudflare Tunnel

Dann ist:

```text
MQTT over WebSocket Secure
wss://mqtt.shareomat.ch/...
```

eine mögliche Variante.

Cloudflare Tunnel unterstützt WebSocket-Verbindungen vollständig.

Mosquitto unterstützt MQTT über WebSockets über einen entsprechend konfigurierten Listener.

Damit kann ein zentraler Broker zunächst auf eigener Infrastruktur betrieben werden, ohne einen eingehenden MQTT-Port am Router zu öffnen.

---

# 25. Cloudflare ist nur Deployment-Infrastruktur

Cloudflare darf nicht Bestandteil der fachlichen Shareomat-Architektur werden.

Das Systemmodell lautet:

```text
Shareomat

MQTT

zentraler Kommunikationsendpunkt
```

Ob dieser Endpunkt später erreichbar ist über:

```text
Cloudflare Tunnel
```

oder:

```text
direkten TLS-Endpunkt eines VPS
```

darf für Shareomat fachlich keine Rolle spielen.

Cloudflare Tunnel baut selbst ausgehende Verbindungen auf und benötigt deshalb am Origin keine öffentliche IP bzw. keinen geöffneten eingehenden Port.

---

# 26. Keine transparente Broker-Bridge bis Emsomat

Eine zentrale Sicherheitsentscheidung lautet:

> **Cloud-MQTT und lokales Emsomat-MQTT dürfen nicht transparent miteinander gebridged werden.**

Nicht:

```text
Cloud MQTT

 automatische Bridge

Local MQTT

Emsomat
```

Sondern:

```text
Cloud MQTT

SHAREOMAT

prüfen
filtern
validieren
übersetzen
Berechtigung prüfen

Local MQTT

EMSOMAT
```

Dadurch bleibt Shareomat die kontrollierte Sicherheitsgrenze.

---

# 27. Zwei Kommunikationskontexte im Shareomat

Ein Shareomat Edge besitzt logisch zwei Kommunikationsseiten.

```text
                    SHAREOMAT

     EXTERN                         INTERN

Cloud / Relay                     Emsomat

Cloud MQTT                     Local MQTT

      Application
                  Gateway
```

Die beiden Seiten werden durch die Shareomat-Fachlogik getrennt.

---

# 28. Geräteidentität

Jeder Shareomat Edge erhält eine eindeutige Identität.

Beispielsweise:

```text
SH-000001
SH-000002
SH-000003
```

Zusätzlich existieren Zuordnungen:

```text
Shareomat

    Site
    LEG
    Rolle
```

Beispiel:

```text
SH-000017

Site:
SITE-0005

LEG:
LEG-0002

Role:
EDGE
```

oder:

```text
SH-000001

Site:
SITE-0001

LEG:
LEG-0002

Role:
EDGE + COORDINATOR
```

---

# 29. Identität darf nicht aus Payload stammen

Ein Client darf nicht einfach senden:

```json
{
  "leg_id": "LEG-0001",
  "shareomat_id": "SH-0001"
}
```

und dadurch diese Identität erhalten.

Stattdessen muss gelten:

```text
Credential

SH-0001

SITE-001

LEG-001
```

Die Authentifizierung bestimmt die Identität.

Payload-Daten dürfen diese Zuordnung nicht überschreiben.

---

# 30. Eigene Credentials pro Shareomat

Jede Installation erhält eigene Zugangsdaten.

Nicht:

```text
alle Shareomaten:
username = shareomat
password = xyz
```

Sondern beispielsweise:

```text
SH-0001 -> eigenes Credential
SH-0002 -> eigenes Credential
SH-0003 -> eigenes Credential
```

Langfristig ist auch eine Geräteidentität über Client-Zertifikate/mTLS möglich.

Ein kompromittiertes Gerät kann dadurch einzeln gesperrt werden, ohne andere Installationen zu beeinflussen.

---

# 31. LEG-Isolation am Broker

Der Broker muss verhindern:

```text
LEG 001 -> Daten LEG 002
```

Beispiel:

```text
LEG-001
  SH-001
  SH-002
  SH-003

LEG-002
  SH-004
  SH-005
```

`SH-002` darf keine Daten von `LEG-002` abonnieren oder dort veröffentlichen.

Diese Trennung muss über Broker-ACLs bzw. eine gleichwertige serverseitige Autorisierung umgesetzt werden.

---

# 32. Coordinator-Rechte

Der Coordinator benötigt innerhalb **seiner eigenen LEG** erweiterte Rechte.

Beispielsweise:

```text
EDGE

darf:
- eigene Standortdaten veröffentlichen
- freigegebene LEG-Zustände lesen
```

```text
COORDINATOR

darf zusätzlich:
- Standortdaten aller Sites seiner LEG lesen
- LEG-Gesamtzustand veröffentlichen
- LEG-spezifische Verteilungsinformationen veröffentlichen
```

Aber auch der Coordinator darf niemals auf andere LEGs zugreifen.

---

# 33. Beispiel Kommunikationsfluss einer LEG

Angenommen:

```text
LEG-001

Haus A:
SH-A = Edge + Coordinator
EMS-A

Haus B:
SH-B = Edge
EMS-B

Haus C:
SH-C = Edge
EMS-C
```

Dann:

```text
EMS-A

local MQTT

SH-A

EMS-B

local MQTT      mqtt.shareomat.ch

SH-B

EMS-C

local MQTT

SH-C
```

---

# 34. Bildung des LEG-Gesamtzustandes

Die Edges veröffentlichen die lokalen Informationen.

```text
SH-A -> lokale Daten Haus A
SH-B -> lokale Daten Haus B
SH-C -> lokale Daten Haus C
```

Der Coordinator erhält ausschließlich die Daten seiner LEG.

```text
                  SH-A
             COORDINATOR

    Haus A     Haus B     Haus C
```

Aus diesen Informationen kann er beispielsweise berechnen:

```text
LEG Gesamtproduktion
LEG Gesamtverbrauch
LEG berschuss
LEG Bedarf
verfügbare LEG-Leistung
Verteilung
```

---

# 35. Rückverteilung des LEG-Zustandes

Anschließend veröffentlicht der Coordinator einen definierten LEG-Zustand:

```text
Coordinator

mqtt.shareomat.ch

     Shareomat A
     Shareomat B
     Shareomat C
```

Jeder Edge entscheidet anschließend, welche Information lokal an Emsomat weitergegeben wird.

```text
LEG State

Shareomat B

local MQTT

Emsomat B
```

Damit kennt Emsomat nur den für ihn relevanten Shareomat-Zustand.

---

# 36. Emsomaten kommunizieren nicht direkt miteinander

Nicht:

```text
EMS-A -> EMS-B
```

Sondern:

```text
EMS-A

SH-A

Relay / Coordinator

SH-B

EMS-B
```

Dadurch kennt Emsomat:

- keine andere Haus-IP,
- keinen anderen Router,
- keine fremden Credentials,
- keinen zentralen Broker,
- keine Cloud-Infrastruktur.

---

# 37. Webportal

Das Benutzerportal wird logisch unter:

```text
https://my.shareomat.ch
```

bereitgestellt.

Der Browser greift niemals direkt auf Emsomat zu.

```text
Browser

my.shareomat.ch

Shareomat Web Backend
```

Auch ein direkter Browserzugriff auf den lokalen MQTT-Broker ist nicht vorgesehen.

---

# 38. Webdaten und Live-Kommunikation

Für Browser-Livewerte können später beispielsweise:

```text
WebSocket
```

oder:

```text
Server-Sent Events
```

verwendet werden.

Das ist vom MQTT-Geräteprotokoll getrennt.

```text
Shareomat MQTT

Backend / State

WebSocket / HTTPS

Browser
```

Der Browser muss deshalb keine MQTT-Zugangsdaten besitzen.

---

# 39. Datenklassen

Es müssen mindestens vier Datenklassen unterschieden werden.

## 39.1 Live-Telemetrie

Beispielsweise:

```text
PV Power
Grid Power
Battery Power
SoC
Load Power
```

Eigenschaften:

```text
häufig
kurzlebig
Monitoring / Live-Anzeige
```

---

## 39.2 Zustandsdaten

Beispielsweise:

```text
LEG State
Operating Mode
Availability
Price
Power Limit
```

Eigenschaften:

```text
aktueller Zustand relevant
letzter gültiger Wert wichtig
```

---

## 39.3 Abrechnungsdaten

Beispielsweise:

```text
15-Minuten-Energie
Meter Values
LEG Allocation
```

Eigenschaften:

```text
dauerhaft
vollständig
nachvollziehbar
keine Datenverluste zulässig
```

---

## 39.4 Commands

Falls später Steuerinformationen übertragen werden:

```text
Setpoint
Power Limit
Mode
Enable
```

müssen Commands als eigene Nachrichtenklasse behandelt werden.

Commands dürfen nicht einfach normale Telemetrie sein.

---

# 40. QoS

MQTT bietet unterschiedliche Quality-of-Service-Stufen. QoS 0 bedeutet at most once, QoS 1 at least once, QoS 2 exactly once auf MQTT-Protokollebene.

Die konkrete Verwendung wird später pro Nachrichtenklasse festgelegt.

Eine mögliche Richtung:

```text
Live Telemetry
 QoS 0 oder 1

State
 QoS 1

abrechnungsrelevante Daten
 QoS 1 + eigene Idempotenz / Persistenz
```

Wichtig:

> MQTT-QoS ersetzt keine fachliche Datenintegrität.

Abrechnungsdaten benötigen zusätzlich eindeutige IDs, Zeitperioden und Schutz gegen Doppelverarbeitung.

---

# 41. Retained Messages

MQTT unterstützt retained Messages, bei denen der Broker den letzten veröffentlichten Zustand eines Topics speichert und neuen passenden Subscriber zur Verfügung stellen kann.

Retain eignet sich beispielsweise für:

```text
aktueller LEG State
aktueller Preis
Availability
aktueller Betriebsstatus
```

Nicht automatisch für:

```text
einmalige Commands
Impulse
Events
```

Die Verwendung wird pro Nachrichtentyp festgelegt.

---

# 42. Zeitstempel und Datenalter

Jede relevante Nachricht benötigt einen eindeutigen Zeitbezug.

Beispielsweise:

```text
timestamp
sequence
source
schema_version
```

Emsomat darf einen erhaltenen LEG-Zustand nicht unbegrenzt verwenden.

Beispiel:

```text
LEG State erhalten:
12:00:00

jetzt:
12:03:00

max_age überschritten

STATE = STALE
```

---

# 43. Verhalten bei veralteten LEG-Daten

Wenn Shareomat oder Kommunikation ausfällt:

```text
LEG DATA

 STALE

EMSOMAT FALLBACK
```

Emsomat muss dann beispielsweise:

- keine unbekannte LEG-Leistung voraussetzen,
- lokale PV-/Batterieinformationen weiterverwenden,
- lokale Sicherheits-/Leistungsgrenzen respektieren,
- auf einen definierten lokalen Betriebsmodus wechseln.

Die genaue Fallback-Strategie wird separat spezifiziert.

---

# 44. Verhalten bei Internetunterbruch

```text
Internet
   X
```

Dann:

1. Emsomat arbeitet lokal weiter.
2. Shareomat Edge bleibt lokal erreichbar.
3. lokale EmsomatShareomat-Kommunikation funktioniert weiter.
4. externe MQTT-Verbindung fällt aus.
5. notwendige Daten werden gepuffert.
6. nach Wiederverbindung erfolgt automatische Synchronisation.

---

# 45. Verhalten bei zentralem Broker-Ausfall

```text
mqtt.shareomat.ch
        X
```

Dann:

```text
Emsomat A -> Shareomat A   funktioniert lokal
Emsomat B -> Shareomat B   funktioniert lokal

Standortübergreifende LEG-Livedaten:
nicht verfügbar
```

Die lokale EMS-Funktion bleibt erhalten.

---

# 46. Verhalten bei Shareomat-Ausfall

```text
Shareomat
    X

Emsomat

lokaler Fallback
```

Emsomat darf nicht unkontrolliert stoppen.

Nach Shareomat-Neustart:

```text
Shareomat startet

Local MQTT reconnect

aktueller Zustand

Synchronisation

Normalbetrieb
```

---

# 47. Persistenz

Nicht jede Nachricht muss dauerhaft gespeichert werden.

Beispielsweise:

```text
30-s-Livewert
 möglicherweise begrenzte Historie

15-Minuten-Energie
 dauerhaft

Abrechnung
 dauerhaft

Konfigurationsänderung
 Audit / dauerhaft
```

Speicherstrategie und Aufbewahrungsfristen werden separat definiert.

---

# 48. Sicherheitszonen

Die Zielarchitektur unterscheidet mindestens:

```text
ZONE 1
Internet / Public

ZONE 2
Shareomat / DMZ

ZONE 3
Emsomat / EMS privat

ZONE 4
weitere interne Infrastruktur
Proxmox / Home Assistant Admin / Router
```

Erlaubte Kommunikation wird explizit definiert.

---

# 49. Sicherheitsziel bei kompromittiertem Shareomat

Es muss angenommen werden:

```text
Shareomat Edge vollständig kompromittiert
```

Trotzdem soll ein Angreifer nicht automatisch Zugriff erhalten auf:

```text
Proxmox Management
ASUS Management
übriges Heimnetz
NAS
andere Server
direkte Gerätekommunikation
generische Emsomat-Administration
```

Der lokale Kommunikationsvertrag zu Emsomat soll möglichst eng begrenzt sein.

---

# 50. Emsomat-MQTT-Berechtigungen

Der lokale Shareomat benötigt nicht automatisch Zugriff auf sämtliche Emsomat-Funktionen.

Beispielsweise:

```text
Shareomat darf lesen:

emsomat/state
emsomat/telemetry
emsomat/energy
```

und darf beispielsweise veröffentlichen:

```text
shareomat/leg/state
shareomat/leg/price
shareomat/leg/availability
```

Nicht automatisch erlaubt:

```text
emsomat/admin
emsomat/config
raw_modbus
raw_eebus
system_shell
```

Die konkrete ACL wird später mit dem MQTT-Contract definiert.

---

# 51. Keine generischen Fernsteuerbefehle

Nicht:

```text
cloud/execute
cloud/shell
cloud/write_any_value
cloud/modbus_write
```

Stattdessen ausschließlich fachlich definierte Nachrichten.

Beispielsweise:

```text
LEG available power
```

oder später:

```text
approved power limit
```

Ein Kommunikationsprotokoll darf nicht zu einer generischen Remote-Control-Schnittstelle werden.

---

# 52. Skalierung auf viele Shareomaten

Die Anzahl der lokalen Shareomaten verändert das Kommunikationsmodell nicht.

```text
10 Shareomaten

mqtt.shareomat.ch
```

```text
100 Shareomaten

mqtt.shareomat.ch
```

```text
10'000 Shareomaten

mqtt.shareomat.ch
```

Die Clients kennen weiterhin nur denselben stabilen Endpunkt.

Skalierung erfolgt serverseitig hinter diesem Namen.

---

# 53. Skalierung des Brokers

Anfang:

```text
mqtt.shareomat.ch

ein Broker
```

Später:

```text
mqtt.shareomat.ch

Broker-Infrastruktur

Node  Node  Node
```

Die Shareomat-Edges sollen von dieser Änderung nichts wissen müssen.

---

# 54. Skalierung auf mehrere LEGs

Beispiel:

```text
                 mqtt.shareomat.ch

      LEG 001          LEG 002           LEG 003

  SH1  SH2  SH3      SH4 SH5          SH6  SH7
```

Jede LEG besitzt:

```text
genau einen Coordinator
```

aber beliebig viele Edges.

---

# 55. Beispiel: 100 Häuser in 20 LEGs

Es existieren beispielsweise:

```text
100 Shareomat Edges
20 Coordinator-Rollen
1 gemeinsamer Kommunikationsendpunkt
```

Nicht:

```text
20 Domains
100 öffentliche IPs
100 Portweiterleitungen
```

sondern weiterhin:

```text
mqtt.shareomat.ch
```

---

# 56. Coordinator-Ausfall

Der Coordinator ist pro LEG ein wichtiger Knoten.

Daher muss sein Ausfall explizit behandelt werden.

Bei Coordinator-Ausfall:

```text
lokale Emsomaten
 funktionieren weiter

lokale Edges
 funktionieren weiter

LEG-weite Echtzeitoptimierung
 eingeschränkt / Fallback

Abrechnungsdaten
 lokal puffern
```

Später kann entschieden werden, ob eine automatische Coordinator-Übernahme erforderlich ist.

Für die erste Version ist diese Hochverfügbarkeit **noch keine zwingende Annahme**.

---

# 57. Versionierung des Kommunikationsvertrags

Der MQTT-Contract wird versioniert.

Beispielsweise:

```text
protocol_version = 1
schema_version = 1
```

oder im Topic:

```text
shareomat/v1/...
```

Dadurch können später alte und neue Softwarestände zeitweise parallel existieren.

---

# 58. Abwärtskompatibilität

Ein Update von Shareomat darf nicht automatisch voraussetzen, dass alle Emsomaten exakt gleichzeitig aktualisiert werden.

Daher muss definiert werden:

```text
welche Protokollversionen werden unterstützt?
wie lange?
wie werden unbekannte Felder behandelt?
wann wird eine Version entfernt?
```

Das gehört später in die Schnittstellenspezifikation.

---

# 59. Monitoring der Kommunikation

Der Zustand der Kommunikationswege muss sichtbar sein.

Beispielsweise:

```text
Local MQTT:
CONNECTED

Cloud MQTT:
CONNECTED

Coordinator:
AVAILABLE

LEG State Age:
8 s
```

oder:

```text
Cloud MQTT:
DISCONNECTED since 12:13

LEG State:
STALE
```

Damit wird Kommunikation zu einem überwachten Systemzustand und nicht zu einer versteckten Fehlerquelle.

---

# 60. Keine direkte Kopplung an Cloudflare

Der Shareomat-Code darf nicht davon abhängen:

```text
if cloudflare:
    ...
```

Die Anwendung kennt lediglich:

```text
Broker Endpoint
Web Endpoint
Credentials
```

Dadurch kann später:

```text
Cloudflare
```

gegen:

```text
VPS
Load Balancer
anderen Reverse Proxy
```

ausgetauscht werden, ohne die Facharchitektur neu zu bauen.

---

# 61. Keine direkte Kopplung an Mosquitto

Dasselbe gilt für den Broker.

Shareomat implementiert:

```text
MQTT Standard
```

und nicht:

```text
Mosquitto-spezifische Fachlogik
```

Mosquitto kann eine erste Broker-Implementierung sein.

Ein späterer Wechsel auf einen anderen MQTT-Broker darf nicht das Shareomat-Datenmodell verändern.

---

# 62. Keine direkte Kopplung Emsomat -> externe Infrastruktur

Emsomat kennt:

```text
lokalen Shareomat
```

aber nicht:

```text
mqtt.shareomat.ch
Cloudflare
Remote Broker
andere Shareomaten
andere Häuser
```

Das ist eine wichtige Entkopplung.

---

# 63. Zielbild

```text
                              INTERNET

        my.shareomat.ch                     mqtt.shareomat.ch
          Webportal                       Communication Relay

                      SHAREOMAT A            SHAREOMAT B            SHAREOMAT C
                      Edge+Coord.               Edge                   Edge

                        local MQTT              local MQTT              local MQTT

                         EMSOMAT A             EMSOMAT B             EMSOMAT C

                    lokale Energietechnik   lokale Energietechnik  lokale Energietechnik
```

---

# 64. Architekturprinzip in einem Satz

> **Emsomat steuert lokal. Shareomat kommuniziert und verwaltet LEG-Informationen. Jeder Standort besitzt bei Bedarf einen Shareomat Edge als Sicherheitsgrenze. Pro LEG übernimmt genau ein Shareomat zusätzlich die Coordinator-Rolle. Alle entfernten Shareomaten verbinden sich ausgehend mit einem gemeinsamen, domainbasierten Kommunikationspunkt.**

---

# 65. Festgelegte Architekturentscheidungen

Für die weitere Konzeptarbeit gelten vorerst folgende Entscheidungen:

1. Emsomat und Shareomat bleiben getrennte Anwendungen.
2. Beide werden intern als modulare Monolithen aufgebaut.
3. Innerhalb der Anwendungen werden direkte Interfaces verwendet.
4. MQTT wird nur an echten Systemgrenzen verwendet.
5. Emsomat besitzt keinen direkten öffentlichen Kommunikationsendpunkt.
6. Ein Shareomat Edge bildet die externe Sicherheitsgrenze eines Standortes.
7. Shareomat -> Emsomat kommunizieren lokal über eine klar definierte MQTT-Schnittstelle.
8. Cloud- und Local-MQTT werden nicht transparent gebridged.
9. Jeder teilnehmende Standort kann einen eigenen Shareomat Edge besitzen.
10. Pro LEG existiert genau eine Coordinator-Rolle.
11. Der Coordinator kann gleichzeitig Edge eines Standortes sein.
12. Verschiedene Häuser kommunizieren nicht über direkte öffentliche IP-Verbindungen.
13. Shareomat-Edges verbinden sich selbst mit einem gemeinsamen Kommunikationspunkt.
14. Eine Domain `shareomat.ch` reicht für das gesamte System.
15. `mqtt.shareomat.ch` bildet logisch den Maschinen-Kommunikationsendpunkt.
16. `my.shareomat.ch` bildet logisch den Benutzerzugang.
17. Geräte-/Standortidentität wird über Authentifizierung bestimmt und nicht aus ungeprüften Payload-IDs übernommen.
18. LEGs werden auf dem zentralen Broker strikt voneinander isoliert.
19. Lokale Emsomat-Funktion bleibt auch bei Internet-, Broker- oder Coordinator-Ausfall erhalten.
20. Cloudflare ist Deployment-/Security-Infrastruktur und nicht Teil der fachlichen Architektur.

---

# 66. Bewusst noch offene Entscheidungen

Folgende Punkte sind **noch nicht entschieden** und dürfen nicht als bereits festgelegt behandelt werden:

## Kommunikation

- MQTT 3.1.1 oder MQTT 5
- exakte Topic-Struktur
- Payload-Format
- JSON, MessagePack oder anderes Format
- konkrete QoS-Stufe pro Datenklasse
- Session-Konfiguration
- Keepalive
- Retry-/Backoff-Verhalten
- retained Messages pro Datentyp

## Security

- Username/Password oder Client-Zertifikate
- mTLS
- Credential-Provisioning
- Credential-Rotation
- genaue Broker-ACL-Struktur
- genaue Netzwerk-Firewallregeln

## Infrastruktur

- welcher MQTT-Broker
- Broker zunächst zuhause oder VPS/Cloud
- MQTT/TLS oder MQTT/WSS für WAN
- Cloudflare-Tunnel dauerhaft oder nur erste Phase
- Hochverfügbarkeit des zentralen Brokers

## Shareomat

- genaue Datenbank
- genaue Persistenzstrategie
- Datenhaltungsfristen
- zentrale oder Coordinator-basierte Web-Datenhaltung
- Backup-Strategie
- Coordinator-Failover

## Emsomat

- genaue Fallback-Zeiten
- zulässiges Alter von LEG-Daten
- welche Shareomat-Daten in Regelalgorithmen eingehen dürfen
- welche Daten nur informativ sind

---

# 66b. Protokollentscheidung Geräte-Ebene vs. LEG-Ebene (Stand 2026-08-22)

Ergänzung zu Abschnitt 66 ("Payload-Format" war dort offen):

- **Geräte-Ebene (innerhalb eines Hauses, Emsomat ↔ Wärmepumpe/Wallbox/Wechselrichter):**
  EEBUS (SPINE-Datenmodell + SHIP-Transport) bleibt hier richtig — siehe Abschnitt 3.
  SHIP ist für direktes mDNS-Pairing/TLS im lokalen Netz gebaut, SPINE modelliert
  einzelne steuerbare Anschlusspunkte. Passt zur Aufgabe.
- **LEG-Ebene (Shareomat ↔ Shareomat, über Relay/WAN):** EEBUS/SPINE/SHIP NICHT
  wiederverwenden — SHIP ist nicht für Broker-Relay über WAN zwischen fremden
  Standorten gebaut, SPINE nicht für Abrechnung/SDAT/Tarife. Stattdessen beim
  eigenen, leichten JSON-über-MQTT-Vertrag bleiben (siehe Abschnitt 67.A), inhaltlich
  an SDAT angelehnt.
- **Vergleich mit anderen Ländern (zur Einordnung, kein Übernahme-Vorschlag):**
  USA nutzt für die WAN-Strecke (zentral ↔ viele Standorte) IEEE 2030.5/SEP2 (Basis
  California Rule 21) und OpenADR (VTN/VEN-Modell) — strukturell dasselbe Muster wie
  Coordinator/Edge/Relay hier, aber schwergewichtig (XML/REST, Zertifizierung).
  China hat keinen vergleichbaren offenen Cross-Vendor-Standard (State Grid nutzt
  IEC-60870-104-Varianten, Hersteller eigene Cloud-APIs). Bestätigt nur, dass das
  Coordinator/Relay-Muster strukturell richtig gedacht ist — kein Grund, ein fremdes
  Protokoll zu importieren.

**Why:** User fragte explizit, ob EEBUS/SPINE für die Shareomat-Kommunikation Sinn
macht bzw. wie es anderswo (USA/China) gelöst wird — Antwort: EEBUS ja, aber nur auf
der Geräte-Ebene, nicht auf der LEG-Ebene.

**Update 2026-08-22 — lokale Ebene (Emsomat ↔ Shareomat, ein Haus) entschieden:**
Für die lokale Strecke (nicht die LEG-/WAN-Ebene oben) gilt jetzt MQTT 5.0 + Eclipse
Sparkplug 3.0 als verbindliche Entscheidung, siehe
`docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md`. Der frühere eigene
JSON-über-MQTT-Entwurf für diese lokale Strecke ist damit obsolet.

**Update 2026-08-22 (Fortsetzung) — LEG-/WAN-Ebene ebenfalls auf Sparkplug
entschieden, korrigiert die Aussage oben:** Nach erfolgreicher lokaler
Implementierung (echter Broker-Test bestanden) wurde die frühere Einschätzung
"eigener leichter Vertrag statt Sparkplug" für die WAN-Ebene revidiert — die
Sparkplug-Topic-Struktur (`group_id`=LEG, `edge_node_id`=Standort,
`device_id`=Teilnehmer) deckt die LEG-Isolation-Anforderung (Abschnitt 31)
und Identität-aus-Authentifizierung (Abschnitt 29) direkt ab, ohne einen
zweiten, separaten Protokoll-Stack zu brauchen. Verbindliche Spezifikation:
`docs/Architektur/Shareomat_CrossHouse_Sparkplug_Vertrag.md`.

---

# 66c. Migrationsbedarf: bestehendes `market/`-Modul in Emsomat

Wichtiger Fund beim Prüfen dieser Architektur gegen den echten Emsomat-Code
(2026-08-22): Emsomat hat bereits ein produktives Nachbar-Koordinations-Feature
(`Emsomat/market/` — `NodeMarketStore`, `TrendAnalyzer`, `NeighborDayPattern`, siehe
`market/README.md`), das **aktuell entgegen dieser Architektur direkt Haus-zu-Haus**
kommuniziert:

- `market/adapter.py` nutzt eine eigene, von der lokalen HA-MQTT-Integration
  unabhängige `paho-mqtt`-Verbindung (`Emsomat/mqtt/adapter.py`), konfigurierbar über
  `CONF_MQTT_HOST`/`CONF_MQTT_PORT` (Default `127.0.0.1:1883`).
- Damit das Feature zwischen echten Häusern funktioniert, müssen alle beteiligten
  Emsomat-Instanzen denselben Broker erreichen — es gibt keine separate
  "Nachbar-Broker"-Konfiguration. Das widerspricht direkt Abschnitt 36 dieses
  Dokuments ("Emsomaten kommunizieren nicht direkt miteinander") und Entscheidung #5/
  #12 ("Emsomat besitzt keinen direkten öffentlichen Kommunikationsendpunkt").

**Konsequenz (Status 2026-08-22 final entschieden, nicht mehr nur Plan):** Die alte
Topic-Struktur `emsomat/market/<node_id>/state` wird nicht "gespiegelt", sondern
vollständig ersetzt. `market/`s Domain-Logik (`NodeMarketStore`, `TrendAnalyzer`,
`NeighborDayPattern`, `RampRateLimiter`, `MarketLedger`) bleibt unverändert in
Emsomat bestehen und kennt weiterhin kein MQTT — nur der Transport wechselt auf den
lokalen Sparkplug-Kanal (siehe unten). Damit ist der hier beschriebene Konflikt
aufgelöst: Emsomat spricht nach der Umsetzung nie mehr mit einem geteilten,
Haus-übergreifenden Broker, sondern ausschliesslich lokal mit seinem eigenen
Shareomat.

**Why:** User erkannte diesen Konflikt selbst beim Planen ("die Kommunikation vom
Emsomat, also market, fällt dann [...] er muss nicht mehr direkt mit anderen
Emsomaten sprechen"). Gegen den Code verifiziert (2026-08-22) — der Konflikt war real,
nicht nur theoretisch, weil `market/` bereits gebaut und aktiv ist (siehe
[[project_quartier_koordination]]).

**Verbindliches Domainmodell + Sparkplug-Mapping:** siehe
`docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md` Abschnitt 27 (gleiches Repo) —
das ist die einzige gültige Dokumentation für diese Strecke. Der frühere
Migrationsplan unter `Emsomat/docs/market_shareomat_migration.md` (JSON-Topic-
Spiegelung) ist obsolet und wurde entfernt.

---

# 67. Nächste Spezifikationen

Auf Basis dieses Architekturkonzepts sollen anschließend getrennte Detaildokumente erstellt werden.

## A. Communication Contract

Definiert:

```text
Topics
Payloads
IDs
Timestamp
Quality
Versionierung
QoS
Retain
Timeouts
```

## B. Security Architecture

Definiert:

```text
Netzwerkzonen
Broker ACL
Device Identity
Credentials
TLS
mTLS
Firewall
Container Isolation
Cloudflare/VPS
```

## C. Shareomat Domain Model

Definiert:

```text
LEG
Site
Participant
MeterPoint
Shareomat
Coordinator
Tariff
Allocation
Billing
```

## D. Emsomat Domain Model

Definiert:

```text
Grid
PV
Battery
Load
EnergyDevice
PowerLimit
Availability
Control
Optimization
```

## E. Failure & Recovery Concept

Definiert:

```text
Internet Down
Broker Down
Shareomat Down
Coordinator Down
Emsomat Down
Reconnect
Resynchronisation
Stale Data
Fallback
```

---

# 68. Abnahmekriterien der Architektur

Das Grundkonzept gilt erst als ausreichend definiert, wenn folgende Fragen eindeutig beantwortet werden können:

```text
[ ] Wer besitzt welchen Zustand?

[ ] Welche Daten gehen Emsomat -> Shareomat?

[ ] Welche Daten gehen Shareomat -> Emsomat?

[ ] Welche Daten verlassen ein Haus?

[ ] Welche Daten dürfen ein Haus erreichen?

[ ] Wie wird ein Shareomat eindeutig identifiziert?

[ ] Wie wird eine LEG eindeutig identifiziert?

[ ] Wie verhindert Broker A Zugriff auf LEG B?

[ ] Was passiert bei Internetverlust?

[ ] Was passiert bei Brokerverlust?

[ ] Was passiert bei Coordinator-Ausfall?

[ ] Was passiert bei Shareomat-Ausfall?

[ ] Wann gilt ein Wert als STALE?

[ ] Welche Daten müssen nachgesendet werden?

[ ] Welche Daten dürfen verloren gehen?

[ ] Welche Daten sind abrechnungsrelevant?

[ ] Wie werden Protokollversionen behandelt?

[ ] Wie wird verhindert, dass eine kompromittierte Web-/Cloud-Seite direkten Zugriff auf Emsomat erhält?
```

---

# 69. Nicht-Ziele dieser Architektur

Dieses Konzept soll ausdrücklich **nicht** zu folgenden Mustern führen:

```text
Microservices für jede Fachfunktion
```

```text
MQTT als Ersatz für interne Klassen
```

```text
direkte Kommunikation aller Emsomaten miteinander
```

```text
öffentliche MQTT-Broker in jedem Haus
```

```text
Portforwarding pro Teilnehmer
```

```text
eigene Domain pro Teilnehmer
```

```text
Cloud-Abhängigkeit für die lokale Regelung
```

```text
transparente Cloud-MQTT-Bridge direkt bis Emsomat
```

---

# 70. Referenzierte technische Grundlagen

Die Architektur verwendet MQTT als Kommunikationsmechanismus an echten Systemgrenzen.

MQTT 5 definiert unter anderem:

- Publish/Subscribe,
- Sessions,
- QoS 0/1/2,
- retained Messages,
- TLS als möglichen Transport,
- WebSockets als möglichen Transport.

Eclipse Mosquitto unterstützt sowohl normale MQTT-Listener als auch MQTT über WebSockets.

Cloudflare unterstützt WebSockets und Cloudflare Tunnel unterstützt WebSocket-Verbindungen vollständig. Cloudflare Tunnel baut die Verbindung vom Origin ausgehend auf, sodass am Origin keine öffentliche IP bzw. eingehende Portfreigabe erforderlich ist.

Diese Komponenten sind **austauschbare Implementierungen der Architektur** und nicht selbst die Architektur.

---

# 71. Architektur-Leitsatz

> **Ein integrierter Kern pro Anwendung, klar definierte Systemgrenzen und Kommunikation nur dort, wo Verteilung tatsächlich notwendig ist.**

Für Shareomat/Emsomat bedeutet das:

```text
               PUBLIC / WAN

               SHAREOMAT
         Communication Boundary

                 MQTT

                EMSOMAT
          Local Energy Core

              ENERGY SYSTEM
```

Und standortübergreifend:

```text
EMSOMAT A

SHAREOMAT A

mqtt.shareomat.ch

SHAREOMAT B

EMSOMAT B
```

**Emsomat bleibt lokal und autonom.
Shareomat bildet die kontrollierte Kommunikationsschicht.
Der zentrale Broker verbindet Shareomaten, nicht Emsomaten.
Pro LEG gibt es genau einen Coordinator, aber pro Standort kann ein Edge existieren.**
