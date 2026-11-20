# Shareomat – Swisseldex FTPES / SDAT-CH 2025 vollständig integrieren

> Auftrag des Betreibers vom 12.11.2026, unverändert abgelegt. Die Prüfung gegen
> Code, Swisseldex-Handbuch und VSE-Unterlagen steht am Ende dieser Datei
> («Prüfung vom 12.11.2026»). Noch nicht freigegeben, noch nicht begonnen.

## Ziel und Ausgangslage

Wir wollen die bestehende Shareomat-Installation um einen vollständigen, zuverlässigen und benutzerfreundlichen Swisseldex-Datenimport erweitern.

**Wichtig: Es geht ausdrücklich nicht um einen Neubau von Shareomat. Die bestehende Anwendung läuft bereits und enthält reale Konfigurations- und Geschäftsdaten.**

Bereits vorhanden beziehungsweise eingerichtet sind unter anderem:

- Unsere LEG-Gemeinschaft
- Teilnehmer mit ihren Stammdaten
- Zugeordnete Messpunkte
- Erfasste und teilweise bereits zugeordnete Verträge
- Vertragsversionen und Tarifinformationen
- Weitere Einstellungen und bestehende Shareomat-Funktionen

Diese Daten dürfen unter keinen Umständen durch die Erweiterung verloren gehen, zurückgesetzt, verändert oder beschädigt werden.

Auch bereits funktionierende Importwege, Abrechnungen, Rechnungen, Gutschriften, Benutzerverwaltung, Home-Assistant-Integration und sonstige Funktionen müssen unverändert weiter funktionieren.

**Ziel dieser Entwicklungsrunde ist eine fertige Swisseldex-Integration, mit der ich unmittelbar einen ersten echten Verbindungstest und eine manuelle Synchronisation durchführen kann.**

Die Zugangsdaten sind bei Swisseldex bereits eingerichtet. Die Verbindung wurde mit FileZilla erfolgreich getestet.

Bitte den Auftrag vollständig implementieren, nicht lediglich analysieren oder ein Konzept erstellen. Die nachfolgend vorgeschriebenen Sicherheitsprüfungen und Tests haben dabei Vorrang.

---

# 1. Oberste Priorität: Bestehende Installation schützen

Das ist eine verbindliche Entwicklungsanforderung.

## 1.1 Vor jeder Änderung

Zuerst untersuchen:

1. Aktueller Git-Status und lokale, noch nicht commitete Änderungen
2. Projektanweisungen in `CLAUDE.md` und `AGENTS.md`
3. Bestehende Architektur und Datenbankstruktur
4. Aktuelle SQLite-Migrationen
5. Import-Pipeline und Automatisierung
6. Bereits vorhandene SDAT-Implementierung
7. Bestehende Testabdeckung

Erstelle eine kurze Bestandsaufnahme.

**Keine bestehenden Dateien, Funktionen oder Daten ersetzen, nur weil eine neue Implementierung einfacher erscheint.**

Bereits laufende Entwicklungsarbeiten anderer Bereiche respektieren. Keine unbeteiligten Änderungen überschreiben.

## 1.2 Datenbank und Migrationen

Shareomat verwendet SQLite und speichert dort reale Stammdaten und Betriebsdaten.

Verbindliche Regeln:

- Keine Datenbank neu initialisieren oder zurücksetzen.
- Keine bestehenden Tabellen löschen.
- Keine Teilnehmer, Messpunkte oder Verträge löschen oder verändern.
- Keine vorhandenen Vertragsversionen neu erzeugen.
- Keine bestehenden Tarifzuordnungen verändern.
- Keine historischen Rechnungen oder Abrechnungsläufe verändern.
- Keine unkontrollierten Datenbankmigrationen.
- Keine Löschung von bestehenden Archiven oder Importstatusdateien.

Neue Tabellen und additive Migrationen sind erlaubt, sofern notwendig.

Migrationen müssen auf einer realistischen Kopie der vorhandenen Datenbank getestet werden.

Bei einer tatsächlich erreichbaren produktiven Datenbank muss vor einer Migration ein konsistentes SQLite-Backup erstellt und dessen Wiederherstellbarkeit geprüft werden. SQLite-WAL-Dateien und laufende Transaktionen korrekt berücksichtigen.

Eine Wiederherstellung muss auch nach einer teilweise fehlgeschlagenen Migration möglich sein.

**Niemals die produktive Datenbank als Testdatenbank verwenden.**

Wenn du die produktive Datenbank nicht direkt erreichen kannst, keine Daten oder Backups als vorhanden behaupten. Alle Tests zunächst mit einer isolierten Testdatenbank durchführen.

## 1.3 Regressionstests

Vor der Implementierung vorhandene Tests ausführen und den Ausgangsstand dokumentieren.

Nach der Implementierung erneut die vollständige Testsuite ausführen.

Zusätzlich gezielt prüfen:

- Gemeinschaft und Teilnehmer
- Messpunkte und Zuordnungen
- Vertragsversionen und Vertragsannahmen
- Tarifberechnung
- Bisheriger CSV-Import
- EBL-Excel-Import
- Bestehender S-DAT-Import
- Monatsabrechnung
- Rechnungserstellung und Gutschriften
- Bestehende Automatisierung
- Login, Session und CSRF-Schutz
- Home Assistant Ingress
- Standalone-Betrieb, soweit testbar

Alte Funktionen dürfen durch die Erweiterung nicht schlechter werden.

---

# 2. Bestehenden SDAT-Code prüfen und wiederverwenden

Bei einer vorherigen externen Codeprüfung wurden folgende bestehende Funktionen festgestellt:

- `shareomat/core/pipeline/raw/sdat_ch.py`
- EIC-Validierung
- `validate_receiver()`
- CEM- und LEG-Konstanten
- E31/E66-Vorbereitung
- Bestehende Import-Router
- SHA-256-Duplikaterkennung
- Import-Scheduler
- Dateiarchivierung

Der E31/E66-Parser wurde damals noch durch `SdatChNotImplementedError` begrenzt.

Bitte den tatsächlichen aktuellen lokalen Code prüfen. Der Entwicklungsstand kann inzwischen weiter sein als die öffentlich sichtbare GitHub-Version.

Für jede Funktion feststellen:

- Vollständig vorhanden
- Teilweise vorhanden
- Fehlerhaft
- Fehlend

**Vorhandene funktionierende Komponenten wiederverwenden. Keine parallele zweite SDAT-Architektur entwickeln.**

Insbesondere zwischen altem S-DAT-Format und SDAT-CH 2025 sauber unterscheiden.

---

# 3. Technische Architektur der neuen Swisseldex-Anbindung

Die Integration muss modular aufgebaut werden.

Gewünschte logische Struktur:

```text
Swisseldex Datahub
       |
       | FTPES
       v
Swisseldex FTPES Client
       |
       v
Download / Empfangsarchiv
       |
       v
SDAT-CH 2025 Parser
       |
       v
Validierung / Staging
       |
       v
Freigegebene Messdaten
       |
       v
Bestehende Shareomat-Abrechnung
```

Die Komponenten sollen klar getrennt sein.

## 3.1 FTPES-Client

Verantwortlich ausschliesslich für:

