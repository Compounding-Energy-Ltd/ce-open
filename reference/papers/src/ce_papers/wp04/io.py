# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""Store surrogate artefacts as TOML (metadata) + CSV (weights).

Layout of a surrogate directory::

    network.toml      architecture, input names and box, free-form metadata
    weights.csv       long format: layer, kind, row, col, value
    pruning.csv       optional per-neuron pruning report

``kind`` is ``W`` / ``b`` for hidden layers (``layer`` 1..L) and ``w_out`` /
``b_out`` for the output layer (``layer`` L+1).
"""

from __future__ import annotations

import csv
import tomllib
from pathlib import Path

import numpy as np

from .mlp import ReluMLP


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        return repr(float(v))
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple, np.ndarray)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def save_network(net: ReluMLP, directory, *, input_names, x_lo, x_hi, metadata: dict | None = None) -> Path:
    """Write ``network.toml`` and ``weights.csv`` into ``directory``."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ReLU surrogate (CE-WP-2026-04 reference implementation)",
        "[network]",
        f"architecture = {_toml_value(list(net.architecture))}",
        f"input_names = {_toml_value(list(input_names))}",
        f"x_lo = {_toml_value(list(map(float, x_lo)))}",
        f"x_hi = {_toml_value(list(map(float, x_hi)))}",
    ]
    if metadata:
        lines.append("")
        lines.append("[metadata]")
        lines += [f"{k} = {_toml_value(v)}" for k, v in metadata.items()]
    (d / "network.toml").write_text("\n".join(lines) + "\n")

    with (d / "weights.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["layer", "kind", "row", "col", "value"])
        for l, (W, b) in enumerate(zip(net.weights, net.biases), start=1):
            for (i, j), v in np.ndenumerate(W):
                w.writerow([l, "W", i, j, repr(float(v))])
            for i, v in enumerate(b):
                w.writerow([l, "b", i, 0, repr(float(v))])
        L = len(net.weights) + 1
        for i, v in enumerate(net.w_out):
            w.writerow([L, "w_out", i, 0, repr(float(v))])
        w.writerow([L, "b_out", 0, 0, repr(float(net.b_out))])
    return d


def load_network(directory) -> tuple[ReluMLP, dict]:
    """Inverse of :func:`save_network`. Returns ``(net, toml_dict)``."""
    d = Path(directory)
    meta = tomllib.loads((d / "network.toml").read_text())
    arch = meta["network"]["architecture"]
    hidden = arch[1:-1]
    Ws = [np.zeros((hidden[l], arch[l])) for l in range(len(hidden))]
    bs = [np.zeros(h) for h in hidden]
    w_out, b_out = np.zeros(hidden[-1]), 0.0
    with (d / "weights.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            l, i, j, v = int(r["layer"]), int(r["row"]), int(r["col"]), float(r["value"])
            if r["kind"] == "W":
                Ws[l - 1][i, j] = v
            elif r["kind"] == "b":
                bs[l - 1][i] = v
            elif r["kind"] == "w_out":
                w_out[i] = v
            elif r["kind"] == "b_out":
                b_out = v
    return ReluMLP(Ws, bs, w_out, b_out), meta


def write_rows_csv(rows: list[dict], path) -> Path:
    """Write a list of homogeneous dicts (e.g. a pruning report) as CSV."""
    path = Path(path)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path
