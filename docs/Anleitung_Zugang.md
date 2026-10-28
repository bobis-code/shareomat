# Shareomat – Zugang einrichten und nutzen

Shareomat ist mit einem Login geschützt, weil darin Rechnungen, Bankdaten und Adressen stehen. Es gibt ein
**Administrator-Konto** mit vollem Zugriff. Mehrere Personen können es gemeinsam verwenden.

## 1. Zugang einrichten (einmalig)

1. In Home Assistant: **Einstellungen → Add-ons → Shareomat → Protokoll** öffnen.
2. Dort steht eine Zeile **«Shareomat Ersteinrichtung: Einmal-Code XXXX-XXXX-XXXX»**.
3. Shareomat öffnen. Es erscheint «Willkommen – Zugang einrichten».
4. Den Code eintragen, einen Benutzernamen wählen (z. B. `admin`) und ein Passwort mit **mindestens 12
   Zeichen** festlegen. Am einfachsten ist ein Satz, z. B. «Sonne scheint auf unser Dach 2026».
5. Fertig. Der Code gilt nur einmal und höchstens 24 Stunden. Nach einem Neustart steht ein neuer Code im
   Protokoll, solange noch kein Zugang eingerichtet ist.

Ohne eingerichteten Zugang zeigt Shareomat keine Daten an. Das gilt auch nach einem Neustart.

## 2. Handy anmelden und angemeldet bleiben

1. Shareomat auf dem Handy öffnen (über Home Assistant oder die eigene Adresse).
2. Benutzername und Passwort eingeben. Passwort-Manager funktionieren.
3. **«Auf diesem Gerät 30 Tage angemeldet bleiben»** ankreuzen. Das nur auf dem eigenen Handy tun.
4. Danach öffnet sich Shareomat direkt mit der Monatsübersicht, ohne erneute Anmeldung.

Ohne das Häkchen endet die Anmeldung nach 30 Minuten ohne Benutzung oder spätestens nach 12 Stunden.

Hinweis zu Home Assistant: In der ersten Version verlangt Shareomat auch innerhalb von Home Assistant das
eigene Passwort. Mit «angemeldet bleiben» geschieht das nur alle 30 Tage.

## 3. Bei Handyverlust den Zugang sperren

1. Auf einem anderen Gerät anmelden.
2. **Mein Konto** öffnen (unten im Menü).
3. **«Alle anderen Geräte abmelden»** drücken. Das verlorene Handy ist sofort abgemeldet.
4. Danach unter **Passwort ändern** ein neues Passwort setzen. Dabei werden ebenfalls alle anderen Geräte
   abgemeldet.

## 4. Passwort vergessen

1. In Home Assistant beim Shareomat-Add-on unter **Konfiguration** die Option **«reset_login»** einschalten
   und das Add-on neu starten.
2. Im **Protokoll** steht ein neuer Einmal-Code.
3. Shareomat öffnen, auf `…/first-login` gehen (oder dem Hinweis auf der Anmeldeseite folgen), Code und neues
   Passwort eingeben.
4. Die Option **«reset_login» wieder ausschalten**.

Das alte Passwort funktioniert, bis ein neues gespeichert ist. Wer den Code nicht aus dem Protokoll hat,
kann nichts ändern.

## 5. Gut zu wissen

- Für Änderungen am **Bankkonto** fragt Shareomat noch einmal nach dem Passwort, wenn die letzte Eingabe
  mehr als 10 Minuten her ist.
- Nach 5 falschen Passwörtern ist die Anmeldung für 15 Minuten gesperrt. Danach geht es wieder.
- Wer was gemacht hat, steht im internen Protokoll. Bei einem gemeinsamen Konto steht dort für alle derselbe
  Name. Getrennte Konten für mehrere Personen sind vorbereitet und kommen später.
- **Zugriff von unterwegs:** nur über eine verschlüsselte Verbindung, also Home Assistant mit HTTPS
  (z. B. Nabu Casa), Tailscale oder einen HTTPS-Proxy. Shareomat nie direkt ins Internet stellen. Docker
  ist standardmässig nur vom eigenen Computer aus erreichbar (`127.0.0.1:8099`).

## Technik (für Prüfende)

- Passwörter: Argon2id (19 MiB, 2 Iterationen, Parallelität 1), Mindestlänge 12, schwache Passwörter
  werden abgelehnt.
- Sitzungen:
  - Das Cookie enthält einen zufälligen 256-Bit-Token; in der Datenbank steht nur dessen SHA-256.
  - Das Cookie ist `HttpOnly` und `SameSite=Lax`. Sein `Path` ist `/`, hinter dem HA-Supervisor nur der
    Ingress-Pfad des Add-ons. Unter HTTPS ist es `Secure`; HTTPS gilt nur, wenn `X-Forwarded-Proto` vom
    HA-Supervisor oder einem konfigurierten Proxy kommt.
- Begrenzung falscher Versuche gilt pro Gerät (Client-Adresse); das Konto selbst wird nie gesperrt, nur
  verlangsamt.
  - Client-Adresse: die TCP-Gegenstelle. Nur wenn diese der HA-Supervisor (`172.30.32.2`) oder ein Eintrag in
    `web.trusted_proxies` ist, gilt der erste Eintrag aus `X-Forwarded-For`.
  - Das ist nur sicher, wenn dieser Proxy ein vom Client mitgeschicktes `X-Forwarded-For` überschreibt.
    Sonst kann ein Angreifer seine Adresse wechseln und die Begrenzung pro Gerät umgehen; die
    Verlangsamung des Kontos bleibt dann der Schutz. Einen eigenen Proxy nur in `web.trusted_proxies`
    eintragen, wenn er den Header so setzt.
  - Needs verification: wie der HA-Supervisor `X-Forwarded-For` unter Ingress setzt, ist noch nicht am echten
    System geprüft. Das ist Teil des ersten Praxistests.
- Uploads überschreiben nie eine vorhandene Datei. Der freie Name (`name-2.csv`, …) wird beim Ablegen atomar
  belegt (`os.link`), sodass auch gleichzeitige Uploads gleichen Namens beide erhalten bleiben.
  - Jede Anmeldung erzeugt einen neuen Token.
- Zentrale Prüfung (`shareomat/web/login.py::gate`):
  - Jede Anfrage ausser statischen Dateien und Login-Seiten braucht eine gültige Sitzung und die Berechtigung
    für die Aktion. Das gilt für Seiten, Downloads, Druckansichten, Uploads und «Lauf starten».
  - Geprüft wird, *bevor* ein Body gelesen wird.
  - `X-Ingress-Path` dient nur zum Bilden von Links und authentifiziert nie.
- Schutz gegen gefälschte Formulare: ein eigener CSRF-Token pro Sitzung (statt eines Tokens für alle),
  Origin-Prüfung bei POST und ein Double-Submit-Token für das Login-Formular. Meldungen gelten pro Sitzung.
- Rollen: vorbereitet in `shareomat/security/permissions.py`. Es gibt eine zentrale Zuordnung von Aktion zu
  Berechtigung; derzeit existiert nur die Rolle «admin».
