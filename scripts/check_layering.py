#!/usr/bin/env python3
"""Layering gate (Track A1): enforce AUTOSAR-style layer boundaries.

Rules (allowlist of #include basenames per gated file):
  swc_brake.h/.c  SWC layer  -> stdint/stddef/own header only.
                  FORBIDDEN: linux/*, sys/*, common.h, rte_*, ecu_* ...
  rte_feature.h   RTE layer  -> stdint/stddef only (no logic, no OS, no SWC).

Any violation exits 1 with a file:line report — wired into `make lint`
(so CI's qa job enforces it too).

Usage:  python3 scripts/check_layering.py [--root DIR]
"""
import argparse
import os
import re
import sys

RE_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')

# file (relative to repo root) -> allowed include basenames
GATED = {
    os.path.join("include", "swc_brake.h"): {"stdint.h", "stddef.h", "swc_brake.h"},
    os.path.join("src", "swc_brake.c"): {"stdint.h", "stddef.h", "swc_brake.h"},
    os.path.join("include", "rte_feature.h"): {"stdint.h", "stddef.h"},
}


def check_file(root, relpath, allowed):
    path = os.path.join(root, relpath)
    if not os.path.exists(path):
        return [f"{relpath}: gated file missing"]
    violations = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            m = RE_INCLUDE.match(line)
            if not m:
                continue
            inc = m.group(1)
            base = os.path.basename(inc)
            if base not in allowed:
                violations.append(
                    f"{relpath}:{lineno}: forbidden include <{inc}> "
                    f"(allowed: {', '.join(sorted(allowed))})")
    return violations


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    args = ap.parse_args()

    violations = []
    for relpath, allowed in GATED.items():
        violations.extend(check_file(args.root, relpath, allowed))

    if violations:
        print("layering gate FAILED (docs/ARCHITECTURE.md §8, Track A1):",
              file=sys.stderr)
        for v in violations:
            print("  " + v, file=sys.stderr)
        return 1
    print(f"layering gate ok ({len(GATED)} gated files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
