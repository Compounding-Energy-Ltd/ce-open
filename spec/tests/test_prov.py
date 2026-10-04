# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""The .prov grammar (SPEC.md section 7.1)."""

from __future__ import annotations

import pytest

from ce_spec.prov import format_prov, parse_prov_text, valid_utc_instant


def test_key_value_lines_comments_and_blanks():
    p = parse_prov_text("# comment\n\nsource=abc\n  rows = 12  \nlicense=Apache-2.0\n")
    assert p.values == {"source": "abc", "rows": "12", "license": "Apache-2.0"}
    assert p.unparsed == [] and p.duplicates == []


def test_first_equals_splits_and_hash_is_part_of_the_value():
    p = parse_prov_text("expr = a=b # not a comment\n")
    assert p.values == {"expr": "a=b # not a comment"}


def test_one_pair_of_quotes_is_stripped():
    p = parse_prov_text('a="  x  "\nb=""y""\nc="unbalanced\n')
    assert p.values == {"a": "  x  ", "b": '"y"', "c": '"unbalanced'}


def test_first_occurrence_wins_and_duplicates_are_recorded():
    p = parse_prov_text("k=1\nk=2\n")
    assert p.values == {"k": "1"}
    assert p.duplicates == [(2, "k")]


@pytest.mark.parametrize("line", ["key: value", "no equals here", "=value", "two words=x", "#=x"])
def test_other_lines_are_unparsed_or_comments(line):
    p = parse_prov_text(line + "\n")
    assert p.values == {}
    assert p.unparsed == ([] if line.startswith("#") else [1])


def test_empty_value_is_allowed():
    assert parse_prov_text("notes=\n").values == {"notes": ""}


def test_format_round_trip():
    vals = {"source": "x y", "notes": "  padded  ", "quoted": '"q"', "rows": 3}
    back = parse_prov_text(format_prov(vals)).values
    assert back == {k: str(v) for k, v in vals.items()}


@pytest.mark.parametrize("bad", ["", "a b", "a=b", "#a"])
def test_format_rejects_invalid_keys(bad):
    with pytest.raises(ValueError):
        format_prov({bad: "v"})


def test_format_rejects_multiline_values():
    with pytest.raises(ValueError):
        format_prov({"k": "a\nb"})


@pytest.mark.parametrize("s,ok", [("2026-10-01T00:00:00Z", True), ("2026-10-01T00:00:00", False),
                                  ("2026-10-01T00:00:00+01:00", False), ("yesterday", False)])
def test_utc_instant(s, ok):
    assert valid_utc_instant(s) is ok
