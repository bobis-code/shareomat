# Sichere E-Mail-Anhänge und Flash-Nachrichten (S-01 / S-02)

## S-01: Dateispeicherung

Der E-Mail-Importer verwendete MIME-Dateinamen direkt als Zielpfad. Dadurch
konnten relative und absolute Namen aus der Inbox ausbrechen. Die bisherige
UID-Präfixierung bei Kollisionen verhinderte weder Verzeichnisausbruch noch
Überschreiben bei wiederholten Namen oder parallelen Schreibzugriffen.

`core/collector/leg_email_importer.py::_save_attachment()` verwendet jetzt nur
die erlaubte Endung `.csv`, `.xml` oder `.xlsx` aus dem MIME-Namen. Der Name der
gespeicherten Datei lautet `email-<UID>-<128-Bit-Zufallswert>.<Endung>`. Der
Originalname wird nicht als Pfad oder als HTML übernommen und nicht zusätzlich
persistiert.

Der Inhalt wird zuerst in einem privaten temporären Unterverzeichnis der Inbox
vollständig geschrieben. Der Inbox-Scanner liest keine Unterverzeichnisse. Die
fertige Datei wird mit `os.link()` unter ihrem endgültigen Namen veröffentlicht.
Diese Operation überschreibt keine bestehenden Dateien oder Symlinks und macht
keine teilweise geschriebene Datei sichtbar. Kollisionen führen zu einem neuen
Zufallsnamen; nach 32 erfolglosen Versuchen wird kontrolliert abgebrochen. Auch
bei Fehlern wird das Staging-Verzeichnis bereinigt. Es gibt keinen unsicheren
Fallback auf Überschreiben.

Inbox und Elternpfade werden auf symbolische Verweise/Junctions geprüft; auch
die Identität des Inbox-Verzeichnisses wird vor der Veröffentlichung nochmals
geprüft. Die Betriebssystemrechte müssen das Verzeichnis und seine Eltern gegen
unbefugten Austausch schützen. Dies ist kein Schutz gegen einen lokalen Angreifer
mit denselben Prozessrechten oder Administratorrechten, der während einzelner
Dateisystemoperationen beliebige Verzeichnisse ersetzen kann.

Das Dateisystem muss die exklusive Hardlink-Veröffentlichung unterstützen.
Andernfalls wird ein Speicherfehler protokolliert und die Nachricht bleibt zur
Wiederholung vorgemerkt. Symlink-/Junction-basierte Inbox-Pfade werden bewusst
nicht mehr akzeptiert; bei solchen Installationen ist ein echter, geschützter
Inbox-Pfad zu konfigurieren.

## Schreibfehler und Wiederanlauf

Ein Speicherfehler beendet nicht mehr den gesamten Importthread. Die betreffende
Nachrichten-UID wird in `email_import_state.json` als `pending_uids` vorgemerkt;
spätere Nachrichten werden weiterhin verarbeitet. Nach Ablauf der gespeicherten
Wartezeit versucht ein Poll offene UIDs zusätzlich zu neuen UIDs. Erst nach erfolgreicher Verarbeitung wird
eine UID aus der Wiederholungsliste entfernt. Alte Zustandsdateien mit lediglich
`last_uid` bleiben lesbar. Keine Datenbankmigration oder neue Konfiguration nötig.

Erfolgreich gespeicherte Anhänge werden inzwischen je Nachricht einzeln
checkpointed. Normale Teilfehler führen dadurch nicht zur erneuten Speicherung
bereits erfolgreicher Anhänge. Ein Absturz oder Zustands-Schreibfehler zwischen
Dateiveröffentlichung und Checkpoint kann weiterhin eine Wiederholung verursachen.
Die bestehende SHA-256-Verarbeitungsprüfung und F-01-Prüfung bleiben unverändert;
eine Exactly-once-Transaktion über Dateisystem und IMAP-Zustand wird nicht eingeführt.

Die anschliessende S-03-/S-04-Härtung ergänzt Ressourcenlimits, UIDVALIDITY und
sichtbare Hinweise zur Vertrauensgrenze. Aktuelle Betriebsanforderungen:
[IMAP-Vertrauen und Ressourcenlimits](imap-import-hardening.md).

