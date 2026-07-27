"""
Health monitoring for ProfitPilot.

Checks performed:
  - RPC connectivity  (Base mainnet + Base Sepolia)
  - Chainlink oracle  freshness (ETH/USD feed)
  - Agent wallet      balance (alert below threshold)
  - Gas price         (alert when abnormally high)

Run standalone to print a JSON health report:
    python monitoring.py
"""

import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()
logger = logging.getLogger(__name__)

_MAINNET_RPC = os.getenv("BASE_MAINNET_RPC", "https://mainnet.base.org")
_TESTNET_RPC = os.getenv("RPC_URL", "https://sepolia.base.org")

# Alert thresholds (configurable via environment)
_ALERT_BALANCE_ETH = float(os.getenv("ALERT_BALANCE_ETH", "0.01"))
_ALERT_GAS_GWEI = float(os.getenv("ALERT_GAS_GWEI", "50"))

_CHAINLINK_ETH_USD = Web3.to_checksum_address(
    "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"
)
_CHAINLINK_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
]


# ── Individual checks ─────────────────────────────────────────────────────────

def _check_rpc(rpc_url: str, label: str) -> dict[str, Any]:
    """Verify RPC connectivity and retrieve the latest block number."""
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
    try:
        connected = w3.is_connected()
        block = w3.eth.block_number if connected else None
        return {"rpc": label, "connected": connected, "latest_block": block}
    except Exception as exc:
        logger.error("RPC check failed (%s): %s", label, exc)
        return {"rpc": label, "connected": False, "error": "RPC check failed"}


def _check_oracle() -> dict[str, Any]:
    """Check Chainlink ETH/USD feed price and data freshness."""
    w3 = Web3(Web3.HTTPProvider(_MAINNET_RPC, request_kwargs={"timeout": 5}))
    try:
        feed = w3.eth.contract(address=_CHAINLINK_ETH_USD, abi=_CHAINLINK_ABI)
        decimals = feed.functions.decimals().call()
        _, answer, _, updated_at, _ = feed.functions.latestRoundData().call()
        price = answer / (10**decimals)
        age = int(time.time()) - updated_at
        stale = age > 3600
        if stale:
            logger.warning("Chainlink ETH/USD is stale: %d s old", age)
        return {
            "oracle": "chainlink_eth_usd",
            "price_usd": round(price, 2),
            "age_seconds": age,
            "stale": stale,
        }
    except Exception as exc:
        logger.error("Oracle check failed: %s", exc)
        return {"oracle": "chainlink_eth_usd", "error": "Oracle check failed"}


def _check_wallet() -> dict[str, Any]:
    """Check the agent wallet's testnet ETH balance."""
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        return {"wallet": "not_configured"}

    w3 = Web3(Web3.HTTPProvider(_TESTNET_RPC, request_kwargs={"timeout": 5}))
    try:
        account = w3.eth.account.from_key(private_key)
        balance_wei = w3.eth.get_balance(account.address)
        balance_eth = float(Web3.from_wei(balance_wei, "ether"))
        low = balance_eth < _ALERT_BALANCE_ETH
        if low:
            logger.warning(
                "LOW WALLET BALANCE: %.6f ETH (threshold: %s ETH)",
                balance_eth,
                _ALERT_BALANCE_ETH,
            )
        return {
            "wallet": account.address[:10] + "...",
            "balance_eth": round(balance_eth, 6),
            "low_balance_alert": low,
        }
    except Exception as exc:
        logger.error("Wallet check failed: %s", exc)
        return {"wallet": "error", "error": "Wallet check failed"}


def _check_gas() -> dict[str, Any]:
    """Check current Base mainnet gas price."""
    w3 = Web3(Web3.HTTPProvider(_MAINNET_RPC, request_kwargs={"timeout": 5}))
    try:
        gas_wei = w3.eth.gas_price
        gas_gwei = float(Web3.from_wei(gas_wei, "gwei"))
        high = gas_gwei > _ALERT_GAS_GWEI
        if high:
            logger.warning(
                "HIGH GAS PRICE: %.4f gwei (threshold: %s gwei)",
                gas_gwei,
                _ALERT_GAS_GWEI,
            )
        return {"gas_price_gwei": round(gas_gwei, 4), "high_gas_alert": high}
    except Exception as exc:
        logger.error("Gas check failed: %s", exc)
        return {"gas": "error", "error": "Gas check failed"}


# ── Aggregated health check ───────────────────────────────────────────────────

def check_system_health() -> dict[str, Any]:
    """Run all health checks and return an aggregated status dict.

    The returned dict contains a ``healthy`` bool and an ``alerts`` list
    that is empty when everything is green.
    """
    checks: dict[str, Any] = {
        "mainnet_rpc": _check_rpc(_MAINNET_RPC, "base-mainnet"),
        "testnet_rpc": _check_rpc(_TESTNET_RPC, "base-sepolia"),
        "oracle": _check_oracle(),
        "wallet": _check_wallet(),
        "gas": _check_gas(),
        "timestamp": time.time(),
    }

    alerts: list[str] = []
    if not checks["mainnet_rpc"].get("connected"):
        alerts.append("Base mainnet RPC disconnected")
    if not checks["testnet_rpc"].get("connected"):
        alerts.append("Base Sepolia RPC disconnected")
    if checks["oracle"].get("stale"):
        alerts.append("Chainlink oracle data stale (>1 h)")
    if checks["wallet"].get("low_balance_alert"):
        alerts.append("Wallet balance below threshold")
    if checks["gas"].get("high_gas_alert"):
        alerts.append("Gas price above threshold")

    checks["healthy"] = len(alerts) == 0
    checks["alerts"] = alerts
    return checks


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)
    print(json.dumps(check_system_health(), indent=2))
