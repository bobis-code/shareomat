# -*- coding: utf-8 -*-
"""Tests fuer Emsomat/sparkplug/vendor/* (vendorte Sparkplug-B-Payload-/Topic-Bausteine)."""

import pytest

from shareomat.sparkplug.vendor.dataset import Column, Dataset
from shareomat.sparkplug.vendor.datatype import DataType
from shareomat.sparkplug.vendor.enums import MessageType
from shareomat.sparkplug.vendor.metric import Metric
from shareomat.sparkplug.vendor.payload import DBirth, DDeath, NBirth, NData, NDeath, State
from shareomat.sparkplug.vendor.topic import Topic


def _metric(name, value, dtype=DataType.DOUBLE, alias=None):
    return Metric(timestamp=1000, name=name, datatype=dtype, value=value, alias=alias)


def test_nbirth_roundtrip():
    m = _metric("Market/ExportPowerNow", 2400.5)
    birth = NBirth(timestamp=1000, seq=0, metrics=(m,))
    decoded = NBirth.decode(birth.encode())
    assert decoded.metrics[0].name == "Market/ExportPowerNow"
    assert decoded.metrics[0].value == 2400.5
    assert decoded.metrics[0].datatype == DataType.DOUBLE
    assert decoded.seq == 0


def test_ndata_roundtrip_self_describing():
    # Emsomats EdgeNodeAdapter schickt NDATA immer self-describing
    # (include_dtypes=True) - keine Alias-Buchhaltung, siehe edge_node.py.
    m = _metric("Market/ImportPowerNow", 0.0)
    data = NData(timestamp=2000, seq=1, metrics=(m,))
    decoded = NData.decode(data.encode(include_dtypes=True))
    assert decoded.metrics[0].value == 0.0
    assert decoded.seq == 1


def test_ndata_resolves_alias_and_dtype_from_birth():
    # NBIRTH mit Alias, damit NDATA nur den Alias mitschickt (include_dtypes=False).
    # Der Sender kennt den echten Datatype weiterhin lokal (fuers Encoding) -
    # include_dtypes=False steuert nur, ob das datatype-Feld mit auf die Leitung
    # geht; der Empfaenger loest ihn stattdessen ueber birth.get_dtype() auf.
    birth_metric = _metric("Market/ExportPowerNow", 100.0, alias=1)
    birth = NBirth(timestamp=0, seq=0, metrics=(birth_metric,))

    ndata_metric = Metric(timestamp=1500, name=None, datatype=DataType.DOUBLE, value=250.0, alias=1)
    data = NData(timestamp=1500, seq=1, metrics=(ndata_metric,))
    raw = data.encode(include_dtypes=False)

    decoded = NData.decode(raw, birth=birth)
    assert decoded.metrics[0].name == "Market/ExportPowerNow"
    assert decoded.metrics[0].value == 250.0


def test_ndeath_carries_bdseq_metric():
    bd = Metric(timestamp=None, name="bdSeq", datatype=DataType.INT64, value=5)
    ndeath = NDeath(timestamp=None, bd_seq_metric=bd)
    decoded = NDeath.decode(ndeath.encode())
    assert decoded.bd_seq_metric.name == "bdSeq"
    assert decoded.bd_seq_metric.value == 5


def test_ddeath_roundtrip():
    ddeath = DDeath(timestamp=3000, seq=7)
    decoded = DDeath.decode(ddeath.encode())
    assert decoded.timestamp == 3000
    assert decoded.seq == 7


def test_birth_requires_name_and_datatype():
    with pytest.raises(ValueError):
        NBirth(timestamp=0, seq=0, metrics=(Metric(timestamp=0, name=None, datatype=DataType.DOUBLE, value=1.0),))
    with pytest.raises(ValueError):
        NBirth(timestamp=0, seq=0, metrics=(Metric(timestamp=0, name="x", datatype=DataType.UNKNOWN, value=1.0),))


def test_state_wire_format_is_plain_string_not_json():
    """Sparkplug 3.0 normative: STATE payload MUST be the bare string ONLINE/OFFLINE,
    not the pre-3.0 JSON convention - siehe Emsomat_Shareomat_MQTT_Vertrag.md Abschnitt 28.2."""
    online = State(online=True)
    assert online.encode() == b"ONLINE"

    offline = State(online=False)
    assert offline.encode() == b"OFFLINE"

    assert State.decode(b"ONLINE").online is True
    assert State.decode(b"OFFLINE").online is False


