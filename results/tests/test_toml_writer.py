# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The small TOML writer used by the synthetic generator round-trips through tomllib."""

from __future__ import annotations

import math
import tomllib

import pytest

from ce_results import _toml


def test_round_trip():
    doc = {
        "name": 'quote " and \\ backslash\nnewline',
        "n": 3, "x": 1.5, "flag": False, "empty": [], "ys": [2030, 2035],
        "a table": {"k": "v", "nested": {"deep": 1}},
        "rows": [{"check": "a", "sub": {"z": 1}}, {"check": "b"}],
    }
    back = tomllib.loads(_toml.dumps(doc))
    assert back == doc


def test_special_floats():
    back = tomllib.loads(_toml.dumps({"a": math.nan, "b": math.inf, "c": -math.inf}))
    assert math.isnan(back["a"]) and back["b"] == math.inf and back["c"] == -math.inf


def test_rejects_unknown_types():
    with pytest.raises(TypeError):
        _toml.dumps({"x": object()})
