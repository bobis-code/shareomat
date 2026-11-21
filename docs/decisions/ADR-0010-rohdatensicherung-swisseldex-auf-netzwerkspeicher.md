# ADR-0010: Rohdatensicherung der Swisseldex-Originale auf einem Netzwerkspeicher

## Status

Vorgeschlagen (21.11.2026). Gebaut mit Schalter: Bis der Netzwerkspeicher (OpenMediaVault)
richtig eingerichtet ist, steht die Sicherung auf «Aus (Übergang)». Mit «Pflicht» gilt diese
Entscheidung vollständig. Auftrag: `docs/development/rohdatensicherung-extern.md`.

## Kontext

* Die vom Swisseldex-Datahub abgeholten Dateien sind die einzige offizielle Grundlage der
  Abrechnung (ADR-0008). Bisher lagen sie nur im lokalen Empfangsarchiv
  (`<state>/swisseldex/received/`) und, nach dem Import, in `shareomat.db`.
* Programmcode entsteht zunehmend mit KI. Ein Fehler im Import oder in der Datenbank darf die
  ursprünglichen Lieferungen nie zerstören. Die Originale müssen unabhängig von Shareomat und
  seiner Datenbank erhalten bleiben, physisch auf einem anderen Gerät.
* Home Assistant bindet Netzwerkspeicher selbst ein. Bei der Verwendung «Freigabe» erscheint der
  Speicher als `/share/<Name>` und ist für Apps zugänglich. Das Add-on hat `share:rw`.
  Geprüft am 21.11.2026: OMV ist als CIFS/SMB 3.1.1 auf `/share/<Name>` eingebunden
  (autofs, `soft`, eigener OMV-Benutzer, `nounix`, `uid=0`, `file_mode=0755`).
* Der Datahub trennt eine Verbindung nach 10 Sekunden ohne Aktivität.

## Entscheidung

1. **Sicherung vor jeder Verarbeitung.** Während der Swisseldex-Sitzung werden Dateien nur in
   lokale Zwischendateien geladen. Nach dem Schliessen der Sitzung wird jede Datei zuerst
   gesichert und geprüft (`shareomat/core/collector/raw_backup.py`), erst danach archiviert
   (die `.gz`-Prüfung liest sie) und dem Import angeboten.
2. **Ziel:** `<Netzwerkspeicher>/<Unterordner>/<JJJJ-MM>/<Originalname>`. Der Monat stammt aus
   dem Datum im Dateinamen (`JJJJMMTT_…`), sonst aus dem lokalen Empfangsmonat. Speicher und
   Unterordner werden unter Einstellungen gewählt. Angeboten werden nur echte Netzwerk-Mounts
   (cifs, smb3, nfs, nfs4) direkt unter `/share`. Es gibt nie einen Rückfall auf den lokalen
   Speicher.
3. **Einmal schreiben, nie ändern.** Dateien werden nur exklusiv neu angelegt
   (`O_CREAT|O_EXCL`). Das Modul enthält kein Umbenennen, Ersetzen, Kürzen oder Löschen, und ein
   Test prüft den Quelltext darauf. Nach dem Schreiben wird die Kopie zurückgelesen und per
   SHA-256 und Grösse verglichen. Erst dann wird die Prüfdatei `<Name>.sha256` exklusiv
   angelegt (Prüfsumme, Grösse, Empfangs- und Sicherungszeit). Nur mit ihr gilt die Sicherung
   als vollständig.
4. **Vorhandene Datei unter dem Namen:**
   * Gleicher Name, gleiche Prüfsumme: Die vorhandene Sicherung wird wiederverwendet.
   * **Abgebrochene Kopie:** Eine Datei ohne Prüfdatei, die genau der Anfang des Originals ist,
     wird automatisch fertiggestellt. Die fehlenden Bytes werden angehängt (`O_APPEND`, nie
     kürzen oder neu schreiben), danach wird die ganze Datei geprüft. Niemand muss eingreifen
     (Entscheid des Betreibers, 21.11.2026: Der Nutzer soll nicht raten müssen).
   * Eine Datei ohne Prüfdatei mit identischem Inhalt (Neustart zwischen Kopie und Prüfdatei)
     wird mit der Prüfdatei abgeschlossen.
   * Jede andere Datei unter dem Namen wird nie angefasst: weder überschrieben noch umbenannt
     noch gelöscht. Die Fehlermeldung nennt Speicher und Pfad und sagt **Schritt für Schritt,
     was zu tun ist**:
     * Datei ohne Prüfdatei, anderer Inhalt: Sie ist keine Sicherung. Auf dem Speicher
       löschen, dann «Jetzt synchronisieren».
     * Andere Lieferung mit Prüfdatei: Beide sind Originale. Nichts löschen und beim
       Netzbetreiber nachfragen. Die neue Lieferung bleibt auf Swisseldex und wird jedes Mal
       erneut versucht.
