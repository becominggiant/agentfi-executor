"""
Deploy FlashArbitrage.sol to Base Sepolia.

Prerequisites:
  1. pip install py-solc-x  (already in requirements.txt)
  2. Set PRIVATE_KEY and RPC_URL in .env (testnet wallet only).
  3. Fund the wallet with a small amount of Base Sepolia ETH.

Usage:
    python deploy_contract.py

On success the script prints and writes the deployed contract address to
the local .env file under FLASH_ARBITRAGE_CONTRACT.

Aave V3 PoolAddressesProvider addresses used:
  Base Sepolia  — 0xE4C23309117Aa30342BFaae6c95c6478e0A4Ad00  (default)
  Base mainnet  — 0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
RPC_URL     = os.getenv("RPC_URL", "https://sepolia.base.org")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")

# Aave V3 PoolAddressesProvider — Base Sepolia default
ADDRESSES_PROVIDER = Web3.to_checksum_address(
    os.getenv(
        "AAVE_ADDRESSES_PROVIDER",
        "0xE4C23309117Aa30342BFaae6c95c6478e0A4Ad00",
    )
)

GAS_PRICE_MULTIPLIER = float(os.getenv("GAS_PRICE_MULTIPLIER", "1.5"))

# Path to the Solidity source
_CONTRACT_SRC = Path(__file__).parent / "contracts" / "FlashArbitrage.sol"


# ── Compilation ───────────────────────────────────────────────────────────────

def compile_contract() -> tuple[list, str]:
    """Compile FlashArbitrage.sol and return (abi, bytecode)."""
    try:
        from solcx import compile_source, install_solc, get_installed_solc_versions
    except ImportError as exc:
        logger.error("py-solc-x not installed. Run: pip install py-solc-x")
        raise SystemExit(1) from exc

    solc_version = "0.8.20"
    if solc_version not in [str(v) for v in get_installed_solc_versions()]:
        logger.info("Installing solc %s …", solc_version)
        install_solc(solc_version)

    source = _CONTRACT_SRC.read_text()
    logger.info("Compiling %s …", _CONTRACT_SRC.name)

    compiled = compile_source(
        source,
        output_values=["abi", "bin"],
        solc_version=solc_version,
        optimize=True,
        optimize_runs=200,
    )

    # compile_source keys look like "<stdin>:ContractName"
    contract_key = next(
        k for k in compiled if k.endswith(":FlashArbitrage")
    )
    abi      = compiled[contract_key]["abi"]
    bytecode = compiled[contract_key]["bin"]
    logger.info("Compilation successful. Bytecode size: %d bytes", len(bytecode) // 2)
    return abi, bytecode


# ── Deployment ────────────────────────────────────────────────────────────────

def deploy_contract(abi: list, bytecode: str) -> str:
    """Deploy the contract and return the deployed address."""
    if not PRIVATE_KEY:
        logger.error("PRIVATE_KEY not set. Add it to .env (testnet key only).")
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        logger.error("Cannot connect to RPC: %s", RPC_URL)
        sys.exit(1)

    account = w3.eth.account.from_key(PRIVATE_KEY)
    logger.info("Deploying from: %s", account.address)

    balance = w3.eth.get_balance(account.address)
    logger.info("Wallet balance: %.6f ETH", float(Web3.from_wei(balance, "ether")))
    if balance == 0:
        logger.error("Wallet has zero balance. Fund it with Base Sepolia ETH first.")
        sys.exit(1)

    factory = w3.eth.contract(abi=abi, bytecode=bytecode)

    gas_price = int(w3.eth.gas_price * GAS_PRICE_MULTIPLIER)
    logger.info("Gas price: %.4f gwei", float(Web3.from_wei(gas_price, "gwei")))

    # Estimate gas for deployment
    try:
        gas_estimate = factory.constructor(ADDRESSES_PROVIDER).estimate_gas(
            {"from": account.address}
        )
        gas_limit = int(gas_estimate * 1.2)  # 20 % buffer
    except Exception as exc:
        logger.warning("Gas estimation failed (%s) — using 2 000 000 fallback", exc)
        gas_limit = 2_000_000

    logger.info("Gas limit: %d", gas_limit)

    nonce = w3.eth.get_transaction_count(account.address)
    deploy_tx = factory.constructor(ADDRESSES_PROVIDER).build_transaction(
        {
            "from":     account.address,
            "nonce":    nonce,
            "gas":      gas_limit,
            "gasPrice": gas_price,
        }
    )

    signed_tx = w3.eth.account.sign_transaction(deploy_tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    logger.info("Deployment tx sent: %s", tx_hash.hex())
    logger.info("Waiting for confirmation …")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt["status"] != 1:
        logger.error("Deployment transaction reverted. Check RPC and gas settings.")
        sys.exit(1)

    contract_address = receipt["contractAddress"]
    logger.info("FlashArbitrage deployed at: %s", contract_address)
    logger.info(
        "Explorer: https://sepolia.basescan.org/address/%s", contract_address
    )
    return contract_address


def _write_env(address: str) -> None:
    """Append or update FLASH_ARBITRAGE_CONTRACT in .env."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        logger.warning(".env not found — skipping auto-write")
        return

    lines = env_path.read_text().splitlines()
    key = "FLASH_ARBITRAGE_CONTRACT"
    new_line = f"{key}={address}"
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = new_line
            updated = True
            break
    if not updated:
        lines.append(new_line)

    env_path.write_text("\n".join(lines) + "\n")
    logger.info("Written %s to .env", new_line)


def _save_abi(abi: list) -> None:
    """Save the ABI to contracts/FlashArbitrage.json for reference."""
    out = Path(__file__).parent / "contracts" / "FlashArbitrage.json"
    out.write_text(json.dumps({"abi": abi}, indent=2))
    logger.info("ABI saved to %s", out)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    abi, bytecode = compile_contract()
    _save_abi(abi)
    address = deploy_contract(abi, bytecode)
    _write_env(address)
    print(f"\nFlashArbitrage contract address: {address}")
    print("Set FLASH_ARBITRAGE_CONTRACT in your .env to use it with the agent.")


if __name__ == "__main__":
    main()
