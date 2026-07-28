"""
Flash-loan arbitrage execution tool for ProfitPilot.

Submits an Aave V3 flash-loan arbitrage transaction on Base Sepolia using the
deployed FlashArbitrage contract.

Required environment variables:
  PRIVATE_KEY                — testnet wallet private key
  FLASH_ARBITRAGE_CONTRACT   — address of the deployed FlashArbitrage contract
  RPC_URL                    — Base Sepolia RPC endpoint (default: https://sepolia.base.org)

Optional environment variables (override default DEX addresses):
  UNIV3_SWAP_ROUTER          — Uniswap V3 SwapRouter02 on the target network
  AERODROME_ROUTER_EXEC      — Aerodrome Router on the target network
  AERODROME_FACTORY_EXEC     — Aerodrome Factory on the target network
"""

import logging
import os
from typing import Any

from dotenv import load_dotenv
from langchain_core.tools import tool
from web3 import Web3
from web3.exceptions import ContractLogicError

load_dotenv()
logger = logging.getLogger(__name__)

# ── RPC (testnet — all writes go to Base Sepolia) ─────────────────────────────
_TESTNET_RPC = os.getenv("RPC_URL", "https://sepolia.base.org")
w3_test = Web3(Web3.HTTPProvider(_TESTNET_RPC))

# ── DEX type constants (must match Solidity) ──────────────────────────────────
_DEX_TYPE_UNIV3     = 0
_DEX_TYPE_AERODROME = 1

# ── DEX router / factory addresses ───────────────────────────────────────────
#   Base Sepolia SwapRouter02 (Uniswap V3)
UNIV3_SWAP_ROUTER = Web3.to_checksum_address(
    os.getenv("UNIV3_SWAP_ROUTER", "0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4")
)
#   Aerodrome on Base mainnet (for mainnet execution)
AERODROME_ROUTER_EXEC = Web3.to_checksum_address(
    os.getenv("AERODROME_ROUTER_EXEC", "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43")
)
AERODROME_FACTORY_EXEC = Web3.to_checksum_address(
    os.getenv("AERODROME_FACTORY_EXEC", "0x420DD381b31aEf6683db6B902084cB0FFECe40Da")
)

# ── Venue name → (dex_address, dex_type, fee_tier, stable) ───────────────────
_VENUE_CONFIG: dict[str, tuple[str, int, int, bool]] = {
    "uniswap_v3_500":    (UNIV3_SWAP_ROUTER,    _DEX_TYPE_UNIV3,     500,  False),
    "uniswap_v3_3000":   (UNIV3_SWAP_ROUTER,    _DEX_TYPE_UNIV3,     3000, False),
    "aerodrome_volatile": (AERODROME_ROUTER_EXEC, _DEX_TYPE_AERODROME, 0,    False),
    "aerodrome_stable":   (AERODROME_ROUTER_EXEC, _DEX_TYPE_AERODROME, 0,    True),
}

# ── Known token decimals (avoids extra RPC calls for common tokens) ───────────
_TOKEN_DECIMALS: dict[str, int] = {
    Web3.to_checksum_address("0x4200000000000000000000000000000000000006"): 18,  # WETH
    Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"): 6,   # USDC
    Web3.to_checksum_address("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"): 8,   # cbBTC
}

_ERC20_DECIMALS_ABI = [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# ── FlashArbitrage contract ABI (minimal — only what the tool needs) ──────────
_FLASH_ARB_ABI = [
    {
        "inputs": [
            {"name": "asset",  "type": "address"},
            {"name": "amount", "type": "uint256"},
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {"name": "tokenIn",      "type": "address"},
                    {"name": "tokenOut",     "type": "address"},
                    {"name": "buyDex",       "type": "address"},
                    {"name": "sellDex",      "type": "address"},
                    {"name": "buyDexType",   "type": "uint8"},
                    {"name": "sellDexType",  "type": "uint8"},
                    {"name": "buyFeeTier",   "type": "uint24"},
                    {"name": "sellFeeTier",  "type": "uint24"},
                    {"name": "buyStable",    "type": "bool"},
                    {"name": "sellStable",   "type": "bool"},
                    {"name": "aeroFactory",  "type": "address"},
                    {"name": "amountOutMin", "type": "uint256"},
                    {"name": "minProfit",    "type": "uint256"},
                ],
            },
        ],
        "name": "executeArbitrage",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "token",  "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "withdrawToken",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "name": "tokenIn",  "type": "address"},
            {"indexed": True,  "name": "tokenOut", "type": "address"},
            {"indexed": False, "name": "borrowed", "type": "uint256"},
            {"indexed": False, "name": "repaid",   "type": "uint256"},
            {"indexed": False, "name": "profit",   "type": "uint256"},
        ],
        "name": "ArbitrageExecuted",
        "type": "event",
    },
]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_decimals(token_address: str) -> int:
    """Return token decimals, using the cache before hitting the chain."""
    addr = Web3.to_checksum_address(token_address)
    if addr in _TOKEN_DECIMALS:
        return _TOKEN_DECIMALS[addr]
    contract = w3_test.eth.contract(address=addr, abi=_ERC20_DECIMALS_ABI)
    dec = contract.functions.decimals().call()
    _TOKEN_DECIMALS[addr] = dec
    return dec


