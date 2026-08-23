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

## Bestehende ADRs

* [ADR-0001](ADR-0001-unveraenderliche-freigegebene-abrechnungslaeufe.md) —
  Freigegebene Abrechnungsläufe sind unveränderlich
* [ADR-0002](ADR-0002-rohformat-normalisierung.md) — Rohformat-Parser
  normalisieren auf ein gemeinsames internes Modell
* [ADR-0003](ADR-0003-raise-dont-guess-externe-formate.md) — "Raise, don't
  guess" bei unverifizierten externen Datenformaten
* [ADR-0004](ADR-0004-sparkplug-b-lokale-emsomat-shareomat-kommunikation.md)
  — MQTT 5.0 + Sparkplug B 3.0 für die lokale Emsomat↔Shareomat-Kommunikation
* [ADR-0005](ADR-0005-cross-house-sparkplug-b-zentraler-relay.md) —
  Cross-House-Kommunikation über zentralen Sparkplug-B-Relay

## Verbindlichkeit

Ein ADR mit Status **Angenommen** ist eine dauerhafte Leitplanke, keine
Momentaufnahme. Vor einer Änderung an einem Bereich, den ein bestehendes ADR
abdeckt (siehe "Betroffene Dateien" im jeweiligen ADR): das ADR lesen. Eine
Änderung, die einer angenommenen Entscheidung widerspricht, braucht selbst
ein neues ADR mit Verweis ("Ersetzt ADR-XXXX") — keine stillschweigende
Abweichung im Code ohne Dokumentation.
