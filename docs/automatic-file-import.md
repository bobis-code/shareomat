# Automatische Dateiauswertung: überlappende Intervallmessungen

Die folgende Regel gilt ausschliesslich für einen automatischen Dateilauf in
`shareomat/core/leg_runner.py`. Sie ändert weder MQTT-Livedaten noch das
Sparkplug-Relay, die Bedarfsprognose oder die interaktive LEG-Abrechnung.

## Fachliche Messwertidentität

`IntervalReading` ist laut `shareomat/models/meter_data.py` ein vollständiger
Energiewert eines Messpunkts und einer Richtung über ein Viertelstundenintervall.
Rohformat-Parser müssen zuerst auf dieses Modell normalisieren (ADR-0002).
Der Schlüssel innerhalb des Dateilaufs lautet deshalb:

`(meter_id, slot_start, direction)`

Zeitangaben mit verschiedenen UTC-Offsets können denselben Zeitpunkt bezeichnen.
Der Dateiname ist ein Herkunftsnachweis, kein zusätzlicher physischer Messkanal.
Unterschiedliche Messpunkte und Import-/Exportrichtungen bleiben getrennt und
werden durch die nachgelagerte, unveränderte Slot-/Matching-Logik verarbeitet.

Additive Teilmessungen mit demselben Schlüssel sind im vorhandenen Modell nicht
unterscheidbar: Es fehlen beispielsweise Teilintervall, Kanal und Quellenrolle.
Solche Daten sind **keine unterstützten vollständigen `IntervalReading`-Werte**.
Sie müssen vor dieser Grenze fachlich korrekt normalisiert werden. Falls dafür
eine verlustfreie Modellierung fehlt, ist zuerst eine explizite Modellerweiterung
nach ADR-0002 erforderlich. Der Runner errät keine Quellenpriorität und verändert
`readings_to_slots()` nicht pauschal.

## Wiederholungen und Konflikte

Nach dem Einlesen und Filtern unbekannter/inaktiver Messpunkte prüft der Runner
die noch nicht verarbeiteten Dateien als gemeinsamen Stapel:

* Gleicher Schlüssel, exakt gleicher normalisierter Zahlenwert und gleiche
  Qualität: einmal berücksichtigen. Alle beteiligten Dateien bleiben in den
  Herkunftsnachweisen des erfolgreichen Laufs erhalten.
* Gleicher Schlüssel, anderer Zahlenwert oder andere Qualität: gesamten Lauf
  abbrechen. Die Diagnose nennt Messpunkt, Zeitpunkt, Richtung, Werte/Qualitäten
  und beide Quelldateien. Es gibt keine Auswahl nach Dateiname oder Änderungsdatum
  und keine neue Rundungs-/Toleranzregel für dateiübergreifende Konflikte.
* Vom CSV-Parser gemeldete abgewiesene Zeilen oder Parser-Ausnahmen: ebenfalls
  abbrechen. Das ist erforderlich, weil der bestehende CSV-Parser eine
  widersprüchliche Doppelzeile melden und trotzdem gültige Teilzeilen liefern kann.
  Die bestehenden Parser selbst und ihre internen Regeln werden nicht verändert.

Bei diesen Fehlern werden keine Rohmesswerte, automatischen Snapshots oder
Berichte geschrieben, keine Dateien als verarbeitet markiert oder archiviert
und keine neuen MQTT-Auswertungswerte veröffentlicht. Bestehende Ergebnisse und
bereits erfolgreich verarbeitete Dateien werden nicht verändert. Die vorhandene
Aufrufschicht in `main.py` kann weiterhin einen Fehlerstatus melden; bisherige
retained MQTT-Werte werden durch diesen Fix nicht gelöscht.

Die Quelldateien bleiben in der Inbox. Nach Klärung/Korrektur beziehungsweise
Entfernung einer widersprüchlichen Eingabedatei kann derselbe Lauf erneut
gestartet werden; ein Zurücksetzen des Verarbeitungszustands ist nicht nötig.

Die Zusage betrifft Fehler beim Einlesen und bei der Überlappungsprüfung, nicht
eine neue atomare Transaktion über sämtliche späteren Datenbank- und
Dateisystemoperationen. Parser, die fehlerhafte Beobachtungen intern ohne
Fehlerrückgabe verwerfen, erhalten durch diesen Fix keinen zusätzlichen
Vollständigkeitsnachweis.

## Abgrenzung der Datenwege

Der Runner schreibt Rohwerte in `meter_readings` und separat berechnete Ergebnisse
in `settlement_cycle_records`, Berichte und MQTT-Auswertungsthemen. Die Prognose
liest `meter_readings`, dessen Upsert gleiche Schlüssel überschreibt statt zu
addieren. Interaktives Billing liest Inbox und Archiv eigenständig erneut ein
und besitzt eigene Duplikat-/Konfliktprüfungen. Es übernimmt keine automatischen
Snapshots als Rechnungsgrundlage.

Die neue Prüfung gilt innerhalb **eines** Laufs. Sie ersetzt weder die vorhandene
Datei-Hash-Erkennung früherer Läufe noch führt sie eine neue laufübergreifende
Versions-/Quellenpolitik ein. Historische Berichte oder Snapshots werden nicht
nachträglich korrigiert. Ein Tagesexport in einem früheren und ein überlappender
Monatsexport in einem späteren Lauf bleiben separate automatische Auswertungen.

## Regressionstests

`tests/test_leg_runner_overlaps.py` prüft gleiche Dateien, überlappende Tages-/
Monatsauszüge, widersprüchliche Werte/Qualitäten, verschiedene Messpunkte und
Richtungen, getrennte Slots, unveränderte Einzeldateien, gleichwertige Zeitzonen,
Parserfehler und Wiederanlauf. Die Tests verwenden temporäre synthetische Dateien
und Datenbanken sowie einen MQTT-Mock, keine externen Dienste. Auch vorbestehende
Ausgaben und erneut in der Inbox vorhandene, bereits verarbeitete Dateien werden
auf unveränderten Inhalt geprüft.

Validierung am 7. November 2026 mit vorhandenem Python 3.13 und pytest 9.0.3:
Vor der Korrektur scheiterten 8 der ersten 12 Regressionstestfälle, 4 Kontrollfälle
bestanden. Nach der Korrektur bestanden diese 12 Fälle. Die anschliessend erweiterte
vollständige Testsuite bestand mit **623 Tests**, einschliesslich **18 neuer
F-01-Fälle**. Es wurden keine Pakete installiert oder produktiven Dienste benutzt.
