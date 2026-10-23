"""Plain stochastic gradient descent with an optional global norm clip."""
from __future__ import annotations

import numpy as np


def sgd_step(params: dict, grads: dict, lr: float, clip: float = 5.0) -> None:
    norm = np.sqrt(sum(float((g ** 2).sum()) for g in grads.values()))
    scale = min(1.0, clip / (norm + 1e-12))
    for k in params:
        params[k] -= lr * scale * grads[k]
