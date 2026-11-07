# IMAP-Import: Vertrauensgrenze, Ressourcen und Wiederanlauf

## S-01: Betriebskompatibilität der Dateispeicherung

Die sichere Speicherung aus S-01 bleibt bestehen: privates Staging innerhalb der
Inbox, vollständiges Schreiben, danach exklusive Hardlink-Veröffentlichung unter
einem zufälligen Servernamen. Weder MIME-Namen noch Fehlerpfade überschreiben
bestehende Dateien. Ein fehlender Hardlink-Support führt nicht zu einem unsicheren
Fallback. Nachricht und Diagnose bleiben für einen späteren Versuch erhalten.

| Betriebsart | Quellcode-/Deployment-Prüfung | Ausführungsnachweis |
|---|---|---|
| HA-Add-on, Python 3.12 Alpine/Linux | Inbox unter `/config/shareomat/inbox`; Staging ist deren Unterverzeichnis. Bind-Mounts sind keine Symlinks. | Hier kein Linux-/HA-System gestartet; geeigneter Linux-Test vor Freigabe erforderlich. |
| Docker, Python 3.12 Slim/Linux | Standard-Inbox unter `/app/data/inbox`, Datenverzeichnis als Volume; Staging unter derselben Inbox. | Kein Container gebaut/gestartet. Reales Volume und dessen Hardlink-Rechte müssen vor Freigabe geprüft werden. |
| Lokales Windows/Python 3.13 | Exklusive Speicherung und Staging auf lokalem Dateisystem ausgeführt. | Erfolgs-, Kollisions-, Teilfehler-, Berechtigungs- und simulierte EXDEV/ENOTSUP-Fälle getestet. |

Der Staging-Pfad ist nicht konfigurierbar und liegt direkt in der Inbox. Ein Test
prüft zusätzlich die gleiche Gerätekennung (`st_dev`) beider Pfade. Ein separater
Mount innerhalb eines zufällig erzeugten Staging-Verzeichnisses oder gleichzeitig
ausgetauschte Verzeichnisse durch privilegierte lokale Prozesse sind kein
unterstütztes Betriebsmodell. Inbox und Elternverzeichnisse müssen geschützt sein.

Unter Windows fehlten weiterhin die Rechte zum Erzeugen echter Symlinks. Die vier
bisherigen Tests werden **nicht entfernt** und dürfen nicht als bestanden gelten.
Vor Produktionsfreigabe auf dem vorgesehenen Linux-Dateisystem ausführen:

```sh
python -B -m pytest tests/test_email_attachment_security.py tests/test_leg_email_limits.py -v -rs
```

Dabei müssen die vier Symlink-Fälle tatsächlich bestehen. Auch ein Linux-System
mit inkompatiblem Netzwerk-/Spezialdateisystem gilt nicht allein wegen seines
Betriebssystemnamens als geprüft.

## S-03: Was die Anwendung vertrauen kann

`allowed_senders` bleibt ein kompatibler Filter auf genau einen eindeutig
auswertbaren From-Absender. Ein solcher Header ist fälschbar. Mehrdeutige
Absenderlisten werden nicht als einzelner erlaubter Autor behandelt. Eine leere
Liste erlaubt weiterhin jeden Absender und deaktiviert bestehende Installationen
nicht. Das Log warnt ausdrücklich; bei aktivem E-Mail-Import erscheint auch in der
Weboberfläche ein Hinweis. Es gibt keine Behauptung, der Filter authentifiziere
den Absender.

Die Anwendung ignoriert frei mitlieferbare `Authentication-Results` als
Vertrauensbeweis. Eine sichere Auswertung würde eine überprüfte Bindung an den
zuständigen Mailserver und dessen Headerbereinigung benötigen. Eine komplexe
DKIM-/SPF-/DMARC-Implementierung wird nicht ergänzt.

Erforderlich für den sicheren Betrieb:

1. Dediziertes Postfach oder serverseitig geschützter Importordner; Zustellung
   dorthin nur nach der vom Betreiber geprüften Absender-/Routing-Policy.
2. Der empfangende Mailserver muss Spoofing-Prüfungen und gegebenenfalls sichere
   Weiterleitungsregeln erzwingen. Eine bloss sichtbare From-Adresse oder ein
   vom Absender eingefügter Prüfergebnis-Header genügt nicht.
