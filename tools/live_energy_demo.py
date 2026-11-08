# -*- coding: utf-8 -*-
"""
Local demo of the "LIVE-ENERGIE" views with test data, without any broker.

Uses a throw-away database in a temporary folder, listens on 127.0.0.1 only and
prints a one-time password. It touches neither Home Assistant nor MQTT, and it
is not part of the add-on.

    python tools/live_energy_demo.py [port]
"""

from __future__ import annotations

import secrets
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shareomat.database.auth import create_admin  # noqa: E402
from shareomat.database.community import save_community  # noqa: E402
from shareomat.database.live_sources import activate_live_source, create_live_source  # noqa: E402
from shareomat.database.meters import create_meter  # noqa: E402
from shareomat.database.participants import create_participant  # noqa: E402
from shareomat.database.sqlite import init_db  # noqa: E402
from shareomat.live.model import RawSample, RawValue, SessionStart  # noqa: E402
from shareomat.live.service import LiveStatusService  # noqa: E402
from shareomat.models.community import Community  # noqa: E402
from shareomat.models.live_source import ADAPTER_EMSOMAT_LOCAL  # noqa: E402
from shareomat.models.meter import Meter  # noqa: E402
from shareomat.models.participant import Participant  # noqa: E402
from shareomat.web.server import _Handler  # noqa: E402
from shareomat.web.state import get_state  # noqa: E402

EDGE_NODE = "EMSOMAT-DEMO"
SOURCE_KEY = f"{ADAPTER_EMSOMAT_LOCAL}:{EDGE_NODE}"


def build_demo(folder: Path) -> tuple[LiveStatusService, str, str]:
    """Seeds a database in `folder`. Returns (service, username, password)."""
    db_path = folder / "shareomat.db"
    init_db(db_path)
    save_community(db_path, Community(community_id="DEMO-LEG", name="Demo-LEG"))
    create_participant(db_path, Participant("P1", "Haus mit PV"))
    create_participant(db_path, Participant("P2", "Nachbar"))
    create_meter(db_path, Meter(meter_id="CH-DEMO-1", participant_id="P1", label="Haus mit PV", role="producer_consumer"))
    create_meter(db_path, Meter(meter_id="CH-DEMO-2", participant_id="P2", label="Nachbar", role="consumer"))
    source_id = create_live_source(db_path, meter_id="CH-DEMO-1", adapter=ADAPTER_EMSOMAT_LOCAL, edge_node_id=EDGE_NODE, max_power_w=17250)
    activate_live_source(db_path, source_id)

    password = secrets.token_urlsafe(18)
    create_admin(db_path, "demo", password)
    service = LiveStatusService(db_path)
    state = get_state()
    state.register_db(db_path)
    state.register_runtime(SimpleNamespace(
        sparkplug=SimpleNamespace(emsomat_edge_node_id=EDGE_NODE),
        web=SimpleNamespace(trusted_proxies=[]),
    ))
    state.register_live_service(service)
    service.submit(SessionStart(ADAPTER_EMSOMAT_LOCAL, SOURCE_KEY, "demo"))
    return service, "demo", password


def feed_once(service: LiveStatusService, export_w: float = 2400.0) -> None:
    stamp = int(datetime.now(timezone.utc).timestamp() * 1000)
    service.submit(RawSample(
        adapter=ADAPTER_EMSOMAT_LOCAL, source_key=SOURCE_KEY, session="demo", received_at=service.now(),
        import_value=RawValue(0.0, timestamp_ms=stamp), export_value=RawValue(export_w, timestamp_ms=stamp),
        payload_timestamp_ms=stamp, time_origin="ha_last_reported",
    ))
    service.process_pending()
    service.tick()


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8199
    with tempfile.TemporaryDirectory(prefix="shareomat-live-demo-") as tmp:
        service, username, password = build_demo(Path(tmp))
        server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"http://127.0.0.1:{port}/live-energy")
        print(f"Benutzer: {username}   Passwort: {password}   (Abbrechen mit Ctrl+C)")
        try:
            while True:
                feed_once(service)
                time.sleep(5)
        except KeyboardInterrupt:
            server.shutdown()


if __name__ == "__main__":
    main()
