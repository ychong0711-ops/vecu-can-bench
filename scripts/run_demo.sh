#!/usr/bin/env bash
# Quick single-run demo: 20 s full stack + 20% background load -> reports/demo
set -euo pipefail
DUR="${DUR:-20}"
./scripts/run_scenario.sh demo "$DUR" clear 20
python3 analysis/analyze.py reports/demo/frames.csv --out reports/demo
echo "demo done -> reports/demo (summary.json, frames.csv, plots, report.md)"
