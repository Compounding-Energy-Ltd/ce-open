# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Run the bundled mock Scenario Studio locally, to try the client offline.

    python examples/mock_studio.py --port 8765

then, in another shell:

    export CE_STUDIO_BASE_URL=http://127.0.0.1:8765
    export CE_STUDIO_API_KEY=demo-key
    ce-studio scopes
    ce-studio submit examples/toy_delta --wait --poll-s 1
    ce-studio results <scenario id> --qa --out toy_results

The mock's lifecycle is scripted (one step per queue poll) and every result
is a synthetic toy table — it exercises the client, not a solver.
"""

from __future__ import annotations

import argparse
import os
import time

from ce_studio.testing import MockStudio


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--api-key-env", default="CE_STUDIO_API_KEY",
                   help="variable holding the key the mock accepts (default: demo-key)")
    args = p.parse_args()
    key = os.environ.get(args.api_key_env) or "demo-key"
    with MockStudio(api_key=key, port=args.port) as mock:
        print(f"mock Scenario Studio on {mock.url} (ctrl-C to stop)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
