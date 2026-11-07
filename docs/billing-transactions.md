# Atomare Freigabe und Stornierung von Abrechnungsläufen

Stand: 7. November 2026. Gezielte Korrektur des Auditbefunds F-02.

## Nachgewiesener Fehler

Die bisherige Freigabe las den Entwurfsstatus ohne reservierende
Schreibtransaktion. Ein zweiter Administrator konnte anschliessend auf einer
anderen Verbindung stornieren und committen. Das spätere, allein nach ID
eingeschränkte Freigabe-UPDATE setzte den stornierten Lauf wieder auf
`released`. Zwei Freigaben konnten ausserdem beide Erfolg melden und zwei
Freigabeereignisse schreiben. Die Neuberechnung vor der Freigabe schloss
nachfolgende Änderungen an Datenbank oder Messdateien nicht aus.

`tests/test_billing_atomicity.py` hält die Ausführung mit Events gezielt vor
SQL-Mutationen beziehungsweise nach der Neuberechnung an. Konkurrierende
Aktionen verwenden getrennte echte SQLite-Verbindungen. Es gibt keine
zeitabhängigen Sleeps. Der bereinigte Testlauf vor der Korrektur ergab
13 fehlgeschlagene und 2 bestandene Fälle. Darunter waren der konkrete
Rückwechsel von `cancelled` nach `released`, doppelte Freigabeereignisse,
unerkannte Änderungen und ungefilterte SQLite-Sperrfehler.

## Transaktionsstrategie

Die Anwendung verwendet weiterhin WAL und den bestehenden SQLite-
`busy_timeout` von 5000 ms. Es gibt keine Schemaänderung.

Die Freigabe mit Laufzeitkonfiguration und vorhandenem Ergebnisfingerprint
geht folgendermassen vor:

1. Eine Beobachterverbindung bleibt während der gesamten Validierung offen.
   Auf ihr wird `PRAGMA data_version` gelesen, ohne eine explizite
   Lesetransaktion offenzuhalten.
2. Die unterstützten Messdateien in Inbox und Archiv werden nach Pfad,
   SHA-256, Grösse und Änderungszeit erfasst. Die bestehende Berechnung wird
   mit `persist_readings=False` erneut ausgeführt und ihr unverändert
   definierter Ergebnisfingerprint mit dem Entwurf verglichen. Nur bei
   dieser Freigabeprüfung entfällt das sonst übliche Schreiben des
   Messwertcaches.
3. Dieselbe Beobachterverbindung reserviert mit `BEGIN IMMEDIATE` die
   SQLite-Schreibtransaktion. Danach wird `data_version` erneut gelesen.
   Jeder zwischenzeitliche Commit einer anderen Verbindung verwirft die
   Prüfung, auch wenn sich die resultierenden Beträge zufällig nicht
   geändert haben oder eine Änderung bereits zurückgesetzt wurde.
4. Das Dateiinventar wird nochmals geprüft. Hinzugefügte, entfernte,
   verschobene und veränderte Dateien führen zur erneuten Prüfung;
   Dateilesefehler verhindern die Freigabe kontrolliert.
5. Status und Qualitätsregeln werden unter der Schreibreservierung geprüft.
   Das UPDATE verlangt zusätzlich `status = 'draft'`; genau eine Zeile
   muss betroffen sein. Statusänderung und Freigabeereignis committen
   gemeinsam. Fehler rollen die gesamte Transaktion zurück.

