# ADR-0012: Architekturumbau – mehrere LEGs, eigenständiger Betrieb, Aufteilung der Daten

## Status

Angenommen (27.11.2026). Grundlage sind die Entscheide des Betreibers E1–E17 vom 27.11.2026.
Die Umsetzung läuft in Schritten; jeder Schritt wird als eigene Version mit Tests geliefert.

Verhältnis zu anderen ADRs:

* **ADR-0008:** teilweise ersetzt. SDAT ist der einzige Abrechnungsweg, der CSV-Weg entfällt.
* **ADR-0009:** ergänzt um die Messpunkt-Zuordnung mit Zeitraum.
* **ADR-0007:** ergänzt. Dessen offene Frage «eine LEG pro Installation oder mehrere?» ist
  beantwortet: mehrere.
* **AGENTS.md:** bewusste Ausnahme von «Schema-Änderungen sind additiv» und «nichts
  umbenennen», wie bei ADR-0009 Punkt 7.

Das ausführliche Arbeitsdokument (Ist-Zustand, Befunde B1–B28, Messungen, Plan) liegt beim
Betreiber und ist nicht Teil des Repos. Dieser ADR hält die Entscheide verbindlich fest.

## Kontext

Schema 18 trägt den Betrieb einer LEG, aber nicht das, was kommt: mehrere LEGs mit zusammen
rund 1000 Teilnehmern, Betrieb ohne Home Assistant und mehrere Jahre Viertelstundendaten.
Belegt sind:

* **Eine LEG fest eingebaut:**
  * `get_community` und `ensure_community_row_id` werden in 25 Modulen aufgerufen.
  * 178 von 200 SQL-Anweisungen auf LEG-Tabellen haben keinen LEG-Bezug.
  * Die technische Konfiguration gibt es nur einmal: ein Swisseldex-Konto, ein SMTP-Absender,
    ein MQTT-Präfix.
* **Verweise über änderbare Texte:**
  * Abrechnung, Belege, Mitteilungen und Protokoll verweisen über die Teilnehmerkennung als
    Text; die Messdaten über die Messpunkt-ID. Beide sind im Formular änderbar.
  * Gleiche Spaltennamen haben verschiedene Bedeutungen: `participant_id` ist mal eine Zahl,
    mal eine Kennung.
* **Zuordnung ohne Zeitraum:** Die Zuordnung Messpunkt → Teilnehmer hat kein Datum. Ein
  Mieterwechsel lässt sich nicht abbilden.
* **Der LEG-Preis steht dreifach:** in der Vertragsversion, in `tariffs` und in
  `participant_contract`. Er lässt sich am Vertrag vorbei ändern (Einrichtungsassistent,
  Tarif-Formulare).
* **Messwert-Kopie:** `meter_readings` kopiert jede Viertelstunde. Gemessen mit 1000
  Teilnehmern: etwa 1 GB pro abgerechnetem Monat, rund die Hälfte der Zeit einer
  Abrechnungsvorschau.
* **Freigabesperre:** Sie bricht bei jedem Schreibzugriff auf die Datenbank ab, auch bei einer
  Login-Sitzung oder einer Änderung in einer anderen LEG.
* **Geheimnis in der Datenbank:** Der ENTSO-E-Token liegt in `settings` und damit in jeder
  Sicherung.
* **Abhängigkeit von Home Assistant:** Konfiguration, Ingress-Vertrauen und Pfade unter
  `/share` setzen HA voraus.

Das Zeitfenster ist günstig: In der produktiven LEG gibt es noch keine freigegebene Abrechnung
und keinen Beleg.

## Entscheidung

1. **Mehrere unabhängige LEGs pro Installation.** Getrennt wird durchgängig: Datenbank,
   Geschäftslogik, Rechte, Hintergrundprozesse und Schnittstellen.
   * Jede Funktion bekommt die LEG ausdrücklich übergeben.
   * Abfragen über eine Zeilen-ID prüfen die LEG mit.
   * Zusammengesetzte Fremdschlüssel `(community_id, id)` verhindern Zuordnungen über LEGs
     hinweg schon in der Datenbank.
   * Jede LEG hat eine von Shareomat erzeugte, unveränderliche und nie wiederverwendete
     Kennung (z. B. `leg-0001`), einen frei änderbaren Anzeigenamen und als eigenes Feld die
     CommunityID des Netzbetreibers.
   * Die gewählte LEG steht in der Adresse (`/leg/<kennung>/…`), mit einer Auswahlliste oben.
   * Rollen: Installations-Admin, dazu pro LEG «LEG-Verwaltung», «Abrechnung» und «Nur
     lesen». Export, Bankverbindung und Zugangsdaten haben eigene Berechtigungen.
   * Teilnehmerkennungen sind nur innerhalb einer LEG eindeutig.
   * Externe Daten wie Börsenpreise und Referenzpreise gibt es nur einmal pro Installation.