def test_state_rejects_unknown_payload():
    with pytest.raises(ValueError):
        State.decode(b'{"online": true}')


def test_topic_edge_node_roundtrip():
    t = Topic(group_id="LEG-0001", message_type=MessageType.NBIRTH, edge_node_id="EMSOMAT-A")
    assert str(t) == "spBv1.0/LEG-0001/NBIRTH/EMSOMAT-A"
    parsed = Topic.from_str(str(t))
    assert parsed.group_id == "LEG-0001"
    assert parsed.message_type == MessageType.NBIRTH
    assert parsed.edge_node_id == "EMSOMAT-A"


def test_topic_device_roundtrip():
    t = Topic(group_id="LEG-0001", message_type=MessageType.DBIRTH, edge_node_id="EMSOMAT-A", device_id="PARTICIPANT-B")
    assert str(t) == "spBv1.0/LEG-0001/DBIRTH/EMSOMAT-A/PARTICIPANT-B"


def test_topic_state_uses_separate_namespace():
    t = Topic(message_type=MessageType.STATE, sparkplug_host_id="SHAREOMAT-A")
    assert str(t) == "spBv1.0/STATE/SHAREOMAT-A"
    parsed = Topic.from_str(str(t))
    assert parsed.message_type == MessageType.STATE
    assert parsed.sparkplug_host_id == "SHAREOMAT-A"


def test_topic_rejects_invalid_namespace():
    with pytest.raises(ValueError):
        Topic.from_str("wrong-namespace/LEG-0001/NBIRTH/EMSOMAT-A")


# ---------------------------------------------------------------------------
# DataSet - fuer LEG/DemandForecast (LegDemandForecast-Slot-Serie, siehe
# Emsomat/shareomat/models.py), nicht von pysparkplug uebernommen (dort
# explizit "Unsupported").
# ---------------------------------------------------------------------------

_LEG_FORECAST_COLUMNS = (
    Column("slot_start", DataType.STRING),
    Column("forecast_kwh", DataType.DOUBLE),
    Column("quality", DataType.STRING),
    Column("sample_count", DataType.INT64),
)


def test_dataset_roundtrip_matches_leg_demand_slot_shape():
    ds = Dataset(
        columns=_LEG_FORECAST_COLUMNS,
        rows=(
            ("2026-08-22T14:00:00Z", 3.5, "ok", 12),
            ("2026-08-22T14:15:00Z", 4.1, "ok", 12),
            ("2026-08-22T14:30:00Z", float("nan"), "insufficient_data", 1),
        ),
    )
    m = Metric(timestamp=1000, name="LEG/DemandForecast", datatype=DataType.DATASET, value=ds)
    birth = DBirth(timestamp=1000, seq=0, metrics=(m,))
    decoded = DBirth.decode(birth.encode())

    decoded_ds = decoded.metrics[0].value
    assert isinstance(decoded_ds, Dataset)
    assert [c.name for c in decoded_ds.columns] == ["slot_start", "forecast_kwh", "quality", "sample_count"]
    assert decoded_ds.rows[0] == ("2026-08-22T14:00:00Z", 3.5, "ok", 12)
    assert decoded_ds.rows[1][1] == 4.1
    import math
    assert math.isnan(decoded_ds.rows[2][1])
    assert decoded_ds.rows[2][2] == "insufficient_data"


def test_dataset_row_length_must_match_columns():
    with pytest.raises(ValueError):
        Dataset(columns=_LEG_FORECAST_COLUMNS, rows=(("only", "two"),))


def test_dataset_rejects_null_cell():
    ds = Dataset(columns=_LEG_FORECAST_COLUMNS, rows=(("2026-08-22T14:00:00Z", None, "ok", 1),))
    with pytest.raises(ValueError):
        ds.to_pb()


def test_dataset_rejects_unsupported_column_datatype():
    with pytest.raises(NotImplementedError):
        Column("template", DataType.TEMPLATE)


def test_dataset_empty_rows_roundtrip():
    ds = Dataset(columns=_LEG_FORECAST_COLUMNS, rows=())
    m = Metric(timestamp=1000, name="LEG/DemandForecast", datatype=DataType.DATASET, value=ds)
    birth = DBirth(timestamp=1000, seq=0, metrics=(m,))
    decoded = DBirth.decode(birth.encode())
    assert decoded.metrics[0].value.rows == ()
