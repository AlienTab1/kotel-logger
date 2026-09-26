"""Generate the Prometheus dashboard without network access."""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
SCOPE = 'job=~"$job",instance=~"$instance",device_id=~"$device_id"'
panels = []


def panel(title, kind, expressions, x, y, w=12, h=8, unit=None):
    item = {
        "id": len(panels) + 1, "title": title, "type": kind, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": chr(65 + i), "datasource": DS, "expr": expr, "legendFormat": legend,
                     "range": kind != "stat", "instant": kind == "stat"} for i, (expr, legend) in enumerate(expressions)],
        "fieldConfig": {"defaults": {"custom": {"spanNulls": False}}, "overrides": []},
        "options": {},
    }
    if unit:
        item["fieldConfig"]["defaults"]["unit"] = unit
    if kind == "stat":
        item["options"]["reduceOptions"] = {"calcs": ["lastNotNull"], "fields": "", "values": False}
    panels.append(item)


def metric(name, extra=""):
    return f"{name}{{{SCOPE}{extra}}}"


panel("Boiler and flue temperatures", "timeseries", [(metric("kotel_temperature_celsius", ',signal=~"s_t_spal|s_t_ko_vy|s_t_ko_vr"'), "{{signal}}")], 0, 0, unit="celsius")
panel("Thermal storage", "timeseries", [(metric("kotel_temperature_celsius", ',signal=~"s_t_akuh|s_t_akus|s_t_akud"'), "{{signal}}")], 12, 0, unit="celsius")
panel("Heating zones and outdoor", "timeseries", [(metric("kotel_temperature_celsius", ',signal=~"s_t_zo1_vy|s_t_zo2_vy|s_t_venku"'), "{{signal}}")], 0, 8, unit="celsius")
panel("Reception age (not measurement age)", "stat", [(metric("kotel_receive_age_seconds"), "Age")], 12, 8, h=4, unit="s")
panel("Last received response", "stat", [(metric("kotel_last_received_timestamp_seconds") + " * 1000", "Received")], 12, 12, h=4, unit="dateTimeAsIso")
panel("Availability", "state-timeline", [
    ('up{job=~"$job",instance=~"$instance"}', "Scrape reachable"),
    (metric("kotel_collector_healthy"), "Scheduler healthy"),
    (metric("kotel_telemetry_available"), "Web data available"),
    (metric("kotel_mqtt_connected"), "MQTT connected"),
], 0, 16, w=24, h=6)
panel("Raw boolean states — verify meaning and polarity", "state-timeline", [(metric("kotel_signal_state"), "{{signal}}")], 0, 22, w=24, h=6)
panel("Session state", "state-timeline", [(metric("kotel_session_state") + " == 1", "{{state}}")], 0, 28, w=24, h=6)
panel("HTTP and session events per second", "timeseries", [
    ("rate(" + metric("kotel_http_errors_total") + "[$__rate_interval])", "HTTP errors"),
    ("rate(" + metric("kotel_login_attempts_total") + "[$__rate_interval])", "Login attempts"),
    ("rate(" + metric("kotel_session_losses_total") + "[$__rate_interval])", "Session losses"),
], 0, 34, unit="ops")
panel("Signal availability", "state-timeline", [(metric("kotel_signal_available"), "{{signal}}")], 12, 34)

panel("Room temperatures and zone water targets", "timeseries", [(metric("kotel_temperature_celsius", ',signal=~"dev_prost_z[12]|s_t_zo[12]_voda_poz"'), "{{signal}}")], 0, 42, unit="celsius")
panel("Additional temperatures", "timeseries", [(metric("kotel_temperature_celsius", ',signal=~"s_t_nezamrz|s_t_blokace|s_t_solar|s_t_aku2.*"'), "{{signal}}")], 12, 42, unit="celsius")
panel("Source maxima — period unverified", "timeseries", [(metric("kotel_maximum_temperature_celsius"), "{{signal}}")], 0, 50, unit="celsius")
panel("Raw operating codes — meaning requires verification", "state-timeline", [(metric("kotel_signal_value", ',signal=~"dev_rezim_z[12]|dev_set_z[12]"'), "{{signal}}")], 12, 50)

dashboard = {
    "uid": "kotelna-prometheus", "title": "Kotelna — Prometheus", "schemaVersion": 39,
    "version": 1, "timezone": "browser", "refresh": "15s", "tags": ["kotelna", "prometheus"],
    "description": "Prometheus scrape history. Missing samples are not backfilled. Reception time is not measurement time. Lines do not connect nulls; long ranges may hide short gaps.",
    "time": {"from": "now-6h", "to": "now"},
    "__inputs": [{"name": "DS_PROMETHEUS", "label": "Prometheus", "type": "datasource", "pluginId": "prometheus", "pluginName": "Prometheus"}],
    "templating": {"list": [
        {"name": "job", "type": "query", "datasource": DS, "query": "label_values(kotel_build_info, job)", "refresh": 1, "current": {"text": "kotel-logger", "value": "kotel-logger"}},
        {"name": "instance", "type": "query", "datasource": DS, "query": 'label_values(kotel_build_info{job=~"$job"}, instance)', "refresh": 1},
        {"name": "device_id", "type": "query", "datasource": DS, "query": 'label_values(kotel_build_info{job=~"$job",instance=~"$instance"}, device_id)', "refresh": 1, "current": {"text": "2985", "value": "2985"}},
    ]},
    "panels": panels,
}
Path("deploy/grafana-dashboard.json").write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
