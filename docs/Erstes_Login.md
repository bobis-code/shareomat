# Shareomat – Erstes Login (Kurzanleitung)

Ab Version 0.6.0 ist Shareomat mit einem Passwort geschützt. Diese Anleitung führt Schritt für Schritt durch
das erste Login nach der Installation oder dem Update in Home Assistant. Ausführlichere Hinweise stehen in
[Anleitung_Zugang.md](Anleitung_Zugang.md).

## 1. Add-on installieren oder aktualisieren

1. Home Assistant: **Einstellungen → Add-ons → Shareomat**.
2. **Aktualisieren** (bzw. bei Neuinstallation **Installieren**) und danach **Starten**.

## 2. Einmal-Code aus dem Protokoll holen

1. Im Shareomat-Add-on den Reiter **Protokoll** öffnen.
2. Dort steht eine Zeile wie:

   ```text
   Shareomat Ersteinrichtung: Einmal-Code  ABCD-EFGH-JKLM  (gültig bis … UTC)
   ```

3. Den Code abschreiben oder kopieren. Er gilt **einmal** und höchstens **24 Stunden**.
   - Kein Code zu sehen? Auf **Aktualisieren** im Protokoll drücken. Nach einem Add-on-Neustart steht ein
     neuer Code im Protokoll, solange noch kein Zugang eingerichtet ist.

## 3. Zugang einrichten

1. **Benutzeroberfläche öffnen** (bzw. Shareomat in der Seitenleiste).
2. Es erscheint **«Willkommen – Zugang einrichten»**.
3. Eintragen:
   - **Einmal-Code** aus dem Protokoll,
   - **Benutzername**, z. B. `admin`,
   - **Passwort** mit mindestens **12 Zeichen**, zweimal. Am einfachsten ist ein Satz, z. B.
     «Sonne scheint auf unser Dach 2026». Passwort gut aufbewahren (Passwort-Manager).
4. Auf dem eigenen Handy **«Auf diesem Gerät 30 Tage angemeldet bleiben»** ankreuzen.
5. Speichern. Shareomat öffnet sich mit der Übersicht. Der Einmal-Code ist ab jetzt ungültig.

## 4. Zweites Handy anmelden

1. Shareomat auf dem zweiten Handy über Home Assistant öffnen.
2. Es erscheint die Anmeldeseite. **Denselben Benutzernamen und dasselbe Passwort** eingeben.
3. Auf dem eigenen Gerät wieder «30 Tage angemeldet bleiben» ankreuzen.

Hinweis: Innerhalb von Home Assistant fragt Shareomat in dieser Version zusätzlich nach dem eigenen Passwort.
Mit «angemeldet bleiben» nur alle 30 Tage.

## 5. Wenn etwas nicht klappt

| Problem | Lösung |
|---|---|
| «Der Einmal-Code ist ungültig oder abgelaufen» | Den **neuesten** Code aus dem Protokoll nehmen; bei Bedarf Add-on neu starten. |
| Nach dem Login sofort wieder auf der Anmeldeseite | Cookie kommt unter Home Assistant nicht durch. Bitte mit Uhrzeit melden (Live-Test). |
| «Zu viele Fehlversuche von diesem Gerät» | 15 Minuten warten oder ein anderes Gerät nehmen. Das Konto selbst wird nie gesperrt. |
| Passwort vergessen | Add-on → **Konfiguration** → **`reset_login`** einschalten → Add-on neu starten → neuer Code im Protokoll → Shareomat öffnen und neues Passwort festlegen → **`reset_login` wieder ausschalten**. |

## 6. Live-Test mit Testdaten

Mit **Testdaten**, keine echten Zahlungsdaten.

1. Auf beiden Handys anmelden (einmal mit, einmal ohne «30 Tage angemeldet bleiben»).
2. Home Assistant neu starten: Ist das Handy mit «30 Tage» noch angemeldet?
3. Auf Handy A **Abmelden** (unten im Menü): Handy B bleibt angemeldet.
4. Auf Handy A dreimal ein falsches Passwort eingeben, dann auf Handy B anmelden: muss sofort klappen.
   Im Protokoll stehen Zeilen `Login: failed attempt from … (peer …)`. Diese Zeilen für die Auswertung
   kopieren.
5. Monat durchgehen: Übersicht → Belege prüfen → freigeben → Sammel-PDF → nur wirklich verschickte Belege
   abhaken → Zahlung → Auszahlung.
6. Menü, PDF öffnen und zurück, Bildschirmtastatur, Querformat ausprobieren. Auffälligkeiten mit Screenshot
   und Uhrzeit notieren.
