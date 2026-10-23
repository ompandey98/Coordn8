"""Synthetic two-class data, a fixed train/validation split and shuffled mini-batches."""
from __future__ import annotations

import numpy as np


def make_dataset(n: int = 2000, seed: int = 13):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 2))
    y = ((x[:, 0] ** 2 + x[:, 1] ** 2) > 1.0).astype(np.int64)
    return x.astype(np.float32), y


def split(x, y, val_fraction: float, seed: int):
    idx = np.random.default_rng(seed).permutation(len(x))
    cut = int(len(x) * (1 - val_fraction))
    return (x[idx[:cut]], y[idx[:cut]]), (x[idx[cut:]], y[idx[cut:]])


def batches(x, y, batch_size: int, rng):
    """Yield shuffled mini-batches; a new order every epoch."""
    order = rng.permutation(len(x))
    for i in range(0, len(x), batch_size):
        sel = order[i:i + batch_size]
        yield x[sel], y[sel]
