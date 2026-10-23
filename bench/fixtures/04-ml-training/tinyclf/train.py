"""The training loop: epochs of mini-batches, validation after each epoch, early stopping."""
from __future__ import annotations

import argparse
import json

import numpy as np

from . import checkpoint
from .data import batches, make_dataset, split
from .evaluate import evaluate
from .model import init_params, loss_and_grads
from .optim import sgd_step


def train(cfg: dict) -> dict:
    x, y = make_dataset(seed=cfg["seed"])
    (xt, yt), (xv, yv) = split(x, y, cfg["val_fraction"], cfg["seed"])
    params = init_params(cfg["hidden"], cfg["seed"])
    rng = np.random.default_rng(cfg["seed"])
    best, waited, history = float("inf"), 0, []
    for epoch in range(cfg["epochs"]):
        for xb, yb in batches(xt, yt, cfg["batch_size"], rng):
            _loss, grads = loss_and_grads(params, xb, yb)
            sgd_step(params, grads, cfg["lr"])
        metrics = evaluate(params, xv, yv)
        history.append(metrics)
        if metrics["loss"] < best - 1e-4:
            best, waited = metrics["loss"], 0
            checkpoint.save(params, cfg["checkpoint"], epoch, best)
        else:
            waited += 1
            if waited >= cfg["patience"]:
                break
    return {"epochs_run": len(history), "best_val_loss": best, "last": history[-1]}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    a = ap.parse_args(argv)
    print(json.dumps(train(json.load(open(a.config)))))


if __name__ == "__main__":
    main()