def _slippage_floor(amount_raw: int, slippage_bps: int = 50) -> int:
    """Apply slippage tolerance and return the minimum acceptable output."""
    return int(amount_raw * (10_000 - slippage_bps) // 10_000)


def _execute_flash_arbitrage(
    token_in: str,
    token_out: str,
    amount_in_human: float,
    buy_venue: str,
    sell_venue: str,
    expected_token_out_human: float,
    min_profit_usd: float,
    chainlink_price_usd: float,
    slippage_bps: int = 50,
) -> dict[str, Any]:
    """Core execution logic — called by the @tool wrapper."""
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        return {"error": "PRIVATE_KEY not set in environment (testnet key required)"}

    contract_address = os.getenv("FLASH_ARBITRAGE_CONTRACT")
    if not contract_address:
        return {
            "error": (
                "FLASH_ARBITRAGE_CONTRACT not set. "
                "Run deploy_contract.py first, then add the address to .env."
            )
        }

    buy_cfg  = _VENUE_CONFIG.get(buy_venue.lower())
    sell_cfg = _VENUE_CONFIG.get(sell_venue.lower())
    if buy_cfg is None:
        return {"error": f"Unknown buy_venue '{buy_venue}'. Valid: {list(_VENUE_CONFIG)}"}
    if sell_cfg is None:
        return {"error": f"Unknown sell_venue '{sell_venue}'. Valid: {list(_VENUE_CONFIG)}"}

    buy_dex,  buy_type,  buy_fee,  buy_stable  = buy_cfg
    sell_dex, sell_type, sell_fee, sell_stable = sell_cfg

    in_addr  = Web3.to_checksum_address(token_in)
    out_addr = Web3.to_checksum_address(token_out)

    in_decimals  = _get_decimals(in_addr)
    out_decimals = _get_decimals(out_addr)

    amount_in_raw       = int(amount_in_human * 10**in_decimals)
    expected_out_raw    = int(expected_token_out_human * 10**out_decimals)
    amount_out_min_raw  = _slippage_floor(expected_out_raw, slippage_bps)

    # Minimum profit in tokenIn units (convert min_profit_usd via oracle price)
    # chainlink_price_usd is the price of tokenIn in USD
    # min_profit_in_token = min_profit_usd / chainlink_price_usd
    if chainlink_price_usd > 0:
        min_profit_raw = int((min_profit_usd / chainlink_price_usd) * 10**in_decimals)
    else:
        min_profit_raw = 0

    arb_params = (
        in_addr,            # tokenIn
        out_addr,           # tokenOut
        Web3.to_checksum_address(buy_dex),   # buyDex
        Web3.to_checksum_address(sell_dex),  # sellDex
        buy_type,           # buyDexType
        sell_type,          # sellDexType
        buy_fee,            # buyFeeTier
        sell_fee,           # sellFeeTier
        buy_stable,         # buyStable
        sell_stable,        # sellStable
        AERODROME_FACTORY_EXEC,  # aeroFactory
        amount_out_min_raw, # amountOutMin
        min_profit_raw,     # minProfit
    )

    account = w3_test.eth.account.from_key(private_key)
    contract = w3_test.eth.contract(
        address=Web3.to_checksum_address(contract_address),
        abi=_FLASH_ARB_ABI,
    )

    gas_multiplier = float(os.getenv("GAS_PRICE_MULTIPLIER", "1.5"))
    gas_price = int(w3_test.eth.gas_price * gas_multiplier)

    try:
        gas_estimate = contract.functions.executeArbitrage(
            in_addr, amount_in_raw, arb_params
        ).estimate_gas({"from": account.address})
        gas_limit = int(gas_estimate * 1.2)
    except ContractLogicError as exc:
        return {"error": f"Simulation reverted (likely unprofitable): {exc}"}
    except Exception as exc:
        logger.warning("Gas estimation failed (%s) — using 500 000 fallback", exc)
        gas_limit = 500_000

    nonce = w3_test.eth.get_transaction_count(account.address)
    tx = contract.functions.executeArbitrage(
        in_addr, amount_in_raw, arb_params
    ).build_transaction(
        {
            "from":     account.address,
            "nonce":    nonce,
            "gas":      gas_limit,
            "gasPrice": gas_price,
        }
    )

    signed  = w3_test.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3_test.eth.send_raw_transaction(signed.raw_transaction)
    logger.info("Flash arbitrage tx sent: %s", tx_hash.hex())

    receipt = w3_test.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        return {
            "error":   "Transaction reverted on-chain",
            "tx_hash": tx_hash.hex(),
            "receipt": dict(receipt),
        }

    # Parse ArbitrageExecuted event
    profit_raw  = 0
    borrowed_raw = 0
    try:
        logs = contract.events.ArbitrageExecuted().process_receipt(receipt)
        if logs:
            ev         = logs[0]["args"]
            profit_raw = ev["profit"]
            borrowed_raw = ev["borrowed"]
    except Exception as exc:
        logger.warning("Event parse failed: %s", exc)

    profit_human = profit_raw / (10**in_decimals)

    return {
        "status":         "success",
        "tx_hash":        tx_hash.hex(),
        "block":          receipt["blockNumber"],
        "gas_used":       receipt["gasUsed"],
        "borrowed":       amount_in_human,
        "profit_token":   round(profit_human, 8),
        "profit_usd_estimate": round(profit_human * chainlink_price_usd, 4),
        "buy_venue":      buy_venue,
        "sell_venue":     sell_venue,
        "explorer":       f"https://sepolia.basescan.org/tx/{tx_hash.hex()}",
        "warning":        "Testnet execution only — real funds not at risk.",
    }


# ── LangChain @tool wrapper ───────────────────────────────────────────────────

@tool
def execute_flash_arbitrage(
    token_in: str,
    token_out: str,
    amount_in_human: float,
    buy_venue: str,
    sell_venue: str,
    expected_token_out_human: float,
    min_profit_usd: float,
    chainlink_price_usd: float,
    slippage_bps: int = 50,
) -> dict:
    """Execute a cross-DEX flash-loan arbitrage on Base Sepolia (testnet only).

    Borrows `amount_in_human` of `token_in` from Aave V3 via a flash loan,
    swaps through two DEXes to capture the price spread, repays the loan, and
    forwards profit to the owner wallet.

    IMPORTANT:
    - Only call this after detect_arbitrage_opportunity confirms a profitable
      spread with net_profit_usd_estimate > min_profit_usd.
    - Requires FLASH_ARBITRAGE_CONTRACT and PRIVATE_KEY in the environment.
    - This submits a real transaction on Base Sepolia (testnet, not mainnet).

    Args:
        token_in: Address of the token to borrow (e.g. USDC).
        token_out: Address of the intermediate token (e.g. WETH).
        amount_in_human: Amount to borrow in human-readable units (e.g. 500.0).
        buy_venue: Venue to buy token_out on (the cheaper side).
                   One of: uniswap_v3_500, uniswap_v3_3000,
                           aerodrome_volatile, aerodrome_stable.
        sell_venue: Venue to sell token_out on (the more expensive side).
                    Same choices as buy_venue.
        expected_token_out_human: Expected token_out amount from the buy leg
                                  (used to set slippage floor). Obtain from
                                  get_uniswap_v3_price or get_aerodrome_price.
        min_profit_usd: Minimum acceptable profit in USD. Transaction reverts
                        if profit falls below this after gas.
        chainlink_price_usd: Current Chainlink oracle price of token_in in USD.
                             Used to convert min_profit_usd to token units.
        slippage_bps: Slippage tolerance in basis points for the buy leg
                      (default 50 = 0.5 %).

    Returns a dict with tx_hash, profit, and explorer link, or an error key.
    """
    try:
        return _execute_flash_arbitrage(
            token_in=token_in,
            token_out=token_out,
            amount_in_human=amount_in_human,
            buy_venue=buy_venue,
            sell_venue=sell_venue,
            expected_token_out_human=expected_token_out_human,
            min_profit_usd=min_profit_usd,
            chainlink_price_usd=chainlink_price_usd,
            slippage_bps=slippage_bps,
        )
    except Exception as exc:
        logger.error("execute_flash_arbitrage failed: %s", exc)
        return {"error": str(exc)}
