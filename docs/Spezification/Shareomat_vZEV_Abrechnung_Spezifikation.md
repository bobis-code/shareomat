# Shareomat – Spezifikation für eine weitgehend autonome vZEV-/ZEV-Abrechnung

**Version:** 0.1 – Arbeitsgrundlage  
**Stand:** 18. Juli 2026  
**Zielsystem:** Shareomat als Docker-Anwendung neben Home Assistant  
**Primärer Anwendungsfall:** Eigentümer mit eigener PV-Anlage und einem vermieteten Haushalt im EBL-Netzgebiet  
**Ziel:** Automatische, transparente und nachvollziehbare Abrechnung von Netzstrom und intern verbrauchtem PV-Strom inklusive PDF-Rechnung, Swiss QR Code, E-Mail-Versand und einfachem Mieterportal.

> **Wichtiger Hinweis:** Dieses Dokument ist eine technische und organisatorische Implementierungsgrundlage, keine verbindliche Rechtsberatung. Vor produktivem Einsatz sollten der Mietvertragszusatz, das konkrete Abrechnungsmodell und eine Musterrechnung einmalig durch eine fachkundige Stelle, beispielsweise HEV, Mietrechtsspezialist oder Abrechnungsdienstleister, geprüft werden. Tarif- und Rechtsänderungen müssen in Shareomat versioniert werden.

---

## 0. MVP-Entscheid (Stand: 18. Juli 2026)

### 0.1 Grundsatz: LEG/ZEV und vZEV koexistieren

Die bestehende LEG/ZEV-Gemeinschaftsabrechnung (mehrere unabhängige Häuser,
Cross-Meter-Matching, `leg_matcher`) **entfällt nicht** und wird durch die
vZEV-Vermieter-Funktionalität nicht ersetzt. Vieles ist gemeinsame
Infrastruktur (Datenimport, 15-Minuten-Verarbeitung, Tarifversionierung,
Reports). Der eigentliche Unterschied liegt auf Beziehungsebene zwischen
Betreiber und Teilnehmer:

```text
connection_type:
  zev_peer     — Teilnehmer ist eigenständiger LEG/ZEV-Partner
                 (bestehende, einfachere Abrechnung, kein Mietverhältnis)
  vzev_tenant  — Teilnehmer ist Mieter des Betreibers im vZEV
                 (komplexere Abrechnung: 80-%-Grenze, Mietrecht,
                 Verbrauchsübersicht, Rechnung)
```

Ein `Participant` erhält dieses Feld zusätzlich zu Rolle/Typ (Kap. 16.2).
Matching und 15-Minuten-Zuordnung bleiben für beide Fälle gleich; Billing,
Compliance-Prüfung und Mieter-Ansicht unterscheiden sich je nach
`connection_type`.

### 0.2 Umgesetzter Umfang für v1

Die folgenden Kapitel bleiben als vollständige fachliche Referenz erhalten
(insbesondere für spätere Phasen und für die einmalige rechtliche Prüfung).
Für die erste produktive Version gilt jedoch die hier getroffene, bewusst
reduzierte Auswahl. Jede betroffene Stelle im Dokument trägt unten eine
kurze **MVP-Notiz**.

**Für v1 umgesetzt:**

- Viertelstunden-Zuordnung proportional zum Verbrauch (Kap. 5) — ein
  Produzent (Eigentümer-PV) statt Cross-Meter-Flows.
- 80-%-Pauschalmethode inklusive Prüfung der Preisobergrenze (Kap. 4.1, 9.3).
- Strikte Trennung `provisional` (Live/Zähler-Rohwerte) vs. `official`
  (EBL-Daten) (Kap. 6).
- Tarifversionierung als unveränderlicher Snapshot an der Rechnung (Kap. 8).
- Mieter-Verbrauchsübersicht: rein lesend, nur eigener Verbrauch, gehostet
  in Shareomat selbst — **nicht** über Home Assistant geteilt, da HA keine
  saubere Mandantentrennung kennt (Kap. 14, reduziert).
- PDF-Rechnung wird generiert; Versand erfolgt manuell per E-Mail oder
  Ausdruck durch den Betreiber, kein automatisierter SMTP-Dispatch (Kap. 13,
  reduziert).
- Statischer TWINT-QR-Code auf der Rechnung (kein Betrag/keine Referenz
  codiert); Zahlender trägt den Betrag selbst ein (ersetzt Kap. 11 für v1).
- Zahlungseingang wird manuell bestätigt (ersetzt Kap. 18.1 für v1).
- Einfache Validierungsfunktion vor Rechnungsfreigabe (Kernpunkte aus
  Kap. 22), statt eigenem Compliance-Engine-Modul mit Event-Log.

**Bewusst verschoben (kein v1):**

- Vollständiges Mieterportal mit Login/2FA/Session-Handling/Rechnungsarchiv/
  Zahlungsstatus (Kap. 14.1–14.5) — nur die Verbrauchsübersicht kommt jetzt.
- Swiss-QR-Bill-Standard mit strukturierter Adresse, QR-IBAN, SIX-Validierung
  (Kap. 11) — erst relevant, falls maschinenlesbare Zahlungszuordnung
  gebraucht wird.
- Automatisiertes Mahnwesen mit Stufenlogik (Kap. 18.2) — Mahnungen macht
  der Betreiber vorerst selbst von Hand.
- CAMT.053-Zahlungsabgleich (Kap. 18.1).
- SPF/DKIM/DMARC-E-Mail-Infrastruktur, Bounce-Erkennung (Kap. 13).
- Formales Audit-Log als eigenes Modul (Kap. 15.1) — Kernideen fliessen in
  die einfache Validierungsfunktion ein.
- Modell B / effektive Kosten (Kap. 4.2).
- Batteriespeicher-Abrechnung (Kap. 5.4).

---

## 1. Ausgangslage

Bei einem virtuellen Zusammenschluss zum Eigenverbrauch (vZEV) bildet der Verteilnetzbetreiber aus bestehenden intelligenten Messpunkten rechnerisch einen gemeinsamen Endverbraucher. Im EBL-Gebiet gilt insbesondere:

- Die vorhandenen EBL-Smart-Meter bleiben bestehen.
- Die EBL bildet den virtuellen Gesamtmesspunkt.
- Für intern innerhalb des vZEV verteilte Energie fällt kein Netznutzungsentgelt an.
- Die EBL rechnet den Gesamtbezug und die Gesamteinspeisung monatlich mit dem vZEV-Vertreter ab.
- Die EBL stellt dem vZEV-Vertreter die Lastgangdaten der einzelnen Produktions- und Verbrauchsmesspunkte bereit.
- Die interne Abrechnung und das Inkasso erfolgen vollständig durch den vZEV-Betreiber oder einen beauftragten Dienstleister.
- Die Datenlieferung erfolgt bei EBL standardmässig monatlich per CSV-E-Mail, alternativ im S-DAT-Format.
- Die EBL erhebt eine Grundgebühr pro vZEV sowie Messkosten pro physischem Messpunkt.
- Der vZEV muss mindestens drei Monate im Voraus angemeldet werden; die Inbetriebnahme erfolgt auf den ersten Tag eines Monats.
- Die installierte Produktionsleistung muss mindestens 10 % der gesamten Anschlussleistung des Zusammenschlusses betragen.
- Alle Anschlüsse müssen am gleichen Anschluss- oder Netzverknüpfungspunkt auf Niederspannungsebene liegen.

**Konsequenz für Shareomat:**  
Shareomat muss nicht die physikalische Energieversorgung steuern. Es muss die offiziellen Messdaten importieren, Zeitreihen korrekt zuordnen, Tarife versionieren, gesetzliche Preisobergrenzen kontrollieren, Rechnungen erzeugen und alle Berechnungsschritte revisionsfähig dokumentieren.

---

## 2. Rollen und Verantwortlichkeiten

### 2.1 vZEV-Vertreter / Vermieter

Der Betreiber ist gegenüber der EBL der gemeinsame Vertragspartner und trägt insbesondere die Verantwortung für:

- fristgerechte Zahlung der EBL-Gesamtrechnung;
- interne Aufteilung des extern bezogenen Stroms;
- interne Aufteilung des zeitgleich produzierten und verbrauchten PV-Stroms;
- korrekte Anwendung der gesetzlichen Preisobergrenzen;
- transparente Abrechnung gegenüber Mietenden;
- Inkasso und Mahnwesen;
- Aufbewahrung von Messdaten, Tarifgrundlagen, Rechnungen und Korrekturen;
- Datenschutz und Zugriffsschutz;
- Behandlung von Ein- und Austritten;
- korrekte Abrechnung bei Mieterwechsel;
- Sicherstellung der Stromversorgung in erforderlicher Qualität.

### 2.2 EBL

Die EBL ist im vorgesehenen Modell zuständig für:

- physische Messpunkte und Smart Meter;
- Bildung des virtuellen Übergabepunkts;
- Ermittlung beziehungsweise Bereitstellung der relevanten Lastgangdaten;
- monatliche Gesamtrechnung an den vZEV-Vertreter;
- Vergütung der Überschusseinspeisung;
- technische und administrative Führung des vZEV auf Netzbetreiberseite.

Die EBL übernimmt **nicht**:

- die interne Rechnung an den Mieter;
- die Preisberechnung des internen Solarstroms;
- die Darstellung im Mieterportal;
- das Inkasso gegenüber dem Mieter;
- die interne Korrektur von Abrechnungen.

### 2.3 Mieter / Teilnehmer

Der Mieter erhält im vZEV keine separate Stromrechnung der EBL mehr. Er erhält die Rechnung vom Vermieter beziehungsweise von Shareomat. Bei einem bestehenden Mietverhältnis muss die Teilnahme korrekt und schriftlich geregelt werden.

---

## 3. Mietrechtliche Grundanforderungen

### 3.1 Vertragliche Grundlage

Die Stromkosten müssen im Mietvertrag oder in einem schriftlichen Zusatz ausdrücklich als Nebenkosten ausgeschieden sein. Allgemeine Formulierungen wie „übliche Nebenkosten“ genügen nicht.

Der Vertrag oder Zusatz sollte mindestens festhalten:

1. Teilnahme am ZEV/vZEV;
2. Vertreter des Zusammenschlusses;
3. Beginn der Teilnahme;
4. Messpunkte und Messmethode;
5. Art der Datenbereitstellung;
6. Abrechnungsperiode;
7. verwendetes Preis- und Abrechnungsmodell;
8. externes Stromprodukt und Regeln für dessen Wechsel;
9. Aufteilung von Fix-, Mess- und Abrechnungskosten;
10. Zahlungsfrist, Akontozahlungen und Jahres- oder Monatsabrechnung;
11. Einsichtsrecht in Abrechnungsgrundlagen;
12. Umgang mit Schätzungen und späteren Korrekturen;
13. Datenschutz und Portalzugriff;
14. Vorgehen bei Mieterwechsel;
15. Beendigung der Teilnahme.

Die Kostenposition sollte sinngemäss klar benannt werden, beispielsweise:

> „Kosten für den individuellen Strombezug aus dem öffentlichen Netz sowie für den zeitgleich innerhalb des ZEV/vZEV produzierten und verbrauchten Solarstrom, inklusive zulässiger Mess-, Datenbereitstellungs-, Verwaltungs- und Abrechnungskosten.“

### 3.2 Bestehende und neue Mietverhältnisse

- Bei einem **neuen Mietvertrag** kann die Teilnahme direkt vertraglich vorgesehen werden.
- Bei einem **bestehenden Mietverhältnis** ist die Einführung nicht einfach stillschweigend vorzunehmen. Die Teilnahme und Änderung der Nebenkostenregelung müssen schriftlich und mietrechtlich korrekt vereinbart werden.
- Shareomat darf einen Teilnehmer erst ab einem dokumentierten Vertragsbeginn abrechnen.
- Ein Vertragsdokument oder dessen Hash sollte dem Teilnehmerdatensatz zugeordnet werden.

### 3.3 Abrechnungshäufigkeit

Mietrechtlich ist mindestens eine periodische, nachvollziehbare Abrechnung notwendig. Für das Projekt empfiehlt sich:

- monatliche Rechnung oder monatliche Akontorechnung;
- jährliche Schlusskontrolle;
- Schlussabrechnung bei Auszug;
- Korrekturrechnung, sobald EBL-Daten oder Tarife nachträglich geändert werden.

Ein rein jährlicher Prozess wäre möglich, ist aber für Transparenz, Inkasso und Kostenkontrolle weniger geeignet. Bei monatlichen offiziellen EBL-Daten liegt eine monatliche vZEV-Rechnung nahe.

---

## 4. Wahl des Abrechnungsmodells

Shareomat soll mindestens zwei gesetzlich vorgesehene Modelle unterstützen.

### 4.1 Modell A: 80-%-Pauschale

Für intern produzierten und zeitgleich verbrauchten Strom inklusive zurechenbarer Stromnebenkosten darf dem einzelnen Mieter höchstens 80 % des Betrags berechnet werden, den er ohne ZEV/vZEV für dieselbe Strommenge beim Bezug des massgebenden Standardstromprodukts bezahlen müsste.