- Verbindung
- Authentifizierung
- Dateiliste
- Download
- Verbindungsbehandlung
- Wiederholungsversuche
- Remote-Dateiverwaltung

Keine LEG-Abrechnungslogik im FTPES-Client.

## 3.2 SDAT-Parser

Verantwortlich für:

- XML-Verarbeitung
- Schema-Validierung
- Nachrichtentyp
- Dokumentidentifikation
- Messpunktidentifikation
- Zeitreihen
- Energiearten
- Qualitätsmerkmale
- Ersetzungen und Stornierungen

Keine FTPES-Logik im Parser.

## 3.3 Messdatenimport

Verantwortlich für:

- Zuordnung zur richtigen Gemeinschaft
- Zuordnung zu Messpunkten
- Plausibilisierung
- Importstatus
- Speicherung und Versionierung
- Bereitstellung für die Abrechnung

Die bestehende Shareomat-Import-Pipeline soll nach Möglichkeit weiterverwendet werden.

Eine klare Trennung dieser Komponenten ist wichtig, damit wir zukünftig weitere Netzbetreiber und Datenquellen anbinden können.

---

# 4. Swisseldex FTPES – bestätigte Verbindungsdaten

Folgende Einrichtung ist bereits vorhanden:

- Swisseldex-Konto eingerichtet
- Marktpartnerrolle: CEM (Community Energy Manager)
- Empfangsvariante: «Server beim Datahub»
- FTPES-Zugangsdaten generiert
- Anmeldung über FileZilla erfolgreich
- TLS-Verbindung erfolgreich
- Remote-Verzeichnis erfolgreich aufgelistet
- Aktuell noch keine Dateien vorhanden

Technische Einstellungen:

```text
Host: datahub.swisseldex.ch
Port: 21
Protokoll: FTPES
TLS: Explicit TLS
FTP-Modus: Passive
Remote-Verzeichnis: /
```

Es wird **kein eigener FTPES-Server** benötigt.

Dateien werden bei Swisseldex bereitgestellt und von Shareomat abgeholt.

Das FTPES-Postfach ist nicht identisch mit dem SDAT Webclient der Swisseldex-Weboberfläche.

## 4.1 Verbindung

Bitte implementieren beziehungsweise prüfen:

- Explizite TLS-Verbindung
- Zertifikats- und Hostnamenprüfung
- Verschlüsselter Steuer- und Datenkanal
- Passiver Modus
- Konfigurierbare Timeouts
- Saubere Behandlung von Verbindungsabbrüchen
- Begrenzte Wiederholungsversuche mit Backoff
- Keine unendlichen Retry-Schleifen
- Keine dauerhafte Verbindung erforderlich

Swisseldex dokumentiert eine Trennung nach kurzer Inaktivität, etwa zehn Sekunden.

Beim FileZilla-Test trat nach erfolgreichem Login und Verzeichnislisting folgender Fehler auf:

`GnuTLS error -110: The TLS connection was non-properly terminated`

Das darf nicht pauschal als fehlgeschlagener Login gewertet werden.

Bitte unterscheiden:

- Fehler beim Verbindungsaufbau
- Fehler beim Login
- Fehler beim Listing
- Fehler während eines Downloads
- Fehler beim Beenden einer ansonsten erfolgreichen Sitzung

Ein unvollständiger Download darf niemals als erfolgreich gelten.

Ein korrekt abgeschlossener Download soll hingegen nicht allein wegen eines nachgelagerten TLS-Schliessfehlers verworfen werden.

## 4.2 Dateiverwaltung

Swisseldex stellt eingehende Dateien im Hauptverzeichnis bereit.

Unterstützte relevante Dateiformate sind insbesondere:

- `.xml`
- `.xml.gz`
- gegebenenfalls weitere vom Datahub unterstützte Nachrichtenformate

Unbekannte Dateien nicht blind importieren, sondern identifizieren und sicher behandeln.

Für Dateien einen stabilen lokalen Empfangsstatus führen.

Dateien müssen vor ihrer Verarbeitung vollständig und dauerhaft lokal gesichert werden.

## 4.3 Remote-Dateien löschen

**Für unseren ersten echten Test ist das automatische Löschen von Dateien auf Swisseldex ausdrücklich deaktiviert.**

Der erste Test soll nur:

1. Verbinden
2. Auflisten
3. Neue Dateien herunterladen, falls vorhanden
4. Lokal sichern
5. Verbindung beenden

Erst nachdem wir die Verarbeitung erfolgreich nachgewiesen haben, darf eine optionale automatische Remote-Bereinigung vorgesehen werden.

Diese muss explizit aktiviert werden und darf nur Dateien löschen, deren Verarbeitung nachweislich abgeschlossen und dauerhaft gespeichert wurde.

Nicht unterstützte, fehlerhafte oder noch nicht zugeordnete Nachrichten dürfen nicht automatisch verschwinden.

---

# 5. SDAT-CH 2025 vollständig unterstützen

Der neue Import muss die für unsere LEG relevanten offiziellen SDAT-CH-Nachrichten verarbeiten können.

Relevant sind insbesondere:

**E66 – Individuelle Messdaten der LEG-Teilnehmer**

**E31 – Aggregierte Messdaten der LEG**

Wichtige Merkmale:

- Marktpartnerrolle CEM
- BusinessReasonCode C40
- CommunityID
- CommunityType
- Messpunkt-ID
- EnergyProductIdentificationCode
- Datenqualität
- Nachrichtenidentität
- Dokumentfunktion
- Zeitintervall
- Energierichtung
- Energieeinheit
- 15-Minuten-Auflösung

Die genaue XML-Struktur und die Codes bitte anhand der offiziellen SDAT-CH-2025-XSDs und Beispiele implementieren.

**Keine Feldnamen, XML-Pfade oder Zuordnungen erfinden.**

Bei nicht eindeutig bekannten Strukturen muss der Import mit einer verständlichen Fehlermeldung stoppen, statt fragwürdige Daten in die Abrechnung zu übernehmen.

## 5.1 LEG-Energiearten sauber speichern

Hier wurde bei der vorherigen Codeprüfung eine mögliche kritische Schwachstelle festgestellt.

Das bisherige `IntervalReading`-Modell unterscheidet hauptsächlich nach:

- Messpunkt
- Zeit
- Richtung
- Wert
- Qualität

Bei den neuen LEG-Nachrichten müssen aber unterschiedliche Energieprodukte getrennt gespeichert werden können.

Beispielsweise:

- Gesamter Bezug beziehungsweise gesamte Einspeisung
- Innerhalb der LEG zugeteilte Energie
- Verbleibender Netzbezug beziehungsweise verbleibende Netzeinspeisung

Diese Werte dürfen niemals versehentlich gegenseitig überschrieben werden.

Bitte das bestehende Datenmodell prüfen.

Falls notwendig, ein additives, versioniertes Datenmodell für die offiziellen SDAT-Zeitreihen ergänzen.

Die bisherige CSV-/Excel-Abrechnung darf dadurch nicht beschädigt werden.

## 5.2 Korrekturlieferungen

Swisseldex beziehungsweise EBL kann dieselben Messperioden mehrfach liefern.

Zu behandeln sind:

