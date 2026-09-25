import pytest

from kotel_reader.model import normalize, validate_payload


def item(name, value, status="ok", formatted="value"):
    return {"Nazev": name, "Hodnota": value, "Stav": status, "HodnotaF": formatted}


def test_all_types_and_quality():
    raw = [item("temp", -50.1, "nepripojeno", "---"), item("bool", True, "nezname"),
           item("time", "PT1H28M"), item("placeholder", 0, "ok", "---"), item("unknown", "new"),
           item("max_t_x", 100), item("wrong", True)]
    mapping = {"temp": {"type": "number"}, "bool": {"type": "boolean"}, "time": {"type": "duration"},
               "placeholder": {"type": "number"}, "wrong": {"type": "number"}, "missing": {"type": "number"}}
    sample = normalize(raw, 2985, mapping, 100)
    assert sample["values"] == {"temp": None, "bool": True, "time": 5280, "placeholder": None,
                                "unknown": "new", "max_t_x": 100, "wrong": None, "missing": None}
    assert sample["raw"] == raw
    assert sample["source_timestamp"] is None
    assert sample["statuses"]["max_t_x"]["kind"] == "maximum_unknown_period"
    assert sample["statuses"]["missing"]["valid"] is False
    assert "unknown" in sample["unknown_signals"]


@pytest.mark.parametrize("raw", [[], {}, [item("x", float("nan"))], [item("x", {})], [item("x", 1), item("x", 2)], [{"Nazev": "x"}]])
def test_reject_invalid_payload(raw):
    with pytest.raises(ValueError):
        validate_payload(raw)


@pytest.mark.parametrize("duration", ["PT", "yesterday", "P1Y", 10])
def test_invalid_duration(duration):
    sample = normalize([item("d", duration)], 1, {"d": {"type": "duration"}}, 0)
    assert sample["values"]["d"] is None

