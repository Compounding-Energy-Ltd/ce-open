# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Agreement with the format specification in ``spec/`` (skipped when it is not alongside).

* The synthetic 1.0 output passes the specification's own validator.
* The specification's example run reads cleanly, and curtailment derived from
  its bundle reproduces the curtailment table the example ships.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from ce_results import Run, capture_prices, congestion_rent
from ce_results.synthetic import write_synthetic_run

SPEC = Path(__file__).resolve().parents[2] / "spec"
TOY_RUN, TOY_BUNDLE = SPEC / "examples" / "toy3_run", SPEC / "examples" / "toy3"


def _unavailable(why: str):
    """Skip — or fail under CE_OPEN_STRICT=1 (CI), so a conformance check never disappears silently."""
    import os

    if os.environ.get("CE_OPEN_STRICT") == "1":
        pytest.fail(f"{why} (required in strict mode)")
    pytest.skip(why)


@pytest.fixture(scope="module")
def ce_spec():
    if not (SPEC / "src").is_dir():
        _unavailable("spec/ is not alongside this package")
    sys.path.insert(0, str(SPEC / "src"))
    try:
        import ce_spec.bundle
        import ce_spec.results
    except ImportError as exc:  # pragma: no cover - depends on the checkout
        _unavailable(f"the spec validator is not importable: {exc}")
    finally:
        sys.path.remove(str(SPEC / "src"))
    return ce_spec


@pytest.mark.parametrize("kw", [{}, {"weather_draws": 2, "weather_weights": [7, 3], "with_curtailment": True},
                                {"formats": ("csv",)}],
                         ids=["base", "two-weather", "csv"])
def test_synthetic_output_is_valid(ce_spec, tmp_path, kw):
    rd, bd = write_synthetic_run(tmp_path / "run", **kw)
    rb = ce_spec.bundle.validate_bundle(bd)
    assert rb.ok, rb.summary()
    rr = ce_spec.results.validate_results(rd, bundle=bd, strict=True)
    assert rr.ok and not rr.warnings, rr.summary()


@pytest.fixture()
def toy():
    if not (TOY_RUN / "manifest.toml").is_file():
        pytest.skip("the spec example run is not available")
    return Run(TOY_RUN, bundle=TOY_BUNDLE)


def test_spec_example_reads(toy):
    assert toy.provenance.format_version == "1.0"
    assert toy.stamps.certified(1e-2)
    assert toy.weights().groupby("year")["annual_h"].sum().round(6).eq(8760.0).all()
    assert toy.lmp_component_residual() < 1e-6
    assert len(capture_prices(toy)) > 0 and len(congestion_rent(toy)) > 0


def test_spec_example_curtailment_is_reproduced(toy, tmp_path):
    written = toy.curtailment()
    run = tmp_path / "toy_run"
    shutil.copytree(TOY_RUN, run)
    for p in run.glob("curtailment.*"):
        p.unlink()
    derived = Run(run, bundle=TOY_BUNDLE).curtailment()
    keys = ["year", "scenario", "weather", "block", "period", "site_name"]
    m = written.merge(derived, on=keys, how="outer", suffixes=("_w", "_d"), indicator=True)
    assert (m["_merge"] == "both").all()
    for c in ("available_mw", "dispatch_mw", "curtailed_mw"):
        assert np.allclose(m[f"{c}_w"], m[f"{c}_d"], atol=1e-6)
    # the example builds its hybrid in 2035 (SPEC.md 9.4): the renewable part is counted
    hyb = derived[derived["site_name"] == "hybrid_coast_c1"]
    assert set(hyb["year"]) == {2035} and hyb["available_mw"].max() > 0


def test_spec_example_hybrid_rows(toy):
    inv = toy.table("investment")
    hyb = inv[inv["name"] == "hybrid_coast_c1"].set_index("component")["built_mw"].to_dict()
    assert hyb == {"renewable": 240.0, "storage": 120.0}
    assert "hybrid_coast_c1" in set(toy.storage()["storage"])
    assert toy.builds(component="renewable").query("name == 'hybrid_coast_c1'")["built_mw"].tolist() == [240.0]
