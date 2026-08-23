# ADR-0003: "Raise, don't guess" bei unverifizierten externen Datenformaten/Regeln

## Status

Angenommen

## Kontext

Mehrere externe Schnittstellen, die Shareomat konsumiert oder konsumieren
soll (EBL-Export-API, SDAT-CH-2025-XML, teils auch ElCom/BFE-Referate), sind
zum Zeitpunkt der Implementierung nicht vollständig oder nicht offiziell
final dokumentiert (siehe `docs/sdat_leg_import.md`, `AGENTS.md`
"Umgang mit Unsicherheit").

## Problem

Eine plausible, aber ungeprüfte Annahme über ein externes Format (z.B. eine
vermutete XML-Struktur, eine angenommene Tarifregel) kann unbemerkt zu einer
falschen Abrechnung führen — ein Fehler, der bei Geldbeträgen (ADR-0001)
besonders teuer ist und sich schlimmstenfalls erst spät bemerkbar macht.

## Entscheidung

Ist ein externes Format oder eine externe Regel nicht anhand offizieller
Unterlagen gesichert, wirft der verarbeitende Code einen klaren, erklärenden
Fehler statt einen geratenen Wert zu erzeugen. Unsichere Annahmen werden
explizit markiert (`Assumption:`, `Open question:`, `Needs verification:`,
siehe `AGENTS.md`).

## Begründung

Ein klarer, sofort sichtbarer Fehler ist immer besser als eine
stillschweigend falsche Abrechnung, die erst bei einer Reklamation oder
einem Audit auffällt. Diese Regel priorisiert Korrektheit (Qualitätsziel 1,
`docs/ARCHITECTURE.md` §1) explizit über Funktionsumfang — ein Format wird
erst unterstützt, wenn es wirklich verstanden ist, nicht "wahrscheinlich
richtig".

## Konsequenzen

* Ein neues externes Format kann initial als "Gerüst ohne funktionierendes
  Parsing" existieren (siehe `sdat_ch.py`, blockiert auf offizielle
  VSE-XSDs) — bewusst kein Teil-Parsing mit geratenen Lücken.
* Erhöht die Sichtbarkeit von Blockern (ein Feature "funktioniert nicht",
  statt heimlich falsche Zahlen zu produzieren) — das ist beabsichtigt, auch
  wenn es Features vorübergehend unbenutzbar erscheinen lässt.

## Betroffene Dateien

* `shareomat/external_data/ebl.py`
* `shareomat/core/pipeline/raw/sdat_ch.py`
* `shareomat/models/settings.py` (HT/NT-Default-Fenster, dort bereits als
  "gängige Startkonvention, keine verifizierte EBL-Regel" markiert)

## Überprüfung

Neu bewerten pro Format, sobald eine offizielle Spezifikation verfügbar und
verifiziert ist — dann wird der Fehler durch echtes Parsing ersetzt, die
Regel selbst bleibt für alle noch unverifizierten Formate bestehen.
