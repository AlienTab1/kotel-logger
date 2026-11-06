from __future__ import annotations

import os
import math
from pathlib import Path
from urllib.parse import urlsplit

import yaml


DEFAULTS = {
    "data_dir": "/data",
    "logging": {"loki_url": "", "queue_size": 2000, "file_max_bytes": 10485760, "file_backups": 5},
    "thematronic": {
        "url": "https://jbr1.thematronic.cz", "device_id": 2985,
        "poll_s": 15, "mode": "read_only", "reload_wait_s": 2,
        "timeout_s": 15, "connect_timeout_s": 5, "read_timeout_s": 10, "max_response_bytes": 1048576,
    },
    "session": {
        "cooldown_s": 60, "quick_relog_s": 30, "tolerance_s": 60,
        "early_loss_s": 120, "expected_session_lifetime_s": None,
        "ambiguous_403_pause_s": 900, "auth_initial_s": 300, "auth_max_s": 3600,
    },
    "retry": {"transient_initial_s": 10, "validation_initial_s": 15, "max_s": 60, "jitter": 0.1},
    "telemetry": {"stale_s": 60},
    "prometheus": {"host": "0.0.0.0", "port": 9108},
    "mqtt": {
        "enabled": True, "host": "", "port": 1883, "username": "",
        "tls": False, "ca_file": None, "prefix": "kotelna/thematronic/2985",
        "discovery_prefix": "homeassistant",
    },
}


def secret(name: str, required: bool = False) -> str:
    path = os.environ.get(name + "_FILE")
    value = Path(path).read_text(encoding="utf-8").rstrip("\r\n") if path else os.environ.get(name, "")
    if required and not value:
        raise ValueError(f"Missing {name} or {name}_FILE")
    return value


