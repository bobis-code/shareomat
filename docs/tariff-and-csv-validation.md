# Tarifvalidierung und fehlerhafte Messdateien (F-03)

Stand: 7. November 2026. Die bestehenden Datenbankformate und fachlichen
Preisformeln bleiben erhalten.

## Bestandsaufnahme vor der Korrektur

| Bereich und Felder | Bisherige Prüfung und tatsächlicher Zweck |
|---|---|
| LEG-Alttarif: `local_rate_chf_kwh`, `grid_rate_chf_kwh`, `feed_in_rate_chf_kwh`, `admin_fee_chf_kwh`, `local_rate_nt_chf_kwh`, `feed_in_rate_nt_chf_kwh` | `models/tariff.py`, `database/tariffs.py`: Decimal-Textspeicherung ohne Endlichkeits- oder Zeitraumprüfung; Startdatum durch SQL `NOT NULL` erforderlich. Kein allgemeines Vorzeichenverbot. Netzbezug ist informativ; `grid_rate_chf_kwh` umfasst keinen separat modellierten Netznutzungsbeleg. |
| Vertrags-LEG-Preis: `local_rate_*`, `feed_in_rate_*`, `admin_fee_chf_kwh` | `web/pages/contract.py:_compute_rates`: Bezug und Gebühr nicht negativ, Gebühr höchstens Bezug, Produzentenvergütung = Bezug − Gebühr. `database/contract_versions.py:_validate_publishable`: Bezug bei Veröffentlichung strikt positiv. Null ist im bearbeitbaren Entwurf möglich. Diese bestehenden Regeln werden nicht geändert. |
| Vertragliche Referenzfelder: `vnb_reference_price_*`, historische `price_reduction_*` | Informative Schnappschüsse; Referenz minus Reduktion ist nicht mehr die aktuelle LEG-Preisformel. Bisher keine Endlichkeitsprüfung. |
| VNB-Produkte: `energy_rate_ht/nt_chf_kwh`, `grid_rate_ht/nt_chf_kwh`, `base_price_chf_year`, `metering_price_chf_year`, `feed_in_rate_chf_kwh`, `hkn_rate_chf_kwh` | `models/external_data.py:SupplierTariff`, `database/supplier_tariffs.py`: optionale Decimal-Komponenten. `None` bedeutet unbekannt, null einen bekannten Nullpreis. Keine allgemeine Vorzeichenprüfung. Produkte dienen der Referenz, nicht der Festsetzung des LEG-Preises. |
| VNB-JSON | `external_data/vnb_tariff_json.py` importiert ausschliesslich Energieprodukte vom Typ `electricity`. CHF/kWh-Zeitfenster werden auf höchsten/niedrigsten Preis als informative HT-/NT-Referenz reduziert; CHF/M-Grundpreise auf CHF/Jahr umgerechnet. Keine Übernahme von `grid`, `metering`, `regional_fees` als LEG-Preis. Bisher ungesicherte Decimal-Konvertierung und fehlende Prüfung umgekehrter Datumsbereiche. |
| ElCom: `energy_chf_kwh`, `grid_chf_kwh`, `aidfee_chf_kwh`, `community_fees_chf_kwh`, `total_chf_kwh`, `fixcosts_chf_year` | Separate Vergleichsdaten (`external_data/elcom.py`, `database/elcom_tariffs.py`), keine LEG-Preisformel. Bisher Decimal-Konvertierung, keine Endlichkeitsprüfung in der Persistenz. |
| BFE-Referenzpreis: `ReferencePrice.price_chf_kwh` | `external_data/bfe.py`, `database/reference_prices.py`. Offizielle Referenzdaten werden separat gespeichert. Die interaktive LEG-Berechnung in `database/billing.py` verwendet weiterhin den gewählten internen Tarif; die Modulbeschreibung einer Referenzpreisablage beweist keine automatische Übernahme in die LEG-Abrechnung. |
| Prognose: `PriceForecast.forecast_price_chf_kwh`, native Marktpreise: `DayAheadPricePoint.price_chf_kwh` | Separate Planungs-/Marktdaten, keine vertraglichen LEG-Preise. Bisher keine Endlichkeitsprüfung der DB-Schreibschnittstellen. Native Marktpreise dürfen ihr Vorzeichen behalten; Auflösung, Umrechnung, MQTT-/Sparkplug-Ausgabe und Emsomat-Anbindung werden nicht umgebaut. |

Die technischen Eingabeprüfungen sind keine neue rechtliche oder fachliche
Zertifizierung aller Tarifbestandteile. Aus einer bislang fehlenden
Vorzeichenprüfung wird insbesondere keine Aussage abgeleitet, dass ein
negativer Netznutzungstarif regulatorisch zulässig sei. Wo keine belegte
projektspezifische Einschränkung existiert, wird kein neues Vorzeichenverbot
eingeführt. Negative Marktpreise und informative negative Preiswerte bleiben
erhalten; ebenso Nullwerte und die HT/NT-Rückfallregel bei fehlendem NT-Preis.

