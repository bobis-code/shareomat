# ADR-0008: Offizielle SDAT-Zuteilung als zweiter Abrechnungsweg

## Status

Vorgeschlagen (12.11.2026, Swisseldex-Auftrag SX-3). Wird «Angenommen» nach dem Audit
durch den Betreiber.

## Kontext

* Shareomat verteilt lokal erzeugte Energie selbst proportional (`leg_matcher.match_all`,
  Art. 19g StromVV) auf Grund von Rohmessdaten, die als CSV oder EBL-Excel kommen.
* Seit dem LEG-Start liefert EBL über den Swisseldex Datahub die offiziellen SDAT-CH-2025-
  Nachrichten an den LEG-Vertreter (Rolle CEM, Business Reason C40, VSE-Prozesse 1.12/1.13):
  * **E66:** pro Messpunkt drei Flüsse, nämlich Total, LEG-Energie und Restenergie, getrennt
    nach Verbrauch (LGZ) und Einspeisung (EGZ).
  * **E31:** dieselben Flüsse als Summen der LEG.
* Die Zuteilung des Netzbetreibers ist für die Netzrechnung der Mitglieder massgebend.

## Problem

* Würden die offiziellen Daten wie bisherige Rohdaten behandelt, verteilte Shareomat die schon
  zugeteilte Energie ein zweites Mal. Das Ergebnis wiche von der Netzrechnung ab.
* `IntervalReading` kennt keine Energieart, und `meter_readings` hat den Schlüssel
  `UNIQUE(meter_id, slot_start, direction)`. Total, LEG- und Restenergie würden sich dort
  gegenseitig überschreiben.
* ADR-0002 verlangt, dass jedes Rohformat auf `IntervalReading` normalisiert wird. Für die
  Zuteilung selbst passt das nicht.

## Entscheidung

1. **Zwei getrennte Wege**, gewählt über die bestehende Einstellung «Messdaten-Quelle»
   (`OperationSettings.meter_data_source`):
   * `email_csv` (Standard): wie bisher, Rohdaten und interne proportionale Verteilung.
     Unverändert.
   * `sdat_leg`: Die offiziellen E66-Werte werden übernommen und nicht neu verteilt.
     `core/pipeline/official_allocation.py` füllt dieselben `MatchResult`-Felder, die
     `compute_billing` liest:
     * LEG-Energie Verbrauch → `meter_local_received`
     * Restenergie Verbrauch → `meter_grid_import`
     * LEG-Energie Einspeisung → `meter_local_supplied`
     * Restenergie Einspeisung → `meter_grid_export`

     Tarife, HT/NT, Gutschriften und Rechnungen bleiben unverändert.
2. **Additive Speicherung** der offiziellen Zeitreihen in eigenen Tabellen `sdat_documents`
   und `sdat_series` (Schema 17). `meter_readings` und alle bestehenden Tabellen bleiben
   unberührt.
   * Ergänzung zu ADR-0002: Die gemessenen Totale werden weiterhin auf `IntervalReading`
     normalisiert, damit die bestehenden Prüfungen für Berechtigung und Vollständigkeit greifen.
   * Nur die Zuteilung wird als eigenes Modell gehalten.
3. **Versionen:** Jede Lieferung bleibt erhalten. Welcher Wert pro Viertelstunde gilt:
   * die neuere Lieferung (Header «Creation»),
   * ausser ein vorläufiger Wert (Qualität 21) würde einen plausibilisierten ersetzen,
   * eine Stornierung (Status 1) entfernt den Wert.
4. **Harte Sperren** (keine Freigabe möglich):
   * keine importierten Daten,
   * fehlende LEG- oder Restenergie zu einem Total,
   * Total ≠ LEG-Energie + Restenergie,
   * vorläufige Werte (21),
   * LEG-Energie des Netzbetreibers auf Messpunkten, die Shareomat an diesem Tag nicht
     abrechnet,
   * die bestehenden Sperren (Clearing, unbekannte Messpunkte usw.).
