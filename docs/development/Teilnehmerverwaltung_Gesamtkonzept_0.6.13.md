# Teilnehmerverwaltung mit zentraler Checkliste – Gesamtkonzept 0.6.13

> Stand 16.11.2026. **Vom Betreiber freigegeben (E1–E8) und in 0.6.13 umgesetzt**;
> Umsetzungsstand und Abweichungen in Abschnitt 14.
>
> Dieses Dokument ersetzt das Konzept vom 15.11.2026 und den Prüfbericht vom
> 15.11.2026. Deren Befunde P1–P4 und Empfehlungen E1–E6 sind hier eingearbeitet
> (Anhang A); die beiden alten Dokumente wurden gelöscht.
>
> Grundlage ist der Auftrag des Betreibers vom 15.11.2026, abgeglichen mit dem Code
> (Stand 0.6.12) und der echten Datenbank (Sicherung vom 16.11.2026, nur lesend
> ausgewertet).
>
> Die Version heisst 0.6.13, weil 0.6.12 die Sicherungsfunktion wurde.
>
> Kennzeichnung nach AGENTS.md: *Assumption*, *Open question*, *Needs verification*.

---

## 1. Ziel

> «Hier stehe ich. Das ist bereits erledigt. Das fehlt noch. Hier kann ich es direkt
> erledigen.»

Eine Seite **VERWALTUNG → Checkliste** wird zur Arbeitsoberfläche für alle Teilnehmer:

* Sie zeigt, was fehlt, warum, und worauf gewartet wird (EBL).
* Man erledigt den Punkt **auf derselben Seite**, in beliebiger Reihenfolge.
* Gleiche Handlungen für mehrere Teilnehmer gehen in **einem** Schritt
  (Vertragsmitteilung, EBL-Meldung, EBL-Bestätigung).
* Shareomat erkennt selbst, was nach einer neuen Vertragsversion wieder offen ist.

Die bestehende Vertragsverwaltung bleibt erhalten. Die Checkliste ist eine
Oberfläche für dieselbe Fachlogik, keine zweite Teilnehmerverwaltung.

---

## 2. Ausgangslage

### 2.1 Echte Daten (Sicherung vom 16.11.2026)

| | |
|---|---|
| Teilnehmer | 2, aktiv, Adresse und E-Mail vollständig |
| Messpunkte | 2, EBL-Format, gültig 01.11.2026–01.11.2027 (bewusst, wird verlängert) |
| Vertragsversionen | 1 (v4), gültig 01.11.2026–28.02.2027 (bewusst: Probezeit) |
| Zuordnungen | 2. Alle Schritte erfasst: Erklärung, Unterschrift mit Papier-Ablageort, eingefrorener Erklärungstext, EBL gemeldet und bestätigt, Beginn 01.11.2026 |
| Abrechnungen, Belege, Messwerte | keine |

Folgerungen:
* Es gibt keine Widersprüche zwischen Versionen (P1 tritt nicht auf), keine
  Unterschrift ohne Nachweis und keinen Altbestand ohne `accepted_version`.
* Eine Datenumstellung lässt sich jetzt Feld für Feld beweisen. So einfach wird es nie
  wieder.

### 2.2 Wichtige Codebefunde (Details in Anhang A)

