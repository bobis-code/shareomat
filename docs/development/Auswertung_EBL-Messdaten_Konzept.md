# Auswertung der EBL-Messdaten – UI-Konzept

Stand: 19.11.2026, Entwurf zur Entscheidung, **noch nicht umgesetzt**.

Die Entwürfe als Bild, mit erfundenen Beispieldaten für 300 Messpunkte, liegen lokal in
`docs/temp/Entwurf_Auswertung/` (git-ignoriert):

* `1_uebersicht.png`: Auswertung → Übersicht mit Messpunkt-Tabelle
* `2_explorer.png`: Auswertung → Explorer
* `3_dashboard.png`: Karte «EBL-Daten» auf der Übersicht

## 1. Ziel

Der Betreiber muss jederzeit sehen und **nachrechnen** können:

* ob die Daten von EBL täglich ankommen und stimmig sind;
* was jeder Messpunkt bezogen und eingespeist hat, wie viel davon LEG-Strom war und wie viel
  vom bzw. ins Netz ging;
* für Tag, Woche, Monat, Jahr oder einen frei gewählten Zeitraum;
* bis hinunter auf die einzelnen 15-Minuten-Originalwerte, mit Export nach Excel.

Auslegung: eine LEG mit **300 Messpunkten**, nicht nur die heutigen zwei.

## 2. Ist-Zustand (geprüft)

| Teil | Datenquelle | Bemerkung |
|---|---|---|
| Seite «Analyse» (`web/pages/analysis.py`) | `settlement_history` (Schnappschüsse der automatischen Läufe aus den Messdateien) | zeigt **keine** EBL-Daten; Diagramme über `components/svg_chart.html` |
| Monatstabelle «Messdaten» (0.6.18) | `sdat_overview.month_overview()` → `load_official_values()` | ersetzt in 0.6.20 durch «Summen & Auswertung» (`sdat_analysis.py`) |
| Abrechnung SDAT (`database/billing.py`) | `load_official_values()` | massgeblich für Rechnungen |
| Rohdaten | `sdat_documents` / `sdat_series` (jede Lieferung vollständig, Werte als Text je Zeitreihe) | jede Viertelstunde kommt mit den Tageslieferungen 5-mal, dazu die Monatslieferung |

`load_official_values()` legt pro Viertelstunde fest, welcher Wert gilt: die neueste Lieferung,
Stornos werden beachtet, und ein plausibilisierter Wert wird nie durch einen vorläufigen ersetzt.

## 3. Mengengerüst bei 300 Messpunkten

Gemessen mit synthetischen Daten: 2 Messpunkte, 60 Tageslieferungen, `load_official_values()`
braucht **0.21 s**. Hochgerechnet:

| Fall | Werte | heutige Abfrage |
|---|---|---|
| 300 Messpunkte, 1 Monat | ~5.2 Mio. wirksame Werte | ~16 s |
| 300 Messpunkte, 1 Jahr | ~63 Mio. wirksame Werte | **~3 Minuten** |

Speicher (Schätzung aus der echten Datei: ~0.45 MB pro Messpunkt und Tageslieferung):

* **Rohdateien:** etwa 135 MB pro Tag, ~49 GB pro Jahr ungepackt. Gepackt (gzip, XML etwa
  15:1) noch ~3 GB pro Jahr.
* **Datenbank:** `sdat_series` speichert jede Lieferung, also jede Viertelstunde etwa 6-mal.
  Das sind mehrere GB pro Jahr.

Needs verification: ob EBL bei vielen Messpunkten eine Datei oder mehrere schickt, und wie
gross die Monatslieferung ist.

**Folgerung:** Die Auswertung kann nicht bei jedem Aufruf alle Lieferungen neu auswerten. Und die
Rohlieferungen dürfen nicht unbegrenzt in der Datenbank wachsen.

## 4. Grundsatz: eine Datengrundlage

Alle Ansichten **und** die Abrechnung lesen dieselben **wirksamen Tageswerte**. Sie werden mit
genau den Regeln von `load_official_values()` gebildet und gespeichert:

* Neue, abgeleitete Tabelle `sdat_day_values`: eine Zeile pro Messpunkt, Richtung (Bezug oder
  Einspeisung) und lokalem Tag.
  * Tagessummen Total, LEG und Rest als Spalten, damit Summen für Monat und Jahr reines SQL sind.
  * die 92/96/100 Viertelstundenwerte je Energieart, gepackt (für den Explorer);
  * Anzahl Viertelstunden, Qualitätskennzeichen und Rundungsfälle;
  * welche Lieferung die Werte liefert und ob es eine Tages- oder Monatslieferung war.
* Bei 300 Messpunkten sind das rund **219'000 Zeilen pro Jahr**. Eine Jahresansicht ist damit
  eine Summe über ≤ 219'000 Zeilen, also Sekundenbruchteile.
* Die Tabelle wird nach jedem Import für die betroffenen Tage neu gebildet, in derselben
  Transaktion. Sie lässt sich jederzeit vollständig aus `sdat_series` neu aufbauen; die
  Rohdaten bleiben massgeblich.
* Tests sichern zu, dass die Tabelle für beliebige Zeiträume genau `load_official_values()`
  entspricht.
* Die Abrechnung liest danach ebenfalls diese Tabelle. Dann **können** Übersicht, Explorer und
  Rechnung nicht verschiedene Zahlen zeigen.

Neue Tabelle und neue Rechengrundlage der Abrechnung: dafür kommt ein **ADR-0010**
(Schema 19, additiv, Sicherung vor der Migration wie bei Schema 17/18).

## 5. Wo was hinkommt (Stand nach Rückmeldung des Betreibers, 19.11.2026)

**Nachtrag 20.11.2026 (Entscheid des Betreibers):** Der Explorer wird in zwei Seiten geteilt,
weil Summen und Viertelstundenwerte zwei verschiedene Aufgaben sind. Menü «Energie & Analyse»,
Abschnitt **ANALYSE**:

* **Übersicht** (die bisherige Seite «Analyse», vorerst unverändert);
* **Summen & Auswertung:** Reiter Tag / Woche / Monat / Jahr / Frei, Auswahl «Alle Messpunkte»
  oder einer, Tabelle je Messpunkt (Bezug Total / aus LEG / vom Netz, Einspeisung Total / an
  LEG / ins Netz, Vollständigkeit, Prüfung), darunter je Tag oder Monat;
* **Messwerte:** ein Messpunkt, Schnellzeiträume, Werte, Auflösung, Lücken-Zeilen, Seiten,
  Herkunft je Viertelstunde.

Die Seiten sind verknüpft: ein Klick auf eine Zahl in den Summen öffnet die Messwerte dieses
Messpunkts mit gesetzten Filtern (Zeitraum, Bezug oder Einspeisung). Umgesetzt in 0.6.20, noch
auf `load_official_values()` (ohne `sdat_day_values`, ohne Export). Die Abschnitte 5.2 und 8
gelten sinngemäss für diese zwei Seiten.

Entwürfe: `4_dashboard_leg.png`, `5_dashboard_teilnehmer.png` (Übersicht), `6_explorer_v2.png`
(Explorer). Die Entwürfe 1–3 sind überholt.

| Ort | Inhalt |
|---|---|
| **Übersicht** (Dashboard) | Systemstatus wie bisher, darunter die Karte **«Energieflüsse»**, darunter die Messpunkte (5 grösste, Suche, Excel). «Offene Aufgaben» und «Schnellzugriff» entfallen. Die Karte «Einrichtung» bleibt für die Ersteinrichtung |
| **BETRIEB → Explorer** (heute «Analyse») | das Prüfwerkzeug: Summen je Messpunkt und alle Werte bis auf die Viertelstunde, mit Export. Die bisherigen Diagramme aus `settlement_history` entfallen (Entscheid F1) |
| **Messdaten** | bleibt technisch: Eingang, Hochladen, Lauf. Die Monatstabelle aus 0.6.18 entfällt dort |

### 5.1 Übersicht: Karte «Energieflüsse» (Bilder 4 und 5)

* Reiter **LEG gesamt / Teilnehmer / Messpunkte**. Bei Teilnehmer oder Messpunkt erscheint ein
  Suchfeld zum Auswählen (bei 300 einfacher als eine Liste).