Der Referenzbetrag umfasst nicht bloss den Energiepreis. Er ist als Betrag des Standardstromprodukts für die entsprechende Strommenge zu verstehen. Nach dem BFE-Leitfaden gehören beim externen Bezug insbesondere dazu:

- Energie;
- Netznutzung;
- Messung am ZEV-Hauptmesspunkt;
- Abgaben und Leistungen an das Gemeinwesen.

Für Shareomat ist daher ein **vollständiger Referenzpreis je kWh** zu bilden. Fixkosten müssen sachgerecht auf die Referenzmenge beziehungsweise Abrechnungsperiode verteilt werden, soweit sie für den Vergleich relevant sind.

**Maximaler interner Gesamtpreis:**

```text
max_internal_total_chf =
    0.80 × reference_standard_cost_for_same_internal_quantity
```

Wenn ein fixer Anteil für interne Messung, Datenbereitstellung, Verwaltung oder Abrechnung separat verrechnet wird, muss Shareomat prüfen, dass:

```text
internal_energy_charge
+ allocated_internal_service_costs
<= 80 % des Referenzbetrags
```

Die 80-%-Grenze darf nicht dadurch umgangen werden, dass der Solarstrom mit 80 % belastet und zusätzlich eine unkontrollierte Administrationspauschale erhoben wird.

**Empfehlung für den ersten produktiven Betrieb:**  
Die Pauschalmethode verwenden. Sie ist einfacher zu automatisieren und verlangt keine vollständige jährliche Gestehungskostenrechnung der PV-Anlage. Dennoch müssen die Referenztarife, Mengen und Nebenkomponenten sauber dokumentiert sein.

### 4.2 Modell B: Effektive Kosten

Alternativ können die effektiven Kosten der intern produzierten Elektrizität inklusive zulässiger Stromnebenkosten angesetzt werden. Dabei sind insbesondere zu berücksichtigen:

- anrechenbare Kapitalkosten;
- Abschreibung/Amortisation;
- Finanzierung beziehungsweise zulässige Verzinsung;
- Betrieb und Unterhalt;
- Ersatzteile und Ersatzanschaffungen;
- interne Messung;
- Datenbereitstellung;
- Verwaltung;
- Abrechnung;
- abzüglich Förderbeiträge;
- abzüglich Erlöse aus eingespeister Elektrizität.

Zusätzlich gelten Preisobergrenzen und eine Beteiligung der Mietenden an einer erzielten Einsparung. Dieses Modell erfordert eine detaillierte Kostenrechnung und ist für Version 1 nicht zu empfehlen.

### 4.3 Extern bezogener Netzstrom

Die effektiven Kosten des extern bezogenen Stroms sind verbrauchsabhängig und **ohne Gewinnaufschlag** weiterzuverrechnen. Dazu gehören nach BFE:

- Energie;
- Netznutzung;
- Messung am virtuellen Hauptmesspunkt;
- Abgaben.

Shareomat muss externe Kosten deshalb getrennt vom internen Solarstrom führen. Ein Gewinn des Betreibers entsteht nicht auf dem Netzstrom, sondern hauptsächlich aus dem zulässigen Preis des intern verbrauchten PV-Stroms abzüglich eigener Kosten und entgangener Einspeisevergütung.

---

## 5. Viertelstundenbasierte Energiezuordnung

### 5.1 Grundsatz

Die interne Zuordnung muss auf zeitgleichen Messintervallen erfolgen. Für EBL sind Lastgangdaten zu erwarten; das System soll standardmässig mit 15-Minuten-Intervallen arbeiten.

Für jedes Intervall `t` werden benötigt:

```text
P(t)      = gesamte PV-Produktion im vZEV
C_i(t)    = Verbrauch des Teilnehmers i
C_sum(t)  = Summe aller Teilnehmerverbräuche
S(t)      = intern nutzbarer Solarstrom
G(t)      = externer Netzbezug
E(t)      = Überschusseinspeisung
```

Berechnung:

```text
S(t) = min(P(t), C_sum(t))
G(t) = max(C_sum(t) - P(t), 0)
E(t) = max(P(t) - C_sum(t), 0)
```

### 5.2 Proportionale Zuordnung

Falls keine abweichende, vertraglich zulässige Verteilregel definiert ist, wird der interne PV-Strom proportional zum zeitgleichen Verbrauch verteilt:

```text
solar_i(t) =
    0, wenn C_sum(t) = 0
    S(t) × C_i(t) / C_sum(t), sonst

grid_i(t) = C_i(t) - solar_i(t)
```

Prüfinvarianten:

```text
Summe_i solar_i(t) = S(t)
Summe_i grid_i(t)  = G(t)
solar_i(t) >= 0
grid_i(t) >= 0
solar_i(t) + grid_i(t) = C_i(t)
```

### 5.3 Nur ein Produzent und zwei Haushalte

Bei einem Eigentümerhaushalt und einem Mieterhaushalt wird genauso gerechnet. Shareomat darf den Eigenverbrauch des Eigentümers nicht automatisch zuerst bedienen, sofern nicht eine rechtlich und vertraglich zulässige Prioritätsregel ausdrücklich hinterlegt ist. Die Standardimplementierung sollte neutral proportional verteilen.

### 5.4 Speicher

Bei Einbindung eines Batteriespeichers muss definiert werden:

- separater Messpunkt oder rechnerische Bilanz;
- Herkunft des geladenen Stroms;
- Verluste;
- Reihenfolge der Zuordnung;
- Behandlung von Netzladung;
- Behandlung der späteren Entladung.

Für Version 1 sollte Speicherabrechnung nur aktiviert werden, wenn die EBL-Datenstruktur und die vertragliche Zuordnungsregel eindeutig sind. Andernfalls ist sie als nicht unterstützte Konfiguration zu blockieren.

---

## 6. Offizielle Daten versus Live-Daten

### 6.1 Offizielle Abrechnungsquelle

Für die definitive Rechnung gelten grundsätzlich die von EBL gelieferten Lastgang- und Rechnungsdaten.

Datenquellen:

- EBL-CSV;
- S-DAT;
- EBL-Gesamtrechnung;
- EBL-Tarifblätter;
- Einspeiseabrechnung;
- Mess- und Grundgebühren.

### 6.2 Shelly als Live- und Kontrollmessung

Ein Shelly beim Mieter kann eingesetzt werden für:

- Live-Verbrauch;
- Tages- und Monatsansicht;
- vorläufige Kostenanzeige;
- Plausibilitätskontrolle;
- Erkennung von Datenlücken;
- Home-Assistant-Integration;
- frühe Information über ungewöhnlichen Verbrauch.

