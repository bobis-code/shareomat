# -*- coding: utf-8 -*-
"""Regression tests for participant IDs encoded in GET and POST URLs."""

from types import SimpleNamespace
from urllib.parse import quote

import pytest

from shareomat.database.participants import create_participant, get_participant
from shareomat.database.meters import create_meter, get_meter
from shareomat.database.sqlite import init_db
from shareomat.models.participant import Participant
from shareomat.models.meter import Meter
from shareomat.web.pages import meters, participants
from shareomat.web.rendering import RequestContext
from shareomat.web.server import _Handler
from shareomat.web.state import get_state


@pytest.mark.parametrize("participant_id", ["Prüfperson_01", "Wohnung 2", "Haus/2?#", "Literal%2F"])
@pytest.mark.parametrize("ingress_path", ["", "/api/hassio_ingress/test"])
def test_participant_links_and_actions_round_trip(tmp_path, participant_id, ingress_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    get_state().register_db(db_path)
    create_participant(db_path, Participant(
        participant_id=participant_id, label="Test participant", participant_type="consumer",
    ))
    encoded_id = quote(participant_id, safe="")
    ctx = RequestContext(method="GET", ingress_path=ingress_path)
    listing = participants.handle_get(ctx)
    assert f'{ingress_path}/participants/{encoded_id}/edit' in listing
    assert f'{ingress_path}/participants/{encoded_id}/toggle' in listing

    rendered = []
    redirects = []
    handler = SimpleNamespace(
        path=f"/participants/{encoded_id}/edit",
        headers={},
        _ingress_path=lambda: ingress_path,
        _send_html=rendered.append,
        _redirect=redirects.append,
        _parse_urlencoded_form=lambda: {
            "csrf_token": get_state().csrf_token,
            "participant_id": participant_id,
            "label": "Updated participant",
            "participant_type": "consumer",
            "active": "on",
        },
    )
    _Handler.do_GET(handler)
    assert "Teilnehmer bearbeiten" in rendered[0]
    assert f'{ingress_path}/participants/{encoded_id}/edit' in rendered[0]
    _Handler.do_POST(handler)
    assert get_participant(db_path, participant_id).label == "Updated participant"
    assert redirects == [f"{ingress_path}/participants"]

    handler.path = f"/participants/{encoded_id}/toggle"
    _Handler.do_POST(handler)
    assert not get_participant(db_path, participant_id).active


def test_missing_participant_explains_stale_link(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    get_state().register_db(db_path)
    html = participants.handle_get(RequestContext(
        method="GET", ingress_path="", segments=["old-id", "edit"],
    ))
    assert "Teilnehmer nicht gefunden" in html
    assert "Teilnehmerliste" in html
    assert "Diese Funktion ist in Vorbereitung." not in html


@pytest.mark.parametrize("meter_id", ["Zähler_01", "Messpunkt 2", "Haus/2?#", "Literal%2F"])
@pytest.mark.parametrize("ingress_path", ["", "/api/hassio_ingress/test"])
def test_meter_links_and_actions_round_trip(tmp_path, meter_id, ingress_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    get_state().register_db(db_path)
    create_participant(db_path, Participant(
        participant_id="test-participant", label="Test participant", participant_type="consumer",
    ))
    create_meter(db_path, Meter(
        meter_id=meter_id, participant_id="test-participant", label="Test meter", role="consumer",
    ))
    encoded_id = quote(meter_id, safe="")
    listing = meters.handle_get(RequestContext(method="GET", ingress_path=ingress_path))
    assert f'{ingress_path}/meters/{encoded_id}/edit' in listing
    assert f'{ingress_path}/meters/{encoded_id}/toggle' in listing
    rendered = []
    redirects = []
    handler = SimpleNamespace(
        path=f"/meters/{encoded_id}/edit",
        headers={},
        _ingress_path=lambda: ingress_path,
        _send_html=rendered.append,
        _redirect=redirects.append,
        _parse_urlencoded_form=lambda: {
            "csrf_token": get_state().csrf_token,
            "meter_id": meter_id,
            "participant_id": "test-participant",
            "label": "Updated meter",
            "role": "consumer",
            "active": "on",
        },
    )
    _Handler.do_GET(handler)
    assert "Messpunkt bearbeiten" in rendered[0]
    assert f'{ingress_path}/meters/{encoded_id}/edit' in rendered[0]
    _Handler.do_POST(handler)
    assert get_meter(db_path, meter_id).label == "Updated meter"
    assert redirects == [f"{ingress_path}/meters"]
    handler.path = f"/meters/{encoded_id}/toggle"
    _Handler.do_POST(handler)
    assert not get_meter(db_path, meter_id).active
