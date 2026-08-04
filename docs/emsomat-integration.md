# Shareomat → Emsomat: MQTT-Datenschnittstelle

Referenz für die Anpassung von Emsomat (externes lokales Regel-/
Optimierungssystem für Batterie- und Verbrauchssteuerung), damit es die von
Shareomat veröffentlichten Daten konsumieren kann.

## Architektur (Kurzfassung)

- Shareomat sammelt, speichert und bereitet Marktpreise, LEG-Tarife und
  LEG-Verbrauchsdaten auf.
- Emsomat behält PV-Prognose, Batterie-/Verbrauchersteuerung, lokale
  Echtzeitdaten, Sicherheits-/Reservegrenzen und seine bestehende
  Fallback-Logik.
- Die Verbindung läuft **ausschliesslich über MQTT** — bewusst keine
  REST-API, kein zusätzlicher Netzwerk-Port. Grund: Emsomat braucht nur
  kurzfristige Steuerungsdaten (Stunden bis max. 7 Tage), keine
  Langzeithistorie; die bleibt in Shareomats SQLite für Analyse/
  Prognosebildung.
- Shareomat sendet **nur Daten**, keine Steuerbefehle. Direkte
  Batterie-/Verbrauchersteuerung bleibt vollständig Aufgabe von Emsomat.
- Emsomat muss auch ohne Shareomat funktionieren (fehlende/veraltete Daten
  → eigene bisherige lokale Logik).

## Topic-Präfix

Alle Topics beginnen mit `{prefix}` = Add-on-Option `base_topic`,
Standardwert **`shareomat`**. Alle drei Topics sind **retained**, QoS wie
in der Shareomat-MQTT-Konfiguration (`qos`, Standard `1`).

---

## 1. `{prefix}/energy_data/prices`

Markt-/Referenzpreise, Rest-heute bis maximal 7 Tage voraus.

```json
{
  "schema_version": 1,
  "created_at": "2026-08-04T12:00:00+00:00",
  "valid_until": "2026-08-04T18:00:00+00:00",
  "source": "shareomat",
  "quality": "ok",
  "data": {
    "currency": "CHF",
    "series": [
      {
        "period_start": "2026-08-04",
        "period_end": "2026-08-04",
        "price_chf_kwh": "0.2500",
        "kind": "forecast"
      }
    ]
  }
}
```

- `series[].kind` ist hier immer `"forecast"` (ENTSO-E-Day-Ahead-Prognose).
- `period_start`/`period_end` sind **Datumswerte** (`YYYY-MM-DD`), keine
  Uhrzeit-Slots — die zugrunde liegenden Preisdaten sind aktuell
  tagesgranular, keine 15-Minuten-Werte.
- `price_chf_kwh` ist ein **String** (Decimal-Repräsentation, kein Float).
- `series` ist leer (`[]`), wenn keine Preisprognose im Horizont vorhanden
  ist (z. B. ENTSO-E-Token noch nicht hinterlegt).

---

## 2. `{prefix}/energy_data/local_grid`

Jüngster **tatsächlicher** lokal/Netz-Verlauf, letzte 7 Tage. `kind` ist
hier immer `"recent_actual"` — niemals eine Prognose.

```json
{
  "schema_version": 1,
  "created_at": "2026-08-04T12:00:00+00:00",
  "valid_until": "2026-08-04T18:00:00+00:00",
  "source": "shareomat",
  "quality": "ok",
  "data": {
    "leg": {
      "series": [
        {
          "period_start": "2026-08-01T00:00:00+00:00",
          "period_end": "2026-08-01T23:59:00+00:00",
          "local_kwh": 12.4,
          "grid_kwh": 3.1,
          "kind": "recent_actual"
        }
      ]
    },
    "participants": {
      "cons_a": [
        {
          "period_start": "2026-08-01T00:00:00+00:00",
          "period_end": "2026-08-01T23:59:00+00:00",
          "local_kwh": 7.0,
          "grid_kwh": 1.2,
          "kind": "recent_actual"
        }
      ]
    }
  }
}
```

- Ein Eintrag in `series` entspricht **einem echten Abrechnungszeitraum**
  eines automatischen Settlement-Zyklus — nicht zwingend genau ein Tag; die
  Länge hängt davon ab, wann/wie Messdateien tatsächlich eintreffen.
- `leg.series` = Summe über alle Teilnehmer. `participants` = Dict, Key ist
  die `participant_id`, Value eine Liste im selben Format.
- `local_kwh`/`grid_kwh` sind kWh als **Float** (keine Strings).
- `local_kwh` = aus dem LEG-Pool lokal bezogene Energie, `grid_kwh` = vom
  öffentlichen Netz bezogene Energie (informativ — wird von EBL direkt
  verrechnet, nicht von Shareomat).

---

## 3. `{prefix}/energy_data/demand_forecast`

Die eigentliche Verbrauchsprognose. `kind` ist hier immer `"forecast"`,
echte 15-Minuten-Slots.