- Erste Lieferung
- Wiederholte identische Lieferung
- Neue Nachricht mit bereits bekannten Zeiträumen
- Korrigierte Werte
- Ersetzungsnachricht
- Stornierung
- Vorläufige Messdaten
- Plausibilisierte beziehungsweise abrechnungsrelevante Messdaten

Identische Nachrichten dürfen keine doppelten Datensätze erzeugen.

Korrekturen müssen nachvollziehbar gespeichert werden.

Vorläufige Daten dürfen finalisierte Werte nicht unzulässig überschreiben.

Historische Originalnachrichten müssen weiterhin nachvollziehbar bleiben.

Bereits freigegebene Abrechnungen dürfen durch nachträgliche SDAT-Korrekturen nicht stillschweigend verändert werden.

Stattdessen ist bei relevanten Abweichungen ein klarer Hinweis auf erforderliche Prüfung oder Korrekturabrechnung vorzusehen.

## 5.3 Datenvalidierung

Prüfen:

- XML gegen zutreffendes Schema
- Absender und Empfänger
- Eigene EIC-Kennung
- CommunityID
- CommunityType
- Messpunktidentität
- Zulässige Energieprodukte
- Vollständige Zeitreihen
- UTC und Zeitzonenbehandlung
- Sommer-/Winterzeit
- Fehlende oder doppelte Intervalle
- Einheiten und numerische Genauigkeit
- Datenqualität
- Plausibilität zwischen Gesamt-, LEG- und Restenergie

Sicheres XML-Parsing ohne XXE und mit Grössenbegrenzungen.

Fachliche Zuordnungen müssen anhand der offiziellen Spezifikation erfolgen.

---

# 6. Bestehende Abrechnungslogik schützen

Shareomat besitzt bereits einen Algorithmus für die proportionale Verteilung lokaler Energie.

Dieser ist weiterhin für Simulationen, Kontrollen und andere unterstützte Importquellen sinnvoll.

Bei offiziellen E66-Daten ist jedoch Folgendes entscheidend:

**Die vom Netzbetreiber bereits zugeteilten LEG-Energiemengen sind für die Abrechnung massgebend.**

Shareomat darf diese Energie nicht nochmals proportional verteilen.

Bitte die bestehende Matching- und Abrechnungslogik darauf prüfen.

Die Auswertung muss erkennen können, ob die Datenquelle:

- Rohmessdaten ohne LEG-Zuteilung liefert
- oder bereits offiziell aufgeteilte LEG-Zeitreihen enthält

Die korrekte Verarbeitung soll innerhalb der bestehenden Architektur erfolgen.

Eine automatische Abrechnung darf nicht allein deshalb gestartet werden, weil neue Dateien von Swisseldex heruntergeladen wurden.

Insbesondere:

**Dateisynchronisation, Datenimport und Abrechnungsfreigabe sind getrennte Vorgänge.**

---

# 7. Benutzeroberfläche – einfach und verständlich

Shareomat soll trotz seiner internen Komplexität sehr einfach bedienbar bleiben.

Bitte die Swisseldex-Anbindung in die bestehenden Verwaltungs- beziehungsweise Integrationsseiten einbauen.

Keine überflüssigen Hauptmenüs.

Mein Vorschlag ist ein Bereich:

**Einstellungen / Datenquellen → Swisseldex SDAT**

Wenn bereits ein geeigneter Bereich existiert, diesen verwenden.

## 7.1 Einstellungen

Folgende Funktionen sollen direkt über die Weboberfläche verfügbar sein:

**Verbindung**

- Swisseldex aktiviert/deaktiviert
- Serveradresse
- Port
- Benutzername
- Passwort
- Verbindung testen

**Synchronisation**

- Automatische Synchronisation aktiviert/deaktiviert
- Synchronisationsintervall
- Manuelle Synchronisation
- Remote-Dateien automatisch löschen: standardmässig AUS

Mögliche Intervalle:

- 1 Stunde
- 3 Stunden
- 6 Stunden
- 12 Stunden
- 24 Stunden

Standardwert: 6 Stunden.

Die automatische Synchronisation bleibt nach der Erstinstallation deaktiviert, bis der Betreiber sie bewusst aktiviert.

Einstellungsänderungen sollen möglichst ohne Neustart wirksam werden.

Die Konfiguration muss nach einem Neustart erhalten bleiben.

## 7.2 Statusanzeige

Direkt auf der Seite anzeigen:

- Verbindungsstatus
- Letzter Verbindungsversuch
- Letzte erfolgreiche Synchronisation
- Nächste geplante Synchronisation
- Anzahl Remote-Dateien
- Anzahl neuer Downloads
- Anzahl verarbeiteter Dateien
- Anzahl Dateien mit Fehlern

Keine komplizierten technischen Meldungen für normale Benutzer.

Beispiele:

- «Verbindung erfolgreich»
- «Postfach leer – keine neuen Dateien»
- «3 Dateien heruntergeladen»
- «2 Dateien importiert, 1 Datei benötigt Prüfung»
- «Anmeldung fehlgeschlagen»
- «Swisseldex ist momentan nicht erreichbar»

Technische Details im Diagnoseprotokoll verfügbar machen.

---

# 8. Zwei manuelle Testmöglichkeiten

Das ist für unseren unmittelbar anstehenden Praxistest besonders wichtig.

## 8.1 Button «Verbindung testen»

Dieser Button darf keine Daten verändern.

Ablauf:

1. FTPES-Verbindung herstellen.
2. Anmelden.
3. Remote-Verzeichnis lesen.
4. Ergebnis anzeigen.
5. Verbindung beenden.

Keine Downloads erforderlich.

Keine Dateien löschen.

Keine Abrechnung auslösen.

Auch wenn das Remote-Verzeichnis leer ist, muss der Test erfolgreich sein können.

Beispiel:

`Verbindung erfolgreich – 0 Dateien vorhanden.`

## 8.2 Button «Jetzt synchronisieren»

Dieser Button startet sofort einen vollständigen Download-Durchlauf.

Ablauf:

1. FTPES-Verbindung herstellen.
2. Remote-Dateien ermitteln.
3. Bereits bekannte Dateien erkennen.
4. Neue Dateien herunterladen.
5. Dateien dauerhaft lokal sichern.
6. Empfang und gegebenenfalls Verarbeitung protokollieren.
7. Ergebnis anzeigen.

Der erste reale Test läuft im sicheren Modus:

**Download und Validierung, aber keine automatische Abrechnung, Rechnungsfreigabe oder Remote-Löschung.**

Das Herunterladen darf unabhängig davon funktionieren, ob die E31/E66-Verarbeitung schon alle Dateien erfolgreich interpretieren kann.

Nicht unterstützte Nachrichten bleiben im sicheren Empfangsarchiv und werden als prüfbedürftig gekennzeichnet.

## 8.3 Parallele Jobs verhindern

Eine manuelle und eine automatische Synchronisation dürfen sich nicht gegenseitig stören.

- Nur eine aktive Synchronisation pro Swisseldex-Konto
- Verständliche Anzeige bei bereits laufendem Job
- Keine doppelten Downloads durch konkurrierende Prozesse
- Korrekte Wiederaufnahme nach Absturz
- Keine blockierte Benutzeroberfläche

