"""Authenticated execution module for Polymarket CLOB.

This module handles the placement of real orders using L2 API Credentials
and L1 EIP-712 signatures. Unlike the rest of this package, it performs
write operations (order placement) and must be used with care.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Literal

try:
    from py_clob_client_v2.client import ClobClient
    from py_clob_client_v2.clob_types import ApiCreds, MarketOrderArgsV2, OrderArgsV2, OrderType
except ImportError:  # pragma: no cover - optional runtime dependency
    ClobClient = None  # type: ignore

LOGGER = logging.getLogger(__name__)

Side = Literal["BUY", "SELL"]

# Polymarket BTC 5-min UP/DOWN markets trade at a 0.01 tick size.
DEFAULT_TICK_SIZE = 0.01


def _floor_to_tick(price: float, tick_size: float = DEFAULT_TICK_SIZE) -> float:
    """Floor `price` to the nearest tradable tick.

    py_clob_client_v2's own market-price auto-calculation can return a price
    with more decimal precision than the tick size allows (e.g.
    0.5600044800358403), which the CLOB rejects with a 400 "breaks minimum
    tick size rule" error. Pre-rounding here avoids that server round-trip.
    """
    ticks = math.floor(price / tick_size + 1e-9)
    return round(ticks * tick_size, 2)


def _read_env_value(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def load_api_credentials(filepath: Path) -> dict[str, str]:
    """Load credentials from the custom `api` file format or JSON."""
    creds = {}
    if not filepath.exists():
        return creds

    content = filepath.read_text(encoding="utf-8").strip()
    if content.startswith("{"):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    for i in range(0, len(lines) - 1, 2):
        key = lines[i].replace(":", "").strip()
        val = lines[i + 1].strip()
        creds[key] = val
    return creds


class PolymarketExecutor:
    """Places live orders on the Polymarket CLOB using py_clob_client_v2."""

    def __init__(
        self,
        api_file_path: Path | None = None,
        host: str = "https://clob.polymarket.com",
        chain_id: int = 137,  # 137 is Polygon Mainnet
    ) -> None:
        if ClobClient is None:
            raise ImportError("The 'py_clob_client_v2' package is required for live execution.")

        creds_data = {}
        if api_file_path and api_file_path.exists():
            creds_data = load_api_credentials(api_file_path)

        api_key = (
            _read_env_value(
                "POLYMARKET_API_KEY",
                "POLYMARKET_KEY",
                "API_KEY",
            )
            or creds_data.get("API Key")
            or creds_data.get("API key")
            or creds_data.get("api key")
        )
        api_secret = (
            _read_env_value(
                "POLYMARKET_API_SECRET",
                "POLYMARKET_SECRET",
                "API_SECRET",
            )
            or creds_data.get("API Secret")
            or creds_data.get("API secret")
            or creds_data.get("api secret")
        )
        passphrase = (
            _read_env_value(
                "POLYMARKET_PASSPHRASE",
                "POLYMARKET_API_PASSPHRASE",
                "PASSPHRASE",
            )
            or creds_data.get("API Passphrase")
            or creds_data.get("Passphrase")
            or creds_data.get("api passphrase")
            or creds_data.get("passphrase")
        )
        private_key = (
            _read_env_value(
                "POLYMARKET_PRIVATE_KEY",
                "PRIVATE_KEY",
            )
            or creds_data.get("Private Key")
            or creds_data.get("Private key")
            or creds_data.get("private key")
        )
        address = (
            _read_env_value(
                "POLYMARKET_ADDRESS",
                "ADDRESS",
            )
            or creds_data.get("Address")
            or creds_data.get("address")
        )

        if not private_key or not address:
            LOGGER.warning(
                "polymarket_executor_missing_l1",
                extra={"detail": "Private Key and Address are required."},
            )

        self.client = ClobClient(
            host,
            chain_id=chain_id,
            key=private_key,
            funder=address,
            signature_type=3,
            creds=ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=passphrase,
            )
            if (api_key and api_secret and passphrase)
            else None,
        )

        self.creds_valid = self.client.creds is not None
        if self.creds_valid:
            LOGGER.info("polymarket_executor_initialized", extra={"mode": "py_clob_client_v2"})
        else:
            LOGGER.warning(
                "polymarket_executor_missing_credentials", extra={"detail": "API L2 keys missing."}
            )

    def place_order(
        self,
        token_id: str,
        price: float,
        quantity: float,
        side: Side,
        order_type: Any = None,
    ) -> dict:
        """Place an order directly via the SDK."""
        if not self.creds_valid:
            raise ValueError("Cannot place order: missing API Keys or Private Key.")

        LOGGER.info(
            "placing_live_order",
            extra={
                "token_id": token_id,
                "price": price,
                "quantity": quantity,
                "side": side,
                "order_type": order_type,
            },
        )

        try:
            if order_type == "FOK" or order_type == getattr(OrderType, "FOK", "FOK"):
                order_args = MarketOrderArgsV2(
                    amount=quantity,
                    side=side,
                    token_id=token_id,
                )
                market_price = self.client.calculate_market_price(
                    token_id, side, quantity, getattr(OrderType, "FOK", OrderType.FOK)
                )
                order_args.price = _floor_to_tick(market_price)
                signed_order = self.client.create_market_order(order_args)
                response = self.client.post_order(signed_order)
            else:
                order_args = OrderArgsV2(
                    price=price,
                    size=quantity,
                    side=side,
                    token_id=token_id,
                )
                signed_order = self.client.create_order(order_args)
                # Polymarket SDK V2 requires passing order_type to post_order for limit orders
                response = self.client.post_order(
                    signed_order, order_type=getattr(OrderType, "GTC", OrderType.GTC)
                )

            LOGGER.info("live_order_placed", extra={"response": response})
            return response
        except Exception as exc:
            LOGGER.error("live_order_failed", extra={"error": str(exc)}, exc_info=True)
            raise