```json
{
  "schema_version": 1,
  "created_at": "2026-08-04T12:00:00+00:00",
  "valid_until": "2026-08-04T18:00:00+00:00",
  "source": "shareomat",
  "quality": "ok",
  "data": {
    "scope": "leg",
    "method": "weekday_time_weighted_recency_v1",
    "data_period_start": "2026-06-13T12:00:00+00:00",
    "data_period_end": "2026-08-04T12:00:00+00:00",
    "series": [
      {
        "slot_start": "2026-08-04T12:00:00+00:00",
        "forecast_kwh": 1.42,
        "quality": "ok",
        "sample_count": 5,
        "kind": "forecast"
      },
      {
        "slot_start": "2026-08-04T12:15:00+00:00",
        "forecast_kwh": null,
        "quality": "insufficient_data",
        "sample_count": 1,
        "kind": "forecast"
      }
    ]
  }
}
```

- `series` deckt jetzt bis +7 Tage ab (15-Minuten-Slots), `slot_start` in
  UTC.
- **`forecast_kwh` kann `null` sein** — genau dann, wenn
  `quality: "insufficient_data"`. Das ist kein Fehler, sondern bedeutet:
  weniger als 3 vergleichbare historische Wochentag/Uhrzeit-Vorkommen in
  den letzten 8 Wochen. **Emsomat muss diesen Fall behandeln** (auf eigene
  Fallback-Logik zurückfallen, nicht mit `0` gleichsetzen).
- Das Verfahren ist rein statistisch (Wochentag/Uhrzeit-Muster,
  rezenz-gewichtet mit 21-Tage-Halbwertszeit) — **kein ML/KI**, keine
  Feiertagsbereinigung, keine echte Teilnehmer-Ebene (`scope` ist aktuell
  immer `"leg"`).
- Das Top-level `quality` im Envelope ist eine grobe Zusammenfassung
  (basiert auf den nächsten 2 Tagen); massgeblich für einzelne Slots ist
  die **pro-Slot-`quality`**.

---

## Gemeinsame Envelope-Felder (bei allen drei Topics gleich)

| Feld | Bedeutung |
|---|---|
| `schema_version` | aktuell `1` — wird bei künftigen Breaking Changes hochgezählt |
| `created_at` | UTC-ISO-Zeitpunkt, an dem dieser Envelope erzeugt wurde |
| `valid_until` | Staleness-Grenze — Standard `created_at` + 6h (`energy_data_ttl_seconds`, Default 21600s) |
| `source` | immer `"shareomat"` |
| `quality` | grobe Gesamt-Einschätzung dieses Envelopes |

**Emsomat-Anforderungen (aus der ursprünglichen Architektur-Vorgabe):**
- Nach Neustart den letzten retained Stand übernehmen.
- `created_at`/`valid_until` prüfen; Payloads nach `valid_until` als
  veraltet behandeln.
- Bei fehlenden/unvollständigen/veralteten Daten mit der bisherigen
  lokalen Logik weiterarbeiten, nicht crashen oder raten.
- Empfangene Daten in die vorhandenen Market-/Slot-Strukturen von Emsomat
  übernehmen.

## Wann wird gesendet?

Bei **jedem** Settlement-Zyklus-Trigger (Start, Cron, Datei-Watcher,
MQTT-Kommando `cmd/run_once`, Web-UI "Jetzt ausführen") — auch wenn dabei
keine neue Messdatei gefunden wurde, damit Preise/Historie regelmässig
frisch bleiben. Die Prognose selbst wird höchstens alle 30 Minuten neu
**berechnet** (Wochentag/Uhrzeit-Muster ändern sich nicht minütlich);
publiziert wird bei jedem Zyklus trotzdem der zuletzt berechnete Stand.

## Bewusst nicht enthalten

- Keine REST-API, kein zusätzlicher LAN-Port.
- Keine Teilnehmer-Ebene bei der Verbrauchsprognose (nur LEG-gesamt).
- Keine Feiertagsbereinigung (keine verifizierte Datenquelle vorhanden).
- Keine Backfill-Historie vor dem Rollout dieser Funktion — Rohdaten
  (`meter_readings`) und lokal/Netz-Historie
  (`settlement_cycle_records`) werden erst ab Add-on-Version 0.2.7
  durchgehend gespeichert.

## Zusätzlich vorhandene, ältere Topics

Nicht Teil der `energy_data`-Schnittstelle, aber evtl. für einen
einfachen Health-Check nützlich:

- `{prefix}/status` — `ok` \| `error` \| `starting` \| `offline` (Last
  Will Testament bei ungeplantem Verbindungsabbruch).
- `{prefix}/last_run` — Zeitstempel des letzten Settlement-Laufs.

## Quellcode-Referenz

- Publish-Logik: `shareomat/ha/mqtt_runtime.py`
  (`publish_energy_data_snapshot`, `publish_demand_forecast`).
- Aufruf-Ort: `main.py::_run_safe_cycle()`.
- Prognose-Algorithmus: `shareomat/core/pipeline/consumption_forecast.py`.
- Datenmodell: `shareomat/database/meter_readings.py`,
  `shareomat/database/settlement_history.py`,
  `shareomat/database/consumption_forecasts.py`.
