# ADR-0011: E-Mail-Versand der Belege mit PDF aus der eingefrorenen Fassung

## Status

Vorgeschlagen (23.11.2026). Gebaut, aber erst nach dem Test am echten Add-on (Test-E-Mail,
Muster-PDF scannen) im Einsatz.

## Kontext

* Bisher erzeugte Shareomat Belege nur als Druckseite. Das PDF entstand im Browser
  («Drucken → als PDF speichern»), versendet wurde von Hand, und die Zustellung wurde danach
  erfasst. Die E-Mail-Vorlagen gab es seit 0.6.16, aber keinen Versand.
* Jeder Beleg ist bei der Ausstellung eingefroren (HTML und SHA-256, siehe
  `models/invoicing.py`). Diese Fassung ist der Beleg. Ein zweites Layout nur für das PDF
  würde früher oder später davon abweichen.
* Für den QR-Zahlteil sind nur die Schriften Arial, Frutiger, Helvetica und Liberation Sans
  zulässig (SIX «Swiss Implementation Guidelines QR-bill» 2.3, Kap. 3.4; ebenso Style Guide
  QR-bill, S. 5). Die Projektspezifikation sieht den QR-Zahlteil im PDF einer E-Mail-Rechnung
  vor (`docs/Spezification/…`, Kap. 11.4). Verboten ist nach dem QR-Auftrag des Betreibers nur
  ein QR-Code, der bloss eine E-Mail-Adresse enthält.

## Entscheidung

1. **PDF aus der eingefrorenen Druckseite.** WeasyPrint 70.0 (fest gepinnt) erzeugt das PDF aus
   derselben Seite wie «Drucken». Es nimmt den gespeicherten Beleg und die SHA-256-Fusszeile,
   ohne Druckknopf. Es wird nichts neu gerechnet oder gerendert.
   * Beim Erzeugen wird nichts geladen. Jede URL im Beleg (http, https, file, data) bricht ab.
     So kommt keine lokale Datei ins PDF, und keine Anfrage verlässt das Add-on.
   * Das Add-on-Image installiert die Pango-Bibliotheken (laut WeasyPrint-Dokumentation für
     Alpine) und als einzige Schrift Liberation Sans (`font-liberation`). Damit landet auch
     jeder Ersatz für «Arial» oder «sans-serif» auf einer zulässigen Schrift.
   * Ohne WeasyPrint/Pango läuft Shareomat weiter. Dann sind nur PDF und Versand gesperrt, mit
     einer klaren Meldung.
2. **Postfach in der Add-on-Konfiguration** (Abschnitt `smtp`), wie IMAP und Swisseldex, nie in
   der Datenbank.
   * Verschlüsselt wird immer: ssl (465) oder starttls (587), jeweils mit Prüfung von Zertifikat
     und Servername. Einen unverschlüsselten Modus gibt es nicht.
   * Ein fehlerhafter Abschnitt verhindert den Start nicht, der Versand bleibt dann aus.
   * Vorgesehen ist ein eigenes Postfach der Gemeinschaft, bei Anbietern mit Zwei-Faktor-Anmeldung
     mit App-Passwort (Entscheid des Betreibers, 23.11.2026).
3. **Gesendet wird nur auf Knopfdruck**, nach einer Vorschau mit Empfänger, Betreff, Text und
   PDF und nach dem Häkchen «geprüft». Es gibt keinen automatischen Versand.
4. **Umfang** (Entscheid des Betreibers, 23.11.2026): Rechnung, Gutschrift, Stornobeleg,
   Zahlungserinnerung/Mahnung und die Willkommens-E-Mail mit dem Vertragspaket
   (Beitrittserklärung und vollständiger Gesellschaftsvertrag).
5. **Empfänger:**
   * Bei Belegen gilt die E-Mail-Adresse, die beim Erstellen mit dem Beleg gespeichert wurde.
   * Bei der Willkommens-E-Mail gilt die aktuelle Adresse des Teilnehmers.
   * Es gibt immer genau einen Empfänger, kein Cc und kein Bcc. Die Kopie liegt im Postfach des
     Absenders unter «Gesendet» (Entscheid des Betreibers: keine Bcc-Kopie).
   * Adressen werden streng geprüft. Betreff und Namen müssen einzeilig sein
     (Header-Injection).
