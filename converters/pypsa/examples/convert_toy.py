# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Convert the synthetic toy network to a CE bundle and back, printing what each direction approximated.

    python convert_toy.py [output_dir]

Writes ``<output_dir>/toy_bundle`` (CE bundle) and ``<output_dir>/toy_back`` (PyPSA CSV folder). PyPSA is
not required. With PyPSA installed, ``pypsa.Network("<output_dir>/toy_back")`` loads the result.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))  # run from a source checkout without installing

from ce_pypsa import export_bundle, import_network  # noqa: E402


def main(out: Path) -> None:
    network = HERE / "toy_network"
    bundle = import_network(network, out / "toy_bundle", currency="EUR", money_year=2030, overwrite=True)
    print(bundle.report)
    problems = bundle.validate()
    print("bundle checks:", "ok" if not problems else problems)
    print()
    for name, df in bundle.tables.items():
        print(f"  {name:20s} {len(df):5d} rows")
    print()
    _, report = export_bundle(out / "toy_bundle", out / "toy_back")
    print(report)
    print(f"\nbundle: {out / 'toy_bundle'}\nPyPSA:  {out / 'toy_back'}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="ce_pypsa_")))
