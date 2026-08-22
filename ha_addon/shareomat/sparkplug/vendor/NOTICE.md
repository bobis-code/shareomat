# Vendored code notice

The files in this directory are adapted from
[pysparkplug](https://github.com/matteosox/pysparkplug) (PyPI: `pysparkplug`,
version 0.6.1), licensed under the Apache License 2.0 (see `LICENSE`).

Identical copy of the vendor layer used in the Emsomat repo
(`Emsomat/sparkplug/vendor/`) - see that copy's NOTICE.md for the full
rationale. Short version for Shareomat specifically: `pysparkplug` requires
`paho-mqtt<2`, but Shareomat's `requirements.txt` pins `paho-mqtt>=1.6.1`
with no upper bound - installing `pysparkplug` would silently constrain
future upgrades and duplicates what `shareomat/ha/mqtt_runtime.py` already
does itself (a small v1/v2 compatibility shim around `paho.mqtt.client`).
Using the same vendored payload/protobuf/DataSet code as Emsomat, on top of
that existing compatibility pattern, avoids depending on two different
Sparkplug B wire-format implementations that could subtly diverge.

## Changes made relative to upstream

Same as the Emsomat copy: import paths rewritten to local relative imports;
`_message.py`/`_client.py`/`_edge_node.py`/`_config.py`/`_error.py` not
vendored (paho-mqtt-client-coupled, replaced by Shareomat's own connection
code in `shareomat/sparkplug/host.py` and `participant_relay.py`); `enums.py`
trimmed to `MessageType`/`QoS`; `payload.py`'s `State` class re-implemented
to the Sparkplug 3.0 bare-string wire format (`b"ONLINE"`/`b"OFFLINE"`)
instead of pysparkplug's own pre-3.0 JSON encoding - see
`Emsomat_Shareomat_MQTT_Vertrag.md` Abschnitt 28.1/28.2 for why.

See `docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md` in this repo for the
full Sparkplug B integration design this supports.