3. IMAP-Zugang und lokaler Datenpfad müssen geschützt sein. Die Verbindung nutzt
   jetzt ausdrücklich `ssl.create_default_context()` mit Zertifikats- und
   Hostnamenprüfung sowie endlichen Timeouts. Private Zertifikate benötigen eine
   vertrauenswürdige CA im vorhandenen System-Truststore; es gibt keinen
   automatischen unsicheren TLS-Fallback.

Die Konfigurationsfelder und Zugangsdaten bleiben unverändert. Bisher nur wegen
fehlender expliziter Zertifikatsprüfung akzeptierte, nicht vertrauenswürdige
Serverzertifikate werden absichtlich nicht mehr akzeptiert. Der Import bleibt
dann mit klarer Verbindungsdiagnose ausstehend.

Referenz: [Python 3.13: IMAP4_SSL](https://docs.python.org/3.13/library/imaplib.html#imaplib.IMAP4_SSL).

## S-04: Konkrete Grenzen

Die Standardwerte stehen zentral im E-Mail-Importer. Bestehende Konfigurationen
benötigen keine neuen Felder. Grosse reguläre Exporte können in kleinere Dateien
geteilt oder über den vorhandenen manuellen Dateieingang bereitgestellt werden;
die Anwendung löscht eine wegen eines Limits abgewiesene E-Mail nicht.

| Grenze | Standard |
|---|---:|
| Vollständige Nachricht, vor Body-Abruf über RFC822.SIZE geprüft | 100 MiB |
| Einzelner dekodierter Messdatenanhang | 64 MiB |
| Summe dekodierter unterstützter Anhänge je Nachricht | 64 MiB |
| Unterstützte Anhänge je Nachricht | 8 |
| MIME-Objekte beim Aufbau des Parsebaums | 64 |
| MIME-Header insgesamt / deren Gesamtumfang | 256 / 64 KiB |
| Nachrichtenversuche je Poll | 20 |
| Nachrichtenbytes je Poll | 128 MiB |
| Reguläre Dateien in der Inbox / Inbox-Dateibytes | 10'000 / 512 MiB |
| Freier Platz nach Aufnahme des nächsten Anhangs | mindestens 256 MiB |
| Offene Nachrichten in der Wiederholungswarteschlange | 1'000 |
| Aktuelle Zustandsdatei / historische Postfachzustände | 4 MiB / 16 MiB |
| Verbindungs-/Socket-Timeout | 30 Sekunden |
| Arbeitsbudget je Poll | 60 Sekunden, Prüfungen zwischen Nachrichten und Literal-Leseblöcken |

Der Abruf verlangt RFC822.SIZE und einen begrenzten `BODY.PEEK[]<0.N>`-Bereich.
Fehlende oder unplausible Grösse löst keinen unbegrenzten Ersatzabruf aus. Die
Antwort muss genau die angekündigte Länge besitzen. Zusätzlich begrenzt ein
Read-Wrapper IMAP-Literale vor deren Allokation, liest höchstens 64 KiB je Block
und begrenzt die kumulierten Literalbytes samt kleiner Protokollreserve.

Der MIME-Parser begrenzt Teile und Header bereits beim Aufbau. Defekte,
unvollständige oder ungültig kodierte Nachrichten bleiben ausstehend. Alle
unterstützten Anhänge werden auf Anzahl und Grösse geprüft, bevor der erste
Anhang dieser Nachricht veröffentlicht wird. Nicht unterstützte Endungen bleiben
entsprechend dem bestehenden Filter auf dem Mailserver; das wird protokolliert.

Die Nachrichtengrösse ist kein exaktes RSS-Limit: Rohbytes, Parserobjekte und
dekodierte Anhänge benötigen zusätzlichen Speicher. Das Poll-Budget ist kein
präemptiver CPU-Abbruch mitten in einem Parseraufruf. Die Inbox-Grenze ist
Backpressure dieses Importers, keine Dateisystemquote gegen andere Prozesse.
Archiv und Mailserver benötigen eine eigene Aufbewahrungs-/Speicherplatzpolitik.
Es werden weder Altdateien noch E-Mails automatisch gelöscht. Spätere XML-/XLSX-
Parser benötigen ihre eigene Absicherung gegen beispielsweise Entpackungsbomben;
der IMAP-Importer entpackt solche Messdateien nicht.

## Fehlerisolation, Wiederholung und Teilfortschritt

Pro Nachricht werden UID, Fehlergrund, Versuchszahl und nächster Versuch im
lokalen Zustand festgehalten. Wiederholung beginnt nach 60 Sekunden und steigt
bis maximal eine Stunde; der normale Poll-Takt kann den Versuch weiter verzögern.
Neue Nachrichten und fällige Wiederholungen wechseln sich ab. Einzelne fehlerhafte
Nachrichten beenden nicht den Importthread. Bei ausgeschöpfter Warteschlange
bleibt neue Post auf dem Server, bis der Betreiber die Ursachen geklärt hat.

Erfolgreich gespeicherte Anhänge werden mit Index, Inhaltshash und Endung
checkpointed. Nach einem normalen Teilfehler werden nur fehlende Anhänge erneut
geschrieben. Bereits gespeicherte Dateien bleiben verfügbar und werden nicht
zurückgerollt. Ein Crash zwischen Veröffentlichung und Checkpoint kann weiterhin
eine Datei wiederholen; eine Exactly-once-Transaktion über Dateisystem und IMAP
wird nicht behauptet. F-01 und die bestehende Dateihash-Prüfung bleiben erhalten.

Verbindungsabbrüche beenden den aktuellen Poll kontrolliert; beim nächsten Poll
entsteht eine neue Verbindung. Logout ist zeitlich begrenzt und die Verbindung
wird auch nach Fehlern geschlossen. Fehlerhafte Zustandsdateien werden nicht
stillschweigend auf UID 0 zurückgesetzt oder überschrieben: Betreiberprüfung ist
erforderlich, während die Nachrichten unverändert auf dem Server bleiben.

## UIDVALIDITY und Migration

Der Cursor ist an Host, Port, Benutzer, Ordner (als Hash) und UIDVALIDITY gebunden.
Ein Ordner-/Postfachwechsel oder eine neue UIDVALIDITY startet bei UID 1; zuvor
wird der alte Zustand samt offenen Diagnosen exklusiv unter `email-epoch-*.json`
gesichert. Ist dafür das Kontingent erschöpft, bleibt der bisherige Zustand
unverändert und der Import stoppt mit Diagnose statt offene Arbeit zu verlieren.

Alte Zustände ohne Postfachgeneration werden einmalig gesichert und das aktuelle
Postfach erneut gescannt. Das kann bereits bekannte Dateien erneut bereitstellen;
die vorhandene Dateihash-Prüfung bleibt zuständig für deren spätere Verarbeitung.
Es werden keine bestehenden Abrechnungen oder Messwerte migriert/geändert.
Fehlende UIDVALIDITY wird nicht geraten. Auswahl ist read-only und Abruf erfolgt
mit PEEK; Nachrichten und Seen-Flags werden nicht verändert.

## Prüfstand

Tests verwenden kleine synthetische Nachrichten und IMAP-Mocks. Grenzwerte werden
nur innerhalb der Tests verkleinert. Neue Regressionen stehen in
`tests/test_leg_email_limits.py`; bestehende E-Mail-Mocks wurden an die zusätzliche
Grössenabfrage und UIDVALIDITY-Antwort angepasst. Die vier Symlink-Tests bleiben
unverändert erhalten.

Vor der Korrektur: 19 der ersten 26 neuen Fälle fehlgeschlagen, 7 Kontrollfälle
bestanden. Abschliessende gezielte E-Mail-Prüfung: **65 bestanden, 4 Symlink-Fälle
übersprungen**. Eine echte Mailserver- oder Linux-/Containerprüfung wurde nicht
durchgeführt.

Vollständige Testsuite am 7. November 2026 mit vorhandenem Python 3.13 und
pytest 9.0.3: **695 bestanden, 4 Symlink-Fälle übersprungen** (38 neue Fälle).
185 Anwendungsquelldateien stimmen mit der Add-on-Kopie überein, ebenso `main.py`.
F-01 und S-02 sind gegenüber dem Beginn dieses Auftrags byteidentisch geblieben.
Keine Installation, kein Commit und kein Push. Die vier echten Symlink-Tests
bleiben zwingende offene Prüfungen vor Produktionsfreigabe unter Linux.
