#!/usr/bin/env python3
"""vECU-Bench analysis: monitor CSV -> statistics, plots, markdown report.

Usage:
  analyze.py reports/demo/frames.csv --out reports/demo
  analyze.py --matrix reports --out reports/matrix      (compare scenarios)

Metrics
  latency   one-way (rx_ms - embedded tx_ms) percentiles per CAN ID
  loss      16-bit sequence-gap counting per CAN ID (wrap-safe)
  bus load  nominal bits (47 + 8*dlc) per 100 ms window vs 500 kbit/s
  e2e       SENSOR_DIST sample -> CMD_BRAKE reaction (via SrcSeq join)
"""
import argparse
import glob
import json
import os
import sys

# Force C locale for locale-independent number parsing (decimal separator, thousands separator)
# Must be set before pandas import
os.environ.setdefault("LC_ALL", "C")

import numpy as np
import pandas as pd

from idmap_gen import ID_TO_NAME

BITRATE = 500_000
NAMES = ID_TO_NAME

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


REQUIRED_COLUMNS = ["rx_us", "id", "dlc", "seq", "tx_ms", "lat_ms", "val"]
NUMERIC_COLUMNS = ["rx_us", "dlc", "seq", "tx_ms", "lat_ms", "val"]


def load_and_validate_csv(path, skip_validation=False):
    df = pd.read_csv(path)
    
    if not skip_validation:
        actual_cols = list(df.columns)
        missing = [c for c in REQUIRED_COLUMNS if c not in actual_cols]
        if missing:
            print(f"ERROR: Missing required columns: {missing}", file=sys.stderr)
            print(f"Expected columns: {REQUIRED_COLUMNS}", file=sys.stderr)
            print(f"Actual columns:   {actual_cols}", file=sys.stderr)
            sys.exit(1)
        
        unexpected = [c for c in actual_cols if c not in REQUIRED_COLUMNS]
        if unexpected:
            print(f"WARNING: Unexpected columns (ignored): {unexpected}", file=sys.stderr)
        
        for col in NUMERIC_COLUMNS:
            if col in df.columns:
                if not pd.api.types.is_numeric_dtype(df[col]):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                    if df[col].isna().any():
                        bad_rows = df[df[col].isna()].index.tolist()
                        print(f"ERROR: Column '{col}' contains non-numeric values at rows: {bad_rows[:10]}", file=sys.stderr)
                        sys.exit(1)
    
    df["id"] = df["id"].map(lambda s: int(str(s), 16)).astype(int)
    df["name"] = df["id"].map(NAMES).fillna(df["id"].apply(lambda i: f"0x{i:03X}"))
    df["bits"] = 47 + 8 * df["dlc"]
    return df


def load_csv(path):
    """Backward-compatible wrapper."""
    return load_and_validate_csv(path, skip_validation=False)


def seq_loss(seq):
    """Wrap-safe lost-frame count from a sequence of u16 counters."""
    if len(seq) < 2:
        return 0
    d = (np.diff(seq.astype(np.int64)) - 1) % 65536
    return int(d[(d > 0) & (d < 32768)].sum())


def per_id_stats(df):
    rows = []
    for (cid, name), g in df.groupby(["id", "name"]):
        lat = g["lat_ms"].dropna()
        rows.append({
            "id": f"0x{cid:03X}", "name": name, "frames": len(g),
            "lost": seq_loss(g["seq"].dropna().values) if g["seq"].notna().any() else 0,
            "lat_n": int(len(lat)),
            "lat_p50": float(lat.quantile(0.50)) if len(lat) else None,
            "lat_p95": float(lat.quantile(0.95)) if len(lat) else None,
            "lat_p99": float(lat.quantile(0.99)) if len(lat) else None,
            "lat_max": float(lat.max()) if len(lat) else None,
        })
    out = pd.DataFrame(rows)
    out["loss_pct"] = 100 * out["lost"] / (out["frames"] + out["lost"])
    return out


