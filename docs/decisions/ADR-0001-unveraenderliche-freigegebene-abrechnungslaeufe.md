# ADR-0001: Freigegebene Abrechnungsläufe sind unveränderlich

## Status

Angenommen

## Kontext

Ein `BillingRun` erzeugt für eine Abrechnungsperiode `billing_records` je
Teilnehmer (lokaler Anteil, Netzbezug, Kosten). Diese Werte fliessen direkt
in reale Zahlungen zwischen LEG-Teilnehmern.

## Problem

Ohne harte Regel könnte ein späterer Codepfad (Web-UI, Re-Import, manueller
Fix) versucht sein, bereits erzeugte `billing_records` nachträglich zu
überschreiben — z.B. weil neue Messdaten für dieselbe Periode eintreffen
oder ein Rechenfehler auffällt. Das macht eine bereits kommunizierte
Abrechnung nachträglich unauffindbar veränderlich und untergräbt die
Nachvollziehbarkeit (Kernqualitätsziel, siehe `docs/ARCHITECTURE.md` §1).

## Entscheidung

Ein `BillingRun` kennt genau drei Status: `draft`, `released`, `cancelled`.
Nur die Übergänge `draft → released` und `draft|released → cancelled` sind
erlaubt. Es gibt keine Funktion, die `billing_records` eines bereits
`released`-Laufs aktualisiert. Eine Korrektur erzeugt einen neuen Lauf,
niemals ein Update-in-place.

## Begründung

Geldbeträge sind das höchste Korrektheits-/Nachvollziehbarkeits-Risiko im
System (`docs/ARCHITECTURE.md` §1, Qualitätsziel 1). Ein Status-Übergangs-
Modell statt Update-in-place macht "was wurde wann kommuniziert" jederzeit
rekonstruierbar, auch wenn sich später herausstellt, dass eine Periode neu
gerechnet werden muss. Alternative (direktes Überschreiben mit Audit-Log)
wurde verworfen, weil ein Log nachträglich gelöscht/übersehen werden kann,
ein fehlender Schreibpfad dagegen strukturell nicht.

## Konsequenzen

* Eine Korrektur nach Freigabe bedeutet immer: alten Lauf `cancelled`,
  neuen Lauf `draft → released` — nie ein Patch am bestehenden Datensatz.
* Erzeugt etwas mehr Datenvolumen (mehrere Läufe pro Periode möglich), aber
  vollständige Historie bleibt erhalten.
* Kein UI-Pfad darf jemals "Bearbeiten" für einen `released`-Lauf anbieten.

## Betroffene Dateien

* `shareomat/database/billing.py` (einzige Stelle mit den Status-Übergängen)
* `shareomat/models/billing_workflow.py` (`BillingRun`)

## Überprüfung

Neu bewerten, falls ein regulatorisch verpflichtender Korrekturmechanismus
(z.B. Gutschrift/Storno mit eigener Buchhaltungslogik) eingeführt wird, der
über ein simples "neuer Lauf" hinausgeht.