## Nachweis und Grundsätze

Die ersten 84 Regressionen am unveränderten Anwendungscode ergaben
68 fehlgeschlagene und 16 bestandene Fälle. Bestätigt wurden unter anderem
persistierte NaN-/Infinity-Werte, fehlerhafte Datentypen mit unkontrollierten
Ausnahmen, ungültige Zeiträume, verlorene Grundpreise von null sowie
Abstürze/fehlende Diagnosen bei verkürzten oder strukturell ungültigen CSVs.

Neue Prüfungen erfolgen an Eingabe- und Persistenzgrenzen. Dataclasses
bleiben Datencontainer, Abrechnung und Matching behalten ihre Berechnungen.
Es gibt keine automatische Bereinigung bereits gespeicherter ungültiger
Werte und keine Schemaänderung.

## Implementierte Prüfungen

`core/price_validation.py` stellt gemeinsame Prüfungen bereit:

- Erforderliche Werte müssen als Decimal, Zahl oder Dezimaltext vorliegen.
  Bool, Container, leere Texte und nicht numerische Inhalte werden kontrolliert
  abgelehnt. Ein Float wird über seinen String und nicht direkt in Decimal
  konvertiert. NaN, sNaN und beide Infinity-Vorzeichen werden abgelehnt.
- Optionale Felder dürfen `None` sein. Das ist von einem bekannten Nullpreis
  verschieden. Ein leerer gespeicherter Dezimaltext wird nicht zu null oder
  zu einem fehlenden NT-Tarif umgedeutet.
- Interne LEG-Rechentarife und die entsprechenden Vertragspreise müssen im
  bestehenden Float-Kern endlich und ohne Unterlauf auf null darstellbar sein.
  Diese technische Darstellungsschranke ist keine wirtschaftliche Preisgrenze.
  Andere Decimal-Referenzwerte werden nicht pauschal auf den Float-Bereich
  begrenzt; beispielsweise kann ein endlicher Wert `1e1000` dort erhalten bleiben.
- Gültigkeitsdaten müssen Kalenderdaten sein; ein Enddatum darf nicht vor dem
  Anfang liegen. Ein einzelner Tag und offene Enden bleiben möglich. Bearbeitbare
  Vertragsentwürfe dürfen wie bisher unvollständige/umgekehrte Datumsbereiche
  enthalten; spätestens die bestehende Veröffentlichungsprüfung verhindert sie.

Die Prüfungen gelten vor Schreibzugriffen in `database/tariffs.py`,
`supplier_tariffs.py`, `contract_versions.py`, `reference_prices.py`,
`price_forecasts.py`, `day_ahead_prices.py` und `elcom_tariffs.py`. Numerische
Lesekonvertierungen prüfen ebenfalls auf Endlichkeit. Ungültige Altbestände
werden gemeldet, nicht stillschweigend verändert oder entfernt. Bei VNB-
Ersetzungen wird die gesamte übergebene Preisgruppe vor dem Löschen des
bisherigen Bestands geprüft. Auch native Day-ahead-Werte bleiben unverändert
aufgelöst und vorzeichenbehaftet; geändert wird nur die Preisprüfung der Ablage.

Formulare akzeptieren weiterhin Dezimalkomma oder Dezimalpunkt und
wissenschaftliche Notation, ohne neue Tausendertrennzeichen zu erraten.
Gemischte Formate wie `1.234,56` werden abgelehnt. JSON-/DB-Dezimaltexte
verwenden weiterhin den Dezimalpunkt. Fehler erscheinen über die bestehenden
Formularfehler; die sichere Flash-Ausgabe aus S-02 bleibt erhalten.

Der VNB-JSON-Parser prüft Preislisten, Einträge, Beträge, Einheiten und
Datumsbereiche vor der Rückgabe der Produkte. Fehler führen zu
`VnbTariffFormatError`, nicht zu einem partiell erfolgreichen Import.
Unbekannte Einheiten werden entsprechend ADR-0003 nicht in ein vermeintlich
vollständiges Produkt umgerechnet. Ein Grundpreis von null bleibt jetzt
ausdrücklich null. Die bisherige informative Reduktion von Zeitfenstern auf
HT-Maximum/NT-Minimum bleibt erhalten; daraus entsteht keine neue Engine zur
verbindlichen Abrechnung beliebiger zeitabhängiger VNB-Produkte.

## CSV-Datenwege

Der Parser bleibt bewusst zeilenweise tolerant: gültige Datensätze vor und
nach einer fehlerhaften, noch abgrenzbaren Zeile werden zurückgegeben,
begleitet von Fehlerliste und Dateiname/Zeilennummer im Log. Neu werden
fehlende/doppelte Pflichtspalten, verkürzte Zeilen und überzählige Felder
kontrolliert gemeldet. Defekte CSV-Anführungszeichen beenden die weitere
Interpretation mit Diagnose, weil Folgezeilen nicht mehr zuverlässig
abgrenzbar sind. Bereits gelesene gültige Datensätze bleiben für die Diagnose
verfügbar. Ungültige Zeitstempel, leere/nicht endliche/negative Energiemengen
und falsche Zahlenformate werden weiterhin abgewiesen.

