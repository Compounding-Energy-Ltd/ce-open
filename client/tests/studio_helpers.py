# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Shared test helpers (a uniquely named module so component test suites never collide)."""
from __future__ import annotations

from pathlib import Path

from ce_studio import ClientConfig, RetryPolicy, StudioClient
from ce_studio.transport import Transport

KEY = "test-key-7f3a"  # a mock credential, accepted only by MockStudio
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def make_client(base_url: str, sleeps: list[float], **cfg) -> StudioClient:
    retry = cfg.pop("retry", RetryPolicy(max_attempts=4, jitter=0.0))
    config = ClientConfig(base_url=base_url, api_key=cfg.pop("api_key", KEY), timeout_s=5,
                          retry=retry, **cfg)
    return StudioClient(config, transport=Transport(config, sleep=sleeps.append))


def write_delta(root: Path, delta_toml: str, files: dict[str, str | bytes] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "delta.toml").write_text(delta_toml)
    for rel, data in (files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
    return root


def delta_toml(name: str = "toy", scope_id: str = "toy_region", buses: str = '["north", "south", "coast"]',
               run: str = 'rigor_tier = "deliverable"', scope_extra: str = "") -> str:
    scope = f"buses = {buses}" if buses else ""
    return (f'[scenario]\nname = "{name}"\nscope_id = "{scope_id}"\n\n'
            f"[scope]\n{scope}\n{scope_extra}\n\n[run]\n{run}\n")
