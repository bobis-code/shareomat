# Synthetische Installation auf Schema 18 (Umbau-Schritt P0a)

Grundlage für alle Prüfungen der Migration auf Schema 19 und der neuen Architektur.
Siehe das Umbaukonzept `docs/development/Datenbank_Struktur_0.6.30.md` (vorerst nur lokal), Kapitel 15.4, 15.5 und 16.1.
**Keine produktiven Daten**: Alle Namen, Adressen, Kennungen und Werte sind erfunden.

| Datei | Inhalt |
|---|---|
| `shareomat-schema18.db.gz` | Die Datenbank (Schema 18), erzeugt mit `tests/synthetic/schema18.py` und dem Code von 0.6.34, danach `VACUUM`, gzip ohne Zeitstempel |
| `fingerprint.txt` | Prüfsumme des Inhalts (`content_fingerprint`): alle Werte ausser Zeitstempeln, Sitzungsschlüsseln, Passwort-Hashes und der Storno-Prüfsumme, die den Zeitpunkt enthält |

**Erzeugung:** `python -m tests.synthetic.schema18 <leerer-ordner>`. Das dauert etwa 40 s
und ist reproduzierbar: Zwei Läufe ergeben dieselbe Prüfsumme.

**Eingefroren:** Sobald Schema 19 gebaut ist, gibt es den Code von Schema 18 nicht mehr.
Ab dann gilt nur noch diese Datei. Der Generator bleibt als Nachweis, wie sie entstanden
ist (wie `tests/fixtures/membership_migration/build_with_0612.py`).

**Inhalt:** 16 Teilnehmer mit je einem eigenen Zustand (Liste `PEOPLE` im Generator):

* Produzent, Bezüger, Prosumer, Speicher; zwei Messpunkte;
* inaktiv, Austritt, Wiedereintritt, wirksamer Beginn später;
* Meldung beim Netzbetreiber offen, ohne Unterschrift, Zustimmung fehlt;
* Namenswechsel nach der Abrechnung, Messpunkt umgehängt.

Dazu kommen:

* **Verträge:** sechs Versionen. v1, v2 mit Ankündigungsfrist, v3 «mit Zustimmung aller»
  befristet und beendet, v4 nach bestätigter Lücke, v5 zurückgezogen (archiviert), v6
  Entwurf.
* **Mitteilungen:** per E-Mail (gesendet, fehlgeschlagen, unklar, geklärt), Brief, von Hand,
  zurückgenommen und neu.
* **SDAT:** Monatslieferungen März (Sommerzeit), April, Mai, Oktober (Winterzeit) mit E31.
  Dazu Tageslieferungen November (vorläufig), eine Korrektur mit Ersatzwerten, eine
  Rundungsdifferenz und eine Datei mit fremder LEG. Abgewiesen werden: falscher Empfänger,
  fremde LEG, Konflikt.
* **Abrechnungen:** storniert; März freigegeben; April+Mai mit Preiswechsel freigegeben;
  Oktober mit Begründung freigegeben; November Entwurf.
* **Belege aus März:**
  * Rechnungen mit QR und eine Gutschrift;
  * volle Zahlung, Teilzahlung, Rückerstattung, Auszahlung;
  * Mahnung (eine verworfen), Mahnsperre;
  * Storno mit Stornobeleg und Ersatzbeleg;
  * Versand per E-Mail und Papier.
* **Weiteres:**
  * Einstellungen inkl. Automatik-Schalter und Test-Token;
  * Login mit Sitzungen und Login-Versuchen;
  * Live-Quellen;
  * externe Daten;
  * Altbestand: Tarif ohne Vertrag, `meter_readings`, `settlement_cycle_records`,
    Prognosen;
  * Prüfhinweise offen und bestätigt.

**Nicht enthalten, weil der Code von Schema 18 es nicht erzeugen kann:** ein zweiter Benutzer
(es gibt nur `create_admin`).

**Stand 0.6.31 (B28 behoben):**

* v1 hat eine Verwaltungsgebühr von 0,005 CHF/kWh, v2 eine von 0,01 CHF/kWh.
* Für die Läufe März und April+Mai (zwei Gebührenfenster) gibt es Belege.
* Jeder Lauf hält seine exakte Geldbilanz fest (`money_*`).
* Die Fassung aus 0.6.30 (Gebühr 0 in v1) wurde mit dieser Korrektur ersetzt. Vorher wurde
  nachgewiesen, dass ihre drei freigegebenen Läufe mit 0.6.31 denselben Fingerabdruck
  ergeben: Die Abrechnungslogik ist unverändert.

**Stand 0.6.34 (CSV-Weg entfernt, ADR-0012; fehlende Viertelstunden, ADR-0013):**

* Neu eingefroren am 28.11.2026. Gegenüber 0.6.31 unterscheidet sich genau eine Zeile: der
  Entwurf November (`billing_runs`, nicht freigegeben). Seine fehlenden Viertelstunden sind
  jetzt ein weicher Prüfpunkt mit der Zeile «Nicht abgerechnet …», dadurch ändern sich
  `quality_issues` und `result_fingerprint` des Entwurfs. Alle anderen Tabellen, die drei
  freigegebenen und der stornierte Lauf sind Feld für Feld gleich; die freigegebenen rechnen mit
  0.6.34 unverändert nach.
* Die Einstellungen der entfernten CSV-Funktionen (`meter_data_source`, `cron_schedule` usw.)
  schreibt der Generator wie 0.6.31 direkt in die Tabelle. Seit 0.6.34 werden sie nicht mehr
  gelesen; eine echte Installation hat sie ebenfalls noch.
