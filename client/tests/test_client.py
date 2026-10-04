# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
import gzip
import io

import pytest
from studio_helpers import delta_toml, write_delta

from ce_studio import (
    BadRequestError,
    ConflictError,
    LocalValidationError,
    NotFoundError,
    ScenarioFailed,
    ScenarioNotFound,
    StudioError,
    ValidationError,
    WaitTimeout,
)


def run_to_end(client, path, **kw):
    sid = client.submit(path, **kw).scenario_id
    return client.wait(sid, poll_s=1, sleep=lambda s: None, raise_on_failure=False)


# ------------------------------------------------------------------ scopes


def test_list_scopes_and_manifest(client):
    scopes = client.list_scopes()
    assert [s.scope_id for s in scopes] == ["toy_region", "toy_region_unrun"]
    assert scopes[0].label.startswith("Toy region")
    m = client.scope_manifest("toy_region")
    assert (m.scope_id, m.currency, m.cost_year) == ("toy_region", "USD", 2024)
    assert m.horizon_years == (2030,) and m.weather_years == (2019,)
    assert m.has_base_run and m.n_buses_in_scope == 3
    assert m.archetypes == ("flat_95pct",)
    assert m.build == "2026-01-01T00:00:00Z" and m.dollar_year == 2024
    assert "candidates" in m.tables
    assert not client.scope_manifest("toy_region_unrun").has_base_run
    buses = client.scope_buses("toy_region")
    assert [(b.bus_id, b.name) for b in buses] == [(1, "north"), (2, "south"), (3, "coast")]
    resolve = client.bus_resolver("toy_region")
    assert resolve("coast") == 3
    with pytest.raises(LocalValidationError) as exc:
        resolve("atlantis")
    assert exc.value.refusals[0].code == "unknown_bus_name"


def test_scopes_need_no_key(mock, sleeps):
    from studio_helpers import make_client

    c = make_client(mock.url, sleeps, api_key=None)
    assert len(c.list_scopes()) == 2


# ------------------------------------------------------------------ submit / lifecycle


def test_submit_sends_one_json_body_in_the_wire_shape(client, mock, toy_delta):
    receipt = client.submit(toy_delta)
    assert receipt.scenario_id.startswith("scn-") and receipt.status == "queued"
    assert receipt.submission_ref and not receipt.duplicate
    (method, path, headers), = [r for r in mock.requests if r[0] == "POST"]
    assert headers["content-type"] == "application/json"
    sent = mock.submissions[receipt.scenario_id]
    assert sent["scope_id"] == "toy_region" and sent["scope"] == {"bus_set": [1, 2, 3]}
    assert sent["tables"]["add_candidates"][0]["bus_id"] == 2
    assert sent["tables"]["add_candidates"][0]["technology"] == "storage"
    assert sent["submission_ref"] == receipt.submission_ref


def test_a_multipart_body_is_refused_like_the_service_does(client, mock):
    """A non-JSON body reads as an empty object: 400 scope_id required."""
    resp_err = None
    try:
        client.transport.request("POST", "/api/scenario/submit", body=b"--x\r\n\r\n--x--\r\n",
                                 headers={"Content-Type": "multipart/form-data; boundary=x"})
    except BadRequestError as err:
        resp_err = err
    assert resp_err is not None and "scope_id required" in str(resp_err)


def test_submit_is_idempotent_with_a_reference(client, mock, toy_delta):
    first = client.submit(toy_delta, submission_ref="my-ref-0001")
    again = client.submit(toy_delta, submission_ref="my-ref-0001")
    assert again.scenario_id == first.scenario_id and again.duplicate
    assert len(mock.submissions) == 1


def test_stale_bus_ids_are_refused_before_sending(client, mock, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(buses="[1, 2]", scope_extra='build = "an-old-build"'))
    with pytest.raises(LocalValidationError) as exc:
        client.submit(path)
    assert exc.value.refusals[0].code == "stale_bus_ids"
    assert mock.count("POST", "/api/scenario/submit") == 0
    ok = write_delta(tmp_path / "e", delta_toml(buses="[1, 2]", scope_extra='build = "2026-01-01T00:00:00Z"'))
    assert client.submit(ok).status == "queued"


def test_service_row_refusals_reach_the_caller(client, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(), {
        "add_candidates.csv": "candidate_id,technology,bus,max_mw\nx,tidal,north,5\n"})
    with pytest.raises(ValidationError) as exc:
        client.submit(path)
    assert exc.value.refusals[0].code == "bad_technology" and exc.value.refusals[0].table == "add_candidates"