Den bestehenden Scheduler und seine Sperrmechanismen nach Möglichkeit wiederverwenden oder sauber erweitern.

---

# 9. Logansicht und Diagnose

Ich möchte als Betreiber nachvollziehen können, was die Schnittstelle macht.

Dazu eine kleine, übersichtliche Logansicht integrieren.

Beispielsweise direkt unter der Swisseldex-Konfiguration.

Pro Synchronisation speichern:

- Startzeit und Endzeit
- Auslöser: manuell oder automatisch
- Verbindungsergebnis
- Gefundene Dateien
- Erfolgreiche Downloads
- Bereits bekannte Dateien
- Erfolgreiche Importe
- Fehlerhafte Dateien
- Wiederholungsversuche
- Fehlermeldungen

Jeder Durchlauf soll einen eigenen Status und eine eindeutige ID besitzen.

Eine Filterung nach erfolgreichen und fehlerhaften Vorgängen ist sinnvoll.

Logs persistent speichern und begrenzen, sodass sie auch nach einem Neustart verfügbar bleiben.

Wichtig ist die Unterscheidung von:

- Verbindung erfolgreich
- Download erfolgreich
- Import erfolgreich
- Daten für Abrechnung freigegeben

Diese vier Zustände dürfen nicht als ein gemeinsamer Erfolg behandelt werden.

**Keine Passwörter, Zugangstokens oder geheimen Konfigurationswerte in Logs speichern.**

---

# 10. Sichere Zugangsdatenverwaltung

Swisseldex-Benutzername und Passwort werden später von mir über die Oberfläche eingetragen.

Bitte sicherstellen:

- Zugangsdaten nicht im Git-Repository
- Nicht in Quellcode hartcodieren
- Nicht im Browser-HTML als Klartext zurückgeben
- Nicht in Fehlerprotokollen
- Nicht in Exporten oder Debug-Ausgaben
- Nur berechtigte Administratoren dürfen Einstellungen ändern
- Bestehenden Authentifizierungs- und CSRF-Schutz nutzen
- Server-Zertifikate korrekt überprüfen

Bitte das vorhandene Shareomat-Konfigurationskonzept berücksichtigen.

Insbesondere muss es sowohl als Home-Assistant-Add-on als auch im Standalone-Betrieb funktionieren.

Sensible Daten sind über einen geeigneten geschützten Konfigurations- oder Secret-Mechanismus zu speichern. Keine scheinbare Verschlüsselung ohne sichere Schlüsselverwaltung einführen.

---

# 11. Fehlerbehandlung, Wiederanlauf und Idempotenz

Die Synchronisation muss langfristig zuverlässig laufen, ohne dass der Betreiber ständig eingreifen muss.

Bitte folgende Fälle abdecken:

- Swisseldex nicht erreichbar
- Falsches Passwort
- TLS-Fehler
- Leeres Verzeichnis
- Timeout beim Verzeichnislisting
- Abbruch während Download
- Verbindungstrennung nach erfolgreichem Download
- Unvollständig gespeicherte Datei
- Neustart während Synchronisation
- Derselbe Dateiname mehrfach
- Unterschiedlicher Inhalt bei gleichem Namen
- Dieselbe Nachricht mit neuem Dateinamen
- Fehlerhafte XML-Datei
- Nicht unterstützter SDAT-Nachrichtentyp
- Noch nicht bekannter Messpunkt
- Korrigierte Messdaten
- Remote-Datei kann nicht gelöscht werden
- Datenbank vorübergehend nicht verfügbar

Wesentliche Invarianten:

1. Kein Datenverlust.
2. Keine unbemerkten Duplikate.
3. Keine unkontrollierte Überschreibung.
4. Keine falschen Abrechnungen.
5. Jeder Fehler muss nachvollziehbar sein.
6. Nach einem Neustart muss die Verarbeitung fortgesetzt werden können.

Dafür geeignete Transaktionen, atomare Dateischreibvorgänge und persistente Importzustände verwenden.

---

# 12. Tests – verbindlich

Bitte die bestehende Testinfrastruktur erweitern.

## 12.1 FTPES-Tests

Mit simuliertem FTPES-Server oder geeignetem Mock prüfen:

- Erfolgreicher Login
- Fehlgeschlagener Login
- TLS-Verbindungsfehler
- Leeres Verzeichnis
- Eine neue Datei
- Mehrere Dateien
- Bereits heruntergeladene Datei
- Downloadabbruch
- Erneuter Versuch
- Dateiduplikate
- Server trennt Verbindung
- Gleichzeitiger manueller und automatischer Start
- Vollständiger Wiederanlauf nach Neustart

Keine destruktiven Tests gegen den echten Swisseldex-Server.

## 12.2 SDAT-Tests

Offizielle XSDs und realistische Testnachrichten verwenden.

Insbesondere:

- E31
- E66
- Mehrere Messpunkte
- Unterschiedliche Energierichtungen
- LEG-Energie und Restenergie
- Vorläufige und finale Werte
- Korrigierte Nachrichten
- Ungültige Datenqualität
- Ungültige XML-Struktur
- Falsche CommunityID
- Falscher Empfänger
- Unbekannter Messpunkt
- Sommer-/Winterzeit
- Wiederholte Nachrichten
- Ersetzungen und Stornierungen

## 12.3 Bestandsschutztests

Sehr wichtig:

Mit einer vorbereiteten Datenbank testen, die bereits folgende Daten enthält:

- Gemeinschaft
- Mehrere Teilnehmer
- Messpunkte
- Vertrag
- Unterschiedliche Vertragsversionen
- Tarifzuordnungen
- Historische Importdaten
- Bereits vorhandene Abrechnungsdaten

Vor und nach der Migration die bestehenden Inhalte und Beziehungen vergleichen.

Es muss nachgewiesen werden, dass keine vorhandenen Geschäftsdaten ungewollt verändert wurden.

Zusätzlich prüfen, dass ein vollständig deaktivierter Swisseldex-Importer keinerlei Auswirkungen auf das bisherige Verhalten von Shareomat hat.

---

# 13. Home Assistant und Deployment

Shareomat wird bereits als Home-Assistant-Add-on verwendet.

Bitte deshalb ausdrücklich beachten:

- Bestehendes Add-on nicht zurücksetzen
- Persistente Datenverzeichnisse nicht verändern
- Keine Neuinstallation erzwingen
- Bestehende Benutzerkonten erhalten
- HA-Ingress-Routing korrekt berücksichtigen
- CSRF- und Authentifizierungsprüfungen erhalten
- Neue Konfiguration über Updates hinweg erhalten

Die kanonische Anwendung befindet sich laut Projektarchitektur in `shareomat/`.

`ha_addon/` enthält die synchronisierte Build-Kopie.

Nach Abschluss:

```bash
./prepare_addon.sh
python tools/prepare_addon.py --check
```

beziehungsweise die unter dem lokalen Betriebssystem vorgesehenen äquivalenten Befehle verwenden.

Den Synchronisationsstatus tatsächlich prüfen.

Nicht automatisch auf die laufende produktive Installation deployen, sofern dadurch reale Daten migriert oder verändert würden. Zuerst isoliert testen und eine sichere, nachvollziehbare Update-Anleitung bereitstellen.