5. **Hinweise** (mit Begründung freigebbar):
   * Ersatzwerte (56),
   * verworfene spätere vorläufige Werte,
   * fehlende oder abweichende E31-Summen. E31 ist nur eine Kontrolle.
6. **Freigegebene Abrechnungen bleiben unverändert** (ADR-0001).
   * Neue offizielle Daten für einen freigegebenen Zeitraum führen nur zu einem Hinweis
     «Korrekturabrechnung prüfen». Dafür rechnet Shareomat neu und vergleicht den
     Ergebnis-Fingerabdruck.
   * Neue Daten zwischen Entwurf und Freigabe verhindern die Freigabe, wie bisher bei
     geänderten Messdateien.
7. **Getrennte Vorgänge:** Empfang (SX-1), Import (SX-2) und Abrechnung/Freigabe sind
   getrennt. Ein Import erstellt nie eine Abrechnung.

## Begründung

* Die Zuteilung des Netzbetreibers ist verbindlich. Eine eigene Neuberechnung kann nur
  abweichen.
* Die Wiederverwendung der `MatchResult`-Schnittstelle hält die Abrechnungslogik unverändert.
  Es entsteht kein paralleler Rechenweg für Beträge, nur eine andere Quelle für die vier
  Mengen.
* Verworfen:
  * Die Zuteilung in `meter_readings` mit neuer Spalte abzulegen. Das ändert Schlüssel und
    Bedeutung einer bestehenden Tabelle (AGENTS.md: keine stillschweigende Schemaänderung)
    und gefährdet CSV/Excel.
  * Offizielle Daten nur als Kontrolle neben der eigenen Verteilung zu führen. Dann wäre die
    Rechnung weiterhin nicht die des Netzbetreibers.
* Die Vergleichsregel «neuere Lieferung gilt, vorläufig ersetzt nie plausibilisiert» folgt
  der VSE-Prozessbeschreibung: täglich nicht plausibilisiert für 5 Tage, plausibilisiert bis
  zum 5. Arbeitstag, «Erreichen einer höheren Datenqualität».

## Konsequenzen

* Mit `sdat_leg` rechnet Shareomat nur, wenn E66-Daten importiert sind. Dateien in Inbox und
  Archiv werden für die Abrechnung dann nicht verwendet.
* Wer an einem Tag nicht abrechnungsberechtigt ist (Vertrag, Beitritt), aber vom
  Netzbetreiber LEG-Energie erhalten hat, sperrt die Abrechnung. Das ist gewollt: Stammdaten
  und Netzbetreiber müssen übereinstimmen.
* Die Tabellen wachsen mit jeder Tageslieferung (etwa 6 Zeitreihen × Mitglieder × 5 Tage pro
  Tag). Ein Aufräumen alter, ersetzter Lieferungen ist bei Bedarf eine spätere, eigene
  Entscheidung.
* Needs verification mit der ersten echten EBL-Lieferung:
  * wie eine Stornierung ihre Zeitreihen bezeichnet,
  * ob Total = LEG-Energie + Restenergie in den echten Daten exakt gilt (in den
    VSE-Beispielen gilt es),
  * die Quittungspflicht (siehe `docs/development/Swisseldex-SDAT-Auftrag_2026-11-12.md`).

## Betroffene Dateien

* `shareomat/core/pipeline/raw/sdat_ch.py` (Parser E66/E31)
* `shareomat/core/collector/sdat_import.py` (Import)
* `shareomat/database/sdat_store.py`, `shareomat/database/sqlite.py` (Schema 17, Backup vor
  Migration)
* `shareomat/core/pipeline/official_allocation.py`
* `shareomat/database/billing.py` (Weg 2, Sperren, Korrekturhinweis)
* `main.py` (Verdrahtung Import und Hinweis)

## Überprüfung

* Nach der ersten echten E66/E31-Lieferung von EBL.
* Wenn ein weiterer Netzbetreiber offizielle LEG-Daten liefert.
* Wenn die Speichergrösse der Lieferungen relevant wird.
