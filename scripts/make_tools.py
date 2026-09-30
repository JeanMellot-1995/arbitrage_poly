"""Portable helpers for the Makefile (stdlib only, works on macOS/Linux/Windows)."""

from __future__ import annotations

import argparse
import csv
import re
import shutil
from pathlib import Path

TARGET_RE = re.compile(r"^([a-zA-Z0-9_-]+):.*?## (.*)$")


def cmd_help(args: argparse.Namespace) -> None:
    for line in Path(args.makefile).read_text(encoding="utf-8").splitlines():
        match = TARGET_RE.match(line)
        if match:
            print(f"  {match.group(1):<18} {match.group(2)}")


def cmd_clean(args: argparse.Namespace) -> None:
    root = Path(".")
    for cache in root.rglob("__pycache__"):
        if cache.is_dir() and ".venv" not in cache.parts:
            shutil.rmtree(cache, ignore_errors=True)
    for name in (".pytest_cache", ".ruff_cache"):
        shutil.rmtree(root / name, ignore_errors=True)


def _num(row: dict[str, str], key: str) -> float:
    value = (row.get(key) or "").strip()
    try:
        return float(value)
    except ValueError:
        return 0.0


def cmd_daily(args: argparse.Namespace) -> None:
    print()
    print(
        f"{'utc_day':<10} {'n':>5} {'acc':>7} {'brier':>7} {'logloss':>8} "
        f"{'bets':>6} {'pnl_usd':>9} {'roi':>8}"
    )
    with open(args.daily_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            print(
                f"{row.get('utc_day', ''):<10} {int(_num(row, 'n')):>5d} "
                f"{100 * _num(row, 'accuracy'):>6.1f}% "
                f"{_num(row, 'brier_score'):>7.4f} {_num(row, 'log_loss'):>8.4f} "
                f"{int(_num(row, 'pnl_bets')):>6d} {_num(row, 'total_pnl_usd'):>+9.2f} "
                f"{100 * _num(row, 'roi'):>+7.2f}%"
            )
    prefix = args.prefix
    print(f"Files: {prefix}.{{csv,json}} and {prefix}-{{bins,daily}}.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    help_parser = sub.add_parser("help", help="List documented Makefile targets")
    help_parser.add_argument("makefile", nargs="?", default="Makefile")
    help_parser.set_defaults(func=cmd_help)

    clean_parser = sub.add_parser("clean", help="Remove caches and bytecode")
    clean_parser.set_defaults(func=cmd_clean)

    daily_parser = sub.add_parser("daily", help="Print the Oracle daily summary table")
    daily_parser.add_argument("daily_csv")
    daily_parser.add_argument("--prefix", required=True)
    daily_parser.set_defaults(func=cmd_daily)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