Der Shelly sollte **nicht ohne weitere Prüfung alleinige definitive Abrechnungsgrundlage** sein, wenn offizielle EBL-Messdaten verfügbar sind.

### 6.3 Zwei Datenebenen

Shareomat soll strikt unterscheiden:

```text
provisional = Shelly/Home-Assistant-Daten
official    = EBL-CSV/S-DAT und EBL-Rechnung
```

Status je Abrechnungsperiode:

- `OPEN_LIVE`
- `WAITING_FOR_EBL_DATA`
- `OFFICIAL_DATA_IMPORTED`
- `RECONCILED`
- `INVOICED`
- `PAID`
- `CORRECTED`
- `CANCELLED`

Im Portal muss sichtbar sein, ob Werte „live/vorläufig“ oder „offiziell abgerechnet“ sind.

---

## 7. Datenimport und Validierung

### 7.1 Importpipeline

1. Eingang einer Datei im überwachten Importverzeichnis oder per sicherem Mailabruf.
2. Viren-/Dateitypprüfung.
3. Speicherung der unveränderten Originaldatei.
4. Berechnung eines SHA-256-Hashs.
5. Parserauswahl nach Format und EBL-Version.
6. Normalisierung auf interne Zeitreihen.
7. Prüfung auf:
   - vollständige Messpunktzuordnung;
   - Zeitzone `Europe/Zurich`;
   - Sommer-/Winterzeit;
   - doppelte Intervalle;
   - fehlende Intervalle;
   - negative oder unplausible Werte;
   - Zählerreset;
   - Einheit Wh/kWh;
   - Importüberschneidungen;
   - Differenzen zum virtuellen Gesamtmesspunkt.
8. Erstellung eines Importberichts.
9. Freigabe für automatische Abrechnung nur bei erfolgreicher Validierung.

### 7.2 Zeitumstellung

An Tagen mit Wechsel der Sommerzeit gibt es nicht immer genau 96 Viertelstundenintervalle. Das System darf nicht starr 96 Datensätze pro Kalendertag verlangen. Zeitstempel sind intern mit Zeitzonenbezug oder UTC zu speichern.

### 7.3 Bilanzprüfung

Pro Periode:

```text
Teilnehmerverbrauch gesamt
≈ offizieller Gesamtverbrauch
```

und

```text
PV-Produktion
≈ interner PV-Verbrauch + Einspeisung
```

Abweichungen sind mit konfigurierbarer Toleranz zu behandeln, beispielsweise:

- Warnung ab 0,5 %;
- manuelle Sperre ab 2 %;
- keine automatische Rechnung bei unbekannter Messpunktlücke.

Die konkreten Toleranzen müssen anhand echter EBL-Dateien festgelegt werden.

---

## 8. Tarifmodell und Versionierung

### 8.1 Keine hart codierten Tarife

Jeder Tarif erhält:

- Anbieter;
- Produktname;
- Kundengruppe;
- gültig von/bis;
- Hoch-/Niedertarif;
- Energiepreis;
- Netznutzung;
- Abgaben;
- Messpreis;
- Grundpreis;
- Mehrwertsteuer;
- Einspeisevergütung;
- HKN-Vergütung;
- Quellenbeleg;
- Dokument-Hash;
- Erfassungsdatum;
- Freigabestatus.

### 8.2 Referenzprodukt

Für jeden Teilnehmer und Zeitraum muss Shareomat das ohne ZEV/vZEV anwendbare Standardstromprodukt bestimmen. Der Referenztarif muss als unveränderlicher Snapshot an der Rechnung gespeichert werden.

### 8.3 Tarifwechsel innerhalb eines Monats

Wenn ein Tarifwechsel innerhalb einer Abrechnungsperiode erfolgt, muss nach Gültigkeitszeit getrennt gerechnet werden. Eine Rechnung kann mehrere Tarifsegmente enthalten.

### 8.4 Hoch- und Niedertarif

Falls das Standardprodukt einen Doppeltarif enthält, muss die Referenzberechnung die jeweiligen Zeitfenster berücksichtigen. Der 80-%-Preis kann:

- je Tarifzeit separat berechnet werden; oder
- als gewichteter, nachvollziehbarer Periodentarif gebildet werden.

Die erste Variante ist transparenter.

---

## 9. Berechnung der Mieterrechnung

### 9.1 Mengen

Für Teilnehmer `i`:

```text
total_kwh_i = Summe_t C_i(t)
solar_kwh_i = Summe_t solar_i(t)
grid_kwh_i  = Summe_t grid_i(t)
```

Kontrolle:

```text
total_kwh_i = solar_kwh_i + grid_kwh_i
```

### 9.2 Netzstromkosten

```text
grid_cost_i =
    Summe über Tarifsegmente(
        grid_kwh_i_segment × tatsächlicher externer Arbeitspreis
    )
    + verursachergerechter Anteil externer Fixkosten
```

Es darf kein Betreibergewinn auf extern bezogenen Strom aufgeschlagen werden.

### 9.3 Interner Solarstrom – Pauschalmethode

Pro Tarifsegment:

```text
reference_cost_i_segment =
    Kosten, die der Teilnehmer für solar_kwh_i_segment
    ohne vZEV beim Standardprodukt bezahlt hätte

max_internal_total_i_segment =
    0.80 × reference_cost_i_segment
```

Die tatsächliche Belastung:

```text
internal_total_i_segment =
    solar_energy_charge_i_segment
    + allocated_internal_metering_cost
    + allocated_data_cost
    + allocated_admin_cost
    + allocated_billing_cost
```

Prüfung:

```text
internal_total_i_segment <= max_internal_total_i_segment
```

### 9.4 Betreibergewinn

Der wirtschaftliche Deckungsbeitrag des Betreibers ist nicht der Rechnungsbetrag, sondern:

```text
operator_margin =
    Einnahmen aus internem Solarstrom
    - entgangene Einspeisevergütung
    - zurechenbare Messkosten
    - vZEV-Grundgebühr
    - Abrechnungskosten
    - Zahlungsverkehrskosten
    - Wartungs-/Betriebskosten
    - allfällige Steuern und Abgaben
```

Für die pauschale Mieterrechnung muss diese interne Gewinnrechnung nicht zwingend auf der Rechnung erscheinen. Sie soll jedoch im Betreiber-Dashboard ausgewiesen werden.

### 9.5 Beispiel

Annahmen:

```text
Mieterverbrauch                  300 kWh
davon Solarstrom                120 kWh
davon Netzstrom                 180 kWh
Standard-Vollkosten Netzstrom  0.30 CHF/kWh
Maximaler interner Gesamtpreis 0.24 CHF/kWh
Tatsächliche Netzkosten        0.30 CHF/kWh
```