2. **Der Preis entsteht nur im Vertrag.** Die Abrechnung liest den Preis der Vertragsversion,
   die am jeweiligen Tag gilt.
   * `tariffs` ist keine Preisquelle mehr. Kein Weg führt am Vertrag vorbei.
   * Jeder Lauf hält fest, welcher Preis in welchem Abschnitt galt (`billing_run_prices`).
   * Netzbetreiber-Tarife bleiben als Vergleich.
3. **Messpunkt-Zuordnung mit Zeitraum** (`meter_assignments`: Messpunkt, Mitgliedschaft, von,
   bis, Grund).
   * Teilnehmer-, Mieter-, Eigentümer- und Zählerwechsel werden ohne Überschneidung
     abgebildet.
   * Zeiträume, die in einer freigegebenen Abrechnung stecken, sind gesperrt.
   * `participants.active` und die Gültigkeit der Teilnehmer entfallen zugunsten der
     Mitgliedschaft.
4. **Daten nach Art getrennt:**

   | Datei | Inhalt | Sicherung |
   |---|---|---|
   | `shareomat.db` | Stammdaten, Verträge, Abrechnungen, Belege, Benutzer, Einstellungen ohne Geheimnisse, Audit | ja |
   | `metering.db` | SDAT-Dokumente und -Reihen wie geliefert | ja |
   | `coordinator.db` | Prognosen und Day-Ahead-Preise für Emsomat | nein, neu berechenbar |
   | `live.db` | flüchtige Live-Werte | nein |

   * SQLite prüft keine Fremdschlüssel über Dateien. Eine Referenzprüfung übernimmt das beim
     Import, bei der Freigabe, nach der Wiederherstellung und beim Start.
   * `metering.db` speichert nur die gelieferten Kennungen; die Abrechnung verbindet sie
     über LEG und Messpunkt.
   * `meter_readings` entfällt.
   * Die Rohdatenablage nach ADR-0010 bleibt unverändert. Keine Migration und keine
     Bereinigung berührt sie.
5. **Interne IDs und Kennungen getrennt.**
   * Verweise laufen immer über unveränderliche Zahlen-IDs.
   * Teilnehmerkennung und Messpunkt-ID sind Inhalt (`participant_code`, `mpid`).
   * Kopien in Belegen heissen ausdrücklich `*_snapshot` bzw. `*_label`.
   * Gleichnamige Spalten werden dafür umbenannt.
6. **Exakte Werte.**
   * Geld wird exakt gerechnet und einmal auf den Rappen gerundet, wie seit 0.6.22.
   * Energie bleibt auch auf dem Weg durch den Rechenkern exakt (kein `float`); die
     Rechenregeln ändern sich nicht.
   * Gelieferte Werte bleiben als gelieferter Text erhalten.
   * Gespeichert wird in UTC. Abrechnungstage sind Kalendertage in Europe/Zurich mit 92, 96
     oder 100 Viertelstunden.
7. **Freigabesperre pro LEG.**
   * Ein Revisionszähler steigt bei abrechnungsrelevanten Änderungen der LEG, ein zweiter mit
     jeder gespeicherten SDAT-Lieferung.
   * Die Freigabe vergleicht beide statt `PRAGMA data_version`.
   * Login-Sitzungen, andere LEGs und Prognosen lösen keinen Abbruch mehr aus.
8. **SDAT ist der einzige Abrechnungsweg; der CSV-Weg entfällt vorerst.**
   * Es entfallen:
     * der automatische Lauf (`leg_runner`) mit seinen Berichten;
     * der Datei-Import über IMAP, Freigabeordner und Eingang;
     * `settlement_cycle_records`;
     * die Einstellung «Messdaten-Quelle»;
     * die MQTT-Sensoren, die nur dieser Lauf füllte. Sie werden in HA sauber abgemeldet.
   * Ein späterer Importadapter bleibt möglich (ADR-0002, ADR-0003).
   * An die Stelle der Berichte tritt ein Datenexport: Excel-Datei (`.xlsx`) und technisches
     CSV. Er umfasst Ergebnisse je Teilnehmer und Messpunkt, Tages- und Viertelstundenwerte;
     bei freigegebenen Läufen eingefroren und ohne neue Berechnung.
