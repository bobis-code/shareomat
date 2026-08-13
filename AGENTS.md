# AGENTS

## Zweck

Diese Datei enthält verbindliche Regeln für KI-Code-Assistenten (Claude Code,
Codex, Cursor, ChatGPT, …), die an Shareomat arbeiten.

Der Agent soll dieses Dokument vor jeder grösseren Änderung lesen. Ziel ist
eine saubere, professionelle Architektur — nicht Geschwindigkeit um jeden
Preis.

---

## Projektziel

**Shareomat** ist eine Python-Abrechnungs-Engine für Schweizer LEG- und
ZEV-Gemeinschaften (Lokale Elektrizitätsgemeinschaften / Zusammenschlüsse
zum Eigenverbrauch): Messdaten importieren, lokal geteilte Energie von
Netzbezug trennen, daraus nachvollziehbare Abrechnungen erzeugen.

Läuft wahlweise als Home-Assistant-Add-on oder eigenständig (Docker/nativ)
— dieselbe Kernanwendung in beiden Fällen.

Kein freies "irgendwie Zahlen verarbeiten". Das Ziel ist ein kontrollierter
Engineering-Kern mit klaren Schichten (siehe Architektur unten), Tests und
nachvollziehbaren Entscheidungen.

---

## Standards-Referenzen

Leitprinzip: **keinen neuen Standard erfinden, wenn bereits ein etablierter
existiert.** Vor einer neuen projektspezifischen Struktur/einem neuen
Workflow prüfen: Gibt es dafür schon eine anerkannte Praxis? Kann die
Entscheidung stattdessen begründet und (bei grösserer Tragweite) als ADR
dokumentiert werden?

Shareomat bündelt — anders als Projekte mit einer `docs/development/`-
Dateisammlung — die meisten dieser Regeln in dieser einen Datei statt in
vielen kleinen. Trotzdem lehnt sich jeder Bereich an eine externe Referenz
an, statt sich etwas Eigenes auszudenken:

| Bereich | Referenz |
|---|---|
| README.md | GitHub-Konventionen |
| `docs/ARCHITECTURE.md` | [arc42](https://arc42.de) |
| `docs/decisions/` | Architecture Decision Records (ADR) |
| Code-Stil (siehe unten) | PEP 8 + Projektregeln |
| Tests | pytest-Best-Practices (Unit/Integration/Roundtrip/Regression) |
| Sprache (siehe unten) | Projektspezifische Sprachregel |
| Commit-Nachrichten | Kurzer, beschreibender Betreff (kein festes Format wie Conventional Commits — bisher nicht eingeführt) |

---

## Sprache

* Quellcode (Klassen, Funktionen, Variablen, Module): **Englisch**
* Docstrings, Code-Kommentare, Log-Meldungen, Commit-Nachrichten: **Englisch**
* Projekt-Dokumentation (README, TODO, `docs/**/*.md`, dieses Dokument):
  **Deutsch**
* Benutzeroberfläche (Web-UI, Formulare, Fehlermeldungen an den Nutzer):
  **Deutsch**

Das ist bereits der durchgängige Ist-Zustand im Projekt — bitte nicht
mischen. Ein Modul, das teils deutsch, teils englisch kommentiert ist, ist
schlechter als eines, das konsequent bei der bestehenden Sprache bleibt.

---

## Architektur

Shareomat ist in vier Schichten aufgeteilt, jede mit einer Richtung:

```text
shareomat/web/          Admin-Weboberfläche (dünne Handler, rendert Templates)
        │  liest/schreibt über
        ▼
shareomat/database/     SQLite-Persistenz, ein Modul pro Entität
        │  liefert Domänen-Objekte an
        ▼
shareomat/core/         Business-Logik: collector → pipeline → report
        │  arbeitet auf
        ▼
shareomat/models/       Reine Dataclasses, keine I/O, keine Business-Logik
```

Wichtige, bereits etablierte Prinzipien — beim Weiterbauen respektieren,
nicht neu erfinden:

* **`shareomat/config.py` (RuntimeConfig, aus Docker/HA-Add-on-Optionen)**
  und **SQLite (Stammdaten/Einstellungen)** sind strikt getrennt.
  `shareomat.database.config_builder.build_leg_config()` ist die einzige
  Stelle, die beides zu einem `LegConfig` zusammenführt — vor jedem
  Abrechnungslauf frisch, damit Änderungen in der Web-UI sofort wirken.
* **Rohformat-Parser normalisieren immer auf ein gemeinsames internes
  Modell**, bevor sie in die Abrechnungslogik gehen (`IntervalReading` /
  `EnergySlot`, siehe `shareomat/models/meter_data.py`). Ein Format-Parser
  in `shareomat/core/pipeline/raw/` kennt nur sein eigenes Format; die
  Abrechnungslogik kennt nie das ursprüngliche Lieferformat. Neue Formate
  bekommen ein eigenes Modul in `raw/`, nicht einen Sonderfall in der
  Abrechnungslogik.
* **SQL-Schema-Änderungen sind additiv.** Neue Spalten über
  `_COLUMN_MIGRATIONS` in `shareomat/database/sqlite.py`
  (`ALTER TABLE ... ADD COLUMN` mit festem Default), nie ein bestehendes
  Feld umbenennen oder seine Bedeutung stillschweigend ändern — eine
  bestehende Installation muss beim Update ohne manuellen Eingriff
  weiterlaufen.
* **Ein freigegebener Abrechnungslauf (`BillingRun`, Status `released`) ist
  unveränderlich.** Es gibt keine Funktion, die `billing_records` nach dem
  Erzeugen aktualisiert — nur `draft → released` und `draft|released →
  cancelled` sind erlaubte Übergänge (siehe `shareomat/database/billing.py`).
  Das ist eine harte Domänen-Invariante, keine Stilfrage.

---

## Grundregeln

1. Änderungen an Abrechnungs-/Matching-Logik nur über die bestehenden,
   getesteten Funktionen (`compute_billing`, `match_all`, …) — kein
   Ad-hoc-SQL oder Parallel-Rechenweg in der Web-Schicht.
2. Keine erfundenen Annahmen über externe Datenformate (SDAT-CH, EBL-Export,
   ElCom/BFE/ENTSO-E-Schnittstellen, …). Ist ein Format/eine Regel nicht
   anhand offizieller Unterlagen gesichert: klar kennzeichnen (siehe
   "Umgang mit Unsicherheit" unten) statt zu raten. Bereits gelebtes
   Beispiel: `shareomat/external_data/ebl.py` und
   `shareomat/core/pipeline/raw/sdat_ch.py` werfen einen klaren Fehler,
   statt falsche Werte zu erzeugen, solange die reale Struktur nicht
   verifiziert ist.
3. Keine stillschweigenden Struktur- oder Schema-Änderungen (siehe
   Architektur oben).
4. Kleine, nachvollziehbare Änderungen bevorzugen. Keine grossen Umbauten
   ohne vorherige Begründung.
5. Dokumentation aktualisieren, wenn sich Verhalten oder Architektur
   ändert (`docs/**`, betroffene Docstrings).
6. Tests ergänzen, wenn sich Verhalten ändert oder ein neuer Zweig
   entsteht — insbesondere für Fehlerfälle und Grenzfälle, nicht nur den
   Erfolgspfad.
7. Grundlegende Architekturentscheidungen als ADR dokumentieren (siehe
   unten) — nicht nur im Chat besprechen und dann verschwinden lassen.
8. Bestehende Struktur, Namen und Konventionen respektieren. Module,
   Funktionen oder Ordner nicht ohne Begründung umbenennen.
9. Test-/Fixture-Daten dürfen nicht wie echte Personendaten aussehen (echte
   E-Mail-Adressen, echte Namen, echte Zählernummern). Es gab bereits einen
   Fall, der nachträglich bereinigt werden musste (`1e3d0d2`) — synthetische
   Platzhalterdaten von Anfang an verwenden.
10. `Decimal`-Werte in der Abrechnungslogik nie direkt aus einem `float`
    konstruieren (`Decimal(0.1) != Decimal("0.1")`) — über den gerundeten
    String-Umweg, wie in `shareomat/database/billing.py:_round4`.

---

## Umgang mit Unsicherheit

Ist eine externe Schnittstelle, ein Tarif-/Abrechnungsdetail oder eine
regulatorische Annahme (VNB-Regel, EBL-spezifisches Verhalten, SDAT-Feld,
…) nicht sicher bekannt, muss der Agent dies klar markieren — im Code als
Kommentar, in der Doku als Fliesstext:

```text
Assumption: ...
Open question: ...
Needs verification: ...
```

Keine unsichere Annahme darf stillschweigend als Tatsache in Code oder
Dokumentation landen. Beispiel aus dem bestehenden Code:
`shareomat/models/settings.py` — die HT/NT-Default-Fenster sind explizit
als "gängige Schweizer Startkonvention, keine verifizierte EBL-Regel"
gekennzeichnet.

---

## Code-Stil

* Klare, aussagekräftige Namen; Funktionen klein und fokussiert; Type
  Hints verwenden (`from __future__ import annotations` + Annotationen,
  wie im ganzen Projekt üblich).
* **Modul-Kopf-Docstring**, konsistentes Format — siehe praktisch jede
  bestehende Datei:

  ```python
  # -*- coding: utf-8 -*-
  """
  File: shareomat/<pfad>/<datei>.py

  Purpose:
      ...

  Part of:
      Shareomat — Swiss LEG/ZEV Settlement Engine

  Notes:
      Nur bei nicht offensichtlichen Invarianten/Entscheidungen.
  """
  ```

* **Reihenfolge innerhalb einer Datei:** private Helfer zuerst (mit `_`
  präfixiert, unter einer `# ── Private helpers ──` -Trennlinie), die
  öffentliche(n) Funktion(en) am Dateiende (unter `# ── Public interface
  ──` bzw. `# ── Public functions ──`). Das ist die bestehende Konvention
  im ganzen Projekt (`leg_parser.py`, `ebl_xlsx.py`, `leg_import.py`, …) —
  bitte fortführen, nicht auf eine andere Reihenfolge wechseln. Helfer und
  öffentliche Funktion(en) bleiben **in derselben Datei**; eine Trennung in
  zwei Dateien nach "privat"/"öffentlich" ist im modernen Python unüblich
  und hier nicht die Konvention.
* **Lesbarkeit vor Kompaktheit — als Leitlinie, nicht als starres Verbot.**
  Einfache List-/Set-/Dict-Comprehensions sind im Projekt Standard und
  bleiben das (z. B. `{m.meter_id for m in config.meters if m.active}`).
  Vermeiden: mehrere Transformationsschritte in einem Ausdruck verketten
  (z. B. Bedingung + Iteration + f-string/`.join()` gleichzeitig in einer
  Zeile), oder Abkürzungen, die eine Zeile schwer entzifferbar machen.
  Im Zweifel: Zwischenwert berechnen, sprechend benennen, dann verwenden.
  Bestehenden Code deswegen nicht pauschal umschreiben — nur wenn eine
  Stelle tatsächlich schwer lesbar ist, bei Gelegenheit aufräumen.
* **Zusammengehörige Logik nicht auseinanderreissen.** Ein Wert wird an
  einer Stelle berechnet; wenn er später an einer entfernten Stelle
  stillschweigend korrigiert/überschrieben wird, entsteht eine unsichtbare
  Abhängigkeit (Shotgun-Surgery-Risiko). Berechnung und notwendige
  Korrektur gehören in dieselbe Funktion oder in unmittelbar
  aufeinanderfolgende, klar benannte Schritte — an der Aufrufstelle
  sichtbar, nicht implizit irgendwo im Modul versteckt.

---

## Architecture Decision Records (ADR)

Grundlegende Architekturentscheidungen (nicht jede Kleinigkeit) werden als
ADR in `docs/decisions/ADR-NNNN-kurzbeschreibung.md` dokumentiert.
`NNNN` ist eine vierstellige, aufsteigend vergebene Nummer. Vorlage und
erste Beispiele: `docs/decisions/README.md`.

Beispiele für ADR-würdige Entscheidungen (bereits getroffen, aber bisher
nicht als ADR festgehalten — gute Kandidaten zum Nachtragen):

* Freigegebene Abrechnungsläufe sind unveränderlich (draft/released/
  cancelled statt Update-in-place).
* Rohformat-Parser normalisieren auf ein gemeinsames internes Modell statt
  formatspezifischer Sonderfälle in der Abrechnungslogik.
* "Raise, don't guess" bei unverifizierten externen Datenformaten.

Bestehende ADRs werden nicht überschrieben. Wird eine Entscheidung
revidiert, entsteht ein neuer ADR, der den alten mit "Ersetzt durch
ADR-XXXX" kennzeichnet.

---

## Prioritäten

Bei Unklarheit, was zu tun ist, gilt diese Reihenfolge:

1. Korrektheit (v. a. Abrechnungs-/Geldbeträge)
2. Nachvollziehbarkeit (jemand ohne Kontext soll verstehen, warum)
3. Testbarkeit
4. Wartbarkeit
5. Geschwindigkeit

---

## Wichtige Dateien

* `README.md`, `TODO.md`
* `docs/ARCHITECTURE.md` — Gesamtbild des Systems (arc42): Schichten,
  Laufzeitszenarien, Deployment, Risiken, Glossar. Vor grösseren
  Architekturänderungen lesen und bei Bedarf nachziehen.
* `docs/Spezification/Shareomat_vZEV_Abrechnung_Spezifikation.md`
* `docs/sdat_leg_import.md`, `docs/emsomat-integration.md`,
  `docs/local-setup.md`
* `docs/decisions/` (ADRs)
* `tests/conftest.py`

---

## Arbeitsweise

Vor jeder Änderung:

1. Bestehende Dateien und Konventionen lesen — nicht nach Gefühl neu
   erfinden, was schon etabliert ist.
2. Zweck der Änderung verstehen; bei Unklarheit nachfragen statt zu raten.
3. Prüfen, ob Dokumentation oder ein ADR betroffen ist.
4. Kleine, in sich abgeschlossene Änderung durchführen.
5. Tests prüfen oder ergänzen.
6. Ergebnis kurz zusammenfassen.