Rechnung:

```text
Netzstrom:
180 kWh × 0.30 CHF = 54.00 CHF

Solarstrom inklusive intern zurechenbarer Nebenkosten:
120 kWh × 0.24 CHF = 28.80 CHF

Total vor weiteren neutralen Positionen:
82.80 CHF
```

Ohne vZEV:

```text
300 kWh × 0.30 CHF = 90.00 CHF
```

Ersparnis des Mieters:

```text
7.20 CHF
```

Bruttoerlös des Betreibers auf dem Solarstrom:

```text
28.80 CHF
```

Davon sind entgangene Einspeisevergütung und Betreiber-/vZEV-Kosten abzuziehen.

---

## 10. Aufbau der Rechnung

Jede Rechnung soll mindestens enthalten:

### 10.1 Kopf

- Name und Adresse des Rechnungsstellers;
- Name und Adresse des Mieters;
- Rechnungsnummer;
- Rechnungsdatum;
- Leistungs-/Abrechnungszeitraum;
- Vertrags-/Teilnehmernummer;
- Messpunktbezeichnung;
- Zahlungsfrist;
- Bankverbindung.

### 10.2 Mengenübersicht

| Position | Menge |
|---|---:|
| Gesamtverbrauch | … kWh |
| Davon intern bezogener Solarstrom | … kWh |
| Davon externer Netzstrom | … kWh |
| Solarstromanteil | … % |

### 10.3 Kostenübersicht

| Position | Menge | Tarif | Betrag |
|---|---:|---:|---:|
| Externer Netzstrom – Energie/Netz/Abgaben | … kWh | … Rp./kWh | CHF … |
| Interner Solarstrom | … kWh | … Rp./kWh | CHF … |
| Zulässige Mess-/Daten-/Abrechnungskosten | … | … | CHF … |
| Gutschrift/Korrektur | | | CHF … |
| Zwischentotal | | | CHF … |
| MWST, soweit anwendbar | | | CHF … |
| Rechnungsbetrag | | | **CHF …** |

### 10.4 Transparenzblock

- angewandtes Modell: `80-%-Pauschale` oder `effektive Kosten`;
- Name des Referenz-Standardprodukts;
- Referenzpreis und Gültigkeitszeitraum;
- maximal zulässiger interner Betrag;
- tatsächlich verrechneter interner Betrag;
- Bestätigung: „Preisobergrenze eingehalten“;
- Hinweis, dass Live-Daten vom definitiven EBL-Abschluss abweichen können;
- Link beziehungsweise QR-Link zur Detailansicht im Portal.

### 10.5 Detailanhang

Optional als zweite PDF-Seite:

- Monats-/Tagesverbrauch;
- PV-/Netzanteil;
- Tarifsegmente;
- Messdatenqualität;
- Korrekturen;
- Vorjahresvergleich;
- Ersparnis gegenüber Referenzprodukt.

Viertelstundenwerte müssen nicht auf jeder Rechnung ausgedruckt werden, sollen aber im Portal exportierbar sein.

---

## 11. Swiss QR Code und Zahlungsprozess

> **MVP-Notiz:** Für v1 ersetzt durch einen statischen TWINT-QR-Code ohne
> codierten Betrag/Referenz (siehe Kap. 0). Dieses Kapitel bleibt als
> Referenz, falls später eine maschinenlesbare Zahlungszuordnung gebraucht
> wird.

### 11.1 Standard

Shareomat muss die aktuell gültigen Schweizer Implementation Guidelines für die QR-Rechnung einhalten. Im Juli 2026 ist Version 2.3 aktuell; Version 2.4 wird ab 14. November 2026 eingeführt, während Version 2.3 übergangsweise bis November 2027 gültig bleibt.

Seit November 2025 ist im Swiss QR Code nur noch die **strukturierte Adresse** zulässig.

### 11.2 Empfohlene Umsetzung

Für CHF-Rechnungen:

- QR-IBAN plus QR-Referenz, sofern eine QR-IBAN verfügbar ist; oder
- normale IBAN plus Creditor Reference (SCOR); oder
- normale IBAN plus unstrukturierte Mitteilung.

Für automatische Zahlungszuordnung ist eine eindeutige Referenz notwendig.

Empfohlenes internes Mapping:

```text
creditor_reference -> invoice_id
payment_import      -> invoice status
```

### 11.3 Validierung

Vor Versand:

- Schema-/Business-Rule-Validierung;
- strukturierte Gläubiger- und Schuldneradresse;
- korrekte Prüfziffer;
- Betrag und Währung identisch mit Rechnung;
- visuelle Prüfung des Zahlteils;
- Testscan mit mehreren Banking-Apps;
- Nutzung des SIX-Validierungsportals während Entwicklung und Releases.

### 11.4 PDF

Bei digitaler E-Mail-Rechnung darf der Swiss QR Code in einem PDF verwendet werden. Bei Papierausgabe gelten zusätzliche Layout- und gegebenenfalls Perforationsvorgaben.

---

## 12. Automatischer Rechnungsworkflow

```text
1. Abrechnungsperiode endet
2. Shareomat wartet auf EBL-Datei und EBL-Rechnung
3. Import und Validierung
4. Abgleich mit Shelly-Livedaten
5. Ermittlung der offiziellen Energiemengen
6. Zuordnung PV/Netz je Viertelstunde
7. Tarifermittlung
8. Berechnung externer Kosten ohne Aufschlag
9. Berechnung interner Kosten
10. Prüfung der 80-%-Obergrenze
11. Verteilung zulässiger Fixkosten
12. Rechnungsvorschau
13. automatische Plausibilitätsprüfung
14. PDF und Swiss QR Code erzeugen
15. unveränderlichen Rechnungssnapshot speichern
16. E-Mail versenden
17. Rechnung im Portal bereitstellen
18. Zahlungseingang importieren/zuordnen
19. bei Fälligkeit Erinnerung/Mahnung nach konfigurierter Regel
20. Jahreskontrolle und allfällige Korrekturrechnung
```

### 12.1 Autonomiestufen

- **Stufe 0:** Nur Berechnungsvorschau.
- **Stufe 1:** Automatische Berechnung, manuelle Freigabe.
- **Stufe 2:** Automatischer Versand bei fehlerfreier Validierung.
- **Stufe 3:** Automatischer Versand, Zahlungsabgleich und Mahnwesen.

Für den Start ist Stufe 1 vorzusehen. Erst nach mehreren fehlerfreien Abrechnungsperioden sollte Stufe 2 aktiviert werden.

---

