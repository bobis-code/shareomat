# Architecture Decision Records

Grundlegende Architekturentscheidungen für Shareomat, siehe `AGENTS.md`
("Architecture Decision Records").

## Wann ein ADR

Nicht jede Codeänderung braucht ein ADR. Ein ADR lohnt sich, wenn eine
Entscheidung:

* mehrere Module/Schichten betrifft (nicht nur eine Funktion),
* schwer rückgängig zu machen ist,
* künftigen Bearbeitern (menschlich oder KI) sonst nicht erklärt, *warum*
  etwas so ist, wie es ist — nur der Code zeigt das *was*.

## Vorlage

```markdown
# ADR-NNNN: <Kurztitel>

## Status

Angenommen | Vorgeschlagen | Ersetzt durch ADR-XXXX

## Kontext

Ausgangslage und Hintergrund der Entscheidung.

## Problem

Was muss entschieden werden? Welche Risiken bestehen ohne Entscheidung?

## Entscheidung

Was wurde entschieden?

## Begründung

Warum wurde so entschieden? Welche Alternativen wurden verworfen, und
warum?

## Konsequenzen

Was folgt daraus — auch negative Konsequenzen/Trade-offs.

## Betroffene Dateien

Welche Module/Dateien sind direkt betroffen?

## Überprüfung

Wann oder unter welchen Umständen soll diese Entscheidung neu bewertet
werden?
```

## Dateikonvention

```text
docs/decisions/ADR-NNNN-kurzbeschreibung.md
```

`NNNN` vierstellig, aufsteigend. Bestehende ADRs werden nicht überschrieben
— eine revidierte Entscheidung bekommt einen neuen ADR mit Verweis
("Ersetzt durch ADR-XXXX") im alten.

## Noch nicht nachgetragen

Diese bereits getroffenen Entscheidungen sind im Code gelebt, aber noch
nicht als ADR festgehalten — gute Kandidaten für ADR-0001 ff., sobald
gewünscht:

* Freigegebene Abrechnungsläufe sind unveränderlich (draft/released/
  cancelled statt Update-in-place) — `shareomat/database/billing.py`.
* Rohformat-Parser normalisieren auf ein gemeinsames internes Modell
  (`IntervalReading`) statt formatspezifischer Sonderfälle in der
  Abrechnungslogik — `shareomat/core/pipeline/raw/`.
* "Raise, don't guess" bei unverifizierten externen Datenformaten —
  `shareomat/external_data/ebl.py`, `shareomat/core/pipeline/raw/sdat_ch.py`.
