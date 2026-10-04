# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Submit a delta bundle, wait for it, then read the summary, QA report and a table.

Against a real service, set CE_STUDIO_BASE_URL and CE_STUDIO_API_KEY. With no
CE_STUDIO_BASE_URL set, this script starts the bundled mock so it runs offline.

    python examples/submit_and_wait.py [DELTA_DIR]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ce_studio import ClientConfig, ScenarioFailed, StudioClient
from ce_studio.testing import MockStudio

HERE = Path(__file__).resolve().parent


def run(client: StudioClient, delta_dir: Path) -> None:
    receipt = client.submit(delta_dir)
    print(f"submitted {receipt.scenario_id}")
    try:
        final = client.wait(receipt.scenario_id, poll_s=1, on_update=lambda s: print("  ", s.headline))
    except ScenarioFailed as err:
        print(err)
        for r in err.refusals:
            print(f"   fix ({r.code}): {r.fix}")
        return

    summary = client.summary(final.scenario_id)
    print(f"objective {summary.objective:,.0f} {summary.currency}; converged={summary.converged}")
    for caveat in summary.caveats():
        print(f"   caveat: {caveat}")

    qa = client.qa_report(final.scenario_id)
    print(f"quality report: {qa.status}")
    for check in qa.checks:
        print(f"   {check.status:<5} {check.family}/{check.check}")

    index = client.tables(final.scenario_id)
    print("tables offered:", ", ".join(e.name for e in index.offered()))
    rows = client.read_table(final.scenario_id, "investment", index=index)
    for row in rows:
        print(f"   built {row['built_mw']} MW of {row['technology']} at bus {row['bus_name']}")
    run_dir = client.download_run(final.scenario_id, HERE / "toy_results")
    print(f"run directory {run_dir} (CSV tables + TOML; open it with ce_results.Run):")
    print("  ", ", ".join(sorted(p.name for p in run_dir.iterdir())))


def main() -> None:
    delta_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "toy_delta"
    if os.environ.get("CE_STUDIO_BASE_URL"):
        run(StudioClient.from_env(), delta_dir)
        return
    with MockStudio(api_key="demo-key") as mock:
        print(f"(no CE_STUDIO_BASE_URL set — using the bundled mock at {mock.url})")
        run(StudioClient(ClientConfig(base_url=mock.url, api_key="demo-key")), delta_dir)


if __name__ == "__main__":
    main()