Keine automatischen Git-Pushes.

---

# 14. Reihenfolge der Implementierung

Bitte in dieser Reihenfolge arbeiten:

**Phase A – Bestandsaufnahme und Bestandsschutz**

- Git-Status prüfen
- Bestehenden Code prüfen
- Ausgangstests ausführen
- Datenbank und Migrationsstrategie prüfen
- Bestehende Komponenten identifizieren

**Phase B – FTPES-Integration**

- Client fertigstellen
- Verbindungstest implementieren
- Datei-Listing
- Sicheren Download
- Empfangsarchiv
- Wiederanlauf und Duplikaterkennung

**Phase C – Benutzeroberfläche**

- Konfiguration
- Verbindungsbutton
- Manueller Sync
- Intervallsteuerung
- Status und Logs

**Phase D – SDAT-CH 2025**

- E31/E66-Parser
- XSD-Validierung
- Energiearten getrennt speichern
- Messpunktzuordnung
- Korrekturlieferungen
- Qualitätsprüfung
- Integration in die vorhandene Messdaten-Pipeline

**Phase E – Abrechnungssicherheit**

- Keine doppelte Energieverteilung
- Offizielle EBL-Werte korrekt verwenden
- Vorläufige Daten kennzeichnen
- Bereits freigegebene Abrechnungen schützen

**Phase F – Abschluss**

- Alle Tests
- Regressionstests
- Migrationsprüfung auf Datenbankkopie
- HA-Add-on-Synchronisation
- Dokumentation
- Anleitung für den ersten echten Swisseldex-Test

Bitte diese Phasen vollständig abarbeiten. Nicht bei Phase B stehen bleiben, sofern keine tatsächlich unbehebbare technische Blockade besteht.

---

# 15. Was ich anschliessend selbst testen möchte

Ich habe bereits einen funktionierenden Swisseldex-Zugang.

Deshalb soll ich nach dieser Entwicklungsrunde in Shareomat folgendes machen können:

1. Shareomat über die vorhandene Oberfläche öffnen.
2. Swisseldex-Konfiguration aufrufen.
3. FTPES-Benutzername und Passwort eingeben.
4. «Verbindung testen» drücken.
5. Erfolgreichen Login und das Remote-Verzeichnis sehen.
6. «Jetzt synchronisieren» drücken.
7. Das Ergebnis und die Logs sehen.
8. Bei vorhandenen SDAT-Dateien diese sicher herunterladen und prüfen.
9. Später den automatischen Synchronisationsintervall aktivieren.

Aktuell war das Remote-Verzeichnis beim FileZilla-Test leer.

Das ist für den ersten Test kein Hindernis.

Ein Ergebnis wie:

**«Verbindung erfolgreich. Synchronisation abgeschlossen. 0 neue Dateien.»**

ist bereits ein gültiger und wichtiger erfolgreicher Praxistest.

Die echte SDAT-Verarbeitung muss zusätzlich mit geeigneten Testdateien geprüft sein, auch solange EBL noch keine produktiven Dateien bereitstellt.

Wenn die ersten echten EBL-Nachrichten später eintreffen, können wir damit die tatsächliche Interoperabilität zusätzlich nachweisen.

---

# 16. Offizielle Referenzen

Swisseldex-Benutzerhandbuch:

https://manuals.datahub.swisseldex.ch/production/user-manual-de.pdf

VSE – SDAT-CH und zugehörige Anhänge:

https://www.strom.ch/sdat

VSE – XML-Schemas:

https://www.strom.ch/de/media/13631/download

VSE – Core Components:

https://www.strom.ch/de/media/13636/download

Shareomat:

https://github.com/bobis-code/shareomat

Bitte die gültigen offiziellen Schemas und die aktuellen lokalen Projektdateien als technische Grundlage verwenden.

---

# 17. Erwarteter Abschlussbericht

Nach Fertigstellung möchte ich eine präzise Zusammenfassung:

### A. Bestehender Code

Was war schon vorhanden und konnte übernommen werden?

### B. Neue Funktionen

Was wurde tatsächlich implementiert?

### C. Datenbanksicherheit

Welche Migrationen wurden benötigt?

Wie wurde nachgewiesen, dass bestehende Teilnehmer, Verträge und Geschäftsdaten erhalten bleiben?

### D. Tests

- Anzahl bestandener Tests
- Fehlgeschlagene Tests
- Neue Regressionstests
- Ergebnisse der SDAT-Tests
- Ergebnisse des FTPES-Tests
- Noch nicht praktisch verifizierbare Funktionen

### E. Bedienung

Wo finde ich in Shareomat die neuen Einstellungen?

Wie teste ich die Verbindung?

Wie starte ich manuell eine Synchronisation?

Wie aktiviere ich später den automatischen Betrieb?

### F. Produktiver erster Test

Welche Schritte muss ich auf meiner bereits laufenden Shareomat-Installation ausführen?

Welche Risiken bestehen beim Update?

Wurde eine produktive Änderung tatsächlich durchgeführt oder nur isoliert vorbereitet?

Bitte tatsächliche Tests von simulierten Tests klar unterscheiden.

---

# Abschliessende verbindliche Vorgabe

**Shareomat läuft bereits mit echten, erfassten Geschäftsdaten. Diese Installation muss vollständig erhalten bleiben.**

Die Swisseldex-Anbindung ist eine Erweiterung, kein Umbau der bestehenden Anwendung.

Die neue Schnittstelle soll unabhängig betrieben, getestet und bei Bedarf deaktiviert werden können.

Die bisherige Verarbeitung darf dadurch nicht unbeabsichtigt beeinflusst werden.

**Trotz dieser Sicherheitsanforderungen soll die Entwicklung jetzt abgeschlossen werden. Ich möchte anschliessend den ersten echten FTPES-Test direkt über Shareomat durchführen können.**

Bitte selbständig umsetzen, bestehende Projektvorgaben beachten, alle erreichbaren Tests durchführen und am Ende klar zwischen vollständig fertig, isoliert getestet und mit echten EBL-Dateien noch nicht verifiziert unterscheiden.

---

# Prüfung vom 12.11.2026

Geprüft gegen den lokalen Code (HEAD `949d7dd`), das Swisseldex-Benutzerhandbuch
(Stand 28.09.2026, Kapitel 3.4 und 4) und die VSE-Unterlagen SDAT-CH 2025
(Stand 04.12.2025).

## Im Auftrag zutreffend (im Code bestätigt)

* `shareomat/core/pipeline/raw/sdat_ch.py` ist ein Stub:
  `parse_sdat_ch()` wirft `SdatChNotImplementedError`. EIC-Prüfung,
  `validate_receiver()` sowie die Konstanten für Produkte, CEM, C40, CT01/CT02,
  Status und Qualität sind vorhanden.
* Die Schwachstelle aus §5.1 besteht tatsächlich. `meter_readings` hat
  `UNIQUE(meter_id, slot_start, direction)`, und `IntervalReading` kennt keine
  Energieart. Gesamt-, LEG- und Restenergie würden sich gegenseitig überschreiben.
