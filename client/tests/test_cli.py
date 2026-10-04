# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import io
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest
from studio_helpers import KEY, delta_toml, write_delta

import ce_studio
from ce_studio.cli import EXIT_API, EXIT_CONFIG, EXIT_FAILED, EXIT_NETWORK, EXIT_OK, main


@pytest.fixture
def env(mock, monkeypatch):
    monkeypatch.setenv("CE_STUDIO_BASE_URL", mock.url)
    monkeypatch.setenv("CE_STUDIO_API_KEY", KEY)
    monkeypatch.setattr(time, "sleep", lambda s: None)  # polls and backoffs return at once
    return mock


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_check_is_offline(toy_delta, tmp_path, monkeypatch):
    monkeypatch.delenv("CE_STUDIO_BASE_URL", raising=False)
    code, out, _ = run("check", str(toy_delta))
    assert code == EXIT_OK and out.strip().endswith("ok")
    bad = write_delta(tmp_path / "bad", delta_toml(buses="", run='rigor_tier = "fast"'))
    code, out, _ = run("check", str(bad))
    assert code == EXIT_FAILED
    assert "scope_missing" in out and "bad_rigor_tier" in out and "fix:" in out
    code, out, _ = run("--toml", "check", str(bad))
    doc = tomllib.loads(out)
    assert doc["ok"] is False and [f["code"] for f in doc["finding"]] == ["scope_missing", "bad_rigor_tier"]


def test_submit_and_wait(env, toy_delta):
    code, out, err = run("submit", str(toy_delta), "--wait", "--poll-s", "1")
    assert code == EXIT_OK, err
    lines = out.strip().splitlines()
    assert lines[0].endswith("queued") and lines[-1].split()[1] == "completed"


def test_submit_toml_output(env, toy_delta):
    code, out, _ = run("--toml", "submit", str(toy_delta))
    assert code == EXIT_OK
    doc = tomllib.loads(out)
    assert doc["scenario_id"].startswith("scn-") and doc["status"] == "queued"


def test_submit_refused_locally(env, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(buses=""))
    code, _, err = run("submit", str(path))
    assert code == EXIT_FAILED and "scope_missing" in err
    assert env.count("POST", "/api/scenario/submit") == 0


def test_submit_refused_by_service(env, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(scope_id="toy_region_unrun"))
    code, _, err = run("submit", str(path))
    assert code == EXIT_FAILED and "base_run_missing" in err and "fix:" in err


def test_failed_scenario_status_and_wait(env, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(name="fail-x"))
    code, out, _ = run("submit", str(path), "--wait", "--poll-s", "1")
    assert code == EXIT_FAILED
    assert "unmappable_bus" in out and "fix: Re-snap" in out and "+3 more" in out
    sid = env.submissions and next(iter(env.submissions))
    code, out, _ = run("--toml", "status", sid)
    doc = tomllib.loads(out)
    assert code == EXIT_FAILED and doc["status"] == "failed"
    assert [r["code"] for r in doc["refusal"]][:2] == ["unmappable_bus", "load_starts_after_horizon"]
    assert doc["refusals_omitted"] == 3


def test_results_with_qa_and_download(env, toy_delta, tmp_path):
    _, out, _ = run("--toml", "submit", str(toy_delta), "--wait", "--poll-s", "1")
    sid = tomllib.loads(out)["scenario_id"]
    code, out, _ = run("results", sid, "--qa", "--out", str(tmp_path / "res"), "--decompress")
    assert code == EXIT_OK
    assert "objective      12,345,000 USD" in out
    assert "quality report: PASS" in out and "withheld" in out
    assert sorted(p.name for p in (tmp_path / "res").iterdir()) == ["investment.csv", "lmp.csv", "qa_checks.csv"]
    code, out, _ = run("--toml", "results", sid, "--qa", "--out", str(tmp_path / "r2"), "--table", "investment")
    doc = tomllib.loads(out)
    assert doc["summary"]["currency"] == "USD" and doc["summary"]["caveats"]
    assert doc["qa"]["status"] == "PASS" and len(doc["qa"]["check"]) == 3
    assert [Path(p).name for p in doc["downloaded"]] == ["investment.csv"]
    assert doc["qa"]["derived_status"] == "PASS" and doc["qa"]["problems"] == []
    code, out, _ = run("results", sid, "--run-dir", str(tmp_path / "run"))
    assert code == EXIT_OK and "ce_results.Run" in out
    assert (tmp_path / "run" / "summary.toml").is_file() and not list((tmp_path / "run").glob("*.json"))


def test_list_withdraw_and_scopes(env, toy_delta):
    env.auto_advance = False
    _, out, _ = run("--toml", "submit", str(toy_delta))
    sid = tomllib.loads(out)["scenario_id"]
    code, out, _ = run("list")
    assert code == EXIT_OK and sid in out
    assert run("withdraw", sid)[0] == EXIT_OK
    code, _, err = run("withdraw", sid)
    assert code == EXIT_API and "409" in err
    code, out, _ = run("scopes")
    assert code == EXIT_OK and "toy_region" in out
    code, out, _ = run("scopes", "toy_region", "--buses")
    assert code == EXIT_OK and "currency USD" in out and "takes scenarios: yes" in out
    assert "build          : 2026-01-01T00:00:00Z" in out and "coast" in out
    code, out, _ = run("--toml", "scopes")
    assert [s["scope_id"] for s in tomllib.loads(out)["scope"]] == ["toy_region", "toy_region_unrun"]


def test_unknown_scenario(env):
    code, _, err = run("status", "scn-missing")
    assert code == EXIT_API and "not in the queue listing" in err


def test_configuration_errors(monkeypatch, toy_delta):
    monkeypatch.delenv("CE_STUDIO_BASE_URL", raising=False)
    monkeypatch.delenv("CE_STUDIO_API_KEY", raising=False)
    code, _, err = run("list")
    assert code == EXIT_CONFIG and "CE_STUDIO_BASE_URL" in err
    code, _, err = run("--base-url", "https://studio.example.com", "list")
    assert code == EXIT_CONFIG and "CE_STUDIO_API_KEY" in err


def test_api_key_env_indirection(mock, monkeypatch):
    monkeypatch.setenv("CE_STUDIO_BASE_URL", mock.url)
    monkeypatch.delenv("CE_STUDIO_API_KEY", raising=False)
    monkeypatch.setenv("MY_STUDIO_KEY", KEY)
    assert run("--api-key-env", "MY_STUDIO_KEY", "list")[0] == EXIT_OK


def test_key_is_never_a_cli_argument():
    with pytest.raises(SystemExit) as exc:
        run("--api-key", "x", "list")
    assert exc.value.code == 2


def test_network_error_exit_code(monkeypatch, closed_port):
    monkeypatch.setenv("CE_STUDIO_BASE_URL", f"http://127.0.0.1:{closed_port}")
    monkeypatch.setenv("CE_STUDIO_API_KEY", KEY)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    code, _, err = run("list")
    assert code == EXIT_NETWORK and "cannot reach" in err


def test_python_dash_m_entry_point():
    src = Path(ce_studio.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(src)}
    out = subprocess.run([sys.executable, "-m", "ce_studio", "--version"], capture_output=True,
                         text=True, env=env, check=True)
    assert out.stdout.strip() == f"ce-studio {ce_studio.__version__}"
