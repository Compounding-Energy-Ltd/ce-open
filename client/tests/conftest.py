# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import shutil
import socket
from pathlib import Path

import pytest
from studio_helpers import EXAMPLES, KEY, make_client

from ce_studio import StudioClient
from ce_studio.testing import MockStudio


@pytest.fixture
def mock():
    with MockStudio(api_key=KEY) as m:
        yield m


@pytest.fixture
def sleeps() -> list[float]:
    """Every backoff the transport asked for (nothing actually sleeps)."""
    return []


@pytest.fixture
def client(mock, sleeps) -> StudioClient:
    return make_client(mock.url, sleeps)


@pytest.fixture
def toy_delta(tmp_path) -> Path:
    dest = tmp_path / "toy_delta"
    shutil.copytree(EXAMPLES / "toy_delta", dest)
    return dest


@pytest.fixture
def closed_port() -> int:
    """A loopback port with nothing listening."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


REPO = Path(__file__).resolve().parents[2]


def import_sibling(module: str, component: str):
    """Import a sibling component of this repository (e.g. ``ce_results`` from ``results/``).

    Skipped when it is unavailable — unless ``CE_OPEN_STRICT=1`` (set in CI), where a
    missing sibling is a failure, so a cross-component check never disappears silently.
    """
    import importlib
    import os
    import sys

    src = REPO / component / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        if os.environ.get("CE_OPEN_STRICT") == "1":
            pytest.fail(f"{module} ({component}/) is required in strict mode: {exc}")
        pytest.skip(f"{module} ({component}/) is not available: {exc}")
