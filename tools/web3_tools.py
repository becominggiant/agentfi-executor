"""
Real Web3 tools for the ProfitPilot agent.

Reads from Base mainnet (read-only, no keys required) for live market data.
All write operations target Base Sepolia (testnet) only.
"""

import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
from langchain_core.tools import tool
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from web3 import Web3
from web3.exceptions import ContractLogicError

load_dotenv()
logger = logging.getLogger(__name__)

# ── RPC connections ─────────────────────────────────────────────────────────────
_MAINNET_RPC = os.getenv("BASE_MAINNET_RPC", "https://mainnet.base.org")
_TESTNET_RPC = os.getenv("RPC_URL", "https://sepolia.base.org")

# w3 targets Base mainnet for read-only market data (no private key needed)
w3 = Web3(Web3.HTTPProvider(_MAINNET_RPC))
# w3_test targets Base Sepolia for any write operations
w3_test = Web3(Web3.HTTPProvider(_TESTNET_RPC))

# ── Well-known token addresses on Base mainnet ─────────────────────────────────
WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
CBBTC = Web3.to_checksum_address("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf")

TOKEN_SYMBOLS: dict[str, str] = {WETH: "WETH", USDC: "USDC", CBBTC: "cbBTC"}

# ── Contract addresses (Base mainnet) ─────────────────────────────────────────
UNISWAP_V3_QUOTER = Web3.to_checksum_address(
    "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
)
AERODROME_ROUTER = Web3.to_checksum_address(
    "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"
)
AERODROME_FACTORY = Web3.to_checksum_address(
    "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"
)
CHAINLINK_ETH_USD = Web3.to_checksum_address(
    "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"
)

# ── Minimal ABIs ───────────────────────────────────────────────────────────────
_ERC20_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
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
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
]

_UNIV3_QUOTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "tokenIn", "type": "address"},
                    {"name": "tokenOut", "type": "address"},
                    {"name": "amountIn", "type": "uint256"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "sqrtPriceX96After", "type": "uint160"},
            {"name": "initializedTicksCrossed", "type": "uint32"},
            {"name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

_AERODROME_ROUTER_ABI = [
    {
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {
                "components": [
                    {"name": "from", "type": "address"},
                    {"name": "to", "type": "address"},
                    {"name": "stable", "type": "bool"},
                    {"name": "factory", "type": "address"},
                ],
                "name": "routes",
                "type": "tuple[]",
            },
        ],
        "name": "getAmountsOut",
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
        "stateMutability": "view",
        "type": "function",
    }
]

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


# ── Retry decorator for flaky RPC calls ───────────────────────────────────────
def _with_rpc_retry(fn):
    """Wrap a callable with exponential-backoff retry logic (3 attempts)."""
    return retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )(fn)


# ── Internal implementations (called by both tools and arbitrage module) ──────

def _get_wallet_balance(address: str) -> dict[str, Any]:
    addr = Web3.to_checksum_address(address)

    @_with_rpc_retry
    def _fetch() -> dict[str, Any]:
        eth_wei = w3.eth.get_balance(addr)
        eth_bal = float(Web3.from_wei(eth_wei, "ether"))
        balances: dict[str, float] = {"ETH": round(eth_bal, 6)}
        for token_addr, symbol in TOKEN_SYMBOLS.items():
            contract = w3.eth.contract(address=token_addr, abi=_ERC20_ABI)
            raw = contract.functions.balanceOf(addr).call()
            decimals = contract.functions.decimals().call()
            balances[symbol] = round(raw / (10**decimals), 6)
        return {"address": addr, "balances": balances, "network": "base-mainnet"}

    return _fetch()


def _get_uniswap_v3_price(
    token_in: str,
    token_out: str,
    amount_in_human: float,
    fee_tier: int = 500,
) -> dict[str, Any]:
    t_in = Web3.to_checksum_address(token_in)
    t_out = Web3.to_checksum_address(token_out)

    @_with_rpc_retry
    def _fetch() -> dict[str, Any]:
        in_contract = w3.eth.contract(address=t_in, abi=_ERC20_ABI)
        out_contract = w3.eth.contract(address=t_out, abi=_ERC20_ABI)
        in_decimals = in_contract.functions.decimals().call()
        out_decimals = out_contract.functions.decimals().call()
        amount_in_raw = int(amount_in_human * 10**in_decimals)

        quoter = w3.eth.contract(address=UNISWAP_V3_QUOTER, abi=_UNIV3_QUOTER_ABI)
        result = quoter.functions.quoteExactInputSingle(
            {
                "tokenIn": t_in,
                "tokenOut": t_out,
                "amountIn": amount_in_raw,
                "fee": fee_tier,
                "sqrtPriceLimitX96": 0,
            }
        ).call()

        amount_out_raw = result[0]
        gas_estimate = result[3]
        amount_out_human = amount_out_raw / (10**out_decimals)
        in_sym = TOKEN_SYMBOLS.get(t_in, t_in[:10])
        out_sym = TOKEN_SYMBOLS.get(t_out, t_out[:10])

        return {
            "dex": "uniswap_v3",
            "pair": f"{in_sym}/{out_sym}",
            "fee_tier": f"{fee_tier / 10000:.2f}%",
            "amount_in": amount_in_human,
            "amount_out": round(amount_out_human, 6),
            "effective_price": round(amount_out_human / amount_in_human, 6),
            "gas_estimate": gas_estimate,
        }

    return _fetch()


