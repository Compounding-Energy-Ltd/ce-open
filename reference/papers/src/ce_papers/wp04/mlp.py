# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""A small ReLU multilayer perceptron and its trainer (CE-WP-2026-04 eq. 1,
Sec. 3.2).

Forward pass (eq. 1)::

    z_1 = W_1 x + b_1,        a_1 = ReLU(z_1)
    z_l = W_l a_{l-1} + b_l,  a_l = ReLU(z_l)        l = 2..L
    f_hat(x) = w_out' a_L + b_out

Training is plain NumPy: He initialisation and mini-batch SGD (with
momentum) on the mean squared error. Inputs are min-max normalised for
training, and the normalisation is folded back into ``W_1, b_1`` so the
stored network takes physical inputs directly -- which is what the MILP
embedding and the bound propagation need.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ReluMLP:
    weights: list[np.ndarray]
    """``W_l`` with shape ``(h_l, h_{l-1})`` for each hidden layer."""
    biases: list[np.ndarray]
    w_out: np.ndarray
    b_out: float

    @property
    def architecture(self) -> tuple[int, ...]:
        """``(n, h_1, ..., h_L, 1)``."""
        return (self.weights[0].shape[1], *[W.shape[0] for W in self.weights], 1)

    def preactivations(self, X) -> list[np.ndarray]:
        """``[z_1, ..., z_L]``, each of shape ``(m, h_l)``."""
        a = np.atleast_2d(np.asarray(X, dtype=float))
        zs = []
        for W, b in zip(self.weights, self.biases):
            z = a @ W.T + b
            zs.append(z)
            a = np.maximum(z, 0.0)
        return zs

    def __call__(self, X) -> np.ndarray:
        zs = self.preactivations(X)
        return np.maximum(zs[-1], 0.0) @ self.w_out + self.b_out


@dataclass
class TrainReport:
    train_mse: float
    val_mse: float
    val_r2: float
    epochs: int
    n_train: int
    n_val: int
    history: list[tuple[int, float, float]] = field(default_factory=list)
    """``(epoch, train_mse, val_mse)`` every ``log_every`` epochs."""


def train_mlp(
    X,
    y,
    *,
    hidden: tuple[int, ...] = (16, 8),
    epochs: int = 800,
    lr: float = 0.01,
    momentum: float = 0.9,
    batch: int = 32,
    val_frac: float = 0.2,
    seed: int = 20260629,
    log_every: int = 50,
) -> tuple[ReluMLP, TrainReport]:
    """Fit a ReLU MLP to ``(X, y)`` with mini-batch SGD.

    The defaults follow the paper's case study (two hidden layers 16-8,
    learning rate 0.01, 800 epochs, 80/20 split); momentum is an addition
    that makes plain SGD converge in a sensible number of epochs.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float).ravel()
    rng = np.random.default_rng(seed)
    m, n = X.shape
    perm = rng.permutation(m)
    n_val = int(round(val_frac * m))
    val, tr = perm[:n_val], perm[n_val:]

    lo, hi = X[tr].min(axis=0), X[tr].max(axis=0)
    span = np.where(hi > lo, hi - lo, 1.0)
    Xn = (X - lo) / span

    sizes = (n, *hidden)
    Ws = [rng.normal(0.0, np.sqrt(2.0 / sizes[i]), (sizes[i + 1], sizes[i])) for i in range(len(hidden))]
    bs = [np.zeros(h) for h in hidden]
    w_out = rng.normal(0.0, np.sqrt(2.0 / hidden[-1]), hidden[-1])
    b_out = float(y[tr].mean())
    params = Ws + bs + [w_out]
    vel = [np.zeros_like(p) for p in params] + [0.0]

    def forward(A):
        acts, zs = [A], []
        for W, b in zip(Ws, bs):
            z = acts[-1] @ W.T + b
            zs.append(z)
            acts.append(np.maximum(z, 0.0))
        return acts, zs, acts[-1] @ w_out + b_out

    def mse(idx):
        return float(np.mean((forward(Xn[idx])[2] - y[idx]) ** 2))

    history = []
    nL = len(hidden)
    for epoch in range(1, epochs + 1):
        order = rng.permutation(tr)
        for start in range(0, order.size, batch):
            idx = order[start : start + batch]
            acts, zs, pred = forward(Xn[idx])
            dy = 2.0 * (pred - y[idx]) / idx.size
            grads_W, grads_b = [None] * nL, [None] * nL
            g_wout, g_bout = acts[-1].T @ dy, float(dy.sum())
            delta = np.outer(dy, w_out) * (zs[-1] > 0)
            for l in range(nL - 1, -1, -1):
                grads_W[l] = delta.T @ acts[l]
                grads_b[l] = delta.sum(axis=0)
                if l > 0:
                    delta = (delta @ Ws[l]) * (zs[l - 1] > 0)
            grads = grads_W + grads_b + [g_wout]
            for i, (p, g) in enumerate(zip(params, grads)):
                vel[i] = momentum * vel[i] - lr * g
                p += vel[i]
            vel[-1] = momentum * vel[-1] - lr * g_bout
            b_out += vel[-1]
        if epoch % log_every == 0 or epoch == epochs:
            history.append((epoch, mse(tr), mse(val)))

    # Fold the input normalisation into the first layer: W_1 (x - lo)/span + b_1.
    W1 = Ws[0] / span[None, :]
    b1 = bs[0] - W1 @ lo
    net = ReluMLP([W1] + [W.copy() for W in Ws[1:]], [b1] + [b.copy() for b in bs[1:]], w_out.copy(), float(b_out))

    val_pred = net(X[val])
    val_mse = float(np.mean((val_pred - y[val]) ** 2))
    var = float(np.var(y[val]))
    report = TrainReport(
        train_mse=float(np.mean((net(X[tr]) - y[tr]) ** 2)),
        val_mse=val_mse,
        val_r2=1.0 - val_mse / var if var > 0 else float("nan"),
        epochs=epochs,
        n_train=tr.size,
        n_val=n_val,
        history=history,
    )
    return net, report