9. **Emsomat und MQTT bleiben, wie sie sind.** Neu ist nur: Jede Verbindung gehört zu genau
   einer LEG.
   * Getrennt wird über die Verbindung: eigenes Themen-Präfix bei der HA-Anzeige, eigene
     Sparkplug-Gruppe und eigener Node bei Emsomat.
   * Die bestehende Verbindung wird unverändert übernommen: gleiche Themen, `unique_id`s und
     Gruppe. Emsomat muss nicht angepasst werden.
   * Prognose und Preise an Emsomat bekommen einen eigenen Takt, alle 15 Minuten und sofort,
     wenn Emsomat online kommt; bisher hingen sie am CSV-Lauf.
   * Live-Werte bleiben ereignisgesteuert.
   * Die Prognose rechnet aus `metering.db`.
10. **Zugangsdaten nie in der Datenbank.**
    * Eine gemeinsame Konfigurationsschnittstelle liest in dieser Rangfolge: Standard,
      Konfigurationsdatei, Geheimnisdatei (nur für Shareomat lesbar), Umgebungsvariablen. Das
      HA-Add-on erzeugt die Datei wie heute aus den Optionen.
    * Profile mit Namen gibt es für Swisseldex (eines pro LEG), SMTP (pro LEG, optional ein
      Standard) und MQTT.
    * Einrichtung auch über die Weboberfläche:
      * Geheimnisse sind dort nie sichtbar.
      * Jede Änderung verlangt das Passwort und steht im Audit-Protokoll, ohne Wert.
    * Geheimnisse erscheinen nie in Sicherung, Protokoll oder Export. Für den Umzug auf einen
      neuen Rechner gibt es eine eigene, mit Passphrase verschlüsselte Datei «Zugangsdaten
      sichern».
11. **Eigenständiger Betrieb.** Es ist dieselbe Anwendung mit derselben Oberfläche, über HA-Ingress
    oder einen eigenen Port.
    * Betriebsarten:
      * HA-Add-on;
      * eigenständig lokal: startet und funktioniert vollständig ohne HA und ohne Proxy;
      * Serverbetrieb: HTTPS über einen Reverse Proxy, kein eigener TLS-Code.
    * HA-Funktionen sind Adapter: Optionen, Ingress-Vertrauen, HA-Anzeige über MQTT.
    * Bind-Adresse und Pfade wie die Rohdatenablage sind konfigurierbar.
    * Anmeldung über unverschlüsseltes HTTP nur lokal oder über Ingress.
    * Das Teilnehmerportal bleibt getrennt (ADR-0007).
12. **Login:**
    * Sitzungen enden nach 30 Minuten ohne Nutzung, spätestens nach 12 Stunden.
    * «Angemeldet bleiben» gilt höchstens 30 Tage und endet nach 7 Tagen ohne Nutzung; für
      öffentliche Server abschaltbar.
    * Abgelaufene Sitzungen werden nach 30 Tagen gelöscht, Login-Versuche nach 90 Tagen.
    * Das Audit-Protokoll bleibt getrennt und wird nie gelöscht.
13. **Automatisierung je LEG.** Die Schalter für automatische Abrechnung, Rechnungen und
    Versand bleiben, ausgegraut bis zu einem eigenen Konzept.
14. **Belege bleiben in der Datenbank** (eingefrorenes HTML, Hashes). Eine spätere Auslagerung
    bleibt möglich.
15. **Sicherung und Wiederherstellung.**
    * Eine Sicherung umfasst `shareomat.db`, `metering.db`, die Dokumente und die Originale
      ohne geprüfte externe Kopie.
    * Gesichert wird im Betrieb mit der Backup-API: zuerst `shareomat.db`, dann `metering.db`.
      Weil `metering.db` nur wächst, enthält sie alles, worauf `shareomat.db` verweist.
    * Jede Sicherung hat ein Manifest und besteht eine Wiederherstellungsprobe. Die
      Wiederherstellung ohne HA gibt es seit 0.6.32.
16. **Migration in einer Version, die sich selbst prüft** (vereinfacht am 27.11.2026):
    * zuerst Sicherung mit Probe;
    * Migration auf einer Kopie, Vergleich jedes Werts, Prüfbericht ohne Personendaten;
    * Umschalten nur, wenn alles stimmt; sonst Wartungsmodus mit unveränderten Daten.
    * Freigegebene Läufe und Belege werden Feld für Feld unverändert übernommen (ADR-0001).
    * Dass der neue Code richtig rechnet, wird mit der eingefrorenen synthetischen
      Testdatenbank bewiesen (`tests/fixtures/schema18_synthetic/`). Produktive Daten werden
      in der Entwicklung nicht verwendet.