Eine nicht vorhandene optionale `quality`-Spalte und die alte Kennung `mpid`
bleiben unterstützt. Exakt gleiche Doppelzeilen werden weiterhin mit Warnung
einmal übernommen; widersprüchliche Werte werden als Fehler gemeldet. Die
F-01-Prüfung über mehrere Dateien bleibt unverändert.

Die Aufrufer entscheiden weiterhin über die fachliche Verwertung:

- Der automatische Runner bricht dank F-01 schon vor Messwertspeicherung,
  Reports, Archivierung, Erfolgsmarkierung und Veröffentlichung ab, sobald
  eine Datei Parserfehler meldet. Diese Regel wurde nicht abgeschwächt.
- Die interaktive Abrechnung verwendet gültige Zeilen zur Diagnose, führt
  Parserfehler aber als nicht übersteuerbare Qualitätsfehler. In
  `database/billing.py:_collect_readings_for_period` wird die Fehlerliste
  jetzt auch übernommen, wenn die Datei keine einzige gültige Zeile im
  Zeitraum liefert. Eine Datei mit ausschliesslich kaputten Daten konnte
  zuvor neben einer gültigen Datei unbemerkt bleiben. Weil sich beschädigte
  Zeitstempel keinem Zeitraum sicher zuordnen lassen, kann die Diagnose
  vorsorglich auch andere gewählte Zeiträume sperren.

## Grenzen

Es wurden keine neuen wirtschaftlichen Höchstpreise festgelegt. Endliche
Eingaben garantieren allerdings keine beliebig grosse spätere Rechnung:
Multiplikationen, Summen und Quantisierung unterliegen weiter den vorhandenen
Float- und Decimal-Rechengrenzen. Die Korrektur ist kein Umbau auf beliebige
Rechenpräzision und keine automatische Sanierung bereits ungültiger Daten.

Die Zahlenprüfung der Preisablagen ersetzt keinen vollständigen Audit der
externen Rohformat-Parser. SNB-Wechselkurse sind keine Tarifart und wurden
nicht verändert. Ebenso unverändert: MQTT/Sparkplug-Protokolle, Emsomat,
Rechnungsfreigabe/Stornierung, E-Mail-Importer und Lizenzdateien.

## Ausgeführte Tests und Konsistenz

Alle Läufe nutzten die bereits vorhandene Python-3.13-/pytest-Umgebung unter
Windows, ohne Installationen und mit synthetischen lokalen Daten. Externe
Abrufe in den Tests sind gemockt.

| Lauf | Ergebnis |
|---|---|
| Erste 84 Regressionen vor Anwendungskorrekturen | 68 fehlgeschlagen, 16 bestanden; 19,92 Sekunden |
| Erweiterter Zwischenstand vor Absicherung der vier weiteren Preisablagen | 12 fehlgeschlagen, 111 bestanden; 42,61 Sekunden |
| Gezielter Lauf nach diesen Korrekturen | 135 bestanden; 44,45 Sekunden |
| Tarif-, Vertrags-, Preisablage-, CSV-, Pipeline-, F-01- und F-02-Auswahl | 378 bestanden; 172,38 Sekunden |
| Abschliessende vollständige Suite, inklusive aller 153 neuen F-03-Fälle | **876 bestanden, 4 übersprungen; 431,97 Sekunden** |

Die vier übersprungenen Fälle sind weiterhin die echten Symlinktests aus
S-01; unter Windows fehlen die Erzeugungsrechte. Sie müssen vor
Produktionsfreigabe unter geeignetem Linux ausgeführt werden. Bestehende
Tests wurden nicht verändert oder abgeschwächt.

Hauptprojekt und Add-on stimmen in allen 186 gespiegelten Quelldateien
überein. Die vor diesem Auftrag gesicherten Quellprüfsummen bestätigen,
dass unter anderem Runner, E-Mail-Importer, MQTT/Sparkplug, Belegtransaktionen,
Webserver und Flash-Template nicht verändert wurden. `database/billing.py`
enthält ausschliesslich die genannte Verschiebung der Fehlerübernahme aus
CSV-Dateien; Freigabe und Stornierung bleiben unverändert. In
`web/rendering.py` wurde nur die Dezimal-Eingabeprüfung mit ihren Imports
ergänzt; der sichere Flash-Datenfluss aus S-02 bleibt bestehen.

Keine Commits, Pushes, History-, Lizenz- oder Umgebungsänderungen. Vorhandene
unversionierte Architekturdateien bleiben unberührt. `git diff --check`
meldete keine Fehler.
