# ADR-0009: Mitgliedschaft getrennt von der Vertragsversion

## Status

Angenommen (16.11.2026, Entscheid E1 des Betreibers zum Gesamtkonzept 0.6.13).

**Ergänzt durch [ADR-0012](ADR-0012-architekturumbau-mehrere-legs-eigenstaendig-datenaufteilung.md)
(27.11.2026):** Die Zuordnung Messpunkt → Mitgliedschaft bekommt einen Zeitraum
(`meter_assignments`).

## Kontext

* Bis Schema 17 stand alles zu einem Teilnehmer in `participant_contract`, eine Zeile
  pro Teilnehmer und Vertragsversion. Dazu gehörten Beitritt, Austritt, EBL-Meldung und
  -Bestätigung, der wirksame Beginn, die Unterschrift sowie die Mitteilung.
* `publish_version()` kopierte diese Daten beim Veröffentlichen einer Folgeversion
  einmal. Alles, was danach in der alten Version erfasst wurde, fehlte in der neuen (P1
  im Prüfbericht vom 15.11.2026). Nachgewiesen wurde:
  * Ein Austritt galt in der Folgeversion nicht. Der Teilnehmer wäre weiter abgerechnet
    worden.
  * Eine nachträgliche Unterschrift fehlte in der Folgeversion.
  * Eine nachträgliche EBL-Bestätigung fehlte ebenfalls.
* Dieselbe Tatsache hätte in jeder Version erfasst werden müssen (P2). Mitteilung und
  Zustimmung standen im selben Feld (P7). Ein Wiedereintritt war wegen
  `UNIQUE(participant_id, contract_version_id)` nicht sauber abbildbar.

## Entscheidung

1. **Neue Tabelle `memberships`**: eine Zeile pro Mitgliedschaft. Sie enthält Beitritt,
   Austritt, Austrittsgrund, EBL gemeldet, EBL bestätigt, wirksamer Beginn,
   Paperless-Verweise und einen Prüfhinweis (`review_note`). Ein Wiedereintritt ergibt
   eine neue Mitgliedschaft; Mitgliedschaften überlappen nie.
2. **`participant_contract`** enthält nur noch, was zu **einer Version** gehört:
   * Erklärung,
   * Unterschrift, auf der Zeile der unterschriebenen Version,
   * Mitteilung (`notified_at`),
   * Zustimmung (`consent_at`, neu getrennt),
   * Nachweise.

   Jede Zeile gehört zu genau einer Mitgliedschaft. Die Eindeutigkeit ist neu
   `UNIQUE(membership_id, contract_version_id)`.
3. **Die Unterschrift gilt** für ihre Version und alle späteren Versionen **derselben**
   Mitgliedschaft. Sie gilt nicht rückwirkend und nicht über einen Wiedereintritt hinweg.
   Eine Unterschrift auf einer zurückgezogenen Version gilt nur dort.
4. **Eine Mitteilung ist nie eine Zustimmung.** Bei einer Version «mit Zustimmung aller»
   prüft die Abrechnung `consent_at`.
5. **Die Abrechnungsregel bleibt inhaltlich gleich**
   (`billing_eligibility._membership_problem`: dieselben Bedingungen in derselben
   Reihenfolge mit denselben Texten). Sie liest Mitgliedschaft und Unterschrift nur aus
   dem neuen Modell. Bei einem Wiedereintritt entscheidet die Mitgliedschaft, die den
   Tag abdeckt.
6. **Migration auf Schema 18** (`shareomat/database/membership_migration.py`):
   * Vorher entsteht die geprüfte Sicherung.
   * Der Umbau läuft in einer Transaktion.
   * Danach wird jeder Wert gegen das Original geprüft. Bei einer Abweichung wird alles
     zurückgerollt und das Add-on startet nicht.
   * Alte Zeilen, die sich widersprechen, werden **nicht still bereinigt**. Übernommen
     werden die Werte der heute gültigen Version; alle Abweichungen kommen in den
     Prüfhinweis.
   * Jede Abrechnungsvorschau ist gesperrt, bis der Betreiber die Mitgliedschaft geprüft
     hat (Entscheid E2).
7. **Die verschobenen Spalten werden aus `participant_contract` entfernt.** Der Betreiber
   hat das erlaubt, sofern Migration und Umstellung aller Zugriffe nachgewiesen sind.
   * Die Tabelle wird mit dem dokumentierten SQLite-Verfahren neu aufgebaut, weil sich
     `UNIQUE` nicht anders ändern lässt.
   * Die Sicherung vor der Migration enthält den alten Stand unverändert.
   * Das ist eine bewusste Ausnahme von der Regel «Schema-Änderungen sind additiv»
     (AGENTS.md).

## Nachweis

Mit dem Code von 0.6.12 wurden zwei Referenzdatenbanken erzeugt:
`tests/fixtures/membership_migration/`, sechs Teilnehmer in allen Stadien und vier
Versionen, eine davon zurückgezogen, dazu eine freigegebene Abrechnung. Festgehalten ist
die Abrechnungsberechtigung für jeden Tag über 24 Monate.
`tests/test_membership_migration.py` belegt:

* **Ohne Widerspruch:** an allen 730 Tagen dieselben Messpunkte, Ausschlüsse mit
  denselben Gründen und dieselben Prüfhinweise. Die Fingerabdrücke der Vorschauen sind
  identisch, die freigegebene Abrechnung ist unverändert. Alle anderen Tabellen sind
  gleich, und das Audit-Log wurde nur ergänzt.
* **Mit P1-Widerspruch:** Unterschiede gibt es nur bei den zwei betroffenen Teilnehmern.
  Beide haben einen Prüfhinweis, und jede Vorschau ist gesperrt.
* **Echte Datenbank (Kopie, 16.11.2026):** 0 Unterschiede an 730 Tagen, kein Prüfhinweis.

## Konsequenzen

* P1 und P2 können nicht mehr entstehen. Ein Austritt, eine Bestätigung oder eine
  Unterschrift wirkt für alle betroffenen Versionen.
* Die Checkliste pro Teilnehmer entspricht direkt einer Mitgliedschaft.
* Ein Rückwechsel auf 0.6.12 ist nur mit der Sicherung vor der Migration möglich
  (`shareomat.before-schema-18-from-17-*.db`), nicht mit der neuen Datenbank.
* Beim Zurückziehen einer Version zählen EBL-Meldungen nur noch als Nachweis, wenn die
  Mitgliedschaft allein durch diese Version besteht (Gründungsversion). Früher machten
  kopierte Meldungen jede Folgeversion zum Archivfall.

## Betroffene Dateien

* `shareomat/database/sqlite.py` (Schema 18), `shareomat/database/membership_migration.py`
* `shareomat/models/membership.py`, `shareomat/database/memberships.py`
* `shareomat/database/participant_contract.py`, `shareomat/database/contract_versions.py`
* `shareomat/database/billing_eligibility.py`, `shareomat/database/billing.py` (Sperre bei Prüfhinweis)