## 13. E-Mail-Versand

> **MVP-Notiz:** Für v1 kein automatisierter Versand. Shareomat erzeugt die
> PDF-Rechnung; der Betreiber verschickt sie manuell per E-Mail oder druckt
> sie aus. SPF/DKIM/DMARC, Bounce-Erkennung und Versandprotokoll sind erst
> relevant, sobald Shareomat selbst als Absender verschickt.

E-Mail-Inhalt:

- Rechnungsnummer;
- Zeitraum;
- Rechnungsbetrag;
- Fälligkeitsdatum;
- PDF im Anhang;
- sicherer Link zum Mieterportal;
- Kontaktmöglichkeit bei Fragen.

Keine detaillierten Verbrauchswerte im unverschlüsselten E-Mail-Text, sofern dies nicht nötig ist.

Technische Anforderungen:

- SMTP mit TLS;
- SPF, DKIM und DMARC für die Absenderdomain;
- Versandprotokoll;
- Bounce-Erkennung;
- erneuter Versand nur kontrolliert;
- PDF-Hash;
- keine Passwörter per E-Mail.

---

## 14. Mieterportal und API

> **MVP-Notiz:** Für v1 nur eine rein lesende Verbrauchsübersicht (eigener
> Verbrauch, kein Rechnungsarchiv, kein Zahlungsstatus, keine anderen
> Teilnehmer sichtbar), gehostet in Shareomat selbst — nicht über Home
> Assistant geteilt, da HA keine saubere Mandantentrennung kennt. Zugriff
> über einen einzelnen Link oder ein einfaches Passwort statt vollem
> Login/2FA/Session-Stack. Rechnungen, Zahlungsstatus, Admin-API bleiben
> vorerst Betreiber-Sache über das bestehende Ingress-Dashboard.

### 14.1 Minimaler Funktionsumfang

Der Mieter sieht nach Login:

- aktuellen Live-Verbrauch;
- vorläufigen Verbrauch des laufenden Monats;
- offiziellen Monatsverbrauch;
- PV- und Netzanteil;
- bisherige vorläufige Kosten;
- definitive Rechnungen;
- Zahlungsstatus;
- Ersparnis gegenüber dem Referenzprodukt;
- Download von PDF und CSV;
- Tarifgrundlage;
- Hinweise zu vorläufigen Daten.

### 14.2 Authentifizierung

Mindestens:

- individuelle Benutzerkonten;
- Passwort-Hash mit Argon2id oder gleichwertig;
- sichere Cookies;
- HTTPS;
- Login-Rate-Limit;
- Passwort-Reset mit kurzlebigem Token;
- Sitzungsablauf;
- optional TOTP-Zwei-Faktor-Authentifizierung;
- keine Freigabe von Home Assistant selbst an den Mieter.

### 14.3 Mandantentrennung

Jeder API-Aufruf muss serverseitig anhand des angemeldeten Benutzers auf erlaubte Teilnehmer und Messpunkte beschränkt werden. Eine lediglich im Frontend versteckte Teilnehmer-ID ist keine Zugriffskontrolle.

### 14.4 Beispiel-API

```text
GET  /api/v1/me
GET  /api/v1/consumption/live
GET  /api/v1/consumption?from=...&to=...&resolution=day
GET  /api/v1/energy-split?period=2026-07
GET  /api/v1/cost-preview?period=2026-07
GET  /api/v1/invoices
GET  /api/v1/invoices/{invoice_id}
GET  /api/v1/invoices/{invoice_id}/pdf
GET  /api/v1/tariffs/current
```

Betreiber-API:

```text
POST /api/v1/admin/imports/ebl
POST /api/v1/admin/billing-runs
POST /api/v1/admin/billing-runs/{id}/approve
POST /api/v1/admin/invoices/{id}/send
POST /api/v1/admin/payments/import
```

### 14.5 API-Antworten

Jeder Kostenwert sollte enthalten:

```json
{
  "amount_chf": "82.80",
  "status": "official",
  "calculation_version": "billing-engine-1.0.0",
  "tariff_snapshot_id": "tariff_2026_ebl_standard_01",
  "source_period": {
    "from": "2026-07-01T00:00:00+02:00",
    "to": "2026-08-01T00:00:00+02:00"
  }
}
```

Geldbeträge intern nie mit binären Gleitkommazahlen berechnen. Dezimaltypen verwenden und Rundungsregeln zentral definieren.

---

## 15. Datenschutz und Sicherheit

Verbrauchsdaten lassen Rückschlüsse auf Anwesenheit und Verhalten zu und sind entsprechend sorgfältig zu schützen.

Shareomat benötigt:

- dokumentierten Zweck der Datenbearbeitung;
- minimale Datenerfassung;
- Rollen- und Berechtigungskonzept;
- Transportverschlüsselung;
- Verschlüsselung sensibler Backups;
- Audit-Log;
- Lösch- und Aufbewahrungskonzept;
- Exportmöglichkeit für betroffene Personen;
- Protokollierung von Datenänderungen;
- getrennte Betreiber- und Mieterzugänge;
- regelmässige Backups und Wiederherstellungstest;
- Schutz der Home-Assistant-/MQTT-Zugangsdaten;
- keine öffentlich erreichbaren Standardpasswörter;
- Reverse Proxy mit HTTPS;
- Sicherheitsupdates.

### 15.1 Audit-Log

Unveränderlich oder manipulationserschwerend speichern:

- Login und fehlgeschlagene Logins;
- Datenimporte;
- Tarifänderungen;
- Vertragsänderungen;
- Rechnungserstellung;
- Freigabe;
- Versand;
- Storno;
- Korrektur;
- Zahlungseingang;
- manuelle Messwertänderung;
- Benutzer- und Rollenänderung.

---

## 16. Datenmodell

### 16.1 Kernobjekte

```text
Property
VzEV
Participant
TenantContract
MeteringPoint
MeterReading
ProductionReading
Tariff
TariffComponent
ReferenceTariffSnapshot
BillingPeriod
AllocationResult
CostAllocation
Invoice
InvoiceLine
Payment
ImportBatch
SourceDocument
AuditEvent
User
Role
```

### 16.2 Wichtige Felder

`Participant`

```text
id
name
role: owner | tenant | producer | consumer
connection_type: zev_peer | vzev_tenant   # siehe Kap. 0.1
valid_from
valid_to
contract_id                # nur bei vzev_tenant relevant
billing_email
portal_user_id
```

`MeteringPoint`

```text
id
external_ebl_id
participant_id
type: consumption | production | storage | common
unit
timezone
valid_from
valid_to
official
```

`MeterReading`