17. **SQLite bleibt.** Die Geschäftslogik bleibt von den Datenbankzugriffen getrennt.
    * Zielwerte bei 1000 Teilnehmern:
      * Monatsvorschau unter 2 Minuten;
      * Sicherung unter 5 Minuten;
      * keine unbehandelten Sperrfehler;
      * Verwaltungsseiten unter 1 Sekunde.
    * Geprüft auch mit einer einzigen LEG mit 1000 Teilnehmern.

## Begründung

* **Gemessen statt angenommen.** Mit 1000 synthetischen Teilnehmern und 24 Monaten SDAT
  (`tests/synthetic/load.py`), auf dem Entwicklungsrechner:
  * Die Sicherung der 3-GB-Datei dauert 33 s, 1000 Belege 14 s.
  * Die Monatsvorschau braucht 6 bis 7,5 Minuten.
  * Davon entfallen rund 50 % auf die Messwert-Kopie und rund 70 % des Rests auf das
    viermalige Lesen derselben Werte.
  * SQLite-Abfragen machen weniger als 2 % aus.

  Ein Wechsel der Datenbank würde das Problem nicht lösen. Messwert-Kopie und Datenfluss zu
  ändern, löst es.
* **Ein Preis, eine Zuordnung, ein Schlüssel.** Doppelte Wahrheiten und änderbare Kennungen
  als Verweis sind die Ursache mehrerer Befunde (B1, B3, B8, B10). Sie lassen sich nur jetzt
  billig beheben, vor der ersten freigegebenen Abrechnung.
* **Verworfene Alternativen:**
  * Additive Einzelkorrekturen: Die Doppelungen blieben bestehen.
  * Ein Wechsel auf PostgreSQL: zusätzlicher Betrieb ohne gemessenen Bedarf.
  * Eine eigene Themenstruktur für MQTT: hätte Emsomat geändert.
  * Eine Vorbereitungsversion mit Vergleichswerten aus dem alten Code: ohne freigegebene
    Abrechnung kein Nutzen; freigegebene Läufe werden ohnehin feldgenau übernommen.

## Konsequenzen

* Grösserer Umbau über alle Schichten, in Schritten geliefert:
  * Konfiguration und Geheimnisse vor dem Schema;
  * Schema 19;
  * `metering.db`;
  * Sicherung;
  * danach Oberfläche, Emsomat/MQTT je LEG, Export, eigenständiger Betrieb.
* Zwischen zwei Versionen fällt keine Funktion aus; was entfernt wird, hat vorher Ersatz oder
  ist ausdrücklich entschieden.
* Tests, die heute über den CSV-Weg laufen, werden auf den SDAT-Weg umgestellt.
* Bestehende HA-Entitäten und die Emsomat-Verbindung bleiben unverändert. Sensoren, die nur
  der CSV-Lauf füllte, verschwinden aus HA.
* Ein Rückweg auf Schema 18 geht nur mit der Sicherung vor der Migration.

## Betroffene Dateien

* **Schema und Migration:** `shareomat/database/sqlite.py`, neue Migration Schema 19, neue
  Datei `metering.db`.
* **Datenbankmodule:** alle unter `shareomat/database/` (LEG-Kontext, neue Tabellen).
* **Rechenkern:** `shareomat/core/pipeline/leg_billing.py`, `official_allocation.py`
  (exakte Energie).
* **CSV-Weg (entfällt):** `shareomat/core/leg_runner.py`, `core/collector/leg_*`,
  `core/report/`.
* **Oberfläche:** `shareomat/web/server.py`, `web/login.py`, `security/permissions.py`, alle
  Seiten.
* **Konfiguration und Start:** `shareomat/config.py`, `main.py`,
  `ha_addon/generate_runtime_config.py`.
* **Schnittstellen:** `shareomat/ha/*`, `shareomat/sparkplug/*`, `shareomat/live/*`.

## Überprüfung

* Nach der Messung auf dem HA-Host: Werden die Zielwerte aus Punkt 17 nicht erreicht, werden
  Datenfluss und Speicherform in `metering.db` neu bewertet, erst danach die
  Datenbanktechnologie.
* Wenn es andere Betreiber gibt: ob die Migration zusätzlich eine eingefrorene Kopie des alten
  Rechencodes braucht, damit niemand eine Version überspringen muss.
