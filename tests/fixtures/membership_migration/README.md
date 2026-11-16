# Referenz für die Migration auf Schema 18 (Mitgliedschaften, ADR-0009)

Diese Dateien wurden am 16.11.2026 **mit dem Code von 0.6.12** (Commit `f605360`)
erzeugt, also vor dem Umbau. Sie halten fest, wie die alte Abrechnungsregel mit
diesen Daten entschieden hat. `tests/test_membership_migration.py` migriert Kopien
mit dem aktuellen Code und vergleicht.

| Datei | Inhalt |
|---|---|
| `v17_clean.db.gz` | Datenbank in Schema 17: 6 Teilnehmer in allen Stadien. Enthalten sind Papier- und Upload-Unterschrift, gemeldet ohne Bestätigung, bestätigt und ausgetreten. Dazu kommen vier Vertragsversionen: v1 Gründung, v2 Vertragsänderung, v3 «mit Zustimmung aller», v4 zurückgezogen und archiviert. Ausserdem eine freigegebene Abrechnung März 2026 |
| `v17_conflict.db.gz` | Dieselbe Datenbank, zusätzlich mit dem Fehler P1: Nach der Veröffentlichung von v2 wurden in v1 eine Unterschrift, eine EBL-Bestätigung und ein Austritt erfasst, die in v2/v3 fehlen |
| `expected_*.json.gz` | Ergebnisse von 0.6.12: Abrechnungsberechtigung für jeden Tag vom 01.12.2025 bis 30.11.2027, Fingerabdrücke der Abrechnungsvorschau für 03/2026, 11/2026 und 02/2027 sowie die freigegebene Abrechnung |
| `inbox/*.csv` | Messwerte für die Vorschauen |
| `build_with_0612.py` | Das Skript, das alles erzeugt hat. Läuft nur mit dem Code von 0.6.12 (`PYTHONPATH` auf einen Checkout von `f605360`). Mit `--clean` entsteht die Datenbank ohne Widerspruch |

Alle Daten sind erfunden: Teilnehmer `p…`/`c_…`, Messpunkte `CH…TEST0n`, Adressen
`example.ch`.

Erwartung nach der Migration:
* **clean:** Jede Abrechnungsentscheidung und jeder Fingerabdruck ist identisch.
* **conflict:** Unterschiede gibt es nur bei den zwei Teilnehmern mit
  widersprüchlichen Daten. Beide erhalten einen Prüfhinweis, und jede
  Abrechnungsvorschau bleibt gesperrt, bis der Betreiber bestätigt.
