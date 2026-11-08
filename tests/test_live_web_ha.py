# -*- coding: utf-8 -*-
"""Live-Energie admin page and HA sensors, with test data and no broker."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from shareomat.config import MqttConfig
from shareomat.database.audit import list_events
from shareomat.database.community import save_community
from shareomat.database.live_sources import activate_live_source, create_live_source, get_live_source, list_live_sources, record_identity_proof
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.ha.live_sensors import LIVE_SENSORS, UNKNOWN, LiveHaPublisher, build_states, publish_live_discovery
from shareomat.live.model import LiveParams, RawSample, RawValue, SessionStart, TransportChange
from shareomat.live.service import LiveStatusService
from shareomat.models.community import Community
from shareomat.models.live_source import ADAPTER_EMSOMAT_LOCAL, ADAPTER_WAN_SPARKPLUG, STATE_ACTIVE, STATE_RETIRED, STATE_TESTING
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.security.context import Principal
from shareomat.security.permissions import has_permission, required_permission
from shareomat.web.navigation import NAV_SECTIONS
from shareomat.web.pages import live_energy
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
EM = ADAPTER_EMSOMAT_LOCAL
EM_KEY = f"{EM}:EMS-A"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class FakeRuntime:
    class sparkplug:
        emsomat_edge_node_id = "EMS-A"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    save_community(path, Community(community_id="LEG-1", name="Test-LEG"))
    create_participant(path, Participant("P1", "Haus 1"))
    create_participant(path, Participant("P2", "Haus 2"))
    create_meter(path, Meter(meter_id="M1", participant_id="P1", label="Haus Eins", role="producer_consumer"))
    create_meter(path, Meter(meter_id="M2", participant_id="P2", label="Haus Zwei", role="consumer"))
    return path


@pytest.fixture
def service(db_path):
    state = get_state()
    state.register_db(db_path)
    state.register_runtime(FakeRuntime)
    live = LiveStatusService(db_path, LiveParams(), clock=Clock())
    state.register_live_service(live)
    yield live
    state.register_live_service(None)
    state.register_runtime(None)


def _get(segments=(), query=None):
    ctx = RequestContext(method="GET", ingress_path="", form={}, query=query or {})
    ctx.segments = list(segments)
    return ctx


def _post(segments, form=None):
    form = dict(form or {})
    form["csrf_token"] = get_state().csrf_token
    ctx = RequestContext(method="POST", ingress_path="", form=form, query={})
    ctx.segments = list(segments)
    return ctx


def _assign_emsomat(meter="M1"):
    live_energy.handle_post(_post([meter, "assign"], {"adapter": EM, "max_power_w": "20000", "freshness_evidence": "C"}))


def _sample(imp=500.0, exp=0.0, ts=NOW_MS):
    return RawSample(EM, EM_KEY, "s1", NOW, import_value=RawValue(imp, timestamp_ms=ts), export_value=RawValue(exp, timestamp_ms=ts),
                     payload_timestamp_ms=ts, time_origin="ha_last_reported")


def _deliver(service, *samples):
    service.submit(SessionStart(EM, EM_KEY, "s1"))
    for sample in samples or (_sample(),):
        service.submit(sample)
    service.process_pending()


def test_menu_is_under_betrieb():
    sections = dict(NAV_SECTIONS)
    assert ("live_energy", "Live-Energie") in sections["BETRIEB"]


def test_page_needs_admin_rights_and_post_is_not_just_view():
    assert required_permission("POST", "live_energy", ["sources", "1", "activate"]) == "masterdata.edit"
    assert not has_permission(None, "masterdata.edit")
    assert not has_permission(None, "view")
    guest = Principal(user_id=1, username="x", display_name="x", role="teilnehmer", session_id=1, csrf_token="t", auth_at=NOW)
    assert not has_permission(guest, "view")


def test_overview_without_sources_never_shows_zero_or_surplus(service):
    service.tick()
    html = live_energy.handle_get(_get())
    assert "Haus Eins" in html and "Keine Live-Messquelle" in html and "Live-Messquelle zuordnen" in html
    assert "Bilanz (+ = Überschuss)" not in html and "Summe über die" not in html


def test_page_without_running_live_layer(db_path):
    state = get_state()
    state.register_db(db_path)
    state.register_live_service(None)
    assert "läuft derzeit nicht" in live_energy.handle_get(_get())


def test_full_flow_assign_check_activate_replace_retire(service, db_path):
    _assign_emsomat()
    source = list_live_sources(db_path)[0]
    assert source.state == STATE_TESTING and source.source_key == EM_KEY

    with pytest.raises(FormError, match="noch nichts empfangen"):
        live_energy.handle_post(_post(["sources", str(source.id), "activate"]))
    assert get_live_source(db_path, source.id).state == STATE_TESTING

    _deliver(service)
    html = live_energy.handle_get(_get())
    assert "Live-Messquelle geplant" in html and "500 W" in html and "Aktivieren" in html

    live_energy.handle_post(_post(["sources", str(source.id), "activate"]))
    assert get_live_source(db_path, source.id).state == STATE_ACTIVE
    service.tick()
    html = live_energy.handle_get(_get())
    assert "Live-Messquelle aktiv" in html and "Keine vollständige Bilanz" in html and "nicht verwenden" in html

    live_energy.handle_post(_post(["sources", str(source.id), "retire"]))
    assert get_live_source(db_path, source.id).state == STATE_RETIRED
    assert service.is_registered(EM, EM_KEY) is False
    assert {"assigned", "activated", "retired"} <= {e.action for e in list_events(db_path, entity="live_source")}


def test_emsomat_source_is_always_the_configured_node(service, db_path):
    live_energy.handle_post(_post(["M1", "assign"], {"adapter": EM, "edge_node_id": "EVIL-NODE", "max_power_w": "20000"}))
    assert list_live_sources(db_path)[0].source_key == EM_KEY


def test_assign_refuses_garbage(service, db_path):
    with pytest.raises(FormError):
        live_energy.handle_post(_post(["M1", "assign"], {"adapter": "shell", "max_power_w": "1000"}))
    with pytest.raises(FormError):
        live_energy.handle_post(_post(["M1", "assign"], {"adapter": EM, "max_power_w": "nan"}))
    with pytest.raises(FormError):
        live_energy.handle_post(_post(["M1", "assign"], {"adapter": ADAPTER_WAN_SPARKPLUG, "edge_node_id": "a/#", "device_id": "x", "max_power_w": "1000"}))
    with pytest.raises(FormError):
        live_energy.handle_post(_post(["NOPE", "assign"], {"adapter": EM, "max_power_w": "1000"}))
    assert list_live_sources(db_path) == []


def test_invalid_source_id_and_unknown_action(service):
    with pytest.raises(FormError):
        live_energy.handle_post(_post(["sources", "1 OR 1=1", "activate"]))
    with pytest.raises(FormError):
        live_energy.handle_post(_post(["sources", "999", "activate"]))
    with pytest.raises(FormError):
        live_energy.handle_post(_post(["wat"]))


def test_edit_keeps_identity_and_updates_values(service, db_path):
    _assign_emsomat()
    source = list_live_sources(db_path)[0]
    assert "20000" in live_energy.handle_get(_get(["sources", str(source.id), "edit"]))
    live_energy.handle_post(_post(["sources", str(source.id), "edit"], {
        "max_power_w": "9000", "freshness_evidence": "C", "measurement_mode": "import_export", "source_key": "emsomat_local:OTHER"}))
    updated = get_live_source(db_path, source.id)
    assert updated.max_power_w == 9000 and updated.source_key == EM_KEY


def test_import_only_offered_only_for_consumers(service):
    assert 'value="import_only"' in live_energy.handle_get(_get(["M2", "assign"], {"adapter": EM}))
    assert 'value="import_only"' not in live_energy.handle_get(_get(["M1", "assign"], {"adapter": EM}))


def test_assign_form_offers_three_kinds(service):
    html = live_energy.handle_get(_get(["M1", "assign"]))
    assert "Emsomat / Sparkplug" in html and "Home Assistant / HA-Sensoren" in html and "Externe Quelle / Gateway" in html


def test_neighbour_source_can_be_planned_without_any_rebuild(service, db_path):
    live_energy.handle_post(_post(["M2", "assign"], {"adapter": "gateway_mqtt", "device_id": "AMS-1", "max_power_w": "9000"}))
    rows = {r.meter_id: r for r in service.meter_rows()}
    assert rows["M2"].live_status == "planned"


def test_user_controlled_text_is_escaped(service, db_path):
    create_meter(db_path, Meter(meter_id="M3", participant_id="P1", label="<script>alert(1)</script>", role="consumer"))
    live_energy.handle_post(_post(["M2", "assign"], {"adapter": ADAPTER_WAN_SPARKPLUG, "edge_node_id": "<b>x", "device_id": "<i>y", "max_power_w": "9000"}))
    html = live_energy.handle_get(_get())
    assert "<script>alert(1)" not in html and "<b>x" not in html


def test_unknown_sender_visible_to_admin(service):
    service.register_unknown_pairs_getter(lambda: (("SITE-X", "P9"),))
    html = live_energy.handle_get(_get())
    assert "SITE-X" in html


# ── HA sensors ──────────────────────────────────────────────────────────────


class FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))


def _config():
    return MqttConfig(enabled=True, broker="b", port=1883, client_id="c")


def _active(service, db_path):
    source_id = create_live_source(db_path, meter_id="M1", adapter=EM, edge_node_id="EMS-A", max_power_w=20000)
    activate_live_source(db_path, source_id)
    service.reload_masterdata()
    return source_id


def test_discovery_defines_expected_read_only_entities():
    client = FakeClient()
    publish_live_discovery(client, _config())
    config = {t: json.loads(p) for t, p, _, _ in client.published}
    assert len(config) == len(LIVE_SENSORS)
    ids = {c["default_entity_id"] for c in config.values()}
    assert {"sensor.shareomat_live_netzbezug", "sensor.shareomat_live_netzeinspeisung", "sensor.shareomat_live_messwert_qualitaet",
            "sensor.shareomat_live_messzeitpunkt", "sensor.shareomat_leg_live_status", "sensor.shareomat_leg_vollstaendigkeit",
            "sensor.shareomat_leg_ueberschuss"} <= ids
    assert all("command_topic" not in c for c in config.values())
    assert all(retain for *_, retain in client.published)


def test_own_values_published_while_leg_balance_incomplete(service, db_path):
    _active(service, db_path)
    _deliver(service, _sample(imp=0.0, exp=2500.0))
    states = build_states(service, service.tick())
    assert states["own/export_w"] == "2500.0" and states["own/import_w"] == "0.0"
    assert states["own/quality"] == "ok" and states["own/time_origin"] == "ha_last_reported"
    assert states["own/measured_at"].startswith("2026-11-08T12:00:00")
    assert states["leg/status"] == "unsicher" and states["leg/surplus_kw"] == UNKNOWN and states["leg/coverage"] == "50"


def test_valid_zero_is_published_as_zero(service, db_path):
    _active(service, db_path)
    _deliver(service, _sample(imp=0.0, exp=0.0))
    states = build_states(service, service.tick())
    assert states["own/import_w"] == "0.0" and states["own/export_w"] == "0.0"


@pytest.mark.parametrize("fault", ["null", "transport", "none"])
def test_no_false_zero_on_failure(service, db_path, fault):
    _active(service, db_path)
    if fault == "null":
        _deliver(service, _sample(imp=None, exp=None))
    elif fault == "transport":
        _deliver(service)
        service.submit(TransportChange(EM, False))
        service.process_pending()
    states = build_states(service, service.tick())
    assert states["own/import_w"] == UNKNOWN and states["own/export_w"] == UNKNOWN
    assert states["own/quality"] == "missing" and states["leg/surplus_kw"] == UNKNOWN


def test_without_source_nothing_is_invented(service):
    states = build_states(service, service.tick())
    assert states["own/import_w"] == UNKNOWN and states["own/quality"] == "missing"
    assert states["leg/status"] == "keine_daten" and states["leg/surplus_kw"] == UNKNOWN


def test_stale_after_restart_of_source(service, db_path):
    _active(service, db_path)
    _deliver(service)
    service._clock.now = NOW + timedelta(seconds=200)
    states = build_states(service, service.tick())
    assert states["own/import_w"] == UNKNOWN and states["own/quality"] == "stale"


def test_surplus_number_only_with_complete_balance(service, db_path):
    ids = [_active(service, db_path)]
    other = create_live_source(db_path, meter_id="M2", adapter=ADAPTER_WAN_SPARKPLUG, edge_node_id="S", device_id="P2", max_power_w=20000)
    activate_live_source(db_path, other)
    ids.append(other)
    _deliver(service, _sample(imp=0.0, exp=2500.0))
    states = build_states(service, service.tick())
    assert states["leg/surplus_kw"] == UNKNOWN
    assert ids


def test_status_attributes_contain_no_other_participants(service, db_path):
    _active(service, db_path)
    _deliver(service)
    attributes = json.loads(build_states(service, service.tick())["leg/status/attributes"])
    text = json.dumps(attributes)
    assert "P2" not in text and "M2" not in text and attributes["messpunkte_erwartet"] == 2


def test_publisher_retains_and_only_republishes_changes(service, db_path):
    _active(service, db_path)
    _deliver(service)
    client = FakeClient()
    publisher = LiveHaPublisher(client, _config())
    status = service.tick()
    publisher(service, status)
    first = len(client.published)
    assert first > 0 and all(retain for *_, retain in client.published)
    publisher(service, status)
    assert len(client.published) == first


def test_identity_proof_needs_a_note(service, db_path):
    source_id = _active(service, db_path)
    with pytest.raises(FormError):
        live_energy.handle_post(_post(["sources", str(source_id), "identity"], {"identity_note": "  "}))
    record_identity_proof(db_path, source_id, note="ACL-Test")
    assert get_live_source(db_path, source_id).identity_verified
    live_energy.handle_post(_post(["sources", str(source_id), "identity-clear"]))
    assert not get_live_source(db_path, source_id).identity_verified
