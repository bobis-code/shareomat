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
`./config` in den Container — Datenbank, Datenordner und Konfiguration liegen also
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

Ohne Docker legt `main.py` den Ordner `data/state` beim ersten Start selbst
an. Die SQLite-Datenbank entsteht unter `data/shareomat.db`.

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

## Testdaten

Abgerechnet wird nur mit den offiziellen SDAT-Lieferungen des Netzbetreibers
(Swisseldex, siehe `docs/sdat_leg_import.md`). Das Einlesen von CSV-Dateien
aus `data/inbox/` wurde in 0.6.34 entfernt (ADR-0012).

Zum Ausprobieren ohne echte Daten gibt es eine synthetische Testinstallation:
`tests/fixtures/schema18_synthetic/` (eingefrorene Datenbank, entpacken und mit
`SHAREOMAT_DB_PATH` starten) oder für Lasttests `python -m tests.synthetic.load`.
Alle Werte darin sind erfunden.

## Daten- und Konfigurationsablage

| Pfad | Inhalt |
|---|---|
| `config/leg_config.yaml` | Technische Laufzeitkonfiguration (Pfade, MQTT, Swisseldex, Web-Port), ohne Zugangsdaten — von Git ignoriert |
| `config/secrets.yaml` | Zugangsdaten (Passwörter, ENTSO-E-Token), Vorlage `config/secrets.example.yaml`. Unter Linux `chmod 600`, sonst startet Shareomat nicht. Von Git ignoriert. Eine Umgebungsvariable `SHAREOMAT_…` hat Vorrang |
| `data/secrets.json` | Der unter «Externe Daten» eingegebene ENTSO-E-Token. Nie in der Datenbank und nie in der Sicherung |
| `data/shareomat.db` | Gemeinschaft, Teilnehmer, Messpunkte, Verträge/Tarife, offizielle Messdaten, Abrechnungen — die eigentliche Anwendungsdatenbank |
| `data/state/` | Swisseldex-Empfangsarchiv und -Status, Sicherungen |

Sowohl im Docker- als auch im nativen Weg bleiben `data/` und `config/` im
Projektordner erhalten — ein Neustart (Container neu bauen, oder `python
main.py` erneut aufrufen) verliert keine Daten.

## Sicherung wiederherstellen

Eine Sicherung aus **Einstellungen → Sicherung** (`shareomat-sicherung-….zip`) lässt sich ohne
Home Assistant zurückspielen, auch auf einem neuen Rechner.

1. **Shareomat beenden** (Docker: `docker compose stop`; nativ: Prozess beenden).
2. Wiederherstellen, im Projektordner:

   ```bash
   python -m shareomat.restore pfad/zur/shareomat-sicherung-20261128T090000Z.zip --db data/shareomat.db
   ```

   Ohne `--db` gilt `SHAREOMAT_DB_PATH` bzw. `data/shareomat.db`.
3. Shareomat wieder starten. Ist die Sicherung von einer älteren Version, migriert der Start
   die Datenbank wie bei jedem Update, mit eigener geprüfter Kopie.

Was dabei geschieht:

* Geprüft wird alles **vor** jeder Änderung, auf einer entpackten Kopie:
  * jeder Eintrag gegen das Inhaltsverzeichnis, mit Grösse und SHA-256;
  * keine fremden Pfade;
  * `integrity_check`;
  * Schema-Version: Eine Sicherung einer neueren Shareomat-Version wird abgelehnt.

  Schlägt etwas fehl, bricht die Wiederherstellung ab und **ändert nichts**.
* Was bisher im Datenordner lag, wandert unverändert nach `vor-wiederherstellung-<Zeit>/`:
  die Datenbank, die Dokumente, `live.db` und `coordinator.db`. Es wird nichts gelöscht und
  nichts überschrieben. `live.db` und `coordinator.db` entstehen beim Start neu.
* Alle Login-Sitzungen der wiederhergestellten Datenbank sind beendet; man meldet sich neu an.
  Die Wiederherstellung steht im Audit-Protokoll.
* Zugangsdaten (MQTT, Swisseldex, SMTP, ENTSO-E-Token) sind nicht in der Sicherung. Sie gehören
  in `config/secrets.yaml` (oder in Umgebungsvariablen) und müssen auf einem neuen Rechner dort
  wieder eingetragen werden; einen unter «Externe Daten» eingegebenen Token dort neu eingeben.

Jede neue Sicherung besteht diese Prüfungen schon beim Erstellen (Wiederherstellungsprobe):
Eine Datei in der Sicherungsliste lässt sich also öffnen.

## Fehlersuche

Teilnehmer- und Messpunkt-IDs dürfen Umlaute, Leerzeichen und Sonderzeichen
enthalten. Die Bearbeitungslinks kodieren diese als URL-Pfadsegment. Nach einer
Änderung der ID muss der Bearbeitungslink über die jeweilige Teilnehmer- oder
Messpunktliste neu geöffnet werden.

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
