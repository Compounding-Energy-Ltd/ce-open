# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import random

import pytest

from ce_studio import ClientConfig, ConfigError, RetryPolicy
from ce_studio.config import PLACEHOLDER_BASE_URL


def test_from_env_reads_variables_and_overrides_win():
    env = {"CE_STUDIO_BASE_URL": " https://studio.example.com/ ", "CE_STUDIO_API_KEY": "k1",
           "CE_STUDIO_TIMEOUT": "12.5"}
    cfg = ClientConfig.from_env(env)
    assert cfg.base_url == "https://studio.example.com/"
    assert cfg.api_key == "k1"
    assert cfg.timeout_s == 12.5
    assert cfg.check_target() == "https://studio.example.com"
    cfg2 = ClientConfig.from_env(env, base_url="https://other.example.com", api_key=None)
    assert cfg2.base_url == "https://other.example.com"
    assert cfg2.api_key == "k1"  # None overrides are ignored


def test_bad_timeout_env_is_a_config_error():
    with pytest.raises(ConfigError, match="CE_STUDIO_TIMEOUT"):
        ClientConfig.from_env({"CE_STUDIO_TIMEOUT": "soon"})


def test_placeholder_base_url_is_refused():
    with pytest.raises(ConfigError, match="CE_STUDIO_BASE_URL"):
        ClientConfig().check_target()
    with pytest.raises(ConfigError):
        ClientConfig(base_url=PLACEHOLDER_BASE_URL + "/").check_target()


@pytest.mark.parametrize("url", ["ftp://studio.example.com", "studio.example.com", "https://",
                                 "https://studio.example.com/?a=1"])
def test_malformed_base_urls(url):
    with pytest.raises(ConfigError):
        ClientConfig(base_url=url).check_target()


def test_plain_http_only_to_loopback_unless_allowed():
    with pytest.raises(ConfigError, match="unencrypted"):
        ClientConfig(base_url="http://studio.example.com").check_target()
    assert ClientConfig(base_url="http://studio.example.com",
                        allow_insecure_http=True).check_target() == "http://studio.example.com"
    for host in ("localhost", "127.0.0.1", "[::1]"):
        assert ClientConfig(base_url=f"http://{host}:8080").check_target()


def test_repr_never_shows_the_key():
    cfg = ClientConfig(base_url="https://studio.example.com", api_key="super-secret-value")
    assert "super-secret-value" not in repr(cfg)
    assert "***" in repr(cfg)


def test_require_key():
    with pytest.raises(ConfigError, match="CE_STUDIO_API_KEY"):
        ClientConfig().require_key()
    assert ClientConfig(api_key="k").require_key() == "k"


def test_backoff_is_exponential_and_capped():
    p = RetryPolicy(base_delay_s=0.5, multiplier=2, max_delay_s=3, jitter=0)
    assert [p.backoff_s(i) for i in range(1, 6)] == [0.5, 1.0, 2.0, 3.0, 3.0]


def test_backoff_jitter_stays_in_band():
    p = RetryPolicy(base_delay_s=1, multiplier=2, max_delay_s=100, jitter=0.2)
    rng = random.Random(7)
    for attempt in range(1, 6):
        nominal = 2 ** (attempt - 1)
        d = p.backoff_s(attempt, rng)
        assert 0.8 * nominal - 1e-9 <= d <= 1.2 * nominal + 1e-9


@pytest.mark.parametrize("kw", [{"max_attempts": 0}, {"jitter": 1.5}, {"multiplier": 0.5},
                                {"base_delay_s": -1}])
def test_invalid_retry_policy(kw):
    with pytest.raises(ConfigError):
        RetryPolicy(**kw)