```text
metering_point_id
interval_start
interval_end
energy_kwh
quality: official | provisional | estimated | corrected
source_import_id
```

`Invoice`

```text
invoice_id
participant_id
billing_period_id
status
issue_date
due_date
currency
net_amount
tax_amount
gross_amount
qr_reference
calculation_version
source_hash
supersedes_invoice_id
```

---

## 17. Umgang mit fehlenden und korrigierten Daten

### 17.1 Keine stillen Schätzungen

Fehlende offizielle Werte dürfen nicht unbemerkt durch Shelly-Werte ersetzt werden.

Erlaubte Varianten:

- Rechnung zurückhalten;
- Akontorechnung ausdrücklich als solche ausstellen;
- geschätzte Rechnung deutlich kennzeichnen;
- nach Eingang offizieller Daten automatisch korrigieren.

### 17.2 Korrekturrechnung

Eine ausgestellte Rechnung darf nicht nachträglich überschrieben werden.

Stattdessen:

- ursprüngliche Rechnung unverändert lassen;
- Storno- oder Korrekturdokument erstellen;
- Differenz ausweisen;
- neue Berechnungsversion und Grund dokumentieren;
- Portalhistorie erhalten.

---

## 18. Zahlung und Mahnwesen

> **MVP-Notiz:** Für v1 kein automatischer Zahlungsabgleich (kein
> CAMT.053-Import) und kein automatisiertes Mahnwesen — der Betreiber
> bestätigt Zahlungseingänge und verschickt allfällige Mahnungen vorerst
> selbst von Hand. Automatisierung ist eine spätere Ausbaustufe.

### 18.1 Zahlungsabgleich

Möglichkeiten:

- CAMT.053/054-Bankdatei importieren;
- QR-Referenz zuordnen;
- manuelle Zahlungserfassung;
- Teilzahlungen unterstützen;
- Überzahlung als Guthaben führen.

### 18.2 Mahnwesen

Konfigurierbare Stufen:

```text
Fälligkeit
+ 5 Tage: freundliche Erinnerung
+ 15 Tage: erste Mahnung
+ 30 Tage: zweite Mahnung / manuelle Prüfung
```

Mahngebühren dürfen nicht beliebig festgelegt werden. Sie müssen vertraglich und rechtlich zulässig sein. Shareomat soll sie standardmässig deaktiviert lassen, bis die konkrete Regelung geprüft ist.

Eine automatische Unterbrechung der Stromversorgung darf Shareomat nicht auslösen.

---

## 19. Betreiber-Dashboard

Das Dashboard soll zeigen:

- Gesamtverbrauch;
- PV-Produktion;
- interner Eigenverbrauch;
- Netzbezug;
- Einspeisung;
- Eigenverbrauchsquote;
- Autarkie;
- Einnahmen aus internem PV-Strom;
- Einspeiseerlös;
- vZEV-/Mess-/Abrechnungskosten;
- Deckungsbeitrag;
- Ersparnis des Mieters;
- offene Forderungen;
- Datenqualität;
- ausstehende EBL-Datei;
- Rechnungsstatus;
- Tarifablauf;
- Vertragsablauf.

Wichtig: „Umsatz“, „Deckungsbeitrag“ und „Gewinn“ getrennt darstellen.

---

## 20. Testanforderungen

### 20.1 Rechentests

- PV = 0;
- Verbrauch = 0;
- PV exakt gleich Verbrauch;
- PV grösser als Verbrauch;
- Netzbezug mit Hoch-/Niedertarif;
- Tarifwechsel mitten im Monat;
- Sommerzeit/Winterzeit;
- Mieterwechsel mitten in der Periode;
- fehlendes Intervall;
- doppelte EBL-Datei;
- korrigierte EBL-Datei;
- negative Einspeisewerte;
- mehrere Produzenten;
- Rundungsdifferenzen;
- 80-%-Grenze exakt erreicht;
- 80-%-Grenze durch Fixkosten überschritten;
- Zahlung exakt, teilweise oder doppelt.

### 20.2 Golden-Master-Test

Ein vollständig manuell geprüftes Abrechnungsbeispiel wird als Golden Master gespeichert. Jede Änderung der Billing Engine muss exakt dasselbe Resultat liefern oder eine bewusst freigegebene Abweichung dokumentieren.

### 20.3 Property-based Tests

Für jedes Intervall:

```text
allocated_solar <= production
allocated_solar <= consumption
allocated_grid >= 0
sum participant consumption = total consumption
sum participant solar = internal solar
sum participant grid = external grid
```

---

## 21. Empfohlene Architektur

```text
EBL CSV / S-DAT ──> Import Service ──> Normalized Meter Store
                                           │
Shelly / MQTT ──> Home Assistant ──> Live Data Adapter
                                           │
                                           v
                                    Allocation Engine
                                           │
                                    Tariff Engine
                                           │
                                    Compliance Engine
                                           │
                                      Billing Engine
                                           │
                   ┌───────────────────────┼───────────────────────┐
                   v                       v                       v
              PDF/QR Service          Tenant API             Operator UI
                   │                       │
                   v                       v
              Email Service          Tenant Portal
                   │
                   v
             Payment Matching
```

Die Billing Engine darf nicht direkt von Home-Assistant-Entitäten abhängen. Adapter normalisieren externe Daten in ein stabiles internes Domänenmodell.

---

## 22. Compliance Engine

> **MVP-Notiz:** Für v1 keine eigene Engine mit Event-Log/Audit-Trail
> (Kap. 15.1), sondern eine einfache Validierungsfunktion, die dieselbe
> Checkliste vor Rechnungsfreigabe prüft und bei Fehlern blockiert.

Vor Freigabe einer Rechnung muss die Compliance Engine mindestens prüfen:

```text
[ ] Vertrag für Teilnehmer und Zeitraum vorhanden
[ ] Messpunkte vollständig zugeordnet
[ ] offizielle EBL-Daten vorhanden
[ ] keine ungeklärten Datenlücken
[ ] EBL-Gesamtrechnung importiert
[ ] Tarif-Snapshot vorhanden
[ ] Referenzprodukt bestimmt
[ ] externer Strom ohne Aufschlag
[ ] 80-%-Grenze inklusive Nebenkomponenten eingehalten
[ ] Mengenbilanz stimmt
[ ] Rundungsdifferenz innerhalb Toleranz
[ ] Rechnung enthält Pflichtangaben
[ ] QR-Code validiert
[ ] Empfängeradresse strukturiert
[ ] PDF unveränderlich archiviert
```

Bei einem Fehler darf kein automatischer Versand erfolgen.

---

## 23. Konkreter MVP

