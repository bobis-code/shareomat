# ADR-0013: Fehlende Viertelstunden und Freigabe mit Begründung

## Status

Angenommen (28.11.2026, Entscheid des Betreibers). Gebaut in 0.6.34.

## Kontext

* Die Abrechnung verwendet seit 0.6.34 nur die offiziellen SDAT-Daten des Netzbetreibers
  (ADR-0008, ADR-0012). Je Messpunkt und Viertelstunde liefert er Total, LEG-Energie und
  Restenergie.
* Bisher galten zwei Regeln:
  1. Fehlte bei offiziellen Daten auch nur ein Wert, war das ein harter Fehler. Der ganze
     Zeitraum liess sich nicht freigeben, auch nicht mit Begründung (Review vom 20.11.2026).
  2. Ein mit Begründung freigegebener Lauf galt nur als interne Auswertung und durfte nicht
     verrechnet werden (Invoicing-Audit vom 27.10.2026, P0-06, `816150e`). Damals konnte eine
     Begründung beim CSV-Weg auch fehlende Messdaten durchlassen.
* Einwand des Betreibers: Wegen eines einzelnen fehlenden 15-Minuten-Werts darf nicht der ganze
  LEG-Monat blockiert sein, es geht ums Geld. Der Betreiber soll entscheiden.
* Pro Viertelstunde gilt: LEG-Energie bezogen = LEG-Energie geliefert. Darauf beruht die
  Geldabstimmung vor den Belegen (B28: Bezüger zahlen = Produzenten erhalten + Gebühr + Rest).

## Entscheidung

1. **Eine Viertelstunde mit fehlendem Wert wird für die ganze LEG nicht abgerechnet.**
   * Fehlend heisst: Bei einem an diesem Tag berechtigten Messpunkt fehlt für einen nötigen
     Kanal (Bezug, Einspeisung je nach Rolle) Total, LEG- oder Restenergie.
   * Die LEG-Energie dieser Viertelstunde zählt für alle Teilnehmer als 0. Nur den Messpunkt
     ohne Wert wegzulassen ginge nicht: Der Produzent bekäme eine Gutschrift, die niemand
     bezahlt, und die Geldabstimmung würde die Belege zu Recht verweigern.
   * Die Vorschau nennt je Messpunkt die fehlenden Viertelstunden und in einer Zeile die
     weggelassenen Viertelstunden mit ihrer LEG-Energie (von der vollständigen Seite).
2. **Eine Lücke ist ein weicher Prüfpunkt.** Freigeben geht nur mit schriftlicher Begründung;
   sie steht im Lauf und im Audit-Protokoll.
3. **Hart bleiben** (nie freigebbar): ein Messpunkt ganz ohne Werte im Zeitraum, keine
   Monatslieferung, nur vorläufige Werte (Qualität 21), Total ≠ LEG + Rest über die Rundung
   hinaus, LEG-Energie an einem nicht berechtigten Messpunkt, unbekannte Messpunkte, fehlende
   Preise, Speicher.
4. **Ein mit Begründung freigegebener Lauf ist verrechenbar.** Die Begründung steht beim
   Erstellen der Belege und im Monatsabschluss sichtbar dabei. Versendet wird solcher Lauf
   nur von Hand: Eine spätere Automatisierung (E8, ADR-0012) darf ihn nie selbst versenden.

## Begründung

* Der Betreiber entscheidet, ob ein Monat mit Lücke abgerechnet wird, statt dass ein einzelner
  Wert alles blockiert.
* Abgerechnet wird nur, was der Netzbetreiber vollständig geliefert hat. Es wird nichts
  geschätzt und kein fehlender Wert als Zahl verrechnet.
* Die Geldabstimmung bleibt auf den Rappen genau, weil jede Viertelstunde ganz oder gar nicht
  zählt.
* Bei SDAT lassen sich mit Begründung nur noch Punkte durchlassen, bei denen die Beträge
  stimmen; der Grund für P0-06 (CSV mit fehlenden Daten) ist entfallen.

## Konsequenzen

* Die LEG-Energie weggelassener Viertelstunden verrechnet niemand. Bei einzelnen
  Viertelstunden sind das Rappen; fällt ein Zähler tagelang aus, fallen diese Tage für die
  ganze LEG weg. Dann beim Netzbetreiber nachfordern. Kommt die Korrektur nach der Freigabe,
  meldet Shareomat das beim freigegebenen Lauf (ADR-0001, unverändert).
* Ob die LEG Viertelstunden so weglassen darf, ist eine Frage des Vertrags. Das entscheidet der
  Betreiber.
* Bereits freigegebene Läufe ändern sich nicht. Sie hatten keine Lücken, weil Lücken bisher
  hart waren.
* Assumption, mit den ersten echten Monatslieferungen von EBL zu prüfen: Der Netzbetreiber
  füllt Lücken in der Monatslieferung mit Ersatzwerten (Qualität 56); dann kommt die Regel
  selten zum Zug.

## Betroffene Dateien

* `shareomat/database/billing.py` (`_incomplete_slots`, Qualitätsprüfung in
  `compute_billing_preview`, `_collect_official_for_period`)
* `shareomat/database/invoicing.py` (`issue_problems`, `month_overview`)
* `shareomat/web/pages/invoices.py`, `templates/invoices/issue_preview.html`
* Tests: `tests/test_billing_quality.py`, `tests/test_billing_review_0622.py`,
  `tests/test_sdat_import_and_billing.py`, `tests/test_invoicing.py`

## Überprüfung

Neu bewerten nach den ersten echten Monatslieferungen von EBL (Dezember 2026) und bevor eine
Automatisierung Belege selbst versendet (E8/P14).
