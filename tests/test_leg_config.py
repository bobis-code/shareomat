# -*- coding: utf-8 -*-
"""Tests for configuration loading and validation."""
from __future__ import annotations

import pytest
import yaml
from pathlib import Path
from shareomat.core.leg_config import load_config, validate_config


def _write_yaml(tmp_path, data):
    p = tmp_path / "leg_config.yaml"
    p.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")
    return p


def _minimal_config():
    return {
        "leg": {"community_id": "ZEV-001", "name": "Test"},
        "participants": [
            {"participant_id": "P1", "label": "Solar", "participant_type": "producer_consumer"},
            {"participant_id": "P2", "label": "Flat 1", "participant_type": "consumer"},
        ],
        "meters": [
            {"meter_id": "M1", "participant_id": "P1", "label": "Solar", "role": "producer_consumer"},
            {"meter_id": "M2", "participant_id": "P2", "label": "Flat 1", "role": "consumer"},
        ],
        "tariffs": {"local_rate_chf_kwh": 0.12, "grid_rate_chf_kwh": 0.28, "feed_in_rate_chf_kwh": 0.08},
        "paths": {"inbox": "/tmp/inbox", "archive": "/tmp/archive", "reports": "/tmp/reports", "state": "/tmp/state"},
        "processing": {"slot_minutes": 15, "archive_processed": True, "unknown_meter_policy": "skip"},
    }


def test_valid_config_loads(tmp_path):
    p = _write_yaml(tmp_path, _minimal_config())
    config = load_config(p)
    assert config.community_id == "ZEV-001"
    assert len(config.participants) == 2
    assert len(config.meters) == 2


def test_no_participants_raises(tmp_path):
    data = _minimal_config()
    data["participants"] = []
    p = _write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="participant"):
        load_config(p)


def test_no_producer_meter_raises(tmp_path):
    data = _minimal_config()
    data["meters"] = [
        {"meter_id": "M2", "participant_id": "P2", "label": "Flat 1", "role": "consumer"},
    ]
    p = _write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="producer"):
        load_config(p)


def test_no_consumer_meter_raises(tmp_path):
    data = _minimal_config()
    data["meters"] = [
        {"meter_id": "M1", "participant_id": "P1", "label": "Solar", "role": "producer"},
    ]
    p = _write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="consumer"):
        load_config(p)


def test_unknown_participant_in_meter_raises(tmp_path):
    data = _minimal_config()
    data["meters"][0]["participant_id"] = "UNKNOWN"
    p = _write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="unknown participant"):
        load_config(p)


def test_invalid_meter_role_raises(tmp_path):
    data = _minimal_config()
    data["meters"][0]["role"] = "invalid_role"
    p = _write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="invalid role"):
        load_config(p)


def test_negative_tariff_raises(tmp_path):
    data = _minimal_config()
    data["tariffs"]["local_rate_chf_kwh"] = -0.1
    p = _write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="tariff"):
        load_config(p)


def test_duplicate_participant_id_raises(tmp_path):
    data = _minimal_config()
    data["participants"].append({"participant_id": "P1", "label": "Dup", "participant_type": "consumer"})
    data["meters"].append({"meter_id": "M3", "participant_id": "P1", "label": "Dup", "role": "consumer"})
    p = _write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        load_config(p)


def test_tls_fields_parse(tmp_path):
    data = _minimal_config()
    data["mqtt"] = {
        "enabled": False,
        "broker": "localhost",
        "port": 8883,
        "tls_enabled": True,
        "tls_ca_cert": "/etc/ssl/ca.pem",
    }
    p = _write_yaml(tmp_path, data)
    config = load_config(p)
    assert config.mqtt.tls_enabled is True
    assert config.mqtt.tls_ca_cert == "/etc/ssl/ca.pem"


def test_cron_schedule_parses(tmp_path):
    data = _minimal_config()
    data["processing"]["cron_schedule"] = "0 6 * * *"
    p = _write_yaml(tmp_path, data)
    config = load_config(p)
    assert config.processing.cron_schedule == "0 6 * * *"


def test_mqtt_port_range_valid(tmp_path):
    data = _minimal_config()
    data["mqtt"] = {
        "enabled": True,
        "broker": "localhost",
        "port": 1883,
    }
    p = _write_yaml(tmp_path, data)
    config = load_config(p)
    assert config.mqtt.port == 1883


def test_processing_defaults(tmp_path):
    data = _minimal_config()
    p = _write_yaml(tmp_path, data)
    config = load_config(p)
    assert config.processing.cron_schedule == ""
    assert config.processing.auto_scan_enabled is False
    assert config.processing.scan_interval_seconds == 60


def test_ingress_defaults(tmp_path):
    data = _minimal_config()
    p = _write_yaml(tmp_path, data)
    config = load_config(p)
    assert config.ingress.enabled is False
    assert config.ingress.port == 8099


def test_email_defaults(tmp_path):
    data = _minimal_config()
    p = _write_yaml(tmp_path, data)
    config = load_config(p)
    assert config.email.enabled is False
    assert config.email.imap_host == "imap.gmail.com"
    assert config.email.imap_port == 993
    assert config.email.folder == "INBOX"
    assert config.email.allowed_senders == []


def test_email_enabled_requires_username(tmp_path):
    data = _minimal_config()
    data["email"] = {"enabled": True}
    p = _write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="email.username"):
        load_config(p)


def test_email_port_range_invalid(tmp_path):
    data = _minimal_config()
    data["email"] = {"enabled": True, "username": "a@example.com", "imap_port": 99999}
    p = _write_yaml(tmp_path, data)
    with pytest.raises(ValueError, match="email.imap_port"):
        load_config(p)


def test_email_allowed_senders_lowercased(tmp_path):
    data = _minimal_config()
    data["email"] = {"allowed_senders": ["Grid@Operator.CH"]}
    p = _write_yaml(tmp_path, data)
    config = load_config(p)
    assert config.email.allowed_senders == ["grid@operator.ch"]
