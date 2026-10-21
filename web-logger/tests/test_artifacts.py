import json
from pathlib import Path

import pytest
import yaml

from kotel_reader.config import load


def test_config_example_and_artifacts():
    config = load("config/config.example.yaml")
    assert config["thematronic"]["mode"] == "read_only"
    assert config["session"]["expected_session_lifetime_s"] is None
    for file in ("compose.yaml", "compose.macvlan.yaml", "compose.test.yaml", "deploy/ha-kotelna.yaml", "deploy/grafana-datasource.yaml", "deploy/prometheus-scrape.yaml", "deploy/truenas-app.yaml"):
        assert isinstance(yaml.safe_load(Path(file).read_text(encoding="utf-8")), dict)
    dash = json.loads(Path("deploy/grafana-dashboard.json").read_text(encoding="utf-8"))
    assert len(dash["panels"]) >= 7
    for panel in dash["panels"]:
        assert "expr" in panel["targets"][0]
        assert panel["datasource"]["type"] == "prometheus"
        if panel["type"] in ("timeseries", "state-timeline"):
            assert not panel["fieldConfig"]["defaults"]["custom"]["spanNulls"]


@pytest.mark.parametrize("section,value", [("thematronic", {"url": "http://bad"}),
    ("thematronic", {"poll_s": 0}), ("session", {"cooldown_s": 10}), ("mqtt", {"enabled": "false"}),
    ("thematronic", {"mode": "control_boiler"}), ("prometheus", {"port": -1}),
    ("prometheus", {"port": 70000}), ("telemetry", {"stale_s": 0})])
def test_bad_config(tmp_path, section, value):
    source = yaml.safe_load(Path("config/config.example.yaml").read_text(encoding="utf-8"))
    source["signals_file"] = str(Path("config/signals.yaml").resolve())
    source[section].update(value)
    file = tmp_path / "bad.yaml"
    file.write_text(yaml.safe_dump(source), encoding="utf-8")
    with pytest.raises(ValueError):
        load(str(file))


def test_old_postgres_configuration_has_explicit_upgrade_error(tmp_path):
    file = tmp_path / "old.yaml"
    file.write_text("postgres:\n  enabled: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Version 0.2 uses Prometheus"):
        load(str(file))


def test_deployment_has_metrics_port_and_no_postgres():
    for name in ("compose.yaml", "deploy/truenas-app.yaml"):
        config = yaml.safe_load(Path(name).read_text(encoding="utf-8"))
        service = config["services"]["collector"]
        assert service["ports"][0]["target"] == 9108
        assert set(service["secrets"]) == {"thematronic_username", "thematronic_password", "mqtt_password"}
        assert "POSTGRES_PASSWORD_FILE" not in service["environment"]
    assert not Path("deploy/truenas-migrate.yaml").exists()
