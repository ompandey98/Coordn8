"""Save and load the best parameters seen so far."""
from __future__ import annotations

from pathlib import Path

import numpy as np


def save(params: dict, path: str, epoch: int, val_loss: float) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, epoch=epoch, val_loss=val_loss, **params)


def load(path: str) -> dict:
    data = np.load(path)
    return {k: data[k] for k in ("w1", "b1", "w2", "b2")}