6. **Protokoll:**
   * Unmittelbar vor der Übergabe an den Server schreibt Shareomat einen Eintrag «Versand
     begonnen» (`attempt`). Danach folgt «gesendet» (mit Message-ID, SHA-256 des PDFs und des
     Belegs) oder «fehlgeschlagen».
   * Bei Belegen stehen die Einträge im Versandprotokoll (`document_deliveries`, neue Spalte
     `sent_by`: `manual` | `shareomat`). Bei der Willkommens-E-Mail stehen sie im Audit-Log der
     Zuordnung.
   * Das Ergebnis eines schon übergebenen Versands wird immer protokolliert. Das gilt auch, wenn
     inzwischen etwa eine Zahlung gebucht wurde.
7. **Ergebnis unklar:**
   * Bricht die Verbindung nach der Übergabe ab oder stürzt Shareomat ab, bleibt «Versand
     begonnen» der letzte Eintrag. Der Beleg zeigt dann «Ergebnis unklar».
   * Erneut gesendet wird erst, wenn jemand im Postfach unter «Gesendet» nachgesehen hat. Ist
     die E-Mail dort, wird die Zustellung erfasst. Sonst wird «Erneut senden» angekreuzt.
8. **Kein zweiter Anspruch:**
   * Ein schon zugestellter Beleg geht nur mit «Erneut senden» noch einmal hinaus. Das ergibt
     nur einen weiteren Protokolleintrag, nie eine neue Forderung.
   * Es wird immer nur eine E-Mail auf einmal gesendet (Sperre). Ein Doppelklick sendet einmal.
   * Der Sammelversand hält beim ersten Fehler an.
9. **Test senden** (E-Mail-Vorlagen): an eine frei eingetragene Adresse (Wunsch des Betreibers,
   23.11.2026).
   * Gewählt wird eine Muster-Rechnung, eine Muster-Gutschrift oder ein reiner Verbindungstest.
   * Das Muster läuft wie ein echter Beleg: gespeicherte Vorlage, Briefkopf aus dem Vertrag,
     Bankkonto mit QR-Zahlteil, PDF. Nur der Empfänger ist die fiktive Person Max Muster,
     Musterweg 24, 9999 Musterdorf.
   * Gekennzeichnet ist es dreifach: Betreff «[TEST]», Stempel «MUSTER – keine Rechnung, nicht
     bezahlen» im PDF und QR-Mitteilung «MUSTER - nicht bezahlen».
   * Es wird nie als Beleg gespeichert, und die Belegnummern R-MUSTER bzw. G-MUSTER stammen
     nicht aus der echten Nummernfolge.
   * Höchstens 20 Test-E-Mails pro Stunde, damit das Formular kein Massenversand wird. Jede steht
     mit Adresse im Audit-Log.
10. **Willkommens-E-Mail:**
   * Sie geht nur, solange die Beitrittserklärung unterschriftsreif und noch nicht
     unterschrieben ist.
   * Nach dem Senden gilt die Erklärung als ausgestellt, eingefroren in genau der gesendeten
     Fassung.
11. **Vertragsmitteilung** (Wunsch des Betreibers, 26.11.2026; ersetzt das reine Erfassen in der
   Checkliste):
   * Checkliste → «Mitteilungen senden»: Teilnehmer, Versandart (E-Mail, Brief, persönlich
     übergeben) und Versanddatum wählen, dann «Vorschau» oder «Senden». Kein Nachweis-Feld:
     den Nachweis schreibt Shareomat selbst.
   * Die Vorschau zeigt pro Teilnehmer die E-Mail (Empfänger, Betreff, Text der Vorlage
     «Vertragsmitteilung») mit dem Gesellschaftsvertrag der neuen Version als PDF-Anhang, dazu
     «Test an mich senden» (an eine frei eingetragene Adresse, mit «[TEST]», nichts eingetragen).
   * E-Mail: Vor der Übergabe wird die Mitteilung abgelegt, danach das Ergebnis. **Erst eine
     gesendete E-Mail trägt die Mitteilung ein**, in derselben Transaktion, mit Message-ID als
     Nachweis. Bei «Ergebnis unklar» geht an diesen Teilnehmer keine zweite E-Mail, bis unter
     Dokumente geklärt ist, ob sie im Postfach unter «Gesendet» liegt. Eine E-Mail geht immer
     heute hinaus.
   * Brief / persönlich: Shareomat erstellt das Schreiben (Text der Vorlage über dem Vertrag) zum
     Ausdrucken und trägt die Mitteilung mit dem gewählten Datum ein, beides oder nichts.
   * Abgelegt unter Dokumente → «Meldungen & Mitteilungen» (Tabelle `contract_notices`). Der
     Vertragstext wird nicht kopiert: Er ist der veröffentlichte, unveränderliche Text der
     Version und wird über seine Prüfsumme kontrolliert, bevor ein PDF erneut erzeugt wird.
   * Wer eine Mitteilung braucht, bestimmen dieselben Regeln wie beim Erfassen von Hand
     (Gesamtkonzept 0.6.13, Abschnitt 3.4). Eine Mitteilung ist keine Zustimmung.
   * **Pro Teilnehmer** (Checkliste → Teilnehmer → «Mitteilung / Zustimmung», ebenso die
     Vertragsseite des Teilnehmers) derselbe Ablauf für diesen einen Teilnehmer: Versandart,
     Versanddatum, «Vorschau» oder «Senden». Das Formular zum Erfassen von Hand (mit Nachweis-
     und Paperless-Feldern) gibt es dort nicht mehr (Nachtrag 26.11.2026).
   * Eine eingetragene Mitteilung zeigt «E-Mail ansehen» bzw. «Schreiben ansehen», wenn
     Shareomat sie erzeugt hat, sonst «von Hand eingetragen, kein Dokument in Shareomat».
     «Mitteilung zurücknehmen» entfernt nur den Eintrag (als Korrektur im Verlauf); das
     Dokument bleibt unter Dokumente, und die Mitteilung kann neu gesendet werden. Datum und
     Versandart werden nicht mehr direkt geändert: zurücknehmen und neu senden. Die Zustimmung
     wird weiterhin von Hand erfasst und korrigiert.

