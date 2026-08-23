# ADR-0002: Rohformat-Parser normalisieren auf ein gemeinsames internes Modell

## Status

Angenommen

## Kontext

Shareomat erhält Messdaten von Netzbetreibern in mehreren, unterschiedlichen
Rohformaten: CSV, EBL-XLSX-Export, künftig SDAT-CH-2025-XML (`docs/sdat_leg_import.md`).
Diese Formate unterscheiden sich in Spaltennamen, Zeitzonen-Konvention,
Intervall-Länge und Struktur.

## Problem

Ohne eine gemeinsame Normalisierungsschicht müsste Matching/Abrechnung
(`core/pipeline/leg_matcher.py`, `leg_billing.py`) für jedes Rohformat einen
eigenen Sonderfall kennen. Das koppelt Kernlogik an Lieferformat-Details und
macht ein neues Format (z.B. ein weiterer Netzbetreiber) zu einer
Änderung an der Abrechnungslogik selbst — genau dort, wo Fehler am teuersten
sind (ADR-0001).

## Entscheidung

Jeder Rohformat-Parser (`core/pipeline/raw/*.py`, z.B. `ebl_xlsx.py`,
`sdat_ch.py`) normalisiert ausschliesslich auf `IntervalReading`/`EnergySlot`
(`shareomat/models/meter_data.py`), bevor die Daten Matching/Abrechnung
erreichen. Ein neues Rohformat bekommt ein neues Modul unter
`core/pipeline/raw/`, niemals einen Sonderfall in der Abrechnungslogik.

## Begründung

Trennt "was liefert der Netzbetreiber" von "wie wird abgerechnet" —
Abrechnungslogik bleibt unabhängig testbar mit synthetischen
`IntervalReading`-Daten, ohne reale Rohformat-Dateien zu benötigen. Ein
neuer Netzbetreiber/ein neues Format erweitert das System additiv (neues
Modul), statt bestehenden, getesteten Code zu verändern.

## Konsequenzen

* Jeder neue Rohformat-Parser muss vollständig auf `IntervalReading`
  abbilden — ein Feld, das das Zielmodell nicht kennt, geht verloren oder
  erzwingt eine bewusste Modellerweiterung (nicht einen Parser-Sonderfall
  weiter unten in der Pipeline).
* Format-spezifische Unsicherheiten (fehlende offizielle Spezifikation)
  werden im Parser-Modul behandelt (siehe ADR-0003), nicht in der
  Abrechnungslogik.

## Betroffene Dateien

* `shareomat/models/meter_data.py` (`IntervalReading`, `EnergySlot`)
* `shareomat/core/pipeline/raw/` (ein Modul pro Format)
* `shareomat/core/pipeline/leg_parser.py` (Format-Router)

## Überprüfung

Neu bewerten, falls ein Rohformat Daten liefert, die sich strukturell nicht
verlustfrei auf `IntervalReading`/`EnergySlot` abbilden lassen (z.B. echte
Sub-15-Minuten-Auflösung mit eigener Bedeutung).