* Die Gefahr aus §6 besteht tatsächlich. `leg_runner` ruft nach jedem Parsen
  `match_all()` auf, also die proportionale Verteilung. Offizielle E66-Daten
  würden ein zweites Mal verteilt.
* Einen FTP- oder FTPES-Code gibt es noch nicht, Phase B beginnt bei null.
* Das Fachkonzept ist bereits vorhanden: `docs/sdat_leg_import.md` mit §1 bis §34
  und den Phasen 1 bis 9. Der Auftrag muss darauf aufbauen und darf kein zweites
  Konzept erzeugen.
* `prepare_addon.sh` und `tools/prepare_addon.py --check` existieren.

## Korrekturen und Ergänzungen

1. **Login-Sperre 60 s (fehlt im Auftrag).** Handbuch 4.4: «Mehrfache Login
   Versuche innerhalb von 60s werden vom FTPES Server abgelehnt.» Folgen daraus:
   * Wiederholungen müssen mindestens 60 s zwischen zwei Logins einhalten.
   * Wer «Verbindung testen» und gleich danach «Jetzt synchronisieren» drückt,
     wird abgewiesen.
   * Es braucht eine gemeinsame Login-Sperre für den Test, den manuellen und den
     automatischen Lauf.
   * Eine Abweisung wegen der Sperre ist kein Anmeldefehler.
2. **Dateiformate (§4.2 unvollständig).** Der Datahub kennt `.xml`, `.xml.gz`,
   `.txt`, `.txt.gz`, `.csv` und `.csv.gz`. Grenzen: höchstens 50 MB pro Datei
   und 512 MB entpackt. Beim Entpacken braucht es deshalb eine Grössengrenze
   (Schutz vor gz-Bomben).
3. **Befehle des Servers (Handbuch 4.3).**
   * `REST` wird nicht unterstützt. Ein abgebrochener Download kann nicht
     fortgesetzt werden und muss neu beginnen.
   * `SIZE` wird unterstützt und eignet sich zur Kontrolle der Vollständigkeit.
   * `MDTM` und `MLSD` werden unterstützt, `FEAT` nicht.
   * Es gibt nur das Hauptverzeichnis, `CWD` ist nur auf `/` erlaubt.
   * Die Login-Reihenfolge ist `AUTH TLS`, `USER`, `PASS`, `PBSZ`, `PROT P`. Ohne
     `PROT P` wird `RETR` mit Status 521 abgelehnt.
4. **30 Tage und Löschen (§4.3 ergänzen).**
   * Nicht abgeholte Nachrichten löscht der Datahub nach 30 Tagen automatisch.
   * Swisseldex empfiehlt, heruntergeladene Dateien zu löschen, weil das Listing
     mit vielen Dateien langsamer wird (etwa 1 s pro 5000 Dateien).
   * Für den ersten Test bleibt «Löschen AUS» richtig. Weil die automatische
     Synchronisation standardmässig aus ist, braucht es aber eine deutliche Warnung,
     wenn seit mehr als etwa 7 Tagen nicht synchronisiert wurde.
   * Needs verification: ob bereits heruntergeladene Dateien ebenfalls nach
     30 Tagen verschwinden.
5. **Referenzen (§16 falsch verlinkt).**
   * `media/13631` ist ein PDF (Anhang 4, Beschreibung), keine XSD-Datei.
   * Die echten XSDs und Beispiele stehen in einer frei ladbaren ZIP-Datei:
     `https://www.strom.ch/system/files/media/documents/20251204-be-standardisierter-datenaustausch-ch-beispieldateien.zip`
     (162 Dateien).
   * Darin unter anderem `ValidatedMeteredData_1p6.xsd`,
     `AggregatedMeteredData_1p3.xsd`, `VSE_CommunityCode_10.xsd` und
     `VSE_EnergyProductIdentificationCode_10.xsd`.
   * Ausserdem die Beispiele «1_12_3 Lastgänge/Einspeisegänge an LEG-Vertreter»
     (E66) und «1_13_3 Aggregate an den LEG-Vertreter» (E31).
   * Damit sind die meisten Punkte aus `docs/sdat_leg_import.md` §32 klärbar.
   * Open question: ob die VSE-Dateien in das öffentliche Repo dürfen (Lizenz).
     Sonst eigene Minimal-Fixtures nach deren Struktur.
6. **Zugangsdaten (§10 widerspricht dem bestehenden Konzept).**
   * Geheimnisse wie `mqtt_password` und `email_password` stehen heute in den
     Add-on-Optionen beziehungsweise in `RuntimeConfig`, nie in SQLite (AGENTS.md:
     `config.py` und SQLite sind strikt getrennt).
   * Eine Eingabe des Passworts in der Weboberfläche hiesse Klartext in
     `shareomat.db` und damit auch in jedem Backup.
   * Entscheidung nötig. Vorschlag: Benutzer und Passwort als Add-on-Optionen
     (`password?`) bzw. Umgebung im Standalone-Betrieb, wie bei der E-Mail.
     Aktivierung, Intervall, Test und Synchronisation in der Weboberfläche.
   * Das ändert Schritt 3 in §15.
7. **Rollen (§10).** Es gibt derzeit nur die Rolle `admin` (Rollen sind für
   später geplant). «Nur Administratoren» ist damit automatisch erfüllt.
8. **Scheduler und Shutdown (§8.3 ergänzen).**
   * Der bestehende Scheduler startet nur den Abrechnungslauf per Cron. Einen
     allgemeinen Job-Scheduler gibt es nicht.
   * Die Synchronisation braucht einen eigenen Thread mit eigener Sperre, getrennt
     von `_RUN_LOCK`, damit kein Download die Abrechnung blockiert.
   * Sie muss in `_ShutdownSequence` eingebunden werden. Ein Download, der beim
     Stoppen abbricht, darf nicht als erledigt gelten.
9. **Quittungen (fehlt im Auftrag).**
   * SDAT Anhang 1 kennt «Acknowledgement of Acceptance» und «Model Error Report».
   * Im SDAT-Webclient quittiert der Datahub automatisch (Auto-Acknowledge).
   * Needs verification bei EBL/Swisseldex: ob der CEM über FTPES selbst quittieren
     muss. Bis dahin werden keine Quittungen gesendet.
10. **TLS auf dem Datenkanal.**
    * Pythons `ftplib` verwendet die TLS-Sitzung des Steuerkanals auf dem
      Datenkanal nicht wieder. Viele FTPES-Server verlangen das, das Handbuch sagt
      dazu nichts.
    * Needs verification im ersten echten Test. Eine kleine Unterklasse mit
      Sitzungswiederverwendung ist einzuplanen.
    * Der Hinweis zu GnuTLS -110 ist richtig. Ein Schliessfehler nach vollständigem
      Download (Kontrolle mit `SIZE`) ist kein Downloadfehler.
11. **10 s Inaktivität.** Nur verbinden, auflisten, herunterladen und trennen. Das
    Parsen erfolgt danach offline, ohne offene Verbindung.
12. **Neue Abhängigkeiten.**
    * XSD-Prüfung geht nicht mit der Standardbibliothek. Es braucht `lxml` oder
      `xmlschema`.
    * Gegen XXE schützt `defusedxml`.
    * Beides fehlt in `requirements.txt` und vergrössert das Add-on-Image. Vorher
      prüfen, ob die Pakete für alle Add-on-Architekturen verfügbar sind.