## Was nicht vollständig gesichert ist

* **Darstellung:** WeasyPrint stellt manche CSS-Regeln anders dar als ein Browser (Flexbox im
  Zahlteil). Needs verification: Muster-PDF aus dem echten Add-on öffnen, im
  SIX-Validierungsportal prüfen und mit einer Banking-App scannen, bevor echte Belege
  hinausgehen.
* **Schriftzuordnung:** Assumption: fontconfig ordnet «Arial»/«Helvetica» Liberation Sans zu.
  Weil keine andere Schrift installiert ist, gibt es keinen anderen Ersatz. Needs verification
  im Muster-PDF.
* **Versandgrenzen des Anbieters:** Assumption: Für eine kleine Gemeinschaft genügen die
  Versandgrenzen üblicher Postfach-Anbieter. Die Grenzen selbst sind nicht geprüft.
* **Zeitpunkt «Ergebnis unklar»:** Zeiten im Protokoll sind wie überall in Shareomat in UTC
  gespeichert.
* **Form der Vertragsmitteilung:** Needs verification: ob eine E-Mail als «schriftlich» im Sinn
  von §8 des Gesellschaftsvertrags gilt (Gesamtkonzept 0.6.13, R2). Shareomat bietet E-Mail und
  Brief an und entscheidet das nicht.

## Konsequenzen

* Das Add-on-Image wird grösser (Pango, Schrift, WeasyPrint). Alle Python-Pakete gibt es als
  fertige musl-Wheels für amd64 und aarch64; im Image wird nichts kompiliert (geprüft
  23.11.2026 mit `pip download --platform musllinux_1_2_*`).
* Der manuelle Weg (drucken, selbst senden, Zustellung erfassen) bleibt unverändert.
* Belege, Beträge und Abrechnungslogik ändern sich nicht.

## Betroffene Dateien

* `shareomat/core/delivery/pdf.py`, `mail.py`, `document_mail.py`, `welcome_mail.py`, `notice_mail.py` (neu)
* `shareomat/database/contract_notices.py`, `shareomat/models/contract_notice.py` (neu, Vertragsmitteilungen),
  `shareomat/database/participant_contract.py` (`mark_notified_in`)
* `shareomat/web/pages/checklist.py`, `documents.py` und `web/templates/documents/`
* `shareomat/config.py` (`SmtpConfig`), `ha_addon/config.yaml`, `ha_addon/generate_runtime_config.py`,
  `ha_addon/translations/*.yaml`, `config/leg_config.example.yaml`
* `shareomat/core/email_templates.py` (Vorlagen Stornobeleg und Mahnung)
* `shareomat/database/invoicing.py`, `shareomat/database/sqlite.py`, `shareomat/models/invoicing.py`
  (Versandprotokoll)
* `shareomat/web/pages/invoices.py`, `contract.py`, `email_templates.py`, `shareomat/web/server.py`
  und die Vorlagen unter `web/templates/invoices/`, `contract/`, `email_templates/`, `checklist/`
* `Dockerfile`, `ha_addon/Dockerfile`, `requirements.txt`, `ha_addon/requirements.txt`
* Tests: `tests/test_email_sending.py`, `tests/test_welcome_mail.py`, `tests/test_contract_notice_mail.py`
