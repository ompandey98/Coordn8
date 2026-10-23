"""Two dense layers with a tanh in between and a softmax output."""
from __future__ import annotations

import numpy as np


def init_params(hidden: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    return {"w1": rng.normal(0, 0.5, (2, hidden)), "b1": np.zeros(hidden),
            "w2": rng.normal(0, 0.5, (hidden, 2)), "b2": np.zeros(2)}


def forward(params: dict, x):
    h = np.tanh(x @ params["w1"] + params["b1"])
    logits = h @ params["w2"] + params["b2"]
    logits -= logits.max(axis=1, keepdims=True)
    p = np.exp(logits)
    return h, p / p.sum(axis=1, keepdims=True)


def loss_and_grads(params: dict, x, y):
    """Cross-entropy loss and its gradients for one batch."""
    h, p = forward(params, x)
    n = len(x)
    loss = -np.log(p[np.arange(n), y] + 1e-9).mean()
    d = p.copy()
    d[np.arange(n), y] -= 1
    d /= n
    grads = {"w2": h.T @ d, "b2": d.sum(axis=0)}
    dh = (d @ params["w2"].T) * (1 - h ** 2)
    grads.update({"w1": x.T @ dh, "b1": dh.sum(axis=0)})
    return loss, grads