* **Tag / Woche / Monat / Jahr** mit ◀ ▶. Rechts der Datenstand («✓ Daten bis 17.11.2026 ·
  vollständig», gelb bei Lücken, rot bei Fehlern im Empfang).
* Vier Zahlen:
  * Gesamtbezug;
  * davon aus der LEG, mit Anteil;
  * Einspeisung;
  * davon an die LEG, mit Anteil.
* **Energieverteilung:** zwei Balken, Bezug (aus LEG / vom Netz) und Einspeisung (an LEG / ins
  Netz).
* **Pro Tag:** Säulen pro Tag (Monat) bzw. pro Monat (Jahr), Grün der LEG-Anteil. Fehlende Tage
  fallen sofort auf.
* **Messpunkte:**
  * LEG gesamt: die 5 grössten Bezüger, Suche, Excel und «Alle in der Auswertung →» (öffnet
    den Explorer mit «Summe je Messpunkt»);
  * Teilnehmer: nur dessen Messpunkte.

### 5.2 Explorer (Bild 6)

Antworten auf die Prüfpunkte aus der Rückmeldung:

1. **Summen je Messpunkt:**
   * Über der Wertetabelle steht immer eine Tabelle mit einer Zeile pro gewähltem Messpunkt:
     Bezug Total / aus LEG / vom Netz, Einspeisung Total / an LEG / ins Netz.
   * Dazu die Vollständigkeit (z. B. 95 / 96 Viertelstunden) und das Ergebnis der Prüfungen
     (Total = LEG + Netz, Rundungen).
   * Darunter die Summenzeile der Auswahl.
   * Die Auflösung **«Summe»** zeigt nur diese Tabelle. Damit wird sie zur Messpunktübersicht
     für alle 300, mit Sortierung, Seiten und Export.
2. **Vollständigkeit im Raster:**
   * Die Wertetabelle beginnt immer bei 00:00 des ersten Tages und hat jede Viertelstunde, die
     es geben muss (92/96/100 je nach Zeitumstellung).
   * Fehlt ein Wert, steht dort eine markierte Zeile **«kein Wert geliefert»**, statt dass die
     Zeile fehlt (abschaltbar).
   * Im ersten Entwurf begann die Tabelle um 10:00. Das war ein gekürzter Ausschnitt, kein
     gewolltes Verhalten.
3. **Sortierung und Zeilenzahl:**
   * Sortierung «Messpunkt, dann Zeit» oder «Zeit, dann Messpunkt».
   * 25 / 50 / 100 / 200 Zeilen pro Seite.
   * Der Export enthält immer alle Zeilen.
4. **Herkunft eines Werts:** Ein Klick auf einen Wert öffnet rechts die Details:
   * Total / LEG / Netz;
   * Qualitätskennzeichen;
   * aus welcher Lieferung er gilt (Tages- oder Monatslieferung, Erstellzeit, Dateiname);
   * **alle Lieferungen dieser Viertelstunde** mit ihrem Wert: gilt, gleich oder geändert.

   Diese Ansicht liest die Rohlieferungen (`sdat_series`), nur für den einen Wert.
5. **Bedienung:**
   * Schnellzeiträume: Gestern, Letzte 7 Tage, Bestimmter Tag, Dieser Monat, Letzter Monat,
     Dieses Jahr, Frei von–bis.
   * Klare Schalter für Werte (Bezug + Einspeisung / nur Bezug / nur Einspeisung) und
     Auflösung (15 Min / Stunde / Tag / Monat / Summe).
   * Tabellenkopf bleibt beim Scrollen stehen (CSS `position: sticky`, ohne JS-Bibliothek).

Weitere Regeln:

* Export als CSV oder Excel (`openpyxl` ist schon im Add-on) mit allen Zeilen der Auswahl,
  höchstens 1'000'000 Zeilen (Grenze von Excel).
* Bei 15 Minuten höchstens 31 Tage auf dem Bildschirm; grössere Zeiträume mit Stunde, Tag,
  Monat oder Summe.

### 5.3 Später

* Diagramme: Tageskurve 15 Min, Balken pro Tag oder Monat. Server-gerendertes SVG wie heute,
  ohne JS-Bibliothek.
* Vergleich mit der eigenen Messung (Sensor am Zähler, Emsomat), zusammen mit LE-10.

