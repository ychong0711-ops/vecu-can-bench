#!/usr/bin/env bash
# Full experiment matrix -> reports/<scenario>/{frames.csv,summary.json,meta.json}
set -euo pipefail
DUR="${DUR:-15}"
./scripts/run_scenario.sh baseline      "$DUR" clear 0
./scripts/run_scenario.sh load20        "$DUR" clear 20
./scripts/run_scenario.sh load50        "$DUR" clear 50
./scripts/run_scenario.sh delay10       "$DUR" delay 0
./scripts/run_scenario.sh loss1         "$DUR" loss 0
./scripts/run_scenario.sh combo_load50_delay "$DUR" combo 50
python3 analysis/analyze.py --matrix reports --out reports/matrix
echo "matrix done -> reports/"