> **MVP-Notiz (Stand 18. Juli 2026):** Die folgenden Phasen sind durch die
> Entscheide in Kap. 0 präzisiert. Phase 1 ist die tatsächliche v1.

### Phase 1 – v1 (aktueller Umsetzungsstand)

- `connection_type` (`zev_peer`/`vzev_tenant`) im Participant-Modell, LEG/ZEV
  bleibt vollständig erhalten (Kap. 0.1);
- manueller Import EBL-CSV, bereits per E-Mail-Import automatisierbar
  (siehe `collector/leg_email_importer.py`);
- manuelle Erfassung der EBL-Rechnung und Tarife;
- Viertelstunden-Zuordnung, 80-%-Pauschalmethode;
- einfache Validierungsfunktion vor Rechnungsfreigabe (statt Compliance
  Engine/Audit-Log als eigenes Modul);
- PDF-Rechnung, Versand manuell (E-Mail oder Ausdruck) durch Betreiber;
- statischer TWINT-QR-Code auf der Rechnung, Betrag manuell eingetragen;
- Mieter-Verbrauchsübersicht (rein lesend, eigener Verbrauch) in Shareomat,
  nicht über Home Assistant geteilt;
- manuelle Zahlungsbestätigung, manuelles Mahnwesen.

### Phase 2 – Automatisierung (später)

- Swiss-QR-Bill-Standard, falls maschinenlesbare Zuordnung nötig wird;
- automatischer E-Mail-Versand mit SPF/DKIM/DMARC;
- vollständiges Mieterportal (Login, Rechnungsarchiv, Zahlungsstatus);
- CAMT.053-Zahlungsabgleich.

### Phase 3 – weitgehend autonom

- automatisiertes Mahnwesen mit Stufenlogik;
- automatische Freigabe bei vollständig grünen Prüfungen;
- Korrekturrechnung;
- Vertrags- und Tarifwarnungen;
- Jahresabschluss;
- Betreiber-Kennzahlen.

---

## 24. Noch zwingend vor Produktivbetrieb zu klären

1. Exaktes Format und Feldmapping der EBL-CSV beziehungsweise S-DAT-Datei.
2. EBL-Abrechnungsbeispiel mit realen Tarifkomponenten.
3. Genaue Behandlung der EBL-vZEV-Grundgebühr und Messkosten in der internen Verteilung.
4. Das für den konkreten Mieter massgebende Standardstromprodukt.
5. Doppeltarifzeiten und allfällige saisonale Tarife.
6. Mehrwertsteuerliche Behandlung im konkreten privaten Vermietungsfall.
7. Wortlaut des Mietvertragszusatzes.
8. Abrechnungsperiode und Akontoregelung.
9. Bank-/QR-IBAN-Modell.
10. Aufbewahrungsfristen für Rechnungen, Messdaten und Vertragsunterlagen.
11. Datenschutzinformation für den Mieter.
12. Verhalten bei Nichtzahlung und zulässige Mahnkosten.
13. Behandlung eines allfälligen Batteriespeichers.
14. Ob und wie Allgemeinstrom einbezogen wird.
15. Einmalige externe Prüfung einer Musterabrechnung.

---

## 25. Rechts- und Fachquellen

### Bundesrecht und Bundesstellen

- **Energiegesetz (EnG), SR 730.0**, insbesondere Regeln zum Zusammenschluss zum Eigenverbrauch und zum Schutz von Mietenden:  
  https://www.fedlex.admin.ch/eli/cc/2017/762/de

- **Energieverordnung (EnV), SR 730.01**, insbesondere die aktuellen Regeln zur Kostenanlastung und Preisobergrenze:  
  https://www.fedlex.admin.ch/eli/cc/2017/763/de

- **BFE/EnergieSchweiz – Leitfaden Eigenverbrauch**: Ausführungen zu externem Strom, 80-%-Pauschale, effektiven Kosten, Messung, Verwaltung und Abrechnung:  
  https://pubdb.bfe.admin.ch/de/publication/download/9329

- **Obligationenrecht, Art. 257a/257b**, Nebenkosten und tatsächliche Aufwendungen:  
  https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de

- **VMWG**, insbesondere Bestimmungen zur Nebenkostenabrechnung:  
  https://www.fedlex.admin.ch/eli/cc/1990/835_835_835/de

- **Datenschutzgesetz (DSG), SR 235.1**:  
  https://www.fedlex.admin.ch/eli/cc/2022/491/de

### EBL

- **EBL – Virtueller Zusammenschluss zum Eigenverbrauch**: Voraussetzungen, Datenlieferung, Abrechnung, Gebühren und 80-%-Hinweis:  
  https://www.ebl.ch/de/strom/eigenverbrauch-vzev

### Mietrechtliche Orientierung

- **Mieterinnen- und Mieterverband – ZEV**: ausdrückliche Erwähnung der Stromkosten im Mietvertrag, Anforderungen an Vertrag, Messung und Abrechnung:  
  https://www.mieterverband.ch/mietrecht/waehrend-der-miete/heiz-und-nebenkosten/zev-zusammenschluss-zum-eigenverbrauch/

### Zahlungsstandard

- **SIX – QR-Rechnung / Swiss Payment Standards**:  
  https://www.six-group.com/de/products-services/banking-services/payment-standardization/standards/qr-bill.html

- **Aktuelle Implementation Guidelines QR-Rechnung, Version 2.3; zukünftige Version 2.4 beachten**:  
  Download über die SIX-Seite.

---

## 26. Entscheid für Shareomat

Für den beschriebenen Fall wird folgende Zielkonfiguration empfohlen:

```yaml
billing_model: vzEV
internal_pricing_method: 80_percent_flat_rate
official_metering_source: EBL_CSV_OR_SDAT
live_metering_source: SHELLY_VIA_MQTT
allocation_interval: 15_minutes
allocation_method: proportional_to_simultaneous_consumption
billing_frequency: monthly
invoice_delivery:
  pdf: true
  swiss_qr: true
  email: true
tenant_portal:
  enabled: true
  authentication: required
  show_live_values: true
  show_official_values: true
  clearly_mark_provisional_data: true
automatic_dispatch:
  initial_mode: manual_approval
compliance_blocking: true
audit_log: true
```

Damit kann Shareomat den grössten Teil des Ablaufs autonom übernehmen, ohne Live-Messung und rechtlich massgebende Abrechnungsdaten miteinander zu vermischen. Der zentrale Grundsatz lautet:

> **Shelly für Transparenz und laufende Anzeige; EBL-Daten für die definitive Rechnung; Shareomat für Zuordnung, Preisprüfung, Rechnung, QR-Zahlung, Portal und Auditierbarkeit.**
