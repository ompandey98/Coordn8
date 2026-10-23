# orderflow

A nightly batch pipeline that takes the day's order exports (CSV), checks every row, removes
duplicates, adds the customer region and writes JSON Lines files that the reporting job reads.
Rows that fail a check go to a quarantine file with the reason, so a bad row never blocks the run.

    python -m pipeline.dag --config config/pipeline.yaml --date 2026-03-01
