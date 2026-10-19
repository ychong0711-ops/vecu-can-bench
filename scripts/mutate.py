#!/usr/bin/env python3
"""Mutation runner (Track A2): seed bugs into the SWC layer, measure detection.

For every seed in mutation/seeds.yaml:
  copy tree -> apply exact text replacement -> build -> run selected tests
  -> DETECTED (tests fail) / SURVIVED (tests pass) / build_error / timeout
then write reports/mutation/mutation.{json,md} and exit non-zero when the
mutation score is below --min-score (CI gate; decision cmt-a2sd02).

Usage:
  python3 scripts/mutate.py                        # default suite, serial
  python3 scripts/mutate.py --tests all            # full pytest (slower)
  python3 scripts/mutate.py --jobs 4               # parallel (see note)
  python3 scripts/mutate.py --only brake_ramp_divisor
  python3 scripts/mutate.py --list

Note on --jobs: mutants share one vcan0 bus, so parallel runs are only
reliable with bus-free test selection (e.g. --tests tests/test_logic.py).
Functional tests in parallel may cross-talk; a warning is printed.
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML required: pip install -r requirements.txt")

DEFAULT_TESTS = ["tests/test_logic.py", "tests/test_functional.py"]
IGNORE = shutil.ignore_patterns(".git", "bin", "bin-asan", "reports", "mutation",
                                ".pytest_cache", "__pycache__", "*.o", ".venv",
                                "env", "node_modules")


def load_seeds(path):
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    target = doc["target"]
    seeds = doc["seeds"]
    for s in seeds:
        for key in ("id", "find", "replace"):
            if key not in s:
                raise SystemExit(f"seed missing '{key}': {s}")
    ids = [s["id"] for s in seeds]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate seed ids")
    return target, seeds


def copy_tree(root, dst):
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(root, dst, ignore=IGNORE)


def apply_seed(work, target, seed):
    """Returns (ok, message). Fails unless the find text occurs exactly once."""
    path = os.path.join(work, target)
    if not os.path.exists(path):
        return False, f"target missing: {target}"
    with open(path, encoding="utf-8") as f:
        src = f.read()
    count = src.count(seed["find"])
    if count != 1:
        return False, f"find text occurs {count}x (need exactly 1)"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(src.replace(seed["find"], seed["replace"], 1))
    return True, "applied"


def run_one(args, root, target, seed, workdir):
    """Executes one mutant end-to-end; returns the result dict."""
    t0 = time.monotonic()
    res = {"id": seed["id"], "desc": seed.get("desc", ""),
           "find": seed["find"], "replace": seed["replace"],
           "expect": seed.get("expect", ""),
           "equivalent": bool(seed.get("equivalent", False))}
    try:
        copy_tree(root, workdir)
    except OSError as e:
        res.update(status="error", note=f"copy failed: {e}", seconds=0)
        return res

    ok, msg = apply_seed(workdir, target, seed)
    if not ok:
        res.update(status="error", note=f"seed not applicable: {msg}",
                   seconds=round(time.monotonic() - t0, 2))
        return res

    build = subprocess.run([args.make, "-C", workdir, "-j", str(args.jobs_build)],
                           capture_output=True, text=True, timeout=args.timeout)
    if build.returncode != 0:
        tail = (build.stderr or build.stdout).strip().splitlines()
        res.update(status="build_error",
                   note=tail[-1] if tail else "make failed",
                   seconds=round(time.monotonic() - t0, 2))
        return res

    cmd = [sys.executable, "-m", "pytest", *args.tests, "-x", "-q",
           "--tb=no", "-rf", "-p", "no:cacheprovider"]
    try:
        test = subprocess.run(cmd, cwd=workdir, capture_output=True,
                              text=True, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        res.update(status="timeout", note=f">{args.timeout}s",
                   seconds=round(time.monotonic() - t0, 2))
        return res

    failed = [ln.split()[1] for ln in test.stdout.splitlines()
              if ln.startswith("FAILED ")]
    detected = test.returncode != 0
    if seed.get("equivalent"):
        # claim: behaviour-preserving -> must SURVIVE; a kill disproves it
        res.update(status="unexpected_kill" if detected else "equivalent",
                   detecting_test=failed[0] if failed else "",
                   seconds=round(time.monotonic() - t0, 2))
        return res
    res.update(status="detected" if detected else "survived",
               detecting_test=failed[0] if failed else "",
               seconds=round(time.monotonic() - t0, 2))
    if not detected and args.keep_work:
        res["workdir"] = workdir
    return res


def write_reports(outdir, results, tests, min_score):
    os.makedirs(outdir, exist_ok=True)
    scored = [r for r in results if r["status"] in ("detected", "survived")]
    detected = [r for r in scored if r["status"] == "detected"]
    survived = [r for r in scored if r["status"] == "survived"]
    equival = [r for r in results if r["status"] == "equivalent"]
    broken = [r for r in results
              if r["status"] in ("build_error", "timeout", "error",
                                 "unexpected_kill")]
    score = (100.0 * len(detected) / len(scored)) if scored else 0.0

    doc = {
        "track": "A2",
        "tests": tests,
        "min_score": min_score,
        "seed_total": len(results),
        "scored": len(scored),
        "detected": len(detected),
        "survived": len(survived),
        "equivalent_verified": len(equival),
        "invalid": len(broken),
        "score_pct": round(score, 2),
        "results": results,
    }
    jpath = os.path.join(outdir, "mutation.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)

    lines = ["# Mutation report — Track A2", "",
             f"- seeds: **{len(results)}**  (scored: {len(scored)}, "
             f"equivalent verified: {len(equival)}, invalid: {len(broken)})",
             f"- detected: **{len(detected)}**  survived: **{len(survived)}**",
             f"- **mutation score: {score:.1f}%** "
             f"(gate: >= {min_score}%)",
             f"- test selection: `{' '.join(tests)}`", "",
             "| seed | status | detecting test | note |",
             "|---|---|---|---|"]
    for r in results:
        note = r.get("detecting_test") or r.get("note") or r.get("desc", "")
        lines.append(f"| `{r['id']}` | {r['status']} | "
                     f"{r.get('detecting_test', '')} | {note if r['status'] != 'detected' else r.get('desc', '')} |")
    if survived:
        lines += ["", "## Survivors (undetected seeds — add tests, A2.4)", ""]
        for r in survived:
            lines.append(f"- `{r['id']}` — {r['desc']} "
                         f"(expected: {r.get('expect', '?')})")
    if equival:
        lines += ["", "## Equivalent mutants (verified survivors, excluded)", ""]
        for r in equival:
            lines.append(f"- `{r['id']}` — {r['desc']}")
    if broken:
        lines += ["", "## Invalid seeds (excluded from score)", ""]
        for r in broken:
            lines.append(f"- `{r['id']}` — {r['status']}: {r.get('note', '')}")
    mpath = os.path.join(outdir, "mutation.md")
    with open(mpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return doc, jpath, mpath


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--root", default=root)
    ap.add_argument("--seeds", default=os.path.join(root, "mutation", "seeds.yaml"))
    ap.add_argument("--tests", nargs="+", default=DEFAULT_TESTS,
                    help="pytest args ('all' selects tests/)")
    ap.add_argument("--jobs", type=int, default=1, help="parallel mutants")
    ap.add_argument("--jobs-build", type=int, default=2, help="make -j per mutant")
    ap.add_argument("--timeout", type=int, default=600, help="per-phase timeout s")
    ap.add_argument("--out", default=os.path.join(root, "reports", "mutation"))
    ap.add_argument("--work", default=os.path.join(root, "mutation", "work"))
    ap.add_argument("--only", nargs="+", default=None, help="run only these seed ids")
    ap.add_argument("--list", action="store_true", help="list seeds and exit")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep surviving mutants' work dirs for inspection")
    ap.add_argument("--min-score", type=float, default=90.0,
                    help="exit non-zero below this score (CI gate)")
    args = ap.parse_args()

    if args.tests == ["all"]:
        args.tests = ["tests/"]

    target, seeds = load_seeds(args.seeds)
    if args.only:
        seeds = [s for s in seeds if s["id"] in set(args.only)]
        if not seeds:
            sys.exit("no seeds matched --only")

    if args.list:
        for s in seeds:
            print(f"{s['id']:28s} {s.get('desc', '')}")
        return 0

    args.make = shutil.which("make")
    if not args.make:
        sys.exit("make not found")

    if args.jobs > 1 and any("test_logic" not in t for t in args.tests):
        print("WARNING: parallel mutants share vcan0; functional tests may "
              "cross-talk. Use --tests tests/test_logic.py for reliable jobs>1.",
              file=sys.stderr)

    print(f"[mutate] {len(seeds)} seeds, target={target}, "
          f"tests={' '.join(args.tests)}, jobs={args.jobs}")
    os.makedirs(args.work, exist_ok=True)
    results = []
    t0 = time.monotonic()

    def workdir_for(seed):
        return os.path.join(args.work, seed["id"])

    if args.jobs > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = {ex.submit(run_one, args, args.root, target, s,
                              workdir_for(s)): s for s in seeds}
            for fut in concurrent.futures.as_completed(futs):
                r = fut.result()
                results.append(r)
                print(f"  {r['id']:28s} {r['status']:12s} "
                      f"{r.get('detecting_test', '')} ({r.get('seconds', '?')}s)")
    else:
        for s in seeds:
            r = run_one(args, args.root, target, s, workdir_for(s))
            results.append(r)
            print(f"  {r['id']:28s} {r['status']:12s} "
                  f"{r.get('detecting_test', '')} ({r.get('seconds', '?')}s)")
            if r["status"] == "survived" and args.keep_work:
                print(f"    kept: {r.get('workdir', '')}")

    results.sort(key=lambda r: [s["id"] for s in seeds].index(r["id"]))
    doc, jpath, mpath = write_reports(args.out, results, args.tests, args.min_score)

    # cleanup work dirs (survivors kept only with --keep-work)
    for s in seeds:
        d = workdir_for(s)
        keep = args.keep_work and any(
            r["id"] == s["id"] and r["status"] == "survived" for r in results)
        if not keep and os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)

    print(f"\n[mutate] score {doc['score_pct']}% "
          f"({doc['detected']}/{doc['scored']} detected, "
          f"{doc['invalid']} invalid) in {time.monotonic() - t0:.1f}s")
    print(f"[mutate] json: {jpath}")
    print(f"[mutate] md  : {mpath}")
    if doc["score_pct"] < args.min_score:
        print(f"[mutate] FAIL: score below gate {args.min_score}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