13. **ADR nötig.** Energiearten in den Messwerten und ein Abrechnungsweg ohne
    `match_all()` für offiziell zugeteilte Daten berühren ADR-0002 (Normalisierung
    auf `IntervalReading`). Nach AGENTS.md braucht das ein neues ADR. Die Nummer
    ADR-0008 ist für LE-22 vorgesehen, also die nächste freie Nummer verwenden
    oder neu vergeben.
14. **Umfang und Ablauf.**
    * Der Auftrag verlangt alle Phasen A bis F ohne Halt. AGENTS.md und die
      Arbeitsliste verlangen kleine Schritte mit Pause nach jeder Aufgabe.
    * Vorschlag für eine eigene Spur ausserhalb der Live-Energie-Liste:
      * **SX-1:** FTPES, Empfangsarchiv, Test und manuelle Synchronisation, Log,
        ohne Parsen. Damit ist der Praxistest möglich.
      * **SX-2:** Parser und versionierte Speicherung nach den VSE-XSDs und
        -Beispielen, mit ADR.
      * **SX-3:** Abrechnungsweg für offizielle Daten, Freigabe, Korrekturhinweis.
    * Zeitrahmen: Die erste Lieferung kommt um den 07.12.2026. Spätestens
      30 Tage danach muss abgeholt sein, also SX-1 vorher produktiv. SX-2 und SX-3
      bis zur ersten Abrechnung aus SDAT. Bis dahin dient der EBL-Excel-Import als
      Rückfallweg.

---

# Entscheidungen vom 12.11.2026 (verbindliche Ergänzung)

Der Betreiber hat den Prüfbericht angenommen und Folgendes festgelegt.

1. **Zugangsdaten**
   * Benutzer und Passwort liegen in den Add-on-Optionen wie bei MQTT und E-Mail,
     im Standalone-Betrieb in der bestehenden Konfiguration (`config.yaml`).
   * Kein Passwort in `shareomat.db`, in Logs oder in Diagnoseausgaben.
   * Keine neue oder parallele Konfigurationsverwaltung.
   * Die Bedienfunktionen sind funktionale Anforderungen, kein Auftrag zum Umbau
     der Oberfläche. Ergänzt wird nur, was für Swisseldex fehlt, an einer
     bestehenden, fachlich passenden Stelle.
   * Keine Menüs umbauen, keine Seiten neu gestalten, keine globalen Änderungen
     an Layout oder Navigation. Würde ein grösserer Umbau nötig, zuerst einen
     minimalinvasiven Vorschlag zeigen.
2. **Drei Phasen, nacheinander, ohne erneute allgemeine Freigabe pro Phase**
   * **SX-1** Swisseldex FTPES: Verbindung, Listing, sicherer Download,
     Empfangsarchiv, Duplikaterkennung, automatische Synchronisation (standardmässig
     aus, Intervall 6 h), Verbindungstest, manuelle Synchronisation, Status,
     Diagnoseprotokoll, Fehlerbehandlung, Wiederanlauf.
     * Die Swisseldex-Besonderheiten aus dem Prüfbericht gelten.
     * Die 60-s-Login-Sperre wird zentral eingehalten. Eine Synchronisation direkt
       nach dem Test wird kontrolliert verzögert, nicht blockierend, mit
       verständlichem Status.
     * Eine Warnung erscheint, wenn die Synchronisation ausbleibt (30-Tage-Frist).
     * Beim ersten Test: kein Löschen auf Swisseldex, keine ausgehenden
       SDAT-Nachrichten, keine Quittungen, keine Abrechnungsaktionen.
     * FTPES-Empfang und SDAT-Verarbeitung bleiben getrennt. SX-1 braucht keine
       neue Abrechnungslogik.
   * **SX-2** SDAT-CH 2025: Parser nach den VSE-XSDs und -Beispielen (E31/E66 an
     den LEG-Vertreter).
     * Die bestehenden EIC- und CEM-Prüfungen werden wiederverwendet.
     * Gesamt-, LEG- und Restenergie werden additiv getrennt gespeichert.
     * CSV-/Excel-Importe und die bisherige Abrechnung bleiben unberührt.
     * Die falschen Dokumentverweise werden korrigiert.
   * **SX-3** Offizielle LEG-Abrechnung: zugeteilte LEG-Energie wird nicht
     nochmals verteilt. Zwei getrennte Wege (Rohdaten mit interner Verteilung,
     offizielle SDAT-Daten). Freigegebene Abrechnungen bleiben geschützt. Dazu
     ein ADR.
3. **Priorität**
   * SX-1 hat ab sofort Vorrang vor LE-23, LE-24, LE-25 und AUD-02. Danach folgen
     SX-2 und SX-3.
   * Die übrigen Aufgaben bleiben unverändert in der Planung. Nichts wird
     gelöscht, als erledigt markiert oder ersetzt.
   * Vor sicherheitskritischen oder potenziell destruktiven Änderungen an der
     produktiven Installation erfolgt eine gesonderte Prüfung.
4. **Bestandsschutz** (höchste Priorität): Daten, Funktionen und Oberfläche der
   laufenden Installation bleiben erhalten.
   * Nur additive Migrationen, zuerst auf einer Kopie geprüft.
   * Ein Backup vor produktiven Migrationen.
   * Keine Tests auf der produktiven Datenbank.
   * Ist Swisseldex deaktiviert, verhält sich Shareomat exakt wie bisher.
5. **Tests:** Ausgangsstand, gezielte Tests pro Phase, volle Regression. Neue
   Abhängigkeiten nur so weit nötig. Keine fremden Altlasten bereinigen.
6. **Quittungen:** Anhand der Swisseldex- und VSE-Unterlagen prüfen, was für den
   CEM vorgeschrieben ist. Ist das nicht eindeutig, wird der Punkt dokumentiert
   und eine Rückfrage an EBL bzw. Swisseldex formuliert. Keine Quittungen auf
   Verdacht. Der lesende Verbindungstest wird dadurch nicht blockiert.
7. **Praxistest:** Der 07.12.2026 ist relevant, eine Lieferung an diesem Tag ist
   aber nicht bestätigt. Ein Listing mit 0 Dateien ist ein gültiger erster Test.
   «Produktiv nachgewiesen» gilt erst mit echten EBL-Dateien, bis dahin wird mit
   den VSE-Beispielen getestet.
8. **Abschluss:** Nach SX-1 folgt eine kurze Übergabe. Keine automatischen
   Pushes. Eine neue Add-on-Version wird zuerst isoliert gebaut und getestet.

# Umsetzungsstand

| Phase | Stand |
|---|---|
| SX-1 | UMGESETZT, simuliert getestet. TEST AUSSTEHEND gegen den echten Datahub |
| SX-2 | UMGESETZT, gegen VSE-Beispiele und synthetische Dokumente getestet. Mit echten EBL-Dateien nicht verifiziert |
| SX-3 | UMGESETZT, getestet, ADR-0008 vorgeschlagen. Mit echten EBL-Dateien nicht verifiziert |

