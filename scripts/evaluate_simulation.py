#!/usr/bin/env python3
"""Calculate performance metrics from a replay simulation CSV."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

REQUIRED_COLUMNS = {
    "window_start_ns",
    "outcome",
    "stake_usd",
    "payout_usd",
    "fees_usd",
    "pnl_usd",
}


def evaluate_simulation(csv_path: Path) -> dict[str, object]:
    """Return aggregate performance metrics for one simulation CSV."""

    total_rows = 0
    orders = 0
    wins = 0
    losses = 0
    ties = 0
    malformed_orders = 0
    duplicate_order_windows = 0
    order_windows: set[str] = set()
    status_counts: Counter[str] = Counter()
    total_staked = 0.0
    total_payout = 0.0
    total_fees = 0.0
    total_pnl = 0.0
    cumulative_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown = 0.0

    with csv_path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        columns = set(reader.fieldnames or [])
        missing_columns = sorted(REQUIRED_COLUMNS - columns)
        if missing_columns:
            raise ValueError(f"missing required CSV columns: {', '.join(missing_columns)}")

        for row in reader:
            total_rows += 1
            status = row.get("economic_status", "")
            if status:
                status_counts[status] += 1
            if not row.get("pnl_usd", ""):
                continue

            orders += 1
            window = row["window_start_ns"]
            if window in order_windows:
                duplicate_order_windows += 1
            order_windows.add(window)

            try:
                stake = float(row["stake_usd"])
                payout = float(row["payout_usd"])
                fees = float(row["fees_usd"])
                pnl = float(row["pnl_usd"])
            except (TypeError, ValueError):
                malformed_orders += 1
                continue

            total_staked += stake
            total_payout += payout
            total_fees += fees
            total_pnl += pnl
            cumulative_pnl += pnl
            peak_pnl = max(peak_pnl, cumulative_pnl)
            max_drawdown = min(max_drawdown, cumulative_pnl - peak_pnl)

            if row["outcome"] == "TIE":
                ties += 1
            elif pnl > 0.0:
                wins += 1
            else:
                losses += 1

    decisive_orders = wins + losses
    return {
        "file": str(csv_path),
        "total_rows": total_rows,
        "orders": orders,
        "distinct_order_windows": len(order_windows),
        "duplicate_order_windows": duplicate_order_windows,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "win_rate": wins / decisive_orders if decisive_orders else None,
        "total_staked_usd": total_staked,
        "total_payout_usd": total_payout,
        "total_fees_usd": total_fees,
        "total_pnl_usd": total_pnl,
        "roi": total_pnl / total_staked if total_staked else None,
        "max_drawdown_usd": max_drawdown,
        "malformed_orders": malformed_orders,
        "economic_status_counts": dict(status_counts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path, help="simulation CSV to evaluate")
    parser.add_argument(
        "--report",
        type=Path,
        help="optional JSON report path; metrics are always printed to stdout",
    )
    args = parser.parse_args()

    report = evaluate_simulation(args.csv_file)
    serialized = json.dumps(report, indent=2, sort_keys=True)
    print(serialized)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())