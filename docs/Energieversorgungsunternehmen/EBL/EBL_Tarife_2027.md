# EBL (Genossenschaft Elektra Baselland) — Tarife 2027

Gültig vom **01.01.2027 bis 31.12.2027**. Alle Preise verstehen sich **exkl. MwSt.**
Arbeitspreise sind in Rp./kWh angegeben, Grund- und Messpreise in CHF/Monat und
Leistungspreise in CHF/kW und Monat.

Verteilnetzbetreiber: EBL (Genossenschaft Elektra Baselland), Mühlemattstrasse 6, 4410 Liestal.
In der Grundversorgung liefert die EBL standardmässig das Produkt **EBL Blau**.

## Quellen

| Dokument | Inhalt | Link |
|---|---|---|
| Mitteilung Tarifänderungen 2027 | Überblick, Vergleich 2026 → 2027 (H4, Gewerbe) | [PDF](https://www.ebl.ch/hubfs/Mitteilung%20zu%20den%20Tarif%C3%A4nderungen%202027.pdf) |
| Preisblatt P27 | Grundversorgung < 50'000 kWh/Jahr **mit Smart Meter** (Standard, Therma, Clima, Flexa) | [PDF](https://www.ebl.ch/hubfs/2026.08.27_EBL_Preisblatt_Grundversorgung_P27_SmartMeter.pdf) |
| Preisblatt P27_OSM | Grundversorgung < 50'000 kWh/Jahr **ohne Smart Meter** | [PDF](https://www.ebl.ch/hubfs/2026.08.27_EBL_Preisblatt_Grundversorgung_P27_ohneSmartMeter.pdf) |
| Preisblatt FP27 | Förderprodukte EBL Standard und EBL Grün | [PDF](https://www.ebl.ch/hubfs/EBL_Preisblatt_F%C3%B6rderprodukte_FP27.pdf) |
| Preisblatt E27 | Vergütung für Rücklieferung und HKN | [PDF](https://www.ebl.ch/hubfs/EBL_Preisblatt_E27.pdf) |
| EBL 2027.json | Maschinenlesbare Tarifdaten aller Kundengruppen (2026 und 2027) | [JSON](https://www.ebl.ch/hubfs/EBL%202027.json) |

Die Werte unten stammen aus der JSON-Datei und wurden mit den Preisblättern abgeglichen.
Sie stimmen überein, mit Ausnahme der unter [Offene Punkte](#offene-punkte) genannten Stellen.

## Was sich 2027 ändert

- **Preise sinken** für alle Kundengruppen, sowohl beim Netz als auch bei der Energie.
  Ein H4-Haushalt mit 4'500 kWh/Jahr zahlt 1'257 CHF, rund 102 CHF weniger als 2026.
  Der Durchschnittspreis sinkt von 30.20 auf **27.94 Rp./kWh** (−7.5 %).
  Ein Kleinbetrieb mit 53'000 kWh/Jahr zahlt 23.34 statt 24.32 Rp./kWh (rund 522 CHF weniger).
- **Einfachtarif statt Hoch-/Niedertarif.** Für Kundinnen und Kunden mit Smart Meter
  entfällt die HT/NT-Unterscheidung bei Energie und Netz.
- **Neue Leistungskomponente.** Kundinnen und Kunden mit Smart Meter zahlen neu einen
  Leistungspreis von 2.40 CHF/kW/Monat. Gemessen wird die höchste 15-Minuten-Leistungsspitze
  des Monats (00–24 Uhr). Davon wird ein **monatlicher Leistungsfreibetrag von 1.5 kW**
  abgezogen (Preisblatt P27 Smart Meter, gilt für Standard, Therma, Clima und Flexa).
- **Neue Wahltarife** für steuerbare Verbraucher: Therma (Boiler/Speicherheizung),
  Clima (Wärmepumpe) und Flexa (zeitvariabel).

## Die drei Tarifgestaltungen

### 1. Standardtarif mit Smart Meter (Einfachtarif + Leistungspreis)

Für Haushalt und Gewerbe < 50'000 kWh/Jahr ohne von der EBL gesteuerte Verbraucher.
Das ist der Normalfall für LEG- und vZEV-Teilnehmende, weil beide einen Smart Meter voraussetzen.

| Komponente | EBL Blau (Standard) | EBL Grau |
|---|---:|---:|
| Energie Arbeitspreis | **9.80** | 9.60 |
| Netznutzung Arbeitspreis | 9.00 | 9.00 |
| Abgaben (siehe [Abgaben](#gesetzliche-abgaben-und-gebühren)) | 3.19 | 3.19 |
| **Total Arbeitspreis (Rp./kWh)** | **21.99** | **21.79** |
| Leistungspreis Netz (CHF/kW/Monat) | 2.40 | 2.40 |
| Grundpreis Energie (CHF/Monat) | 4.90 | 4.90 |
| Grundpreis Netz (CHF/Monat) | 4.00 | 4.00 |
| Messtarif (CHF/Monat) | 5.00 | 5.00 |
| **Fixkosten total (CHF/Monat)** | **13.90** | **13.90** |

**Vergleich H4-Haushalt 2026 → 2027 (aus der Mitteilung):**

| Komponente | 2026 | 2027 |
|---|---|---|
| Energie Arbeitspreis | HT 11.70 / NT 9.05 | ET 9.80 |
| Netz Arbeitspreis | HT 14.55 / NT 10.10 | ET 9.00 |
| Leistungspreis | – | 2.40 CHF/kW/Monat (neu) |
| Grundpreis Netz | 4.35 CHF/Monat | 4.00 CHF/Monat |
| Messtarif | 5.65 CHF/Monat | 5.00 CHF/Monat |
| Abgaben Netz | 0.73 Rp./kWh | 0.55 Rp./kWh |
| Abgaben allgemein | 2.64 Rp./kWh | 2.64 Rp./kWh |

### 2. Grundversorgung ohne Smart Meter (Preisblatt P27_OSM)

Für Kundinnen und Kunden < 50'000 kWh/Jahr ohne Smart Meter. Hier gibt es weiterhin
einen Einfach- und einen Doppeltarif, aber **keinen Leistungspreis**.

| Komponente (Rp./kWh) | Blau ET | Blau HT | Blau NT | Grau ET | Grau HT | Grau NT |
|---|---:|---:|---:|---:|---:|---:|
| Energie | 9.90 | 10.40 | 9.40 | 9.70 | 10.20 | 9.20 |
| Netznutzung | 11.45 | 13.40 | 9.50 | 11.45 | 13.40 | 9.50 |
| Abgaben | 3.19 | 3.19 | 3.19 | 3.19 | 3.19 | 3.19 |
| **Total** | **24.54** | **26.99** | **22.09** | **24.34** | **26.79** | **21.89** |

Fixkosten wie beim Standardtarif: Grundpreis Energie 4.90, Grundpreis Netz 4.00 und
Messkosten 5.00 CHF/Monat.

**Tarifzeiten Doppeltarif:**

- Hochtarif: Montag bis Freitag, 06:00–21:00 Uhr.
- Niedertarif: Montag bis Freitag 21:00–06:00 Uhr, das ganze Wochenende und an Feiertagen
  (Neujahr, Karfreitag, Ostermontag, Auffahrt, Pfingstmontag, 1. August, 25./26. Dezember).
- Die Zeitabgrenzung richtet sich nach den Toleranzgrenzen in den AGB.

### 3. Wahltarife (steuerbare und flexible Verbraucher)

| Wahltarif | Für wen | Netz Arbeitspreis | Leistungspreis | Grundpreis Netz | Energie Blau / Grau |
|---|---|---:|---:|---:|---:|
| **Therma** | Elektroboiler oder Speicherheizung, zeitlich von der EBL gesteuert | 9.00 | 0.80 | 4.00 | 9.80 / 9.60 (+ 4.90 CHF/Mt.) |
| **Clima** (mit Smart Meter) | Wärmepumpe, zeitlich von der EBL gesteuert | 5.40 | 2.40 | – | 9.80 / 9.60 (kein Grundpreis) |
| **Clima** (ohne Smart Meter) | wie oben, ohne Smart Meter | 7.70 | – | – | 9.80 / 9.60 (kein Grundpreis) |
| **Flexa** | Kundschaft, die verbrauchsintensive Geräte selbst zeitlich verschiebt | HT 11.70 / NT 6.30 | 2.40 | 4.00 | HT 10.80 / NT 7.90 (Blau), HT 10.60 / NT 7.70 (Grau), + 4.90 CHF/Mt. |

Arbeitspreise in Rp./kWh, Leistungspreis in CHF/kW/Monat und Grundpreise in CHF/Monat.
Bei allen Smart-Meter-Tarifen gilt ein **monatlicher Leistungsfreibetrag von 1.5 kW**
(laut Preisblatt P27 Smart Meter; in der JSON-Datei nicht abgebildet).
Clima setzt einen separaten Zähler an der Wärmepumpe voraus und hat keine Grundpreise
(nur Messkosten 5.00 CHF/Monat).
Für alle Wahltarife beträgt der Messtarif 5.00 CHF/Monat und es kommen die Abgaben von
3.19 Rp./kWh hinzu.

Totale Arbeitspreise inkl. Abgaben, mit EBL Blau:

- Therma: 21.99 Rp./kWh.
- Clima mit Smart Meter: 18.39 Rp./kWh.
- Clima ohne Smart Meter: 20.69 Rp./kWh (Preisblatt P27_OSM).
- Flexa: 25.69 Rp./kWh im Hochtarif und 17.39 Rp./kWh im Niedertarif.

**Flexa-Zeitfenster (gilt an allen Wochentagen):**

| Saison | Hochtarif | Niedertarif |
|---|---|---|
| Winter (Jan–Mär, Okt–Dez) | 05:00–23:00 | 23:00–05:00 |
| Sommer (Apr–Sep) | 05:00–11:00 und 17:00–23:00 | 23:00–05:00 und **11:00–17:00** |

Im Sommer fällt der Niedertarif über Mittag auf die Zeit der höchsten PV-Produktion.
Der Tarif lohnt sich deshalb, wenn Lasten wie Wärmepumpe, E-Auto oder Boiler in diese
Zeit verschoben werden.

## Gesetzliche Abgaben und Gebühren

Diese Abgaben gelten für alle Produkte gleich (NE7, Rp./kWh):

| Abgabe | Rp./kWh | Gruppe in der Mitteilung |
|---|---:|---|
| Systemdienstleistungen Swissgrid | 0.19 | Abgaben Netz (0.55) |
| Stromreserve | 0.17 | Abgaben Netz |
| Solidarisierte Kosten | 0.19 | Abgaben Netz |
| Netzzuschlag (Bund) | 2.30 | Abgaben allgemein (2.64) |
| Kommunale Abgaben und Leistungen | 0.34 | Abgaben allgemein |
| **Total** | **3.19** | |

Laut JSON beträgt die kommunale Abgabe 2027 in allen EBL-Gemeinden einheitlich
0.34 Rp./kWh, ohne Grundpreis. Das gilt für NE7 und NE5.

## Grössere Kundengruppen (Kurzübersicht)

| Kundengruppe | Netz Arbeitspreis | Leistungspreis | Grundpreis Netz | Messtarif | Energie Blau / Grau |
|---|---:|---:|---:|---:|---:|
| 50'000–100'000 kWh/Jahr | 7.25 | 8.00 | 5.00 | 7.00 | 9.80 / 9.60 |
| > 100'000 kWh/Jahr (NE5) | 6.90 | 8.00 | 5.00 | 16.00 | 9.80 / 9.60 |
| > 100'000 kWh/Jahr und ≥ 400 A | 6.05 | 8.00 | 5.00 | 16.00 | 9.80 / 9.60 |
| Trafostation (Mittelspannung) | 2.05 | 9.00 | 20.00 | 35.00 | 9.80 / 9.60 |
| Öffentliche Beleuchtung | 5.15 | – | 2.35 | 5.00 | 9.80 / 9.60 |

Für die Kategorie > 100'000 kWh/Jahr weist die JSON-Datei zusätzlich `refundStorage`
0.038 CHF/kWh aus. Für ≥ 400 A und Trafostation kommt ein Blindenergiepreis von
3.8 bzw. 2.3 Rp./kvarh hinzu.

## Förderprodukte (Preisblatt FP27)

Die Förderprodukte basieren auf EBL Grau. Zusätzlich wird ein Förderzuschlag erhoben,
den die EBL in neue erneuerbare Produktionsanlagen investiert. Die Preise gelten für
Kundinnen und Kunden < 50'000 kWh/Jahr mit Smart Meter.

| Produkt | Energie (Grau) | Förderzuschlag | Netz | Abgaben | **Total Rp./kWh** | Leistungspreis | Fix CHF/Mt. |
|---|---:|---:|---:|---:|---:|---:|---:|
| EBL Standard | 9.60 | 1.50 | 9.00 | 3.19 | **23.29** | 2.40 | 13.90 |
| EBL Grün | 9.60 | 5.00 | 9.00 | 3.19 | **26.79** | 2.40 | 13.90 |

## Rückliefervergütung (Preisblatt E27)

Gilt für Anlagen, die fest an das EBL-Verteilnetz angeschlossen sind und ab dem 01.01.2015
in Betrieb genommen wurden.

| Anlage | Leistung | Rückliefertarif Strom | Mindestvergütung | HKN |
|---|---|---|---:|---:|
| Photovoltaik | bis 30 kW | Day-Ahead-Spotmarktpreis | 6.00 | 3.00 |
| Photovoltaik | 30–150 kW mit Eigenverbrauch | Day-Ahead-Spotmarktpreis | 6.00 für den Anteil bis 30 kW, 0.00 darüber | 3.00 |
| Photovoltaik | 30–150 kW ohne Eigenverbrauch | Day-Ahead-Spotmarktpreis | 6.20 | 3.00 |
| Photovoltaik | über 150 kW | Day-Ahead-Spotmarktpreis | keine | 3.00 |
| Kleinwasserkraft | bis 150 kW / über 150 kW | Day-Ahead-Spotmarktpreis | 12.00 / keine | 3.00 |
| Wind, übrige erneuerbare | alle | Day-Ahead-Spotmarktpreis | keine | 3.00 |
| Nicht erneuerbar (BHKW, Speicher, Lifte usw.) | alle | 5.00 (Einheitstarif) | – | keine |

So funktioniert die Vergütung:

- **Mindestvergütung.** Liegt der Referenz-Marktpreis unter der Mindestvergütung, zahlt die
  EBL zusätzlich zum Day-Ahead-Spotmarktpreis die Differenz bis zur Mindestvergütung.
- **HKN-Vergütung.** HKN werden nur vergütet, wenn die EBL sie zusammen mit dem Strom
  übernimmt. Für Anlagen unter 2 kWp bzw. 2 kVA gibt es keine HKN-Vergütung.
  Ist ein Speicher installiert, muss nachgewiesen sein, dass die Rücklieferung rein
  erneuerbar ist.
- **Eigenverbrauch.** Vergütet wird nur, was ins EBL-Verteilnetz zurückgeliefert wird.
- **Auszahlung** einmal pro Quartal.
- **Altanlagen** mit Inbetriebnahme vor 2015 behalten ihre Sätze bis zum 31.12. des
  25. Betriebsjahres. Wird eine solche Anlage erweitert, gilt ein Mischsatz.

## Bedeutung für Shareomat

- **VNB-Referenzenergiepreis für den LEG-Vertrag 2027: 9.80 Rp./kWh.** Das ist der
  Energie-Arbeitspreis des Standardprodukts EBL Blau mit Smart Meter.
  - Da 2027 ein Einfachtarif gilt, wird das Tarifmodell im Vertrag auf **Einheitstarif**
    gestellt. Die Niedertarif-Felder bleiben leer.
  - Mit EBL Grau als Referenz wären es 9.60 Rp./kWh.
  - Der LEG-Preis ersetzt nur die **Energiekomponente**. Netznutzung, Abgaben (SDL,
    Stromreserve, solidarisierte Kosten, Netzzuschlag, kommunale Abgaben), Messkosten und
    Leistungspreis bezahlen die Teilnehmenden weiterhin direkt an die EBL. Deshalb darf der
    Gesamtpreis (21.99 Rp./kWh) **nicht** als LEG-Referenzpreis verwendet werden.
- **Referenzpreis für die vZEV-80-%-Prüfung** (nur vZEV/ZEV, nicht LEG; Spezifikation Kap. 4.1): Massgebend ist der
  vollständige Preis des Standardprodukts.
  - Arbeitspreis 21.99 Rp./kWh.
  - Dazu kommen anteilig die Fixkosten (13.90 CHF/Monat) und der Leistungspreis
    (2.40 CHF/kW/Monat).
- **Leistungspreis.** Für die Abrechnung des Netzbezugs braucht es neu die monatliche
  15-Minuten-Leistungsspitze pro Messpunkt. Shareomat bildet diese Komponente bisher
  nicht ab.
- **Maschinenlesbare Quelle.** Die [EBL-Tarifdatei](https://www.ebl.ch/hubfs/EBL%202027.json) enthält alle Werte strukturiert
  (`tariffType` = `grid` / `electricity` / `metering` / `regional_fees`, Preise in CHF/kWh).
  Die Funktion „Referenzpreis automatisch ermitteln“ kann diese Datei als Quelle nutzen:
  Gesucht ist der Eintrag `tariffType == "electricity"` mit dem Namen
  „Strombezug < 50'000 kWh/Jahr Netz Energie EBL Blau 2027“.

## Offene Punkte

- **Doppeltarif ohne Smart Meter, Tarifzeiten.** In der JSON-Datei gilt der Hochtarif
  beim **Netz** nur Montag bis Freitag 06–21 Uhr. Bei der **Energie** gilt er dagegen an
  allen sieben Wochentagen von 06–21 Uhr. Das Preisblatt zeigt eine gemeinsame Zeittabelle.
  Bevor diese Werte verwendet werden, muss bei der EBL geklärt werden, welche Zeiten gelten.
- **Feiertage im Niedertarif** sind nur im Preisblatt aufgeführt, nicht in der JSON-Datei.