def test_wait_reports_each_change_and_completes(client, toy_delta):
    sid = client.submit(toy_delta).scenario_id
    seen, slept = [], []
    final = client.wait(sid, poll_s=2, on_update=lambda s: seen.append(s.status), sleep=slept.append)
    assert seen == ["ingested", "running", "completed"]
    assert final.succeeded and final.is_terminal and final.converged is True
    assert slept == [2, 2]


def test_running_rows_carry_progress(client, mock, toy_delta):
    sid = client.submit(toy_delta).scenario_id
    client.status(sid)  # ingested
    row = client.status(sid)
    assert row.status == "running"
    assert row.progress.startswith("iteration") and row.expected_wall_s == 900
    assert "iteration" in row.headline


def test_local_check_blocks_before_sending(client, mock, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(buses=""))
    with pytest.raises(LocalValidationError) as exc:
        client.submit(path)
    assert [r.code for r in exc.value.refusals] == ["scope_missing"]
    assert mock.count("POST", "/api/scenario/submit") == 0


def test_service_side_empty_scope_is_refused_at_submit(client, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(buses=""))
    with pytest.raises(ValidationError) as exc:
        client.submit(path, check=False)
    assert exc.value.status == 422 and exc.value.refusals[0].code == "bus_set_missing"


def test_failed_scenario_raises_with_parsed_refusals(client, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(name="fail-demo"))
    sid = client.submit(path).scenario_id
    with pytest.raises(ScenarioFailed) as exc:
        client.wait(sid, poll_s=1, sleep=lambda s: None)
    err = exc.value
    assert err.scenario.status == "failed"
    assert [r.code for r in err.refusals] == ["unmappable_bus", "load_starts_after_horizon", "scope_missing"]
    assert err.parsed.omitted == 3
    assert err.refusals[0].table == "add_candidates" and err.refusals[0].id == "site-a"
    assert "+3 more" in str(err)


def test_unserved_region_with_bus_names_is_refused_locally(client, mock, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(scope_id="somewhere_else"))
    with pytest.raises(LocalValidationError) as exc:
        client.submit(path)
    assert exc.value.refusals[0].code == "unknown_scope"
    assert mock.count("POST", "/api/scenario/submit") == 0


def test_unserved_region_is_a_validation_error(client, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(scope_id="somewhere_else", buses="[1, 2, 3]"))
    with pytest.raises(ValidationError) as exc:
        client.submit(path)
    assert exc.value.status == 422
    assert "not a served region" in str(exc.value)
    assert [r.code for r in exc.value.refusals] == ["scope_unserved"]
    assert "not a served region" in exc.value.refusals[0].message


def test_base_run_missing_maps_to_a_refusal(client, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(scope_id="toy_region_unrun"))
    with pytest.raises(ValidationError) as exc:
        client.submit(path)
    refusals = exc.value.refusals
    assert [r.code for r in refusals] == ["base_run_missing"]
    assert refusals[0].table == "scope" and refusals[0].category == "scope"
    assert "ce-studio scopes" in refusals[0].fix


def test_withdraw_only_while_queued(client, mock, toy_delta):
    mock.auto_advance = False
    sid = client.submit(toy_delta).scenario_id
    row = client.withdraw(sid)
    assert row.status == "withdrawn" and mock.scenario_status(sid) == "withdrawn"

    mock.auto_advance = True
    sid2 = client.submit(toy_delta).scenario_id
    client.status(sid2)  # the service starts it
    with pytest.raises(ConflictError) as exc:
        client.withdraw(sid2)
    assert exc.value.status == 409


def test_withdrawn_wait_raises(client, mock, toy_delta):
    mock.auto_advance = False
    sid = client.submit(toy_delta).scenario_id
    client.withdraw(sid)
    with pytest.raises(ScenarioFailed) as exc:
        client.wait(sid, poll_s=1, sleep=lambda s: None)
    assert exc.value.refusals[0].code == "withdrawn"


def test_status_of_unknown_id(client):
    with pytest.raises(ScenarioNotFound):
        client.status("scn-nope")


def test_wait_timeout(client, mock, toy_delta):
    mock.auto_advance = False
    sid = client.submit(toy_delta).scenario_id
    t = [0.0]

    def fake_sleep(s):
        t[0] += s

    with pytest.raises(WaitTimeout) as exc:
        client.wait(sid, poll_s=10, timeout_s=35, sleep=fake_sleep, clock=lambda: t[0])
    assert exc.value.scenario.status == "queued"
    assert t[0] == 30


