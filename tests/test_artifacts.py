import json
from pathlib import Path

import pytest
import yaml

from kotel_reader.config import load


def test_config_example_and_artifacts():
    config = load("config/config.example.yaml")
    assert config["thematronic"]["mode"] == "read_only"
    assert config["session"]["expected_session_lifetime_s"] is None
    for file in ("compose.yaml", "compose.macvlan.yaml", "compose.test.yaml", "deploy/ha-kotelna.yaml", "deploy/grafana-datasource.yaml"):
        assert isinstance(yaml.safe_load(Path(file).read_text(encoding="utf-8")), dict)
    dash = json.loads(Path("deploy/grafana-dashboard.json").read_text(encoding="utf-8"))
    assert len(dash["panels"]) >= 7
    for panel in dash["panels"]:
        assert "$__timeFilter" in panel["targets"][0]["rawSql"]
        if panel["type"] in ("timeseries", "state-timeline"):
            assert "LEFT JOIN" in panel["targets"][0]["rawSql"]
            assert not panel["fieldConfig"]["defaults"]["custom"]["spanNulls"]


@pytest.mark.parametrize("section,value", [("thematronic", {"url": "http://bad"}),
    ("thematronic", {"poll_s": 0}), ("session", {"cooldown_s": 10}), ("mqtt", {"enabled": "false"}),
    ("thematronic", {"mode": "control_boiler"}), ("queue", {"max_items": -1})])
def test_bad_config(tmp_path, section, value):
    source = yaml.safe_load(Path("config/config.example.yaml").read_text(encoding="utf-8"))
    source["signals_file"] = str(Path("config/signals.yaml").resolve())
    source[section].update(value)
    file = tmp_path / "bad.yaml"
    file.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(ValueError):
        load(str(file))

