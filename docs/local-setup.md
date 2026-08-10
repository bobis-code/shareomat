# Lokales Setup

Anleitung, um Shareomat lokal auf dem eigenen Rechner zu starten — unabhängig
von Home Assistant. Zwei Wege stehen zur Wahl: Docker Compose (empfohlen,
kein lokales Python nötig) oder ein natives Python-Setup (für Entwicklung
oder falls kein Docker verfügbar ist). Beide starten dieselbe Anwendung mit
derselben Weboberfläche.

## Voraussetzungen

| Weg | Benötigt |
|---|---|
| Docker Compose | [Docker](https://docs.docker.com/get-docker/) mit Compose-Plugin |
| Native (Python) | Python 3.12 oder neuer, `pip` |

Beide Wege brauchen ausserdem `git`, um das Repository zu klonen.

---

## Weg 1: Docker Compose (empfohlen)

```bash
git clone https://github.com/bobis-code/shareomat.git
cd shareomat

# Technische Konfiguration einmalig anlegen:
cp config/leg_config.example.yaml config/leg_config.yaml

docker compose up --build
```

Beim ersten Start baut Docker das Image (`python:3.12-slim` als Basis,
Abhängigkeiten aus `requirements.txt`), danach startet der Container direkt.
`docker-compose.yml` bindet Port `8099` an den Host und mountet `./data` und
`./config` in den Container — Datenbank, Inbox und Konfiguration liegen also
weiterhin im Projektordner, nicht nur im Container.

Weboberfläche öffnen: **http://localhost:8099**

Zum Stoppen: `Ctrl+C`, oder in einem zweiten Terminal `docker compose down`.
Für einen Neustart im Hintergrund: `docker compose up -d`.

---

## Weg 2: Nativ mit Python

```bash
git clone https://github.com/bobis-code/shareomat.git
cd shareomat

# Virtuelle Umgebung (empfohlen, nicht zwingend):
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp config/leg_config.example.yaml config/leg_config.yaml

python main.py
```

Ohne Docker legt `main.py` die Ordner `data/inbox`, `data/archive`,
`data/reports` und `data/state` beim ersten Start selbst an. Die
SQLite-Datenbank entsteht unter `data/shareomat.db`.

Weboberfläche öffnen: **http://localhost:8099**

Zum Stoppen: `Ctrl+C` im Terminal.

---

## Erste Einrichtung

Unabhängig vom gewählten Weg ist die Datenbank beim allerersten Start leer.
Die Weboberfläche führt automatisch durch den Einrichtungsassistenten:

1. **Gemeinschaft** — Name und Adresse der LEG/ZEV
2. **Teilnehmer** — mindestens ein Produzent und ein Bezüger
3. **Messpunkte** — den Teilnehmern zugeordnete Zähler
4. **Vertrag** — Vertrags-Stammdaten, Preise, Veröffentlichung (erzeugt den
   ersten gültigen Tarif — siehe Haupt-README, Abschnitt "Quick start")

Alle vier Schritte lassen sich jederzeit über die Seitenleiste erneut öffnen
und anpassen; nichts davon steckt in `leg_config.yaml`, alles landet in
`data/shareomat.db`.

## Testdaten einspielen

Um den Abrechnungslauf ohne echte Messdaten des Netzbetreibers zu testen,
genügt eine einfache CSV-Datei in `data/inbox/`:

```csv
timestamp,mpid,value_kwh,direction
2024-06-01T12:00:00+00:00,CH001...,0.125,export
2024-06-01T12:00:00+00:00,CH002...,0.080,import
```

`mpid` muss einem in Schritt 3 angelegten Messpunkt entsprechen. Danach auf
der Seite **"Messdaten"** auf **"Jetzt ausführen"** klicken, oder die Datei
per Auto-Scan automatisch aufnehmen lassen (siehe "Automatisierung" in der
Seitenleiste). Ergebnisse landen unter `data/reports/`, die Eingabedatei wird
nach erfolgreicher Verarbeitung nach `data/archive/` verschoben.

## Daten- und Konfigurationsablage

| Pfad | Inhalt |
|---|---|
| `config/leg_config.yaml` | Technische Laufzeitkonfiguration (Pfade, MQTT, E-Mail, Web-Port) — von Git ignoriert, da hier Zugangsdaten stehen können |
| `data/shareomat.db` | Gemeinschaft, Teilnehmer, Messpunkte, Verträge/Tarife, Automatik — die eigentliche Anwendungsdatenbank |
| `data/inbox/` | Hier abgelegte Mess-Dateien werden beim nächsten Lauf verarbeitet |
| `data/archive/` | Bereits verarbeitete Dateien |
| `data/reports/` | Erzeugte Abrechnungs- und Detail-Reports (CSV/JSON) |
| `data/state/` | Interner Verarbeitungsstatus (z. B. bereits gesehene Dateien) |

Sowohl im Docker- als auch im nativen Weg bleiben `data/` und `config/` im
Projektordner erhalten — ein Neustart (Container neu bauen, oder `python
main.py` erneut aufrufen) verliert keine Daten.

## Fehlersuche

- **`http://localhost:8099` antwortet nicht (Docker):** Prüfen, ob der
  Container läuft (`docker compose ps`) und ob Port 8099 bereits von einem
  anderen Prozess belegt ist. Bei Bedarf in `docker-compose.yml` und
  `config/leg_config.yaml` einen anderen Host-Port eintragen (beide Werte
  müssen zusammenpassen).
- **Seite zeigt eine Fehlermeldung statt der Einrichtung:** Meist eine
  fehlerhafte oder fehlende `config/leg_config.yaml` — Shareomat startet die
  Weboberfläche trotzdem (kein Absturz), zeigt den Grund aber als
  Warnbanner an. `config/leg_config.example.yaml` erneut als Vorlage
  kopieren und die YAML-Syntax prüfen.
- **`ModuleNotFoundError` beim nativen Start:** `pip install -r
  requirements.txt` erneut ausführen, ggf. in der aktivierten virtuellen
  Umgebung.
- **MQTT-Warnung im Dashboard:** Nur relevant, falls `mqtt.enabled: true`
  gesetzt wurde — für den reinen Weboberflächen-Betrieb kann dieser
  Abschnitt in `leg_config.yaml` unverändert (`enabled: false`) bleiben.

## Nächste Schritte

- Tests laufen lassen: `pip install -r requirements-dev.txt && pytest tests/`
  (siehe Haupt-README, Abschnitt "Running tests").
- Für den Betrieb als Home-Assistant-Add-on statt eigenem Container: siehe
  Haupt-README, Abschnitt "Optional: run as a Home Assistant add-on".
