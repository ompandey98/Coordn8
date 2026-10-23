# Benchmark

Six fixed fixtures (small CLI, web API, data pipeline, ML training loop, infrastructure-as-code,
documents-only), each with a brief in `briefs/`. `python3 bench/run.py` scores a build on the six quality
dimensions, the palette gate, diagram-plan quality, language pass rate, claims coverage, page
accessibility, frames per second, peak disk and wall time, and writes `bench/report.md`.
`evals/` holds skill-vs-baseline evals with counted assertions.