def bus_load_series(df, window_ms=100):
    t0 = df["rx_us"].min()
    win = ((df["rx_us"] - t0) // (window_ms * 1000)).astype(int)
    bits = df.groupby(win)["bits"].sum()
    pct = bits / (BITRATE * window_ms / 1000) * 100
    return pct


def e2e_reaction(df):
    """SENSOR_DIST sample -> CMD_BRAKE reaction time (ms).

    CMD_BRAKE payload value = (SrcSeq8 << 8) | brake_level, where SrcSeq8 is
    the low byte of the SENSOR_DIST seq that triggered the command. We join on
    that key and pick the most recent matching sensor sample not newer than
    the command reception time. The result includes the 100 ms control cycle
    of ecu_feature -- i.e. the true sensor-to-actuator reaction chain.
    """
    if "val" not in df.columns:
        return None
    dist = df[df["id"] == 0x101].dropna(subset=["tx_ms", "seq"])
    brake = df[(df["id"] == 0x200)].dropna(subset=["tx_ms", "lat_ms", "val"])
    if dist.empty or brake.empty:
        return None
    d_seq8 = dist["seq"].values.astype(np.int64) % 256
    d_tx = dist["tx_ms"].values.astype(np.int64)
    src8 = (brake["val"].values.astype(np.int64) >> 8) & 0xFF
    b_rx = (brake["tx_ms"].values.astype(np.int64)
            + brake["lat_ms"].values.astype(np.int64))
    out = []
    for i in range(len(brake)):
        cand = np.flatnonzero((d_seq8 == src8[i]) & (d_tx <= b_rx[i]))
        if len(cand):
            j = cand[np.argmax(d_tx[cand])]
            dt = int((b_rx[i] - d_tx[j]) % (2 ** 32))
            if dt < 60000:
                out.append(dt)
    return np.array(out) if out else None


def plot_e2e(e2e, out):
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.hist(e2e, bins=40, color="#1a5276", alpha=0.85)
    ax.axvline(np.percentile(e2e, 99), color="#922b21", linestyle="--",
               label=f"p99 = {np.percentile(e2e, 99):.0f} ms")
    ax.set_xlabel("sensor sample -> brake command (ms)")
    ax.set_ylabel("count")
    ax.set_title("End-to-end reaction time (SENSOR_DIST -> CMD_BRAKE)")
    ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(out, dpi=140); plt.close(fig)


def plot_latency_cdf(df, out):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for (cid, name), g in df.groupby(["id", "name"]):
        lat = g["lat_ms"].dropna()
        if len(lat) < 10 or cid in (0x400,):
            continue
        xs = np.sort(lat.values)
        ys = np.arange(1, len(xs) + 1) / len(xs) * 100
        ax.step(xs, ys, where="post", label=name, linewidth=1.2)
    ax.set_xscale("symlog", linthresh=1)
    ax.set_xlabel("one-way latency (ms, symlog)")
    ax.set_ylabel("percentile of frames (%)")
    ax.set_title("Latency CDF per CAN ID")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def plot_bus_load(pct, out):
    fig, ax = plt.subplots(figsize=(7, 3.5))
    t = pct.index * 0.1
    ax.plot(t, pct.values, linewidth=1.0)
    ax.set_xlabel("time (s)")
    ax.set_ylabel("bus load (%)")
    ax.set_title("Bus load per 100 ms window (nominal bits vs 500 kbit/s)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    plt.close(fig)


def analyze_single(csv_path, outdir, skip_validation=False):
    os.makedirs(outdir, exist_ok=True)
    df = load_and_validate_csv(csv_path, skip_validation=skip_validation)
    stats = per_id_stats(df)
    pct = bus_load_series(df)
    dur = (df["rx_us"].max() - df["rx_us"].min()) / 1e6 if len(df) > 1 else 0
    summary = {
        "frames_total": int(len(df)),
        "duration_s": round(float(dur), 3),
        "avg_load_pct": round(float(df["bits"].sum() / (BITRATE * max(dur, 1e-9) ) * 100), 3),
        "max_load_pct_100ms": round(float(pct.max()), 3) if len(pct) else 0,
        "ids": json.loads(stats.to_json(orient="records")),
    }
    plot_latency_cdf(df, os.path.join(outdir, "latency_cdf.png"))
    plot_bus_load(pct, os.path.join(outdir, "bus_load.png"))
    e2e = e2e_reaction(df)
    if e2e is not None and len(e2e):
        summary["e2e_dist_to_brake_ms"] = {
            "n": int(len(e2e)),
            "p50": float(np.percentile(e2e, 50)),
            "p95": float(np.percentile(e2e, 95)),
            "p99": float(np.percentile(e2e, 99)),
            "max": float(e2e.max()),
        }
        plot_e2e(e2e, os.path.join(outdir, "e2e_reaction.png"))
    with open(os.path.join(outdir, "analysis.json"), "w") as f:
        json.dump(summary, f, indent=2)
    md = ["# vECU-Bench run report", "",
          f"- frames: **{summary['frames_total']}** over {summary['duration_s']} s",
          f"- bus load: avg **{summary['avg_load_pct']}%**, max(100 ms) {summary['max_load_pct_100ms']}%", "",
          "| ID | Name | Frames | Lost | Loss % | p50 | p95 | p99 | max |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in summary["ids"]:
        p = lambda k: "-" if r[k] is None else f"{r[k]:.0f}"  # noqa: E731
        md.append(f"| {r['id']} | {r['name']} | {r['frames']} | {r['lost']} | "
                  f"{r['loss_pct']:.3f} | {p('lat_p50')} | {p('lat_p95')} | {p('lat_p99')} | {p('lat_max')} |")
    md += ["", "![latency CDF](latency_cdf.png)", "", "![bus load](bus_load.png)", ""]
    if "e2e_dist_to_brake_ms" in summary:
        e = summary["e2e_dist_to_brake_ms"]
        md += ["", f"End-to-end reaction (SENSOR_DIST -> CMD_BRAKE): n={e['n']}, "
                   f"p50={e['p50']:.0f} ms, p95={e['p95']:.0f} ms, "
                   f"p99={e['p99']:.0f} ms, max={e['max']:.0f} ms",
               "", "![e2e reaction](e2e_reaction.png)", ""]
    with open(os.path.join(outdir, "report.md"), "w") as f:
        f.write("\n".join(md))
    print(f"analysis -> {outdir} (analysis.json, report.md, 2 plots)")
    return summary


def analyze_matrix(root, outdir):
    os.makedirs(outdir, exist_ok=True)
    rows = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        sp = os.path.join(d, "summary.json")
        mp = os.path.join(d, "meta.json")
        if not os.path.isdir(d) or not os.path.exists(sp):
            continue
        s = load_json(sp)
        meta = load_json(mp) if os.path.exists(mp) else {}
        dist = s["ids"].get("0x101", {})
        pong = s["ids"].get("0x501", {})
        rows.append({
            "scenario": meta.get("scenario", os.path.basename(d)),
            "netem": meta.get("netem", "-"), "load_pct": meta.get("load_pct", 0),
            "meas_load": round(s["bus"]["avg_load_pct"], 2),
            "dist_p99": (dist.get("lat_ms") or {}).get("p99"),
            "dist_loss": dist.get("loss_pct"),
            "rtt_p50": (pong.get("lat_ms") or {}).get("p50"),
            "rtt_p99": (pong.get("lat_ms") or {}).get("p99"),
            "frames": s["frames_total"],
        })
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(outdir, "matrix.csv"), index=False)
    if len(df):
        fig, ax = plt.subplots(figsize=(8, 4))
        x = np.arange(len(df))
        ax.bar(x - 0.2, df["rtt_p50"].fillna(0), 0.4, label="RTT p50 (ms)")
        ax.bar(x + 0.2, df["rtt_p99"].fillna(0), 0.4, label="RTT p99 (ms)")
        ax.set_xticks(x, df["scenario"], rotation=20, ha="right", fontsize=8)
        ax.set_ylabel("ms"); ax.set_title("PING/PONG RTT by scenario")
        ax.grid(alpha=0.3, axis="y"); ax.legend()
        fig.tight_layout(); fig.savefig(os.path.join(outdir, "matrix_rtt.png"), dpi=140)
        plt.close(fig)
    with open(os.path.join(outdir, "matrix.md"), "w") as f:
        f.write("# Scenario matrix\n\n" + df.to_markdown(index=False) + "\n")
    print(f"matrix -> {outdir} (matrix.csv, matrix.md, matrix_rtt.png)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", nargs="?", help="monitor frames.csv")
    ap.add_argument("--matrix", help="root dir with scenario subdirs")
    ap.add_argument("--out", default="reports/out")
    ap.add_argument("--skip-validation", action="store_true",
                    help="bypass CSV schema validation (for edge cases)")
    a = ap.parse_args()
    if a.matrix:
        analyze_matrix(a.matrix, a.out)
    elif a.csv:
        analyze_single(a.csv, a.out, skip_validation=a.skip_validation)
    else:
        ap.error("provide a CSV file or --matrix DIR")


if __name__ == "__main__":
    sys.exit(main())