def _get_aerodrome_price(
    token_in: str,
    token_out: str,
    amount_in_human: float,
    stable: bool = False,
) -> dict[str, Any]:
    t_in = Web3.to_checksum_address(token_in)
    t_out = Web3.to_checksum_address(token_out)

    @_with_rpc_retry
    def _fetch() -> dict[str, Any]:
        in_contract = w3.eth.contract(address=t_in, abi=_ERC20_ABI)
        out_contract = w3.eth.contract(address=t_out, abi=_ERC20_ABI)
        in_decimals = in_contract.functions.decimals().call()
        out_decimals = out_contract.functions.decimals().call()
        amount_in_raw = int(amount_in_human * 10**in_decimals)

        router = w3.eth.contract(address=AERODROME_ROUTER, abi=_AERODROME_ROUTER_ABI)
        routes = [
            {"from": t_in, "to": t_out, "stable": stable, "factory": AERODROME_FACTORY}
        ]
        amounts = router.functions.getAmountsOut(amount_in_raw, routes).call()
        amount_out_raw = amounts[-1]
        amount_out_human = amount_out_raw / (10**out_decimals)
        in_sym = TOKEN_SYMBOLS.get(t_in, t_in[:10])
        out_sym = TOKEN_SYMBOLS.get(t_out, t_out[:10])

        return {
            "dex": "aerodrome",
            "pair": f"{in_sym}/{out_sym}",
            "pool_type": "stable" if stable else "volatile",
            "amount_in": amount_in_human,
            "amount_out": round(amount_out_human, 6),
            "effective_price": round(amount_out_human / amount_in_human, 6),
        }

    return _fetch()


def _get_chainlink_price(feed_address: str = CHAINLINK_ETH_USD) -> dict[str, Any]:
    feed_addr = Web3.to_checksum_address(feed_address)

    @_with_rpc_retry
    def _fetch() -> dict[str, Any]:
        feed = w3.eth.contract(address=feed_addr, abi=_CHAINLINK_ABI)
        decimals = feed.functions.decimals().call()
        _, answer, _, updated_at, _ = feed.functions.latestRoundData().call()
        price = answer / (10**decimals)
        age_seconds = int(time.time()) - updated_at
        return {
            "source": "chainlink",
            "feed": feed_addr,
            "price_usd": round(price, 4),
            "decimals": decimals,
            "age_seconds": age_seconds,
            "stale": age_seconds > 3600,
        }

    return _fetch()


# ── LangChain @tool wrappers ──────────────────────────────────────────────────

@tool
def get_wallet_balance(address: str) -> dict:
    """Get ETH and token balances for a wallet address on Base mainnet.

    Returns ETH balance and balances for WETH, USDC, and cbBTC.
    The address must be a valid Ethereum address (checksummed or lowercase).
    """
    try:
        return _get_wallet_balance(address)
    except ValueError as exc:
        return {"error": f"Invalid address: {exc}"}
    except Exception as exc:
        logger.error("get_wallet_balance failed: %s", exc)
        return {"error": str(exc)}


@tool
def get_uniswap_v3_price(
    token_in: str,
    token_out: str,
    amount_in_human: float,
    fee_tier: int = 500,
) -> dict:
    """Get a swap price quote from Uniswap V3 on Base mainnet.

    Args:
        token_in: Address of the token to sell (e.g. WETH 0x4200...0006).
        token_out: Address of the token to buy (e.g. USDC 0x8335...2913).
        amount_in_human: Human-readable sell amount (e.g. 1.0 for 1 WETH).
        fee_tier: Pool fee in hundredths of a bip.
                  500 = 0.05 %, 3000 = 0.3 %, 10000 = 1 %.

    Returns a dict with amount_out and effective price, or an error key.
    """
    try:
        return _get_uniswap_v3_price(token_in, token_out, amount_in_human, fee_tier)
    except ValueError as exc:
        return {"error": str(exc)}
    except ContractLogicError as exc:
        return {"error": f"Pool may not exist for fee_tier={fee_tier}: {exc}"}
    except Exception as exc:
        logger.error("get_uniswap_v3_price failed: %s", exc)
        return {"error": str(exc)}


@tool
def get_aerodrome_price(
    token_in: str,
    token_out: str,
    amount_in_human: float,
    stable: bool = False,
) -> dict:
    """Get a swap price quote from Aerodrome Finance on Base mainnet.

    Aerodrome is the leading AMM on Base, offering both volatile (Uniswap-V2-style)
    and stable-swap pools.

    Args:
        token_in: Address of the token to sell.
        token_out: Address of the token to buy.
        amount_in_human: Human-readable sell amount.
        stable: True for stableswap pools (pegged pairs); False for volatile pools.

    Returns a dict with amount_out and effective price, or an error key.
    """
    try:
        return _get_aerodrome_price(token_in, token_out, amount_in_human, stable)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("get_aerodrome_price failed: %s", exc)
        return {"error": str(exc)}


@tool
def get_chainlink_price(feed_address: str = CHAINLINK_ETH_USD) -> dict:
    """Get the latest price from a Chainlink aggregator on Base mainnet.

    Args:
        feed_address: Chainlink aggregator contract address.
            ETH/USD: 0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70 (default)
            BTC/USD: 0xCCADC697c55bbB68dc5bCdf8d3CBe83CdD4E071E

    Returns price_usd, data age in seconds, and a staleness flag (>1 h = stale).
    """
    try:
        return _get_chainlink_price(feed_address)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        logger.error("get_chainlink_price failed: %s", exc)
        return {"error": str(exc)}
