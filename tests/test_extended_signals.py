import json
from pathlib import Path

import pytest
import yaml
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from kotel_reader.metrics import Metrics
from kotel_reader.model import normalize
from kotel_reader.mqtt import discovery
from kotel_reader.telemetry import Telemetry


@pytest.mark.parametrize("page", ["temperatures", "states"])
def test_captured_signals_export_without_confusing_sensors(page, cfg):
    root = Path(__file__).resolve().parents[1]
    mapping = yaml.safe_load((root / "config/signals.yaml").read_text(encoding="utf-8"))
    raw = json.loads((root / f"tests/fixtures/{page}_signals.json").read_text(encoding="utf-8"))
    sample = normalize(raw, 2985, mapping, 100)
    telemetry = Telemetry(60)
    exporter = Metrics(cfg["prometheus"], mapping, 2985, telemetry)
    exporter.update("POLLING", {}, "connected")
    telemetry.offer(sample)
    exported = {(s.name, s.labels.get("signal")): s.value
                for family in text_string_to_metric_families(generate_latest(exporter.registry).decode())
                for s in family.samples}
    assert exported["kotel_temperature_celsius", "dev_prost_z2"] == 21
    assert exported["kotel_temperature_celsius", "s_t_zo2_voda_poz"] == 39.8
    assert exported["kotel_maximum_temperature_celsius", "max_t_spal"] == 369.6
    assert ("kotel_temperature_celsius", "max_t_spal") not in exported
    for signal in ("dev_prost_z1", "s_t_zo1_voda_poz", "s_t_aku2h", "max_t_aku2h", "s_t_tuv"):
        assert exported["kotel_signal_available", signal] == 0
        assert ("kotel_temperature_celsius", signal) not in exported
        assert ("kotel_maximum_temperature_celsius", signal) not in exported
    assert exported["kotel_signal_state", "s_o_zona2_top"] == 1
    assert exported["kotel_signal_value", "dev_set_z2"] == 1
    assert ("kotel_signal_value", "dev_term_z2") not in exported
    entities = {config["unique_id"]: config for _, config in discovery(cfg["mqtt"], mapping, 2985, 60)}
    assert "thematronic_2985_dev_prost_z2" in entities
    assert "thematronic_2985_dev_term_z2" in entities
    assert "device_class" not in entities["thematronic_2985_s_o_zd1_porucha"]
    assert "s_o_rez_inp3" in sample["unknown_signals"]
