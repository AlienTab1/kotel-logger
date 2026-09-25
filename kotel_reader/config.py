from __future__ import annotations

import os
import math
from pathlib import Path
from urllib.parse import urlsplit

import yaml


DEFAULTS = {
    "data_dir": "/data",
    "thematronic": {
        "url": "https://jbr1.thematronic.cz", "device_id": 2985,
        "poll_s": 15, "mode": "read_only", "reload_wait_s": 2,
        "timeout_s": 15, "max_response_bytes": 1048576,
    },
    "session": {
        "cooldown_s": 300, "quick_relog_s": 30, "tolerance_s": 60,
        "early_loss_s": 120, "expected_session_lifetime_s": None,
        "ambiguous_403_pause_s": 900,
    },
    "queue": {"max_items": 100000, "max_bytes": 268435456, "max_age_s": 604800},
    "postgres": {
        "enabled": True, "host": "172.30.1.122", "port": 5432,
        "dbname": "kotelna", "user": "kotel_writer", "sslmode": "prefer",
        "retention_days": 90,
    },
    "mqtt": {
        "enabled": True, "host": "", "port": 1883, "username": "",
        "tls": False, "ca_file": None, "prefix": "kotelna/thematronic/2985",
        "discovery_prefix": "homeassistant", "stale_s": 60,
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
    t, s, m, p, q = (cfg[k] for k in ("thematronic", "session", "mqtt", "postgres", "queue"))
    u = urlsplit(t["url"])
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("Thematronic URL must be a plain HTTPS origin")
    t["url"] = t["url"].rstrip("/")
    if t["mode"] not in ("read_only", "reload_then_read"):
        raise ValueError("Unsupported polling mode")
    for section, fields in ((t, ["device_id", "poll_s", "timeout_s", "max_response_bytes"]),
                            (s, ["cooldown_s", "quick_relog_s", "tolerance_s", "early_loss_s", "ambiguous_403_pause_s"]),
                            (m, ["port", "stale_s"]), (p, ["port", "retention_days"]),
                            (q, ["max_items", "max_bytes", "max_age_s"])):
        for field in fields:
            if type(section[field]) not in (int, float) or not math.isfinite(section[field]) or section[field] <= 0:
                raise ValueError(f"{field} must be a positive number")
    if type(t["reload_wait_s"]) not in (int, float) or not math.isfinite(t["reload_wait_s"]) or t["reload_wait_s"] < 0:
        raise ValueError("reload_wait_s must be non-negative")
    if t["poll_s"] < 10 or s["cooldown_s"] < 300 or s["quick_relog_s"] < 30:
        raise ValueError("Minimum poll/cooldown/quick_relog: 10/300/30 seconds")
    life = s["expected_session_lifetime_s"]
    if life is not None and (type(life) not in (int, float) or not math.isfinite(life) or life <= 0):
        raise ValueError("expected_session_lifetime_s must be null or positive")
    for section in (m, p):
        if type(section["enabled"]) is not bool:
            raise ValueError("enabled must be boolean")
        if type(section["port"]) is not int or not 1 <= section["port"] <= 65535:
            raise ValueError("port must be an integer in 1..65535")
    for section, key in ((t, "device_id"), (t, "max_response_bytes"), (q, "max_items"), (q, "max_bytes"), (p, "retention_days")):
        if type(section[key]) is not int:
            raise ValueError(f"{key} must be an integer")
    if not isinstance(cfg["data_dir"], str) or not cfg["data_dir"]:
        raise ValueError("data_dir must be a non-empty path")
    if type(m["tls"]) is not bool:
        raise ValueError("mqtt.tls must be boolean")
    if p["sslmode"] not in ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"):
        raise ValueError("Invalid postgres.sslmode")
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
        if any(type(meta[k]) is not bool for k in ("publish", "invert") if k in meta):
            raise ValueError("signal publish/invert must be boolean")
        if "invalid_statuses" in meta and (not isinstance(meta["invalid_statuses"], list) or not all(isinstance(s, str) for s in meta["invalid_statuses"])):
            raise ValueError("invalid_statuses must be a list of strings")
    return cfg
