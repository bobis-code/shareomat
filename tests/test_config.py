# -*- coding: utf-8 -*-
"""Tests for shareomat.config: load_runtime_config() and validate_leg_config()."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from shareomat.config import LegConfig, ProcessingConfig, load_runtime_config, validate_leg_config
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff


def _write_yaml(tmp_path, data):
    p = tmp_path / "leg_config.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return p


def _minimal_runtime_yaml():
    return {
        "paths": {"inbox": "/tmp/inbox", "archive": "/tmp/archive", "reports": "/tmp/reports", "state": "/tmp/state"},
    }


# ── load_runtime_config: only technical sections, no master data ──────────────


def test_runtime_config_has_no_master_data_fields(tmp_path):
    p = _write_yaml(tmp_path, _minimal_runtime_yaml())
    runtime = load_runtime_config(p)
    assert not hasattr(runtime, "participants")
    assert not hasattr(runtime, "community_id")
    assert runtime.paths.inbox == Path("/tmp/inbox")


def test_runtime_config_tls_fields_parse(tmp_path):
    data = _minimal_runtime_yaml()
    data["mqtt"] = {
        "enabled": False, "broker": "localhost", "port": 8883,
        "tls_enabled": True, "tls_ca_cert": "/etc/ssl/ca.pem",
    }
    p = _write_yaml(tmp_path, data)
    runtime = load_runtime_config(p)
    assert runtime.mqtt.tls_enabled is True
    assert runtime.mqtt.tls_ca_cert == "/etc/ssl/ca.pem"


def test_runtime_config_web_defaults(tmp_path):
    p = _write_yaml(tmp_path, _minimal_runtime_yaml())
    runtime = load_runtime_config(p)
    assert runtime.web.enabled is True
    assert runtime.web.port == 8099


def test_runtime_config_email_defaults(tmp_path):
    p = _write_yaml(tmp_path, _minimal_runtime_yaml())
    runtime = load_runtime_config(p)
    assert runtime.email.enabled is False
    assert runtime.email.imap_host == "imap.gmail.com"
    assert runtime.email.allowed_senders == []


def test_runtime_config_email_allowed_senders_lowercased(tmp_path):
    data = _minimal_runtime_yaml()
    data["email"] = {"allowed_senders": ["Grid@Operator.CH"]}
    p = _write_yaml(tmp_path, data)
    runtime = load_runtime_config(p)
    assert runtime.email.allowed_senders == ["grid@operator.ch"]


# ── validate_leg_config: rules operate on the assembled LegConfig ─────────────


def _leg_config(**overrides) -> LegConfig:
    defaults = dict(
        community=Community(community_id="ZEV-001", name="Test"),
        participants=[
            Participant("P1", "Solar", "producer_consumer"),
            Participant("P2", "Flat 1", "consumer"),
        ],
        meters=[
            Meter("M1", "P1", "Solar", "producer_consumer"),
            Meter("M2", "P2", "Flat 1", "consumer"),
        ],
        tariff=Tariff(
            local_rate_chf_kwh=Decimal("0.12"), grid_rate_chf_kwh=Decimal("0.28"),
            feed_in_rate_chf_kwh=Decimal("0.08"),
        ),
        paths=None,
        processing=ProcessingConfig(),
    )
    from shareomat.config import PathConfig
    defaults["paths"] = PathConfig(
        inbox=Path("/tmp/inbox"), archive=Path("/tmp/archive"),
        reports=Path("/tmp/reports"), state=Path("/tmp/state"),
    )
    defaults.update(overrides)
    return LegConfig(**defaults)


def test_valid_config_passes():
    validate_leg_config(_leg_config())  # must not raise


def test_no_participants_raises():
    with pytest.raises(ValueError, match="Teilnehmer"):
        validate_leg_config(_leg_config(participants=[]))


def test_no_producer_meter_raises():
    config = _leg_config(meters=[Meter("M2", "P2", "Flat 1", "consumer")])
    with pytest.raises(ValueError, match="producer"):
        validate_leg_config(config)


def test_no_consumer_meter_raises():
    config = _leg_config(meters=[Meter("M1", "P1", "Solar", "producer")])
    with pytest.raises(ValueError, match="consumer"):
        validate_leg_config(config)


def test_unknown_participant_in_meter_raises():
    config = _leg_config(meters=[
        Meter("M1", "UNKNOWN", "Solar", "producer_consumer"),
        Meter("M2", "P2", "Flat 1", "consumer"),
    ])
    with pytest.raises(ValueError, match="unknown participant"):
        validate_leg_config(config)


def test_invalid_meter_role_raises():
    config = _leg_config(meters=[
        Meter("M1", "P1", "Solar", "invalid_role"),
        Meter("M2", "P2", "Flat 1", "consumer"),
    ])
    with pytest.raises(ValueError, match="invalid role"):
        validate_leg_config(config)


def test_negative_tariff_raises():
    config = _leg_config(tariff=Tariff(
        local_rate_chf_kwh=Decimal("-0.1"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"),
    ))
    with pytest.raises(ValueError, match="tariff"):
        validate_leg_config(config)


def test_duplicate_participant_id_raises():
    config = _leg_config(participants=[
        Participant("P1", "Solar", "producer_consumer"),
        Participant("P2", "Flat 1", "consumer"),
        Participant("P1", "Dup", "consumer"),
    ])
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        validate_leg_config(config)


def test_missing_community_id_raises():
    config = _leg_config(community=Community(community_id="", name="No ID"))
    with pytest.raises(ValueError, match="Gemeinschaft"):
        validate_leg_config(config)


def test_wrong_slot_minutes_raises():
    config = _leg_config(processing=ProcessingConfig(slot_minutes=5))
    with pytest.raises(ValueError, match="slot_minutes"):
        validate_leg_config(config)