# ------------------------------------------------------------------ results


def test_summary_and_caveats(client, toy_delta, tmp_path):
    final = run_to_end(client, toy_delta)
    s = client.summary(final.scenario_id)
    assert s.converged is True and s.converged_to_gap
    assert s.currency == "USD" and s.objective == pytest.approx(1.2345e7)
    assert s.wall_clock.solve_s == 61 and s.stop_cause == "gap"
    assert s.is_exploration and s.representative_blocks == 24
    assert any("representative" in c for c in s.caveats())

    slow = run_to_end(client, write_delta(tmp_path / "slow", delta_toml(name="slow-run")))
    s2 = client.summary(slow.scenario_id)
    assert not s2.converged_to_gap and not s2.is_exploration
    cav = s2.caveats()
    assert any(c.startswith("not converged") for c in cav)
    assert any("iteration_cap" in c for c in cav)


def test_results_not_published_yet(client, mock, toy_delta):
    mock.auto_advance = False
    sid = client.submit(toy_delta).scenario_id
    with pytest.raises(NotFoundError, match="not published"):
        client.fetch(sid, "investment.csv")


def test_json_wire_documents_are_never_written_to_disk(client, toy_delta, tmp_path):
    sid = run_to_end(client, toy_delta).scenario_id
    for fname in ("summary.json", "tables.json", "buses.geojson"):
        with pytest.raises(StudioError, match="wire format"):
            client.download(sid, fname, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_results_file_names_are_validated(client):
    for bad in ("../secret", "a/b.csv", "", ".hidden"):
        with pytest.raises(ValueError):
            client.fetch("scn-1", bad)


def test_table_index_and_downloads(client, toy_delta, tmp_path):
    sid = run_to_end(client, toy_delta).scenario_id
    idx = client.tables(sid)
    assert [e.name for e in idx.offered()] == ["investment", "lmp", "qa_checks"]
    withheld = idx.withheld()
    assert [e.name for e in withheld] == ["flows"] and withheld[0].reason
    assert idx.register == {"asrun_version": "0" * 64}

    gz = client.download_table(sid, "lmp", tmp_path / "out", index=idx)
    assert gz.name == "lmp.csv.gz"
    assert gzip.decompress(gz.read_bytes()).startswith(b"year,scenario,weather,block,bus,bus_name,period,")
    plain = client.download_table(sid, "lmp", tmp_path / "out2", decompress=True, index=idx)
    assert plain.name == "lmp.csv" and plain.read_text().count("\n") == 3 * 48 + 1

    with pytest.raises(StudioError, match="withheld"):
        client.download_table(sid, "flows", tmp_path / "out", index=idx)
    with pytest.raises(StudioError, match="no table"):
        client.download_table(sid, "nonexistent", tmp_path / "out", index=idx)

    paths = client.download_results(sid, tmp_path / "all")
    assert sorted(p.name for p in paths) == ["investment.csv", "lmp.csv.gz", "qa_checks.csv"]
    assert not list((tmp_path / "all").glob("*.part"))


def test_download_size_mismatch_is_discarded(client, mock, toy_delta, tmp_path):
    sid = run_to_end(client, toy_delta).scenario_id
    mock.published[sid]["investment.csv"] += b"2030,renewable,9,x,solar,1,north,1.0\n"  # tampered after indexing
    with pytest.raises(StudioError, match="download discarded"):
        client.download_table(sid, "investment", tmp_path)
    assert not (tmp_path / "investment.csv").exists()


def test_read_table(client, toy_delta):
    sid = run_to_end(client, toy_delta).scenario_id
    rows = client.read_table(sid, "investment")
    assert rows[0] == {"year": "2030", "component": "renewable", "id": "1", "name": "solar_north_1",
                       "technology": "solar", "bus": "1", "bus_name": "north", "built_mw": "120.0"}
    lmp = client.read_table(sid, "lmp")  # gzip on the wire, transparent here
    assert len(lmp) == 144


def test_read_table_as_dataframe(client, toy_delta):
    pd = pytest.importorskip("pandas")
    sid = run_to_end(client, toy_delta).scenario_id
    df = client.read_table(sid, "lmp", as_dataframe=True)
    assert isinstance(df, pd.DataFrame) and "lmp_gbp_per_mwh" in df.columns and len(df) == 144


def test_qa_report_pass(client, toy_delta):
    sid = run_to_end(client, toy_delta).scenario_id
    qa = client.qa_report(sid)
    assert qa.enabled and qa.passed and qa.status == "PASS"
    assert [c.check for c in qa.checks] == ["blocks_solved", "balance_residual", "policy_target"]
    assert qa.by_status("NA")[0].note == "no policy rows"
    assert qa.checks[1].tolerance == pytest.approx(1e-3) and qa.checks[1].unit == "MW"
    assert [c.population for c in qa.checks] == [24, 72, 0]
    assert qa.derived_status == "PASS" and qa.problems() == [] and qa.vacuous_passes == []


def test_qa_report_flags_a_vacuous_pass(client, mock, toy_delta):
    sid = run_to_end(client, toy_delta).scenario_id
    csv_text = mock.published[sid]["qa_checks.csv"].decode()
    mock.published[sid]["qa_checks.csv"] = csv_text.replace(
        "balance_residual,balance,PASS,0.0001,0.001,MW,bus 2 hour 17,0,72,", "balance_residual,balance,PASS,0.0001,"
        "0.001,MW,bus 2 hour 17,0,0,").encode()
    qa = client.qa_report(sid)
    assert [c.check for c in qa.vacuous_passes] == ["balance_residual"]
    assert qa.status == "PASS" and qa.derived_status == "INCOMPLETE"
    assert any("examined nothing" in p for p in qa.problems())


def test_qa_blocked_run_still_has_a_report(client, tmp_path):
    path = write_delta(tmp_path / "d", delta_toml(name="qa-block-demo"))
    sid = client.submit(path).scenario_id
    with pytest.raises(ScenarioFailed) as exc:
        client.wait(sid, poll_s=1, sleep=lambda s: None)
    assert exc.value.refusals[0].code == "qa_failed"
    assert exc.value.refusals[0].message == "balance_residual"
    qa = client.qa_report(sid)  # tables.json is absent on a blocked run
    assert qa.status == "FAIL" and qa.failing == ("balance_residual",) and qa.blocking is True
    assert qa.by_status("FAIL")[0].worst_where == "bus 2 hour 17"


def test_artifact_table_reads_parquet(client, mock, toy_delta):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    sid = run_to_end(client, toy_delta).scenario_id
    buf = io.BytesIO()
    pq.write_table(pa.table({"site_id": ["toy-bess-1"], "built_mw": [35.0]}), buf)
    mock.published[sid]["asset_economics.parquet"] = buf.getvalue()
    t = client.artifact_table(sid, "asset_economics")
    assert t.column("built_mw").to_pylist() == [35.0]


# ------------------------------------------------------------------ a run directory


def test_download_run_writes_toml_and_csv_only(client, toy_delta, tmp_path):
    sid = run_to_end(client, toy_delta).scenario_id
    out = client.download_run(sid, tmp_path / "run")
    names = sorted(p.name for p in out.iterdir())
    assert names == ["investment.csv", "lmp.csv", "manifest.toml", "qa_checks.csv", "qa_report.toml",
                     "summary.toml"]
    import tomllib

    summary = tomllib.loads((out / "summary.toml").read_text())
    assert summary["cost"]["total"] == pytest.approx(1.2345e7)
    assert summary["solve"] == {"relative_gap": 0.012, "converged": True, "stop_cause": "gap", "wall_s": 61}
    assert tomllib.loads((out / "manifest.toml").read_text())["money"] == {"currency": "USD"}
    inv = (out / "investment.csv").read_text().splitlines()
    assert inv[1].split(",")[4] == "solar_pv" and inv[3].split(",")[4] == "battery"  # CE format codes


def test_download_run_opens_in_the_results_reader(client, toy_delta, tmp_path):
    """The repository's results reader (results/) reads a downloaded run (a hard failure in strict mode)."""
    from conftest import import_sibling

    ce_results = import_sibling("ce_results", "results")
    sid = run_to_end(client, toy_delta).scenario_id
    run = ce_results.Run(client.download_run(sid, tmp_path / "run"))
    assert run.stamps.objective == pytest.approx(1.2345e7) and run.stamps.stop_cause == "gap"
    assert run.provenance.currency == "USD"
    builds = run.builds(by=["technology"])
    assert set(builds["technology"]) == {"solar_pv", "wind_onshore", "battery"}
    assert len(run.lmp()) == 144 and run.qa.status == "PASS"