Kein Commit und kein Push vor dem Audit durch den Betreiber (Vorgabe vom 12.11.2026).

## SX-2: Umsetzung

* **Parser** `shareomat/core/pipeline/raw/sdat_ch.py`: E66 und E31 nach den VSE-XSDs und
  -Beispielen.
  * Geprüft werden Struktur, Codes, Positionen, Einheiten, Nachkommastellen, Intervalle und
    Sommer-/Winterzeit.
  * DTD und Entities werden abgewiesen (kein XXE). Die Grösse ist begrenzt.
  * Ein Dokument kann mehrere Gemeinschaften enthalten.
  * Die bestehenden EIC- und CEM-Prüfungen werden wiederverwendet.
* **Speicher** `shareomat/database/sdat_store.py`: neue Tabellen `sdat_documents` und
  `sdat_series` (Schema 17, additiv). Gesamt-, LEG- und Restenergie werden getrennt gehalten.
  `meter_readings` bleibt unberührt.
* **Backup vor Migration:** Bevor das Schema einer bestehenden Datenbank geändert wird,
  entsteht eine geprüfte Kopie (`shareomat.before-schema-17-from-16-<Zeit>.db` neben der
  Datenbank). Ist die Kopie nicht prüfbar, wird nicht migriert.
* **Import** `shareomat/core/collector/sdat_import.py`: Der Import prüft:
  * Empfänger: eigene EIC, Rolle CEM, C40.
  * CommunityID: abgeleitet aus den eigenen Messpunkten. Ist sie einmal festgelegt, ist eine
    Abweichung ein Fehler.
  * Unbekannte Messpunkte sperren die ganze Datei.
  * Eine E31 wartet auf die erste E66.
  * Abgewiesene Dateien werden erneut geprüft, ein Import wird nie doppelt ausgeführt.
* **Automatik:** Nach jeder erfolgreichen Synchronisation folgt der Import. Auf der Karte gibt
  es zusätzlich den Button «Empfangene Dateien erneut prüfen und importieren».
* **Anzeige:** Import-Status pro Datei samt Grund, CommunityID und welche Daten die
  Abrechnung verwendet.
* **Quittung:** Verlangt der Absender eine Quittung (`isIntelligibleCheckRequired`), wird das
  angezeigt. Gesendet wird keine.

## SX-3: Umsetzung

Siehe ADR-0008.

* Mit «Messdaten-Quelle: SDAT (EBL/Swisseldex)» übernimmt die Abrechnung die
  LEG-/Restenergie des Netzbetreibers unverändert und verteilt nichts neu
  (`shareomat/core/pipeline/official_allocation.py`).
* Mit der Standardquelle bleibt alles wie bisher (Test: gleicher Fingerabdruck vor und nach
  einem SDAT-Import).
* Sperren:
  * fehlende Teile,
  * Total ≠ LEG-Energie + Restenergie,
  * vorläufige Werte,
  * LEG-Energie auf nicht abrechnungsberechtigten Messpunkten.
* Hinweise: Ersatzwerte und die E31-Kontrolle.
* Freigegebene Abrechnungen bleiben unverändert. Neue Daten für einen freigegebenen Zeitraum
  erzeugen den Hinweis «Korrekturabrechnung prüfen».
* Neue Daten zwischen Entwurf und Freigabe verhindern die Freigabe.

## SX-1: Umsetzung

* Neu sind `shareomat/core/collector/swisseldex_ftpes.py` (FTPES-Client) und
  `shareomat/core/collector/swisseldex_receiver.py` (Empfang, Archiv, Login-Sperre,
  Protokoll, Automatik, Warnung).
* Ergänzt wurden:
  * `SwisseldexConfig` in `shareomat/config.py`,
  * Start und geordnetes Stoppen in `main.py` (nur wenn aktiviert),
  * zwei Methoden in `shareomat/web/state.py`,
  * eine Karte und zwei Aktionen auf der bestehenden Seite «Externe Daten».
* Add-on: Optionsabschnitt `swisseldex` in `ha_addon/config.yaml`, Übersetzungen,
  `ha_addon/generate_runtime_config.py`. Standalone: `config/leg_config.example.yaml`.
* Keine Datenbankmigration, keine neue Tabelle, kein Schreibzugriff auf
  `shareomat.db`.
* Keine neue Abhängigkeit, verwendet werden nur `ftplib`, `ssl` und `gzip` aus der
  Standardbibliothek.
* Menüs, Navigation und die übrigen Seiten bleiben unverändert.
* Einzelheiten stehen in `docs/sdat_leg_import.md` §16 unter «Umsetzung des
  Empfangs».

## Quittungen: Ergebnis der Prüfung

* SDAT-CH 2025 Anhang 1 (Stand 04.12.2025), §2.1:
  * Ein «Acknowledgement of Acceptance» wird gesendet, wenn die Nachricht
    interpretiert werden kann **und der Sender es in der Nachricht verlangt**.
  * Ein «Model Error Report» wird im Fehlerfall **immer** gesendet.
  * Frist: unverzüglich, spätestens 1 Arbeitstag nach Eingang (Tabelle 2).
* Bei beschädigten oder schemawidrigen Dateien wird der Absender per E-Mail oder
  Telefon informiert (§3 Abs. 5 und 6).
* Swisseldex-Handbuch: Im SDAT-Webclient quittiert der Datahub automatisch
  («Auto-Acknowledge»). Für das FTPES-Postfach ist das nicht beschrieben.
* Folgerung:
  * Offen ist, ob EBL bei E31/E66 an den LEG-Vertreter eine Quittung verlangt und
    ob der Datahub beim FTPES-Postfach stellvertretend quittiert.
  * In SX-1 werden keine Quittungen gesendet.
  * SX-2 erkennt beim Parsen, ob eine Nachricht eine Quittung verlangt, und zeigt
    das an.
  * Vor dem produktiven Betrieb muss die Antwort auf die Rückfrage vorliegen.

## Rückfrage an EBL bzw. Swisseldex (Entwurf)

> Betreff: SDAT E31/E66 an LEG-Vertreter (CEM) – Quittierung und Postfach
>
> Guten Tag
>
> Wir empfangen als LEG-Vertreter (Rolle CEM, EIC <EIC des LEG-Vertreters>)
> SDAT-Nachrichten E31/E66 über das FTPES-Postfach «Server beim Datahub».
> Gemäss SDAT-CH 2025 Anhang 1 wird ein «Acknowledgement of Acceptance» gesendet,
> wenn der Sender es verlangt, und ein «Model Error Report» im Fehlerfall.
>
> 1. Verlangen Sie bei E31/E66 an den LEG-Vertreter ein «Acknowledgement of
>    Acceptance»?
> 2. Erwarten Sie im Fehlerfall einen «Model Error Report» über den Datahub, oder
>    genügt eine Mitteilung per E-Mail?
> 3. Quittiert der Datahub stellvertretend, wenn die Nachricht aus dem
>    FTPES-Postfach abgeholt wird (wie das Auto-Acknowledge im SDAT-Webclient)?
> 4. Werden bereits heruntergeladene Dateien im Postfach ebenfalls nach 30 Tagen
>    gelöscht, oder nur nicht abgeholte?
>
> Freundliche Grüsse