Der Vergleich von `data_version` erfolgt ausdrücklich auf **derselben**
Verbindung. Ein Vergleich von Werten verschiedener Verbindungen wäre
ungeeignet. Siehe [SQLite: data_version](https://www.sqlite.org/pragma.html#pragma_data_version)
und [SQLite: Transaktionen](https://www.sqlite.org/lang_transaction.html).

Die Stornierung reserviert weiterhin vor Status- und Belegprüfung mit
`BEGIN IMMEDIATE` den Schreiber. Das UPDATE verlangt nun auch den zuvor
gelesenen erlaubten Ausgangsstatus und genau eine betroffene Zeile.

Belegerstellung und Belegstornierung hatten bereits eine reservierende
Transaktion. Ihre fachlichen Regeln bleiben erhalten. Sperrfehler aus
`SQLITE_BUSY`/`SQLITE_LOCKED`, einschliesslich erweiterter Codes, werden in
den vorhandenen Fehlerklassen `BillingWorkflowError` beziehungsweise
`InvoicingError` gemeldet. Die bestehenden Web-Handler zeigen sie als
Formularfehler mit Wiederholungshinweis. Andere Datenbankfehler werden
nicht als blosse Sperre umgedeutet. Es gibt keine automatische Wiederholung
einer bereits ausgeführten fachlichen Aktion.

## Erhaltene Regeln und Kompatibilität

- Erlaubt bleiben nur `draft → released` und `draft|released → cancelled`.
- Ein stornierter Lauf kann nicht erneut freigegeben werden.
- Nicht stornierte Rechnungen/Gutschriften verhindern weiterhin die
  Stornierung des zugehörigen Laufs.
- Qualitätsfehler, schriftliche Übersteuerung und die Beschränkung
  übersteuerter Freigaben auf interne Auswertungen bleiben unverändert.
- Abrechnungspositionen, Quellen- und Empfängersnapshots werden nicht
  aktualisiert. Dokumentnummern, eingefrorene HTML-Belege und Prüfsummen
  verwenden die bestehenden Regeln.
- Bestehende Datenbanken, Entwürfe und Belege bleiben lesbar. Alte Entwürfe
  ohne Fingerprint sowie direkte Aufrufe ohne Laufzeitkonfiguration
  behalten die bisherige Möglichkeit einer Freigabe ohne Neuberechnung;
  die Statusübergänge sind auch dort atomar. Der Web-Endpunkt übergibt
  weiterhin die Laufzeitkonfiguration.
- Vorschau und normaler Datenimport behalten ihren bisherigen
  Messwertcache-Schreibeffekt. F-01, E-Mail, MQTT und Sparkplug werden
  durch diese Korrektur nicht verändert.

## Grenzen und Betrieb

Die Datenbankprüfung ist bewusst konservativ: Auch ein fachlich
unabhängiger Commit während der Neuberechnung verlangt eine erneute
Freigabeprüfung. Bei ständig schreibenden Hintergrundprozessen kann das
mehrere Versuche erfordern. Es wird kein veralteter Zustand stillschweigend
akzeptiert. Die zusätzliche abschliessende Dateiprüfung hält die
Schreibreservierung während des Hashens; grosse Archive können dadurch
andere Schreiber verzögern.

SQLite sperrt **keine externen Messdateien**. Die Inventarvergleiche erkennen
die getesteten Änderungen während der Prüfung, stellen aber keine gemeinsame
atomare Transaktion von Dateisystem und Datenbank her. Ein nicht kooperierender
Prozess kann unmittelbar nach der letzten Dateiprüfung noch Dateien ändern;
auch gezieltes zwischenzeitliches Ändern und Zurücksetzen ist nicht allgemein
ausgeschlossen. Messdateien sollen vollständig und atomar bereitgestellt und
während der fachlichen Freigabe nicht in-place verändert werden. Eine absolute
Garantie gegen solche externen Schreiber würde eine weitergehende Umstellung
auf unveränderliche Eingabesnapshots oder koordinierte Dateischreiber
erfordern. Dies ist nicht Teil der gezielten F-02-Korrektur.

Ein späterer berechtigter Commit darf nach einer erfolgreichen Freigabe
Stammdaten ändern; die bereits gespeicherten Abrechnungs- und Belegsnapshots
bleiben davon unberührt. Ein anderer Administrator kann nach dem Commit
auch einen erlaubten nächsten Statusübergang ausführen. Ein anschliessendes
Neuladen liefert entsprechend den dann aktuellen Zustand.

Die Versionsvergabe beim parallelen Anlegen neuer Entwürfe, Stromausfalltests,
Netzwerkdateisysteme sowie eine laufende Produktionsinstallation wurden hier
nicht verändert beziehungsweise nicht dynamisch geprüft.

## Regressionstests

Die neuen Tests prüfen unter anderem beide Reihenfolgen von Freigabe und
Stornierung, zwei Freigaben, zwei Stornierungen, einen wartenden zweiten
Schreiber, geänderte Tarife/Teilnehmer/Dateien, zurückgesetzte Änderungen,
Sperrfehler und Wiederholung, die Formularfehler der Web-Endpunkte,
Belegerstellung gegen Stornierung und einen zweiten Aussteller, unveränderte
Snapshots, bedingte UPDATEs ohne betroffene Zeile sowie vollständiges
Rollback nach Audit- und Teilbelegfehlern.

Ergebnisse mit der bereits vorhandenen Python-3.13-/pytest-Umgebung unter
Windows, ohne Installationen:

| Prüfung | Ergebnis |
|---|---|
| Erste 15 neue Fälle am unveränderten Anwendungscode | 13 fehlgeschlagen, 2 bestanden |
| Relevante Abrechnungs-, Vertrags-, Beleg- und Webtests, einschliesslich aller 28 neuen Fälle | 125 bestanden, 92,03 Sekunden |
| Vollständige Suite einschliesslich F-01 und S-01 bis S-04 | **723 bestanden, 4 übersprungen**, 309,89 Sekunden |

Die vier übersprungenen Fälle sind die unveränderten echten Symlinktests
der E-Mail-Speicherung; unter Windows fehlen die Erzeugungsrechte. Sie
müssen weiterhin vor Produktionsfreigabe auf geeignetem Linux bestehen.
Keine bestehenden Tests wurden abgeschwächt. Hauptprojekt und Add-on wurden
über 185 gespiegelte Quelldateien ohne Abweichung verglichen; `main.py` ist
ebenfalls identisch. `git diff --check` meldete keine Fehler.
