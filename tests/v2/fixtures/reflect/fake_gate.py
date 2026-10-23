#!/usr/bin/env python3
"""Test double for a gate: prints a palette-gate result. argv[1] == 'pass' -> PASS, exit 0;
otherwise the seeded contrast failure, exit 1. Prints a marker so a test can tell it ran."""
import json
import sys
from pathlib import Path

print("FAKE-GATE-RAN", file=sys.stderr)
if len(sys.argv) > 1 and sys.argv[1] == "pass":
    print(json.dumps({"gate": "palette", "status": "PASS", "summary": "palette and scene pass"}))
    sys.exit(0)
print((Path(__file__).parent / "palette_contrast_fail.json").read_text().replace("\n", " "))
sys.exit(1)