## S-02: Flash-Nachrichten sind Text

`web/templates/components/messages.html` gab zuvor jede Flash-Nachricht mit
`safe` als HTML aus. Tarifimporte und gespeicherte Produktnamen sowie
FormError-Meldungen konnten dadurch fremde HTML-Elemente einschleusen.

Gewöhnliche Flash-Nachrichten werden jetzt automatisch escaped. Vor der Ausgabe
werden auch eventuell übergebene `Markup`-Objekte in gewöhnlichen Text
umgewandelt; ein solcher Typ umgeht die Regel nicht. Tarifnamen und Fehlertexte
werden nicht anhand bestimmter JavaScript-Ausdrücke gefiltert, sondern insgesamt
als Text behandelt. Die Fehlertexte des Web-Uploads werden nicht mehr vorab
escaped, damit Sonderzeichen genau einmal verarbeitet und korrekt angezeigt
werden.

Die legitime Upload-Hervorhebung bleibt erhalten: `upload_flash_message()`
serialisiert ausschliesslich Dateiname und Dateigrösse mit einer festen
Formatkennung in das bestehende Flash-Textfeld. Der Renderer akzeptiert dafür
nur einen String-Dateinamen und eine nicht negative Integer-Grösse. Das Template
erzeugt selbst das feste `<strong>`-Element, Häkchen und Pfeil. Der Dateiname
bleibt automatisch escaped. Auch eine künstlich erzeugte passende Formatkennung
kann somit keinen beliebigen HTML-Quelltext freischalten. Unbekannte oder
ungültige Formate erscheinen als escaped Text. Das Sitzungsschema bleibt gleich.

Weitere `safe`-Stellen für erzeugte Vertrags-, Beleg-, Bericht- und QR-Fragmente
sind keine alternativen Flash-Ausgaben und werden nicht pauschal geändert.
Tariflisten bleiben unter Jinja-Autoescaping. Die Regressionstests prüfen die
tatsächlich geparsten HTML-Elemente und den vollständigen sichtbaren Text,
einschliesslich Anführungszeichen, Ampersands und bereits geschriebener Entities.

## Abgrenzung und Tests

Die Korrektur betrifft den E-Mail-Importer sowie Flash-Rendering und
Upload-Meldungen. F-01, Intervall-/Abrechnungslogik, MQTT, Sparkplug und Emsomat
werden nicht geändert. Die betroffenen Anwendungsdateien werden unverändert in
die Quellkopie unter `ha_addon/shareomat` übernommen.

Neue Tests: `tests/test_email_attachment_security.py` und
`tests/test_web_flash_security.py`. Zwei vorhandene E-Mail-Tests prüfen jetzt
servergenerierte statt fremdbestimmte Dateinamen und weiterhin den exakten Inhalt,
die Anzahl der importierten Dateien und den Schutz vor Überschreiben.

Vor der Korrektur scheiterten im bereinigten Testaufbau 28 gezielte Fälle;
2 Kontrollfälle bestanden. Vier Tests mit echten Symlinks wurden auf dem
Windows-Testsystem übersprungen, weil die erforderlichen Rechte zum Anlegen der
Links fehlen. Die Tests bleiben für entsprechend berechtigte Umgebungen erhalten.
Zusätzliche Tests prüfen unabhängig davon exklusive Kollisionen, Reparse-Pfad-
Ablehnung, Schreib-/Veröffentlichungsfehler, Wiederholung und paralleles Speichern.

Validierung am 7. November 2026, vorhandenes Python 3.13 / pytest 9.0.3:

* Gezielt nach der ersten Korrektur: 30 bestanden, 4 Symlink-Fälle übersprungen.
* Erweiterte Regressionen und relevante bestehende Tests: 102 bestanden,
  4 Symlink-Fälle übersprungen.
* Vollständige Suite einschliesslich der bisherigen 623 Tests und insgesamt
  38 neuer Prüffälle: **657 bestanden, 4 Symlink-Fälle übersprungen**.
* Vergleich von 185 Anwendungsquelldateien zwischen Hauptprojekt und Add-on:
  keine Abweichungen. Der F-01-Runner ist gegenüber dem Beginn dieses Auftrags
  byteidentisch. Keine neuen Pakete installiert, kein Commit oder Push.
