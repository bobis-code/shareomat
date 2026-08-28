# Shareomat → Emsomat: Coordinator-/Market-Daten (OBSOLETE)

**Dieses Dokument ist obsolet.** Es beschrieb den fruehen Plain-JSON-MQTT-
Vertrag (`{prefix}/energy_data/{prices,local_grid,demand_forecast}`,
retained Topics). Dieser Vertrag wurde vollstaendig durch die lokale
Sparkplug-B-Kommunikation (ADR-0004) ersetzt — Hard Cut, kein
Compatibility Layer, keine der drei Topics existiert mehr.

**Verbindliche Nachfolgedokumente:**

- `docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md` (Abschnitt 9/10) —
  der vollstaendige, verbindliche Vertrag: Shareomat liefert `LEG/
  ExportPrice`, `LEG/FeedInPrice` und `LEG/DemandForecast` per Sparkplug-
  NCMD an Emsomats Edge Node; Emsomat bestaetigt die Uebernahme per NDATA.
- `docs/decisions/ADR-0004-sparkplug-b-lokale-emsomat-shareomat-kommunikation.md`
- Sendeseite (Shareomat): `shareomat/sparkplug/host.py::SparkplugHost.
  publish_coordinator_ncmd()`, `shareomat/sparkplug/coordinator_mapping.py`.
- Empfangsseite (Emsomat): `Emsomat/sparkplug/edge_node.py::EdgeNodeAdapter`,
  `Emsomat/sparkplug/leg_mapping.py`, `Emsomat/shareomat/adapter.py`.

**Was aus dem alten Vertrag inhaltlich weiterlebt** (nur der Transport hat
sich geaendert, nicht die fachliche Semantik):

- `local_grid` (LEG-Ist-Historie lokal/Netz-Split) wurde **ersatzlos
  entfernt** — Emsomat hatte dafuer nie einen Consumer. Shareomat nutzt
  seine interne Historie (`shareomat/database/settlement_history.py`)
  weiterhin selbst fuer Forecast/Abrechnung.
- `demand_forecast`s Qualitaets-/Gueltigkeits-Semantik (`forecast_kwh`
  kann `null` sein bei `quality: "insufficient_data"`, niemals als `0`
  werten; `created_at`/`valid_until`-Staleness-Pruefung) gilt unveraendert
  fort, jetzt als `LEG/DemandForecast`-Sparkplug-Metric.
- `prices` lebt fachlich als `LEG/ExportPrice` weiter — speist jetzt
  Emsomats kanonischen `export_price` direkt, statt eines separaten,
  unklaren Wertes.
- Neu (existierte im alten Vertrag nicht): `LEG/FeedInPrice`, die LEG-
  Produzentenverguetung (`feed_in_rate_chf_kwh`/`_nt`, niemals
  `local_rate_chf_kwh`).