| Nr. | Befund |
|---|---|
| P1 | Mitgliedschaftsdaten stehen **pro Vertragsversion** und werden beim Veröffentlichen einmal kopiert. Danach laufen sie auseinander. Nachgestellt: Ein Austritt nach dem Veröffentlichen einer Folgeversion gilt dort nicht, der Teilnehmer wird **weiter abgerechnet** |
| P2 | Dieselbe Tatsache (z. B. EBL-Meldung) müsste pro Version erfasst werden |
| P3 | `participants.active` und `meters.active` wirken ohne Datum, also auch auf die Vergangenheit. Die Zuordnung Messpunkt → Teilnehmer ist nicht historisiert. **Nicht Teil von 0.6.13**, nur dokumentiert, und die Checkliste bietet diese Schalter nicht an |
| P4 | Bei übernommenen Zuordnungen zeigt die Detailseite eine **neu erzeugte** Erklärung statt der unterschriebenen. Der Papier-Ablageort ist dort nicht sichtbar |
| P5 (neu) | `publish_version()` setzt das Ende der Vorversion **immer** auf «Tag vor neuem Beginn» ([contract_versions.py:486-496](../../shareomat/database/contract_versions.py#L486-L496)). Bei einer befristeten Vorversion (wie v4 bis 28.02.2027) **verlängert** das die alte Version stillschweigend, mit altem Preis |
| P6 (neu) | Jeder Schritt ist nur einmal erfassbar. Es gibt **keine Korrektur** |
| P7 (neu) | Mitteilung und Zustimmung stehen im **selben Feld** `notified_at`. Die Bedeutung hängt nur an `consent_based` der Version |
| P8 (neu) | Shareomat kann **keine E-Mails versenden**. Belegzustellungen werden nur von Hand erfasst ([invoicing.py:51](../../shareomat/database/invoicing.py#L51), `record_delivery`) |

### 2.3 Rechtliche Punkte aus dem veröffentlichten Vertragstext

Das ist keine Rechtsberatung. Diese Punkte sollte eine Fachperson oder EBL bestätigen.

| Punkt | Vertragstext | Bedeutung für Shareomat |
|---|---|---|
| R1 | §5: Vertrag «auf unbestimmte Zeit abgeschlossen» | Die Probezeit bis 28.02.2027 steht **nicht** im unterschriebenen Text. Das Enddatum in Shareomat stoppt nur die Abrechnung, die Mitgliedschaft läuft rechtlich weiter. **Needs verification:** ob die Probezeit separat vereinbart wurde |
| R2 | §8: Änderungen «mindestens N Monate vor Inkrafttreten **schriftlich** mitgeteilt», Austrittsrecht per Inkrafttreten | Mitteilung pro Teilnehmer mit Datum und Art festhalten. **Needs verification:** ob eine E-Mail als «schriftlich» gilt. §5 nennt für die Auflösung ausdrücklich «E-Mail oder Brief», §8 nicht |
| R3 | Version «mit Zustimmung aller» (in Shareomat ohne Frist) | Im Vertragstext nicht geregelt. *Assumption:* Stimmen alle Gesellschafter zu, gilt die Frist nicht, weil sie nur vor Änderungen ohne Zustimmung schützt. Die Zustimmung muss **pro Teilnehmer belegt** sein. Eine Mitteilung ist **keine** Zustimmung |
| R4 | Art. 19g StromVV: Meldung an den Netzbetreiber (im Code: Bildung 3 Monate, Austritt 1 Monat vorher) | Fristen als Hinweis in der Checkliste. **Needs verification:** Fristen bei EBL |

---

## 3. Architekturentscheid: Mitgliedschaft und Vertragsversion

### 3.1 Die zwei Varianten

**Variante A: Struktur behalten, Zeilen synchron halten.** Jede Schreibfunktion
schreibt Mitgliedschaftsdaten auf alle Zuordnungszeilen des Teilnehmers.

**Variante B: Mitgliedschaft in eine eigene Tabelle.**
* Eine neue Tabelle `memberships` enthält pro Teilnehmer und Mitgliedschaft: Beitritt,
  Austritt, Grund, EBL gemeldet, EBL bestätigt, wirksamer Beginn sowie die
  Paperless-Verweise.
* `participant_contract` behält nur, was zu **einer Version** gehört:
  * Erklärung (ausgestellt, eingefrorener Text),
  * Unterschrift (auf der Zeile der unterschriebenen Version),
  * Mitteilung und Zustimmung zu dieser Version.

### 3.2 Vergleich

| Kriterium | A | B |
|---|---|---|
| Datenkonsistenz | Hängt davon ab, dass **jede** heutige und künftige Schreibfunktion an die Synchronisation denkt (AGENTS.md: «Shotgun-Surgery-Risiko») | Jede Tatsache steht **einmal**. P1/P2 können nicht mehr entstehen |
| Benutzerführung | Gleich, die Checkliste arbeitet pro Teilnehmer | Gleich, und die Checkliste entspricht 1:1 einer Tabellenzeile |
| Wartbarkeit | Synchronisationsregeln (Gruppen M/U/V) bleiben dauerhaft erklärungsbedürftig | Einfaches Modell: Mitgliedschaft / Vertrag / Mitteilung |
| Änderung an der Abrechnung | Keine | `billing_eligibility._membership_problem` liest Beitritt, Austritt und Beginn aus `memberships`, etwa 20 Zeilen. Beweis durch Vergleich pro Tag (§10, A3) |
| Migration | Keine Datenumstellung | Neue Tabelle, befüllt aus den bestehenden Zeilen. Alte Spalten **bleiben** unverändert stehen (AGENTS.md: additiv) und dienen als eingefrorene Historie |
| Aufwand | kleiner | etwa 30–40 % mehr in Datenbank und Tests, gleich viel Oberfläche |
| Risiko heute | gering | gering. 2 Teilnehmer, 1 Version, keine Abrechnungen: Die Migration ist vollständig prüfbar |
| Risiko später | wächst mit jeder neuen Funktion | sinkt, weil das Modell eindeutig ist |
| ADR | nicht zwingend | **ADR-0009** nötig (Mitgliedschaft getrennt von der Vertragsversion) |

### 3.3 Empfehlung: **Variante B**

* **Begründung:** Nur B behebt P1/P2 an der Ursache. Der grössere Umbau ist jetzt am
  billigsten und am sichersten, weil es keine Abrechnungen gibt und die Daten klein
  und widerspruchsfrei sind.
* **Abrechnungsregel:** Die einzige Änderung ist, **woher** drei Daten gelesen werden.
  Die Regel selbst (welche Bedingungen, welche Gründe) bleibt Wort für Wort gleich.

### 3.4 Datenmodell B im Detail

```text
participants (unverändert)
  └─ memberships (NEU)              1 Zeile pro Mitgliedschaft (Wiedereintritt = neue Zeile, ohne Überlappung)
        joined_at, left_at, exit_reason,
        vnb_notified_at, vnb_confirmed_at, effective_start_at,
        vnb_notified_paperless_id/_asn, vnb_confirmed_paperless_id/_asn,
        created_at, updated_at
  └─ participant_contract (bestehend, 1 Zeile pro Teilnehmer und Version)
        Erklärung:    declaration_issued_at, declaration_snapshot, declaration_sha256
        Unterschrift: accepted_at, accepted_version, signed_*, paper_original_location,
                      signed_paperless_id/_asn (NEU)            ← nur auf der Zeile der unterschriebenen Version
        Mitteilung:   notified_at, notice_evidence,
                      notice_method, notice_batch_id, notice_recorded_by/_at (NEU)
        Zustimmung:   consent_at, consent_evidence, consent_paperless_id/_asn (NEU)
        unverändert stehen gelassen, nicht mehr geschrieben/gelesen:
                      joined_at, left_at, effective_*, exit_reason, vnb_* (Historie bis 0.6.12)
```

**Regeln in B:**

| Frage | Antwort |
|---|---|
| Ist X am Tag d Mitglied? | `memberships`: `joined_at ≤ d`, `left_at` leer oder `≥ d`, `effective_start_at` leer oder `≤ d` |
| Hat X unterschrieben? | Eine Zeile von X mit `accepted_at` in einer Version dieser Mitgliedschaft. Gleiches Ergebnis wie heute mit der kopierten Unterschrift |
| Braucht X für Version v eine Mitteilung? | Ja, wenn X die Version v nicht selbst unterschrieben hat und die Mitgliedschaft am Beginn von v noch besteht |
| Braucht X für v eine Zustimmung? | Nur wenn v «mit Zustimmung aller» ist und X v nicht selbst unterschrieben hat. **Abrechnungsrelevant**, wie heute |
| Wann entstehen Zeilen für eine neue Version? | Beim **Veröffentlichen** (Status `published`), nicht beim Entwurf. Für alle, deren Mitgliedschaft am Beginn von v noch besteht. Künftige Austritte werden berücksichtigt |
| Neuer Teilnehmer, während eine Folgeversion angekündigt ist? | Er bekommt Zeilen für die gültige **und** für die angekündigte Version. Die Mitteilung zur Folgeversion ist sofort offen, es sei denn, er unterschreibt direkt die Folgeversion |

### 3.5 Migration auf B (Schema 18)

1. Geprüfte Sicherung vor der Migration (vorhanden seit 0.6.11).
2. `memberships` anlegen und pro Teilnehmer aus seinen Zuordnungszeilen befüllen:
   * **Alle Zeilen gleich:** eine Mitgliedschaft mit diesen Werten. Das ist der Fall
     bei der echten Datenbank.
   * **Widerspruch** zwischen Zeilen (nur möglich, wenn P1 schon zugeschlagen hat):
     Die Werte kommen aus der Zeile der **heute gültigen** Version (das, was die
     Abrechnung heute für die laufenden Tage verwendet). Alle abweichenden Werte
     werden als Befund gespeichert. Die Checkliste zeigt «⚠ Prüfen» mit beiden
     Werten, bis der Betreiber den richtigen Wert bestätigt. **Nichts wird still
     bereinigt.**
3. Die alten Spalten bleiben unverändert. Nichts wird gelöscht oder umbenannt.
4. Beweis vor dem Commit: Abschnitt 10, A1–A4.

---

## 4. Die Checkliste

### 4.1 Aufbau

```text
VERWALTUNG → Checkliste
┌─────────────────────────────────────────────────────────────────────────────┐
│ LEG gesamt                                                         ✓ 6 / ⚠ 1 │
│  ✓ Gemeinschaft, EIC gültig   ✓ LEG-Start 01.11.2026   ✓ LEG-Preis          │
│  ✓ Zahlungsangaben            ✓ Swisseldex verbunden                         │
│  ⚠ Vertrag v4 endet am 28.02.2027 – Folgeversion nötig (Frist 4 Monate)     │
│  ⚠ 2 Messpunkte gelten nur bis 01.11.2027                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ [Alle 2] [Offen 0] [Wartet auf EBL 0] [Prüfen 0]   Suche: [______]          │
│ Abrechnungsbereit für: [November 2026 ▾]            [+ Teilnehmer aufnehmen] │
├──────────────┬────────┬─────────┬─────────┬─────────┬────────────┬──────────┤
│ Teilnehmer   │Stamm-  │Mess-    │Vertrag  │EBL      │Mitteilung/ │Abrech-   │
│              │daten   │punkt    │& Unter- │         │Zustimmung  │nung      │
│              │        │         │schrift  │         │            │Nov. 2026 │
├──────────────┼────────┼─────────┼─────────┼─────────┼────────────┼──────────┤
│ Teilnehmer A │✓       │✓        │✓        │✓        │– nicht nötig│✓ bereit │
│ Teilnehmer B │✓       │✓        │✗ Unter- │⏳ wartet│– nicht nötig│✗ nicht  │
│              │        │         │ schrift │ auf Best.│           │ unterschr.│
└──────────────┴────────┴─────────┴─────────┴─────────┴────────────┴──────────┘
```

* **Sechs Spalten**, keine zwanzig. Jede Spalte fasst zusammengehörige Punkte zusammen.
  Bei ✗ und ⏳ steht der **konkrete fehlende Punkt** in der Zelle.
* Die Spalte **Mitteilung/Zustimmung** erscheint nur, wenn eine Version das verlangt.
* **Ein Klick auf eine Zeile** öffnet die Detailansicht darunter (Abschnitt 4.3).
* **Kleine Bildschirme:** Jede Zeile wird zu einer Karte mit derselben Reihenfolge.
* **Viele Teilnehmer:** Filter, Suche und Zähler. Die Berechnung läuft einmal pro
  Seitenaufruf über alle Teilnehmer, mit demselben Zwischenspeicher wie in der
  Abrechnung.

### 4.2 Zustände (immer Symbol **und** Wort, nicht nur Farbe)

| Symbol | Wort | Bedeutung | Beispiel |
|---|---|---|---|
| ✓ | erledigt | alle Voraussetzungen erfüllt | Unterschrift mit Nachweis |
| ✗ | offen | Angabe oder Handlung fehlt | E-Mail fehlt |
| ⏳ | wartet | erledigt, Antwort einer externen Stelle fehlt | bei EBL gemeldet, Bestätigung fehlt |
| – | nicht nötig | für diesen Teilnehmer oder diese Version nicht relevant | Mitteilung bei normaler Version für jemanden, der sie selbst unterschrieben hat |
| ⚠ | prüfen | Widerspruch oder Fehler | Migrationsbefund, wirksamer Beginn vor dem Beitritt |

### 4.3 Detailansicht eines Teilnehmers (alles auf einer Seite)

Jeder Abschnitt zeigt entweder den erfassten Wert mit **«korrigieren»** oder direkt das
Eingabefeld:

| Abschnitt | Inhalt | Speichert über (bestehende Logik) |
|---|---|---|
| Stammdaten | Name, Adresse, PLZ, Ort, E-Mail | Teilnehmer-Speichern (`update_participant`) |
| Messpunkte | Liste; «Messpunkt hinzufügen» mit vorausgewähltem Teilnehmer | Messpunkt-Anlegen (`create_meter`) |
| Vertrag | Zuordnung (Version, Beitritt per), Erklärung drucken/ausstellen | `assign`, `mark_declaration_issued` |
| Unterschrift | Datum, Art, unterzeichnet von, Nachweis: Upload · Paperless-Nr./ASN · Ablageort Papier | `mark_accepted` (Upload über die bestehende Route) |
| EBL | gemeldet am · bestätigt am + wirksam ab · Paperless-Nr. | neue Mitgliedschafts-Funktionen |
| Mitteilung/Zustimmung | pro betroffener Version: mitgeteilt am, Art, Nachweis · bei Zustimmungsversion zusätzlich Zustimmung erhalten am + Nachweis | neue Funktionen in `participant_contract` |
| Austritt | Austritt per (Monatsende), Grund | Mitgliedschaft |
| Verlauf | Audit-Einträge dieses Teilnehmers | `list_events` |

Die Formulare schicken an die **bestehenden** Endpunkte und springen danach mit einem
Rücksprung-Parameter zur Checkliste zurück. Upload, CSRF, Berechtigungen und
Passwortbestätigung bleiben dieselben.

### 4.4 Drei getrennte Bereiche

| Bereich | Gilt als erfüllt, wenn | Quelle |
|---|---|---|
| **Administrativ vollständig** | Stammdaten inkl. E-Mail (schon heute Pflicht für die Erklärung), Messpunkt mit Rolle im EBL-Format, Zuordnung, Erklärung, Unterschrift mit Nachweis, EBL gemeldet und bestätigt, Mitteilung bzw. Zustimmung zu jeder betroffenen Version | Daten, nichts gespeichert |
| **Abrechnungsbereit für Zeitraum Z** | `EligibilityResolver` gibt den Messpunkt für die Tage in Z frei. Angezeigt wird «bereit», «bereit ab 01.12.» oder «nicht bereit: Grund» (Text der Abrechnung) | **dieselbe Funktion** wie die Abrechnung |
| **Technisch verbunden** | Messwerte für den letzten abgeschlossenen Monat (je nach Datenquelle), Live-Quelle zugeordnet (falls genutzt), Swisseldex-Synchronisation | getrennt angezeigt, hält **nie** administrative Punkte offen |

Kundenportal, Shareomat Reader und externes Webportal (LE-22) sind nicht Teil von
0.6.13.

---

## 5. Keine feste Reihenfolge

| Heute | Neu |
|---|---|
| «Bestätigt» erst nach «gemeldet» (Datenbank) | entfällt |
| EBL-Meldung erst nach der Unterschrift (nur Oberfläche) | entfällt |
| Jeder Schritt nur einmal | Korrigieren möglich (Abschnitt 8) |
| Status = am weitesten fortgeschrittener Schritt | Jeder Punkt hat seinen eigenen Zustand |

**Bleibt**, weil fachlich nötig:
* Beitritt am Monatsersten, Austritt am Monatsletzten, wirksamer Beginn am Monatsersten.
* Unterschriftsdatum nicht in der Zukunft, mindestens ein Nachweis zur Unterschrift.
* Zuordnung nur mit aktivem Messpunkt mit Rolle.
* Erklärung nur, wenn sie unterschriftsreif ist.

**Neu:**
* Wirksamer Beginn nicht vor dem Beitritt.
* Bestätigungsdatum nicht in der Zukunft.

---

## 6. Neue Vertragsversion → Aufgaben entstehen automatisch

Der Ablauf ist wie gewünscht:
1. Neue Version in der Vertragsverwaltung erstellen und veröffentlichen.
2. Beim **Veröffentlichen** (nicht beim Entwurf) erhält jeder betroffene Teilnehmer
   die offene Aufgabe «Vertragsänderung v5 mitteilen». Betroffen ist, wessen
   Mitgliedschaft am Beginn von v5 noch besteht.
3. Bei einer Version «mit Zustimmung aller» kommt zusätzlich «Zustimmung zu v5
   einholen» dazu.
4. Mitgliedschaft, Unterschrift und EBL-Bestätigung bleiben **unverändert**. Niemand
   wird neu aufgenommen.
5. Die Mitteilungen zu v4 bleiben erhalten.

**Behebung P5:**
* Eine befristete Vorversion wird beim Veröffentlichen **nie verlängert**.
* Ist zwischen ihrem Ende und dem neuen Beginn eine Lücke, warnt Shareomat **vor** dem
  Veröffentlichen, etwa «Vom 01.03. bis 31.03.2027 gilt kein Vertrag – keine
  Abrechnung».
* Für LEG-TEST heisst das: Die Anschlussversion ab 01.03.2027 entweder mit Zustimmung
  aller (ohne Frist) veröffentlichen, oder die Lücke bewusst in Kauf nehmen.

---

## 7. Sammelaktionen

### 7.1 Vertragsmitteilung (Versandart A: selbst versendet)

```text
Checkliste → Hinweis «v5 veröffentlicht: 20 Mitteilungen offen» → [Mitteilungen erfassen]
┌──────────────────────────────────────────────────────────────────┐
│ Mitteilung zu Vertrag v5 (gültig ab 01.06.2027)                  │
│ [x] Alle offenen auswählen (20)                                  │
│ [x] Teilnehmer A   [x] Teilnehmer B   [ ] Teilnehmer C  …        │
│ Versandart: (•) E-Mail  ( ) Brief  ( ) persönlich übergeben      │
│ Versanddatum: [01.02.2027]   Nachweis (optional): [Paperless-Nr.]│
│ [Vorschau]  →  «18 Teilnehmer, E-Mail, 01.02.2027» [Übernehmen]  │
└──────────────────────────────────────────────────────────────────┘
```

* **Häkchen sind nur Auswahl.** Gespeichert wird erst mit «Übernehmen» nach der
  Vorschau. Danach zeigt die Checkliste den Zustand aus den gespeicherten Daten.
* **Pro Teilnehmer gespeichert:** Version, Datum, Versandart, Nachweis, Benutzer,
  Zeitpunkt und eine **Sammel-Nummer**, die alle Einträge einer Aktion verbindet.
* **Alles oder nichts:**
  * Erst wird jeder Teilnehmer geprüft, dann alle in **einer** Transaktion gespeichert.
  * Pro Teilnehmer entsteht ein Audit-Eintrag, dazu ein Sammel-Eintrag.
  * Fehlt bei einem etwas (etwa ausgetreten), wird er in der Vorschau angezeigt und
    nicht übernommen.
* **Wiederholung ist sicher:**
  * Bereits erfasste Mitteilungen zu dieser Version werden übersprungen und in der
    Vorschau so angezeigt.
  * Ein Formular lässt sich nur einmal absenden (Einmal-Kennung), ein Doppelklick
    erzeugt also nichts doppelt.
* **Die Frist wird geprüft:** Liegt das Versanddatum weniger als N Monate vor dem
  Beginn, erscheint eine Warnung: «§8 verlangt N Monate».
* **Eine Mitteilung ist nie eine Zustimmung.** Bei einer Zustimmungsversion bleibt die
  Zustimmung offen.

### 7.2 Zustimmung (nur bei Versionen «mit Zustimmung aller»)

* **Grundsätzlich pro Teilnehmer**, mit eigenem Nachweis.
* **Als Sammelaktion nur mit einem gemeinsamen Nachweis**, zum Beispiel einem von allen
  unterschriebenen Protokoll mit Paperless-Nr. (siehe Entscheid E3).

### 7.3 Weitere Sammelaktionen

| Aktion | Sammeln? | Begründung |
|---|---|---|
| EBL gemeldet | ja (gleiches Datum) | Eine Meldung an EBL betrifft oft mehrere Teilnehmer |
| EBL bestätigt | ja (gleiches Datum, gleicher Beginn, ein gemeinsamer Nachweis) | Die Bestätigung kommt oft als ein Schreiben |
| Unterschrift, Messpunkt, Stammdaten, Austritt | **nein** | persönlich bzw. pro Teilnehmer verschieden |
| «Alles als erledigt markieren» | **nein, gibt es nicht** | |

### 7.4 Versandart B: Versand durch Shareomat (nicht in 0.6.13)

Shareomat kann heute keine E-Mails versenden (P8). Dafür bräuchte es:
* SMTP-Zugang in den Add-on-Optionen (nie in der Datenbank), TLS mit
  Zertifikatsprüfung.
* Einen Postausgang mit einer Kennung pro Version und Teilnehmer, damit nie doppelt
  versendet wird.
* Einen Status pro Empfänger und eine bewusste Freigabe durch den Betreiber.

Als «mitgeteilt» gilt erst die erfolgreiche Übergabe an den Mailserver; eine Zustellung
lässt sich nicht beweisen. Vorher ist R2 zu klären: Gilt eine E-Mail als «schriftlich»?

**Empfehlung:** ein eigener späterer Schritt, gemeinsam mit dem E-Mail-Versand der Belege
(steht schon in TODO). Das Datenmodell von 0.6.13 sieht Versandart und Status bereits
vor, damit später nichts umgebaut werden muss.

---

## 8. Korrekturen

| Art | Felder | Regel |
|---|---|---|
| **Verwaltung** | Erklärung ausgestellt, EBL gemeldet, Nachweise (Upload, Papier, Paperless), unterzeichnet von, Art der Zustimmung, Mitteilung (Datum/Art/Nachweis) bei normaler Version, Austrittsgrund | jederzeit korrigierbar |
| **abrechnungsrelevant** | Beitritt, Austritt, wirksamer Beginn, Unterschrift ja/nein, Zustimmung bei Zustimmungsversion, EBL bestätigt (steuert den Prüfhinweis) | frei, solange keine freigegebene Abrechnung den Zeitraum ab dem betroffenen Datum umfasst. Sonst nur mit Begründung |

Für beide Arten gilt:
* Das Audit-Log hält immer alten Wert, neuen Wert, Benutzer, Zeitpunkt und
  gegebenenfalls die Begründung fest.
* **Ist eine freigegebene Abrechnung betroffen,** gilt zusätzlich:
  * Sie bleibt unverändert (ADR-0001).
  * Shareomat berechnet den Fingerabdruck der betroffenen Läufe neu, wie bei den
    SDAT-Korrekturen, und zeigt bei einer Abweichung «Korrekturabrechnung prüfen».
* Rein administrative Korrekturen kommen ohne Begründung und ohne Rückfrage aus.

---

## 9. Paperless-ngx (optional)

* **Konfiguration:** Add-on-Option `paperless_url`. Leer bedeutet: keine
  Paperless-Felder.
* **Prüfung der Adresse:**
  * `http` oder `https`, Host vorhanden,
  * keine Zugangsdaten in der URL, kein Query, kein Fragment.
* **Pro Nachweis:** Dokument-Nr. (positive Ganzzahl) und optional die ASN.
* **Link:** `<url>/documents/<Nr>/details`, öffnet in neuem Tab mit `noopener`.
  Shareomat ruft Paperless **nie** auf: kein Token, kein Abruf.
* **Paperless ist ein dritter, gleichwertiger Nachweis** neben Upload und Papier,
  freiwillig. Bestehende Uploads und Ablageorte bleiben unverändert.
* **Needs verification:** Ob eine HA-Ingress-Adresse von Paperless dauerhaft
  funktioniert. Sonst die direkte LAN-Adresse eintragen.

---

## 10. Tests und Nachweis «nichts kaputt»

**A. Vorher/nachher (Pflicht vor dem Commit)**

| # | Test |
|---|---|
| A1 | Echte Datenbank (Kopie aus der Sicherung) und eine künstliche Datenbank mit allen Stadien migrieren. Die künstliche enthält: Papier, Upload, gemeldet ohne Bestätigung, bestätigt, ausgetreten, Versionswechsel normal und «mit Zustimmung aller», zurückgezogene Version, absichtlicher P1-Widerspruch, künstliche freigegebene Abrechnung |
| A2 | Alle bisherigen Tabellen und Spalten byte-gleich. Sicherung = Altstand. Audit-Log nur ergänzt |
| A3 | Für jeden Tag über 24 Monate: `EligibilityResolver` alt gegen neu, mit gleichen Messpunkten, gleichen Ausschlüssen und gleichen Gründen. **Einzige erlaubte Abweichung:** der absichtliche P1-Widerspruch, der dann als «⚠ Prüfen» erscheinen muss |
| A4 | Fingerabdruck der künstlichen freigegebenen Abrechnung identisch. Volle Testsuite grün |

**B. Fachtests**

| Bereich | Tests |
|---|---|
| Reihenfolge | alle Reihenfolgen der Schritte (Permutationen); Unterschrift vor und nach der EBL-Meldung; EBL-Bestätigung ohne Meldung; Beginn vor dem Beitritt abgewiesen |
| Vertragsversionen | Veröffentlichen öffnet Mitteilungen nur für Betroffene, auch bei künftigem Austritt; Entwurf öffnet nichts; neue Teilnehmer bei angekündigter Folgeversion; Austritt wirkt über Versionen; Zustimmung ≠ Mitteilung; befristete Vorversion wird nicht verlängert (P5), Warnung bei Lücke; Detailseite zeigt die unterschriebene Erklärung (P4) |
| Sammelaktionen | einzelne/alle auswählen, Auswahl ändern, Vorschau, Übernehmen; pro Teilnehmer gespeichert und auditiert; Ausgetretene werden übersprungen; Wiederholung erzeugt nichts doppelt; ein Fehler führt zu nichts gespeichert; Fristwarnung |
| Korrekturen | Verwaltung frei; abrechnungsrelevant ohne Freigabe frei; mit Freigabe nur mit Begründung + Hinweis; alt → neu im Audit |
| Paperless | gültige und ungültige Nummern und URLs (Zugangsdaten, `javascript:`, Query); leer bedeutet ausgeblendet |
| Oberfläche | jeder offene Punkt führt zum richtigen Feld; Status aktualisiert sich nach dem Speichern; Berechtigungen und Passwortbestätigung unverändert; Checkliste schreibt nichts (`data_version` gleich); 200 Teilnehmer in vertretbarer Zeit |
| Mehrere Messpunkte | gemeinsamer Beginn; später hinzugefügter Messpunkt erst ab `valid_from` |

---

## 11. Szenarien: heute und neu

Gezählt werden **Menüwechsel** (andere Seite über das Menü) und **Bedienaktionen**
(Klick auf Knopf oder Link, Formular absenden). Ausfüllen von Feldern zählt nicht.

### A – Neuer Teilnehmer

| | Heute | Neu |
|---|---|---|
| Ablauf | 1. Menü *Teilnehmer* → «Neu» → speichern · 2. Menü *Messpunkte* → «Neu» → Teilnehmer wählen → speichern · 3. Menü *Vertrag* → nach unten scrollen → Teilnehmer wählen → «hinzufügen» · 4. «Details» → Erklärung drucken → «ausgestellt» · später je: Menü *Vertrag* → Zeile suchen → «Details» → Unterschrift · EBL-Meldung · EBL-Bestätigung | 1. Menü *Checkliste* → «Teilnehmer aufnehmen» → Stammdaten → speichern · 2. Im selben Detailbereich: Messpunkt → speichern · Vertrag zuordnen → Erklärung drucken · später: Checkliste → Filter «Offen» → Zeile → Punkt erfassen |
| Menüwechsel | **6** (3 beim Erfassen, 3 später) | **1**, später je 1 (die Checkliste ist die Startseite der Verwaltung) |
| Bedienaktionen | ≈ 17 | ≈ 11 |
| Selbst wissen/suchen | Reihenfolge Teilnehmer → Messpunkt → Vertrag (sonst Fehlermeldung «zuerst unter Messpunkte zuweisen»); dass die E-Mail für die Erklärung Pflicht ist (Fehler erst beim Ausstellen); dass EBL-Schritte unter «Details» liegen; dass die EBL-Meldung erst nach der Unterschrift erscheint | Nichts: Die offenen Punkte stehen da, mit Grund |

### B – EBL-Bestätigung kommt Wochen später

| | Heute | Neu |
|---|---|---|
| Ablauf | Menü *Vertrag* → in der Versionstabelle die Teilnehmer mit «beim Netzbetreiber gemeldet» suchen → «Details» → Bestätigung + Beginn → speichern. Bei 5 Teilnehmern 5-mal | Menü *Checkliste* → Filter «Wartet auf EBL» (zeigt nur diese) → «Sammel: EBL bestätigt» → auswählen → Datum, Beginn, Paperless-Nr. → Vorschau → Übernehmen |
| Menüwechsel | 1 | 1 |
| Bedienaktionen (5 Teilnehmer) | ≈ 15 | ≈ 5 |
| Erkennbar, wer fehlt? | nur über den Status-Text in der Tabelle | Zähler, Filter und ⏳ pro Zeile |

### C – Neue Vertragsversion, 20 Teilnehmer benachrichtigen

| | Heute | Neu |
|---|---|---|
| Ablauf | Version veröffentlichen → im neuen Versionsblock 20 Zeilen «⏳ Mitteilung ausstehend» → je: «Details» → Datum + Nachweis-Text → speichern → zurück | Version veröffentlichen → Hinweis «20 Mitteilungen offen» → «Mitteilungen erfassen» → «Alle auswählen» → Versandart, Datum, Nachweis → Vorschau → Übernehmen |
| Menüwechsel | 1 (+ 20 Seitenwechsel Details/zurück) | 1 |
| Bedienaktionen | ≈ 61 (20 × 3 + 1) | ≈ 5 |
| Nachvollziehbarkeit | Freitext pro Teilnehmer | pro Teilnehmer: Version, Datum, Art, Nachweis, Benutzer, Zeitpunkt, Sammel-Nummer |

### D – Falsches Datum korrigieren

| | Heute | Neu |
|---|---|---|
| Ablauf | **Nicht möglich**, jeder Schritt ist nur einmal erfassbar. Es bleibt höchstens Zurückziehen/Archivieren der ganzen Version | Menü *Checkliste* → Teilnehmer suchen → Zeile → beim Wert «korrigieren» → neuer Wert (+ Begründung, falls eine freigegebene Abrechnung betroffen ist) → speichern |
| Menüwechsel | – | 1 |
| Bedienaktionen | – | ≈ 4 |
| Nachvollziehbar | – | Audit: alt → neu, Benutzer, Zeit, Grund; bei Bedarf Hinweis «Korrekturabrechnung prüfen» |

---

## 12. Umsetzung in Teilschritten (jeder für sich geprüft)

| Schritt | Inhalt | Sichtbar für den Betreiber |
|---|---|---|
| T1 | ADR-0009; Schema 18 (`memberships`, neue Spalten); Migration mit Befund-Erkennung; Abrechnung liest Mitgliedschaft aus `memberships`; **Beweis A1–A4** | nichts (bewusst) |
| T2 | Schreibfunktionen pro Teilnehmer; Korrekturen mit Audit und Freigabe-Prüfung; Reihenfolge-Sperren weg; P4- und P5-Behebung; Veröffentlichen öffnet Mitteilungsaufgaben; Mitteilung und Zustimmung getrennt | Vertragsseite: Korrigieren möglich, keine feste Reihenfolge |
| T3 | Checkliste nur lesend: LEG gesamt mit Fristwarnungen, Tabelle, Filter, Suche, drei Bereiche | neue Seite |
| T4 | Bearbeiten direkt aus der Detailansicht (bestehende Endpunkte + Rücksprung) | Arbeiten ohne Menüwechsel |
| T5 | Sammelaktionen: Mitteilung, Zustimmung (nach E3), EBL gemeldet/bestätigt | Szenarien B und C |
| T6 | Paperless | Links zu Nachweisen |
| T7 | Doku (ARCHITECTURE, Docstrings, Benutzerhinweise), Abschluss-Testlauf, Versionssprung 0.6.13 | Update |

Nach T1 und nach T5 zeige ich dir den Stand, bevor es weitergeht.

---

## 13. Zu entscheiden

| # | Frage | Vorschlag |
|---|---|---|
| E1 | Variante A oder **B** (Mitgliedschaft getrennt)? | **B** |
| E2 | Widerspruch bei der Migration: Werte der heute gültigen Version + «⚠ Prüfen» | ja |
| E3 | Zustimmung als Sammelaktion nur mit gemeinsamem Nachweis (z. B. unterschriebenes Protokoll)? | ja |
| E4 | Automatischer E-Mail-Versand: eigener späterer Schritt, 0.6.13 nur «selbst versendet» | ja |
| E5 | Befristete Vorversion beim Veröffentlichen nie verlängern, stattdessen Lücken-Warnung (P5) | ja |
| E6 | Commit-Rhythmus: jeden Teilschritt lokal committen, **Push und Version 0.6.13 erst am Schluss**? Oder alles in einem Commit? | lokal pro Teilschritt, Push am Schluss |
| E7 | Aus dem ersten Konzept: E-Mail ist Pflicht (bereits heute für die Erklärung); Messdaten nur unter «technisch»; Häkchen vor einer Freigabe ohne Begründung entfernbar; Paperless nur Nummern | wie vorgeschlagen |
| E8 | R1: Die Probezeit steht nicht im Vertragstext. Soll die Checkliste das Vertragsende als «⚠ Prüfen» mit diesem Hinweis anzeigen? | ja |

---

## 14. Umsetzungsstand 0.6.13

### Entscheide des Betreibers (16.11.2026)

E1 Variante B · E2 keine stille Bereinigung, betroffene Abrechnungen bleiben gesperrt ·
E3 Sammelzustimmung nur mit gemeinsamem Nachweis der ausdrücklichen Zustimmung jedes
Teilnehmers · E4 automatischer E-Mail-Versand später · E5 keine Verlängerung befristeter
Versionen, Lücken anzeigen · E6 lokale Commits pro Teilschritt, Push am Schluss ·
E7 wie vorgeschlagen · E8 Warnhinweis Befristung ↔ Vertragstext, keine automatische
Änderung. Ergänzung: Wiedereintritte berücksichtigen; alte Spalten dürfen entfallen,
wenn Migration und Umstellung nachgewiesen sind.

### Umgesetzt

| Schritt | Ergebnis |
|---|---|
| T1 | Schema 18, ADR-0009, Migration mit Prüfung jedes Werts und Rückrollen, Prüfhinweise mit Abrechnungssperre, Wiedereintritt (`UNIQUE(membership_id, contract_version_id)`) |
| T2 | keine feste Reihenfolge, neue Plausibilitätsprüfungen, Mitteilung ≠ Zustimmung (eigene Felder und Formulare), Korrekturen mit alt → neu und Begründungspflicht bei freigegebenen Abrechnungen, Unterschrift entfernen, P4 (Detailseite zeigt die unterschriebene Erklärung), P5 (keine Verlängerung, Lücke nur mit Bestätigung, Rückzug stellt das alte Ende wieder her), neue Teilnehmer erhalten angekündigte Versionen automatisch |
| T3/T4 | `VERWALTUNG → Checkliste` mit LEG gesamt (inkl. Vertragsende-Warnung nach E8 und Messpunkt-Fristen), sechs Spalten, Filter, Suche, Monat, Detailansicht mit allen Formularen (bestehende Endpunkte, Rücksprung zur Checkliste) |
| T5 | Sammelaktionen Mitteilung, Zustimmung (mit gemeinsamem Nachweis und Bestätigung), EBL gemeldet, EBL bestätigt: Auswahl → Vorschau → Übernehmen, Einmal-Kennung, eine Transaktion, Sammel-Nr. pro Eintrag und im Audit-Log |
| T6 | Paperless: Add-on-Option `paperless_url` (geprüft), Dokument-Nr./ASN bei Unterschrift, Meldung, Bestätigung, Mitteilung, Zustimmung |
| T7 | ARCHITECTURE.md, Docstrings, dieses Dokument, Tests |

### Abweichungen vom Konzept

* Die verschobenen Spalten (`joined_at`, `left_at`, `effective_*`, `exit_reason`,
  `vnb_*`) wurden aus `participant_contract` **entfernt** statt stehen gelassen (vom
  Betreiber erlaubt). Der alte Stand liegt unverändert in der Sicherung vor der
  Migration.
* Paperless-Felder gibt es zusätzlich für Mitteilung und Zustimmung.
* Beim Zurückziehen einer Version zählen EBL-Meldungen nur noch als Nachweis, wenn die
  Mitgliedschaft allein durch diese Version besteht (siehe ADR-0009, Konsequenzen).

### Offen

* Automatischer Versand von Mitteilungen (E4, zusammen mit dem E-Mail-Versand der Belege).
* R1–R4 (Abschnitt 2.3) bleiben rechtlich zu klären.
* Historisierung von Messpunkt-Zuordnung und `active` (P3).
* Zurückspielen einer Sicherung (TODO).

---

## Anhang A – Codebefunde im Detail (aus dem Prüfbericht vom 15.11.2026)

**P1, nachgestellt auf einer Wegwerf-Datenbank:** v1 ab 01.01.2026; v2 veröffentlicht am
15.11.2026 mit Beginn 01.06.2027; danach in v1 erfasst:

| Erfasst in v1 | Ergebnis an einem Tag in v2 |
|---|---|
| p1 Austritt per 31.01.2027 | p1 wird abgerechnet (`left_at` in v2 leer) |
| p3 Unterschrift | p3 «Beitritt nicht unterschrieben» |
| p2 EBL bestätigt, wirksam ab 01.02.2027 | p2 in v2 «nicht bestätigt», Beginn leer |

**Was `publish_version()` kopiert** ([contract_versions.py:531-546](../../shareomat/database/contract_versions.py#L531-L546)):

| Feld | Verhalten |
|---|---|
| Beitritt, Austritt, Unterschrift, EBL, Beginn, `declaration_issued_at` | kopiert |
| `notified_at` | geleert (gewollt) |
| `paper_original_location`, `declaration_snapshot`, `declaration_sha256`, `notice_evidence` | nicht kopiert |

**Abrechnungsberechtigung** ([billing_eligibility.py:121-139](../../shareomat/database/billing_eligibility.py#L121-L139)):

* Messpunkt gültig und aktiv; Teilnehmer aktiv (beides ohne Datum, P3).
* Vertrag in Kraft; nach dem LEG-Start.
* Zugeordnet; **unterschrieben (nur ob, nicht wann)**; im Zeitraum Beitritt/Austritt;
  nach dem wirksamen Beginn.
* Bei einer Zustimmungsversion: Zustimmung erfasst.

Eine fehlende EBL-Bestätigung ist nur ein Prüfhinweis. Wird trotzdem freigegeben,
gibt es keine Rechnungen.

**Weitere Leser von `participant_contract`:**
* Nur [server.py:512](../../shareomat/web/server.py#L512) (Upload-Referenz).
* Rechnungen lesen keine Zuordnungen.

**Wiederverwendbar:**
* `record_event` (Audit in derselben Transaktion),
* `_guard_reassignment` (Muster «in freigegebener Abrechnung»),
* `released_runs_needing_review` (Fingerabdruck-Vergleich),
* `EligibilityResolver`,
* Upload-Route und Passwortbestätigung.
