# tinyclf

A small training loop for a two-layer classifier, written with NumPy only. It is the
reference loop the team copies when it needs a quick baseline.

    python -m tinyclf.train --config config.json

The loop shuffles the data each epoch, trains in mini-batches, evaluates on a held-out split,
keeps the best checkpoint and stops early when the validation loss stops improving.
