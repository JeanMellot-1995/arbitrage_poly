"""Download Binance BTCUSDT daily aggTrades and concatenate them for the backtest.

The UTC day range is derived from the Polymarket markets JSON (or given with
--start-date/--end-date). Output is a header-less CSV sorted chronologically,
as expected by ``arbitrage-poly-backtest --trades``.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

BASE_URL = (
    "https://data.binance.vision/data/spot/daily/aggTrades/{symbol}/{symbol}-aggTrades-{day}.zip"
)
WINDOW = timedelta(minutes=5)


def markets_day_range(markets_path: Path) -> tuple[date, date]:
    records = json.loads(markets_path.read_text(encoding="utf-8-sig"))
    ends = [
        datetime.fromisoformat(record["endDate"].replace("Z", "+00:00"))
        for record in records
        if isinstance(record, dict) and record.get("endDate")
    ]
    if not ends:
        raise ValueError(f"no endDate found in {markets_path}")
    return (min(ends) - WINDOW).astimezone(UTC).date(), max(ends).astimezone(UTC).date()


def download_day(symbol: str, day: date) -> bytes:
    url = BASE_URL.format(symbol=symbol, day=day.isoformat())
    print(f"Downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def write_day(archive: bytes, output: io.TextIOBase) -> int:
    rows = 0
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for name in zipped.namelist():
            with zipped.open(name) as member:
                for raw in io.TextIOWrapper(member, encoding="utf-8", newline=""):
                    if not raw[:1].isdigit():
                        continue
                    output.write(raw if raw.endswith("\n") else raw + "\n")
                    rows += 1
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--markets", type=Path, help="Polymarket markets JSON used for the day range"
    )
    parser.add_argument("--start-date", type=date.fromisoformat, help="First UTC day (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=date.fromisoformat, help="Last UTC day, inclusive")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    start, end = args.start_date, args.end_date
    if start is None or end is None:
        if args.markets is None:
            parser.error("--markets is required unless --start-date and --end-date are given")
        markets_start, markets_end = markets_day_range(args.markets)
        start = start or markets_start
        end = end or markets_end
    if end < start:
        parser.error("end date is before start date")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(args.output.name + ".part")
    total = 0
    with partial.open("w", encoding="utf-8", newline="") as output:
        day = start
        while day <= end:
            total += write_day(download_day(args.symbol, day), output)
            day += timedelta(days=1)
    partial.replace(args.output)
    print(f"Wrote {total} aggTrades ({start} to {end}) to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
