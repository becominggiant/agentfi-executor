"""
Cross-DEX arbitrage detection for Base mainnet.

Compares Uniswap V3 (multiple fee tiers) vs Aerodrome prices for a given pair
and reports any spread that exceeds the estimated gas cost — i.e. a net-positive
opportunity worth investigating.

All detected opportunities are simulation-only.  No trades are submitted.
"""

import logging
from typing import Any

from langchain_core.tools import tool

from tools.web3_tools import (
    AERODROME_FACTORY,
    CHAINLINK_ETH_USD,
    USDC,
    WETH,
    _get_aerodrome_price,
    _get_chainlink_price,
    _get_uniswap_v3_price,
)

logger = logging.getLogger(__name__)

# Minimum spread in percentage points to flag an opportunity.
# e.g. 0.15 means the best venue's price must be at least 0.15% higher than
# the worst venue before the spread is considered worth reporting.
# 0.15 % comfortably covers typical gas costs on Base L2 (~$0.02–$0.10 per swap).
MIN_SPREAD_PCT = 0.15

# Conservative gas cost estimate in USD for two swaps (buy + sell)
GAS_COST_USD = 0.10


def _detect_arbitrage(
    token_in: str = WETH,
    token_out: str = USDC,
    amount_in_human: float = 1.0,
) -> dict[str, Any]:
    """Core arbitrage scan — called by both the @tool wrapper and tests."""
    quotes: dict[str, float] = {}
    raw: dict[str, Any] = {}

    # --- Uniswap V3 fee tier 0.05 % (most liquid WETH/USDC pool on Base) -----
    try:
        q = _get_uniswap_v3_price(token_in, token_out, amount_in_human, fee_tier=500)
        raw["uniswap_v3_500"] = q
        if "effective_price" in q:
            quotes["uniswap_v3_500"] = q["effective_price"]
    except Exception as exc:
        raw["uniswap_v3_500"] = {"error": str(exc)}

    # --- Uniswap V3 fee tier 0.3 % -------------------------------------------
    try:
        q = _get_uniswap_v3_price(token_in, token_out, amount_in_human, fee_tier=3000)
        raw["uniswap_v3_3000"] = q
        if "effective_price" in q:
            quotes["uniswap_v3_3000"] = q["effective_price"]
    except Exception as exc:
        raw["uniswap_v3_3000"] = {"error": str(exc)}

    # --- Aerodrome volatile pool -----------------------------------------------
    try:
        q = _get_aerodrome_price(token_in, token_out, amount_in_human, stable=False)
        raw["aerodrome_volatile"] = q
        if "effective_price" in q:
            quotes["aerodrome_volatile"] = q["effective_price"]
    except Exception as exc:
        raw["aerodrome_volatile"] = {"error": str(exc)}

    # --- Chainlink reference price (oracle truth) ------------------------------
    try:
        cl = _get_chainlink_price(CHAINLINK_ETH_USD)
        raw["chainlink"] = cl
        if "price_usd" in cl:
            quotes["chainlink"] = cl["price_usd"]
    except Exception as exc:
        raw["chainlink"] = {"error": str(exc)}

    dex_quotes = {k: v for k, v in quotes.items() if k != "chainlink"}

    if len(dex_quotes) < 2:
        return {
            "opportunity": False,
            "reason": "Need at least 2 DEX quotes — check RPC connectivity",
            "raw": raw,
        }

    best_venue = max(dex_quotes, key=lambda k: dex_quotes[k])
    worst_venue = min(dex_quotes, key=lambda k: dex_quotes[k])
    best_price = dex_quotes[best_venue]
    worst_price = dex_quotes[worst_venue]

    spread_pct = (best_price - worst_price) / worst_price * 100
    gross_profit_usd = (best_price - worst_price) * amount_in_human
    net_profit_usd = gross_profit_usd - GAS_COST_USD
    opportunity = spread_pct >= MIN_SPREAD_PCT and net_profit_usd > 0

    return {
        "opportunity": opportunity,
        "spread_pct": round(spread_pct, 4),
        "gross_profit_usd": round(gross_profit_usd, 4),
        "net_profit_usd_estimate": round(net_profit_usd, 4),
        "best_venue": best_venue,
        "best_price": round(best_price, 4),
        "worst_venue": worst_venue,
        "worst_price": round(worst_price, 4),
        "chainlink_reference_usd": quotes.get("chainlink"),
        "action": (
            f"BUY on {worst_venue} at {worst_price:.4f} → "
            f"SELL on {best_venue} at {best_price:.4f}"
            if opportunity
            else "No profitable arbitrage detected at current prices"
        ),
        "warning": (
            "SIMULATION ONLY — verify slippage, liquidity depth, and "
            "execution risk before attempting any real trade"
        ),
        "raw": raw,
    }


@tool
def detect_arbitrage_opportunity(
    token_in: str = WETH,
    token_out: str = USDC,
    amount_in_human: float = 1.0,
) -> dict:
    """Scan Uniswap V3 and Aerodrome for cross-DEX price discrepancies on Base.

    Compares quotes for the same swap across Uniswap V3 (0.05 % and 0.3 % fee
    tiers) and Aerodrome volatile pools, then cross-references a Chainlink oracle.
    Reports any spread that exceeds the minimum profitable threshold after gas.

    Args:
        token_in: Address of the token to sell (default: WETH).
        token_out: Address of the token to buy (default: USDC).
        amount_in_human: Human-readable sell amount (default: 1.0 WETH).

    Returns a detailed dict including spread %, estimated net profit (USD),
    the best/worst venues, and a recommended action — all as simulation only.
    """
    try:
        return _detect_arbitrage(token_in, token_out, amount_in_human)
    except Exception as exc:
        logger.error("detect_arbitrage_opportunity failed: %s", exc)
        return {"error": str(exc), "opportunity": False}
