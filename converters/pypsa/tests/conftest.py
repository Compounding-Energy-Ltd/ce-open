# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG / "src"))
sys.path.insert(0, str(HERE))

EXAMPLES = PKG / "examples"
SPEC_SRC = PKG.parent.parent / "spec" / "src"   # the repository's reference validator, when present


def _load_toy_writer():
    spec = importlib.util.spec_from_file_location("make_toy_network", EXAMPLES / "make_toy_network.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def toy_writer():
    return _load_toy_writer()


@pytest.fixture()
def toy_folder(tmp_path, toy_writer):
    return toy_writer.write(tmp_path / "toy_network")


@pytest.fixture()
def clean_folder(tmp_path):
    from synthetic import write_clean_network

    return write_clean_network(tmp_path / "clean_network")


def import_sibling(module: str, src: Path):
    """Import a sibling component of this repository; skipped when unavailable, but a failure under
    ``CE_OPEN_STRICT=1`` (set in CI) so a cross-component check never disappears silently."""
    import importlib
    import os

    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        if os.environ.get("CE_OPEN_STRICT") == "1":
            pytest.fail(f"{module} is required in strict mode: {exc}")
        pytest.skip(f"{module} is not available: {exc}")


@pytest.fixture(scope="session")
def spec_validator():
    """``ce_spec.bundle.validate_bundle`` from the repository's spec/ package (see :func:`import_sibling`)."""
    return import_sibling("ce_spec.bundle", SPEC_SRC).validate_bundle


@pytest.fixture(scope="session")
def pypsa():
    import warnings

    warnings.filterwarnings("ignore")
    return pytest.importorskip("pypsa")
