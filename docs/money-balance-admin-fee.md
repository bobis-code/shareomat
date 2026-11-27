# Geldabstimmung mit Verwaltungsgebühr (B28, behoben in 0.6.31)

## Nachgewiesener Fehler

Mit einer Verwaltungsgebühr über 0 liessen sich zu keiner freigegebenen Abrechnung Belege
erstellen. `issue_documents` brach jedes Mal ab mit:

> Rappenabstimmung fehlgeschlagen: Rechnungen CHF … ≠ Gutschriften CHF …

* **Gefunden:** am 27.11.2026 beim Bau der synthetischen Testdatenbank für den
  Architekturumbau (`tests/synthetic/schema18.py`). Im Beispiel standen Rechnungen
  CHF 641.95 gegen Gutschriften CHF 609.85; die Differenz von CHF 32.10 ist genau die Gebühr.
* Kein bestehender Test hatte eine Gebühr zusammen mit Belegen.
* Die produktive LEG hat eine Gebühr über 0. Die erste Rechnungsstellung wäre daran
  gescheitert.

## Ursache

Die Abrechnung rechnet richtig nach dem Vertrag: **Bezugspreis = Produzentenvergütung +
Verwaltungsgebühr**.

* `billing.py` prüft das beim Berechnen als harte Regel.
* `_allocate_cents` verteilt die Rappen so, dass die Summe der Rechnungsbeträge die
  kaufmännisch gerundete Summe aller exakten Bezugsbeträge ist. Für die Gutschriften gilt
  dasselbe mit den Vergütungen.
* Die Rechnungen sind damit um die Gebühr höher als die Gutschriften. Genau so steht es im
  Kommentar von `_allocate_cents`.

Die Prüfung vor dem Erstellen der Belege (`invoicing.issue_problems`) verlangte dagegen
**Rechnungen = Gutschriften**. Das gilt nur ohne Gebühr.

## Korrektur

Die Abrechnungsregeln, die Beträge und die Fingerabdrücke der Läufe sind **unverändert**.
Geändert hat sich nur die Abstimmung.

1. **Beim Berechnen** hält die Vorschau zusätzlich vier exakte Summen fest. Gespeichert
   werden sie mit dem Entwurf in `billing_runs.money_*` (additive Spalten).

   | Summe | Bedeutung | Berechnet aus |
   |---|---|---|
   | `money_consumers_chf` | Summe der exakten Bezugsbeträge | Beträgen |
   | `money_producers_chf` | Summe der exakten Vergütungen | Beträgen |
   | `money_admin_fee_chf` | Verwaltungsgebühr | LEG-kWh bezogen × Gebühr je Preisfenster, **nicht** aus den Beträgen |
   | `money_rounding_chf` | Rest aus der Rundung des Netzbetreibers | Vergütungssatz × (LEG-kWh bezogen − LEG-kWh geliefert) je Preisfenster und HT/NT |

   Der Rest entsteht, weil der Netzbetreiber die offizielle Aufteilung pro Viertelstunde auf
   0,001 kWh rundet. Diese Differenz ist schon heute ein Hinweis in der Abrechnung, kein
   Fehler. Ohne sie ist der Rest 0.
2. **Vor dem Erstellen der Belege** (`billing.money_balance_problems`, aufgerufen von
   `issue_problems`) gelten vier exakte Prüfungen, ohne Toleranz und ohne Ausgleichsbuchung:
   * pro Teilnehmer: Saldo = Rechnung − Gutschrift;
   * Summe der Rechnungsbeträge = gerundete Summe der Bezugsbeträge;
   * Summe der Gutschriften = gerundete Summe der Vergütungen;
   * **Bezugsbeträge − Vergütungen = Verwaltungsgebühr + Rundungsrest**, exakt.

   Die letzte Gleichung gilt genau dann, wenn in jedem Preisfenster Bezugspreis =
   Vergütung + Gebühr ist, für HT **und** NT. Jede andere Marge wird als «Differenz ohne
   vertragliche Grundlage» abgelehnt. Bisher prüfte die Abrechnung diese Regel nur für HT.
3. **Läufe, die vor 0.6.31 gespeichert wurden**, haben keine gespeicherte Bilanz. Für sie
   gilt die alte Regel weiter. Mit Gebühr heisst es dann: stornieren, neu berechnen,
   freigeben. In der produktiven LEG gab es bis dahin keine freigegebene Abrechnung.

## Nachweis «Abrechnung unverändert»

Die drei freigegebenen Läufe der synthetischen Testdatenbank wurden mit 0.6.30 erstellt.
Mit 0.6.31 nachgerechnet, ergeben sie denselben Fingerabdruck
(`released_runs_differing` = leer, 27.11.2026).

Für diesen Nachweis wurden zwei Dinge entfernt, die der Generator erst nach den Freigaben
anlegt: der Tarif ohne Vertrag und der offene Prüfhinweis. Danach wurde die Testdatenbank
mit 0.6.31 neu erzeugt, jetzt mit Gebühr in jeder Version.

Für den Architekturumbau ist B28 ein **vor dem Umbau behobener Fehler**. Die
Vergleichswerte der Vorbereitungsversion (≥ 0.6.31) enthalten die Korrektur bereits.
Beim Vergleich alt gegen neu erscheint B28 deshalb nicht als Unterschied.

## Regressionstests

`tests/test_money_balance_b28.py` (SDAT-Weg) prüft:

* Gebühr 0 (Rechnungen = Gutschriften);
* Gebühr > 0 mit allen Belegen;
* mehrere Bezüger und Produzenten, Prosumer (saldiert, trägt die Gebühr auf seinem Bezug);
* zwei Preisfenster mit verschiedener Gebühr;
* Rappenrundung von Beträgen mit Bruchteilen eines Rappens;
* Rundungsrest des Netzbetreibers.

Abgelehnt werden müssen weiterhin:

* ein geänderter Rechnungsbetrag;
* ein Saldo, der nicht zu Rechnung und Gutschrift passt;
* eine geänderte Gebühr;
* eine Marge ohne vertragliche Grundlage;
* ein Lauf von vor 0.6.31 mit Gebühr.

`tests/test_synthetic_schema18.py` rechnet die freigegebenen Läufe der eingefrorenen
Testdatenbank nach. Der Fingerabdruck muss gleich bleiben.