5. **Modus «Pflicht»:**
   * Ohne geprüfte Sicherung wird eine Datei weder archiviert noch als gesehen vermerkt. Die
     nächste Synchronisation lädt sie erneut.
   * Dem Import werden nur Dateien mit geprüfter Sicherung angeboten (`received_files()`). Das
     gilt auch für den Knopf «erneut prüfen und importieren».
   * Fehlgeschlagene Versuche werden mit Zeitpunkt und Fehlermeldung im Receiver-Zustand
     festgehalten. Eine Warnung erscheint auf jeder Seite.
   * Schon empfangene Dateien ohne Sicherung werden aus dem Empfangsarchiv nachgesichert, nur
     wenn die Archivdatei noch die Prüfsumme des empfangenen Originals hat.
6. **Modus «Aus (Übergang)»:** Shareomat arbeitet wie bisher. Jede Datei wird als «aus»
   vermerkt, und die Einstellungsseite und die Swisseldex-Karte zeigen das deutlich an.
7. **Speichertest:**
   * Der Knopf «Speicher testen» legt in `<Unterordner>/verbindungstest/` eine kleine neue Datei
     an, liest sie zurück, vergleicht SHA-256 und prüft, dass ein erneutes exklusives Anlegen
     abgelehnt wird. Damit ist am echten Speicher nachgewiesen, dass er Überschreiben verhindert.
     Zum Schluss zeigt er den freien Platz an.
   * Beim Umschalten auf «Pflicht» läuft der Test automatisch. Schlägt er fehl, wird nicht
     umgeschaltet.
   * Testdateien bleiben liegen, denn Shareomat löscht auf dem Speicher nie etwas.
8. **Ändern der Einstellung:** Dafür gibt es eine eigene Berechtigung
   (`raw_backup.configure`). Ist die letzte Passworteingabe älter als 10 Minuten, braucht es
   das Passwort erneut. Jede Änderung kommt mit altem und neuem Wert ins Audit-Log.
9. **Nachweis:** Die Prüfdatei auf dem Speicher selbst ist der Nachweis der Sicherung,
   unabhängig von Shareomat. Zusätzlich führt der Receiver-Zustand pro Datei Speicherpfad,
   Zeitpunkt und Ergebnis. Die Datenbank ist nicht der einzige Nachweis.

## Was nicht vollständig durchsetzbar ist

* **Nur Schutz im Code** (Entscheid des Betreibers, 21.11.2026). Mit normalen SMB-Rechten lässt
  sich «neue Dateien anlegen» nicht von «eigene Dateien ändern oder löschen» trennen. Der
  OMV-Benutzer kann es technisch. Schutz auf Serverseite wäre Samba `vfs_worm` oder Snapshots
  auf OMV; das wird später ergänzt. Mit `nounix` sind die Linux-Rechte am Mount nur simuliert.
* **Zurücklesen:** Mit `cache=strict` kann der Lesevorgang aus dem Cache des HA-Rechners kommen.
  `posix_fadvise(DONTNEED)` bittet den Kernel, den Cache zu verwerfen. Ein Beweis, dass die Bytes
  vom Server gelesen wurden, ist das nicht. Needs verification am echten Speicher.
* **`O_EXCL` über CIFS:** Erwartet wird, dass der Server ein exklusives Anlegen atomar
  ausführt (SMB2 CREATE mit FILE_CREATE). Der Speichertest weist das am echten Speicher nach.
* **Leserecht:** Beim ersten Test am 21.11.2026 durfte der Benutzer der Freigabe eine Datei
  anlegen, aber nicht lesen. Am selben Abend auf OMV korrigiert; laut Betreiber geht `cat` jetzt.
* **Fortsetzen:** Das Fortsetzen setzt voraus, dass nur ein Shareomat in diesen Ordner schreibt.
  In einem Prozess sind Sicherungen über eine Sperre nacheinander.
* **30 Tage:** Swisseldex löscht nicht abgeholte Dateien nach 30 Tagen. Ob ein Download ohne
  Übernahme als abgeholt gilt, ist nicht geklärt. Darum erscheint die Warnung sofort, nicht
  erst nach 7 Tagen.

## Konsequenzen

* Fällt der Netzwerkspeicher aus, werden mit «Pflicht» keine neuen Swisseldex-Daten
  übernommen, bis er wieder erreichbar ist. Nichts geht verloren: Shareomat löscht auf dem
  Datahub nie etwas. Die Abrechnung kann sich verzögern, wird aber nicht falsch.
* Eine abgebrochene Kopie wird beim nächsten Lauf automatisch fertiggestellt. Von Hand braucht es
  nur noch echte Konflikte, und die Meldung sagt, was zu tun ist.
* Import und Abrechnung bleiben unverändert.

## Betroffene Dateien

* `shareomat/core/collector/raw_backup.py` (neu)
* `shareomat/core/collector/swisseldex_receiver.py` (Zwischendateien, Sicherung vor Archiv,
  Nachsicherung, Filter für den Import, Warnung)
* `main.py` (Verdrahtung)
* `shareomat/models/settings.py`, `shareomat/leg_const.py` (Einstellungen)
* `shareomat/web/pages/settings.py`, `shareomat/web/templates/settings/edit.html`
* `shareomat/web/templates/external_data/edit.html` (Status auf der Swisseldex-Karte)
* `shareomat/security/permissions.py`, `shareomat/web/server.py` (Berechtigung, Passwort)
* Tests: `tests/test_raw_backup.py`, `tests/test_swisseldex_raw_backup.py`,
  `tests/test_raw_backup_settings.py`