## 6. Aufbewahrung und Grösse (gehört dazu, sonst wächst es unkontrolliert)

| Was | Vorschlag |
|---|---|
| Wirksame Tageswerte (`sdat_day_values`) | unbegrenzt (Grundlage der Abrechnung, Aufbewahrung 10 Jahre nach OR 958f) |
| Monatslieferungen, roh (XML) und in `sdat_series` | 10 Jahre |
| Tageslieferungen, die durch eine Monatslieferung ersetzt sind | in `sdat_series` löschen, sobald die Monatslieferung den Tag abdeckt; Original-XML noch **100 Tage** behalten, dann löschen |
| Rohdateien im Archiv | gepackt (gzip) ablegen, Prüfsumme über den Originalinhalt |
| «Sicherung erstellen» | enthält zusätzlich die Original-XML (gepackt) |

Löschen nur, wenn die Monatslieferung die Tage vollständig abdeckt. Das Löschen wird
protokolliert, mit Anzahl und Zeitraum. Needs verification an der ersten Monatslieferung: wie
sie sich von Tageslieferungen unterscheidet (Berichtszeitraum ganzer Monat?).

## 7. Sicherheit und Datenschutz

* Verbrauchsdaten von 300 Haushalten sind Personendaten (DSG).
  * Ansehen: Recht `view`.
  * **Export**: eigenes Recht (z. B. `data.export`, vorerst nur Administrator), Eintrag im
    Audit-Log mit Zeitraum, Anzahl Messpunkte und Zeilen.
  * Antworten mit `Cache-Control: no-store`.
* CSV- und Excel-Export: Textzellen gegen Formel-Einschleusung schützen. Der Schutz besteht
  schon in `core/report/leg_report.py` und wird wiederverwendet.
* Alle Filter sind GET-Parameter und werden streng geprüft:
  * Datum,
  * bekannte Messpunkt-IDs,
  * feste Werte für Auflösung und Richtung,
  * Obergrenzen für Zeitraum und Zeilen.
* Kein SQL aus Benutzereingaben.
* Keine JS-Bibliothek. Die Seiten bleiben server-gerendert wie der Rest von Shareomat.

## 8. Etappen

| Version | Inhalt |
|---|---|
| 0.6.19 | ADR-0010, Tabelle `sdat_day_values` mit Aufbau nach jedem Import und einmaligem Nachbau aus den vorhandenen Daten; Abrechnung liest sie; Übersicht mit «Energieflüsse» und Messpunkten; Monatstabelle unter «Messdaten» entfernt |
| 0.6.20 | Explorer (Summen je Messpunkt, Werte mit Lücken-Zeilen, Herkunft je Wert, Schnellzeiträume), Export CSV/Excel, Recht und Audit für Export |
| nach der ersten Monatslieferung (Dez.) | Aufbewahrung: ersetzte Tageslieferungen aufräumen, Archiv gepackt, Originale in der Sicherung |
| später | Diagramme, Vergleich mit eigener Messung |

Jede Etappe mit Tests, auch mit einem synthetischen Datensatz von 300 Messpunkten über ein Jahr
für die Antwortzeiten (Ziel: jede Ansicht unter 2 s auf dem HA-Gerät).

## 9. Entscheide des Betreibers

| Nr. | Frage | Vorschlag |
|---|---|---|
| F1 | «Analyse» wird zum «Explorer»; die bisherigen Diagramme entfallen? | **entschieden 19.11.: Explorer** (Diagramme: Vorschlag entfallen) |
| F2 | Übersicht: «Offene Aufgaben» und «Schnellzugriff» durch «Energieflüsse» und Messpunkte ersetzen? | **entschieden 19.11.: ja** |
| F3 | Abrechnung liest ebenfalls `sdat_day_values` (eine einzige Datengrundlage)? | ja, mit ADR-0010 |
| F4 | Aufbewahrung wie in Abschnitt 6 (ersetzte Tageslieferungen: Original 100 Tage, Monatslieferungen 10 Jahre)? | ja |
| F5 | Export nur für Administratoren, mit Audit-Eintrag? | ja |
| F6 | Spalten der Messpunkt-Tabelle wie in Bild 1, «vom Netz» und «ins Netz» immer sichtbar? | ja |
