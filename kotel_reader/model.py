from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone


def utcnow() -> float:
    return datetime.now(timezone.utc).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class PayloadError(ValueError):
    def __init__(self, reason, index=None, item=None):
        super().__init__(reason)
        self.details = {"item_index": index} if index is not None else {}
        if isinstance(item, dict):
            self.details["field_types"] = {key: type(item[key]).__name__ if key in item else "missing"
                                          for key in ("Nazev", "Hodnota", "Stav", "HodnotaF")}


def validate_payload(raw):
    if not isinstance(raw, list) or not raw:
        raise PayloadError("expected_array" if not isinstance(raw, list) else "empty_array")
    names = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or not all(k in item for k in ("Nazev", "Hodnota", "Stav", "HodnotaF")):
            raise PayloadError("missing_fields_or_invalid_item", index, item)
        name, value = item["Nazev"], item["Hodnota"]
        if not isinstance(name, str) or not name or name in names:
            raise PayloadError("invalid_or_duplicate_name", index, item)
        if not isinstance(item["Stav"], str) or not isinstance(item["HodnotaF"], str):
            raise PayloadError("invalid_status_or_format_type", index, item)
        if value is not None and type(value) not in (int, float, bool, str):
            raise PayloadError("invalid_value_type", index, item)
        if type(value) is float and not math.isfinite(value):
            raise PayloadError("nonfinite_value", index, item)
        names.add(name)
    return raw


def normalize(raw: list, device_id: int, mapping: dict, received_at: float) -> dict:
    validate_payload(raw)
    values, statuses, unknown = {}, {}, []
    for item in raw:
        name, value, status = item["Nazev"], item["Hodnota"], item["Stav"]
        meta = mapping.get(name)
        valid = value is not None and status not in (meta or {}).get("invalid_statuses", ["nepripojeno"])
        if type(value) in (int, float) and item["HodnotaF"] == "---":
            valid = False
        if meta is None:
            unknown.append(name)
        else:
            kind = meta["type"]
            if kind == "number":
                valid &= type(value) in (int, float) and item["HodnotaF"] != "---"
            elif kind == "boolean":
                valid &= type(value) is bool
                if valid and meta.get("invert", False):
                    value = not value
            elif kind == "string":
                valid &= isinstance(value, str)
            elif kind == "duration":
                match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", value) if isinstance(value, str) else None
                valid &= bool(match and any(match.groups()))
                if valid:
                    h, m, s = (float(x or 0) for x in match.groups())
                    value = h * 3600 + m * 60 + s
        values[name] = value if valid else None
        statuses[name] = {"raw": status, "valid": bool(valid),
                          "kind": "maximum_unknown_period" if name.startswith("max_t_") else "signal"}
    for name in mapping.keys() - values.keys():
        values[name] = None
        statuses[name] = {"raw": "missing", "valid": False, "kind": "signal"}
    return {"sample_id": str(uuid.uuid4()), "device_id": device_id, "received_at": iso(received_at),
            "source_timestamp": None, "raw": raw, "values": values, "statuses": statuses,
            "unknown_signals": unknown, "source_freshness": "unverified"}