def load(path: str) -> dict:
    import copy
    cfg = copy.deepcopy(DEFAULTS)
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be a mapping")
    if "postgres" in raw or "queue" in raw:
        raise ValueError("Version 0.2 uses Prometheus: remove postgres/queue sections and use config.example.yaml")
    for key, value in raw.items():
        if key == "signals_file":
            continue
        if key not in cfg:
            raise ValueError(f"Unknown configuration section: {key}")
        if isinstance(cfg[key], dict):
            if not isinstance(value, dict) or set(value) - set(cfg[key]):
                raise ValueError(f"Unknown or invalid settings in {key}")
            cfg[key].update(value)
        else:
            cfg[key] = value
    # Legacy explicit timeout_s keeps its original meaning unless split values are supplied.
    if "timeout_s" in raw.get("thematronic", {}):
        for key in ("connect_timeout_s", "read_timeout_s"):
            if key not in raw["thematronic"]:
                cfg["thematronic"][key] = raw["thematronic"]["timeout_s"]
    for section, fields in ((cfg["retry"], ("transient_initial_s", "validation_initial_s", "max_s")),
                            (cfg["thematronic"], ("connect_timeout_s", "read_timeout_s")),
                            (cfg["session"], ("auth_initial_s", "auth_max_s"))):
        for key in fields:
            if type(section[key]) not in (int, float) or not math.isfinite(section[key]) or section[key] <= 0:
                raise ValueError("Invalid retry/timeout setting: " + key)
    retry = cfg["retry"]
    if not 1 <= retry["transient_initial_s"] <= retry["max_s"] or not 1 <= retry["validation_initial_s"] <= retry["max_s"]:
        raise ValueError("Retry initial delay must be within 1..max_s")
    if type(retry["jitter"]) not in (int, float) or not 0 <= retry["jitter"] <= 0.5:
        raise ValueError("Retry jitter must be in 0..0.5")
    if not 60 <= cfg["session"]["auth_initial_s"] <= cfg["session"]["auth_max_s"]:
        raise ValueError("Authentication retry initial delay must be >=60 and <=auth_max_s")
    log = cfg["logging"]
    log["loki_url"] = os.environ.get("KOTEL_LOKI_URL", log["loki_url"])
    if not isinstance(log["loki_url"], str):
        raise ValueError("logging.loki_url must be a string")
    if log["loki_url"]:
        endpoint = urlsplit(log["loki_url"])
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("logging.loki_url must be an HTTP(S) URL without credentials or query")
    for key, maximum in (("queue_size", 100000), ("file_max_bytes", 104857600), ("file_backups", 20)):
        if type(log[key]) is not int or not 1 <= log[key] <= maximum:
            raise ValueError("Invalid logging limit: " + key)
    t, s, m, p, telemetry = (cfg[k] for k in ("thematronic", "session", "mqtt", "prometheus", "telemetry"))
    u = urlsplit(t["url"])
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("Thematronic URL must be a plain HTTPS origin")
    t["url"] = t["url"].rstrip("/")
    if t["mode"] not in ("read_only", "reload_then_read"):
        raise ValueError("Unsupported polling mode")
    for section, fields in ((t, ["device_id", "poll_s", "timeout_s", "max_response_bytes"]),
                            (s, ["cooldown_s", "quick_relog_s", "tolerance_s", "early_loss_s", "ambiguous_403_pause_s"]),
                            (m, ["port"]), (p, ["port"]), (telemetry, ["stale_s"])):
        for field in fields:
            if type(section[field]) not in (int, float) or not math.isfinite(section[field]) or section[field] <= 0:
                raise ValueError(f"{field} must be a positive number")
    if type(t["reload_wait_s"]) not in (int, float) or not math.isfinite(t["reload_wait_s"]) or t["reload_wait_s"] < 0:
        raise ValueError("reload_wait_s must be non-negative")
    if t["poll_s"] < 10 or s["cooldown_s"] < 60 or s["quick_relog_s"] < 30:
        raise ValueError("Minimum poll/cooldown/quick_relog: 10/60/30 seconds")
    life = s["expected_session_lifetime_s"]
    if life is not None and (type(life) not in (int, float) or not math.isfinite(life) or life <= 0):
        raise ValueError("expected_session_lifetime_s must be null or positive")
    if type(m["enabled"]) is not bool:
        raise ValueError("enabled must be boolean")
    for section in (m, p):
        if type(section["port"]) is not int or not 1 <= section["port"] <= 65535:
            raise ValueError("port must be an integer in 1..65535")
    for section, key in ((t, "device_id"), (t, "max_response_bytes")):
        if type(section[key]) is not int:
            raise ValueError(f"{key} must be an integer")
    if not isinstance(cfg["data_dir"], str) or not cfg["data_dir"]:
        raise ValueError("data_dir must be a non-empty path")
    if type(m["tls"]) is not bool:
        raise ValueError("mqtt.tls must be boolean")
    if not isinstance(p["host"], str) or not p["host"]:
        raise ValueError("prometheus.host must be a non-empty listen address")
    if telemetry["stale_s"] < 1:
        raise ValueError("telemetry.stale_s must be at least 1 second")
    if m["enabled"] and not m["host"]:
        raise ValueError("Configure mqtt.host or disable MQTT")
    if not m["prefix"] or not m["discovery_prefix"] or any(c in m["prefix"] + m["discovery_prefix"] for c in "+#\x00"):
        raise ValueError("MQTT prefixes must not contain wildcards")
    signals_path = Path(raw.get("signals_file", "signals.yaml"))
    if not signals_path.is_absolute():
        signals_path = Path(path).resolve().parent / signals_path
    cfg["signals"] = yaml.safe_load(signals_path.read_text(encoding="utf-8")) or {}
    if not isinstance(cfg["signals"], dict):
        raise ValueError("signals must be a mapping")
    import re
    for key, meta in cfg["signals"].items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", key) or not isinstance(meta, dict):
            raise ValueError("Invalid signal mapping")
        if meta.get("type") not in ("number", "boolean", "string", "duration"):
            raise ValueError(f"Unsupported type for {key}")
        if meta.get("component", "sensor") not in ("sensor", "binary_sensor"):
            raise ValueError(f"Unsupported component for {key}")
        if meta.get("component") == "binary_sensor" and meta.get("type") != "boolean":
            raise ValueError("binary_sensor requires boolean type")
        if any(type(meta[k]) is not bool for k in ("publish", "invert", "prometheus", "required") if k in meta):
            raise ValueError("signal publish/invert/prometheus must be boolean")
        if "invalid_statuses" in meta and (not isinstance(meta["invalid_statuses"], list) or not all(isinstance(s, str) for s in meta["invalid_statuses"])):
            raise ValueError("invalid_statuses must be a list of strings")
    return cfg
