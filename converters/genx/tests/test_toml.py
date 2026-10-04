# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import datetime as dt
import math
import tomllib

import pytest

from ce_genx._toml import dumps


def test_round_trip_nested_document():
    doc = {
        "title": 'quote " and \\ backslash\nnewline',
        "n": 3,
        "x": 2.0,
        "big": 1e20,
        "flag": True,
        "skip": None,
        "years": [2030, 2035],
        "when": dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc),
        "time": {"zone": "UTC", "blocks": [{"id": 1, "w": 6570.0}, {"id": 2, "w": 2190.0}]},
        "x_ext": {"inner": {"deep": "yes"}, "list": [{"a": 1}]},
    }
    back = tomllib.loads(dumps(doc))
    assert back["title"] == doc["title"]
    assert back["n"] == 3 and back["x"] == 2.0 and back["big"] == 1e20 and back["flag"] is True
    assert "skip" not in back
    assert back["years"] == [2030, 2035]
    assert back["when"] == doc["when"]
    assert back["time"]["blocks"][1] == {"id": 2, "w": 2190.0}
    assert back["x_ext"]["inner"]["deep"] == "yes"
    assert back["x_ext"]["list"] == [{"a": 1}]


def test_special_floats():
    back = tomllib.loads(dumps({"a": math.inf, "b": -math.inf, "c": math.nan}))
    assert back["a"] == math.inf and back["b"] == -math.inf and math.isnan(back["c"])


def test_naive_datetime_is_utc():
    text = dumps({"t": dt.datetime(2020, 6, 1, 12, 30)})
    assert "2020-06-01T12:30:00Z" in text


def test_unsupported_value():
    with pytest.raises(TypeError):
        dumps({"a": object()})
