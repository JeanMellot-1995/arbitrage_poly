"""Command-line entry point for placing a single manual order on Polymarket.

Defaults to a dry run (no network call, no funds at risk). Pass ``--yes`` to
actually submit the order via ``PolymarketExecutor.place_order``.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from arbitrage_poly.polymarket.execution import PolymarketExecutor

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Place a single manual order on the Polymarket CLOB"
    )
    parser.add_argument("--token-id", required=True, help="CLOB token id of the outcome to trade")
    parser.add_argument(
        "--price", type=float, required=True, help="Limit price (ignored for FOK market orders)"
    )
    parser.add_argument("--quantity", type=float, required=True, help="Order size")
    parser.add_argument("--side", choices=["BUY", "SELL"], required=True)
    parser.add_argument("--order-type", choices=["GTC", "FOK"], default="GTC")
    parser.add_argument(
        "--creds-file",
        type=Path,
        default=Path("creds.json"),
        help="Path to the API credentials file (default: ./creds.json)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually submit the order. Without this flag, only a dry run is printed.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    print(
        f"Order: side={args.side} token_id={args.token_id} price={args.price} "
        f"quantity={args.quantity} order_type={args.order_type}"
    )

    if not args.yes:
        print("Dry run only (no order sent). Re-run with --yes to submit for real.")
        return

    executor = PolymarketExecutor(api_file_path=args.creds_file)
    response = executor.place_order(
        token_id=args.token_id,
        price=args.price,
        quantity=args.quantity,
        side=args.side,
        order_type=args.order_type,
    )
    print(f"Response: {response}")


if __name__ == "__main__":
    main()
