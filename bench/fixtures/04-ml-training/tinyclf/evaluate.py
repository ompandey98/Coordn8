"""Validation loss and accuracy, computed without updating anything."""
from __future__ import annotations

import numpy as np

from .model import forward, loss_and_grads


def evaluate(params: dict, x, y) -> dict:
    loss, _ = loss_and_grads(params, x, y)
    _, p = forward(params, x)
    return {"loss": float(loss), "accuracy": float((p.argmax(axis=1) == y).mean())}
