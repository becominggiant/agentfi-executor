"""
Register ProfitPilot on Base Sepolia via the ERC-8004 Identity Registry.

Prerequisites:
  1. Upload agent_card.json to IPFS/Filecoin and obtain a CID.
  2. Set AGENT_URI=ipfs://<CID>/agent_card.json in .env
  3. Ensure PRIVATE_KEY and RPC_URL are set in .env (testnet wallet only).
  4. Fund the testnet wallet with a small amount of Sepolia ETH.

Then run:
    python register_agent.py
"""

import os
import sys
import logging

from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
RPC_URL = os.getenv("RPC_URL", "https://sepolia.base.org")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
AGENT_URI = os.getenv("AGENT_URI", "ipfs://YOUR_CID_HERE/agent_card.json")

# Identity Registry on Base Sepolia — confirm address at https://github.com/erc-8004/erc-8004-contracts
IDENTITY_REGISTRY = Web3.to_checksum_address(
    "0x7177a6867296406881E20d6647232314736Dd09A"
)

REGISTRY_ABI = [
    {
        "inputs": [{"internalType": "string", "name": "agentURI", "type": "string"}],
        "name": "register",
        "outputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def main() -> None:
    # Validate required env vars
    if not PRIVATE_KEY:
        logger.error("PRIVATE_KEY not set. Add it to .env (testnet key only).")
        sys.exit(1)

    if "YOUR_CID_HERE" in AGENT_URI:
        logger.error(
            "AGENT_URI still contains placeholder. "
            "Upload agent_card.json to IPFS and set AGENT_URI in .env."
        )
        sys.exit(1)

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        logger.error("Failed to connect to RPC: %s", RPC_URL)
        sys.exit(1)

    account = w3.eth.account.from_key(PRIVATE_KEY)
    logger.info("Using account: %s", account.address)

    balance = w3.eth.get_balance(account.address)
    logger.info("Account balance: %.6f ETH", float(Web3.from_wei(balance, "ether")))
    if balance == 0:
        logger.error("Account has zero balance. Fund it with testnet ETH first.")
        sys.exit(1)

    contract = w3.eth.contract(address=IDENTITY_REGISTRY, abi=REGISTRY_ABI)

    # Use dynamic gas pricing with configurable multiplier for inclusion confidence
    gas_multiplier = float(os.getenv("GAS_PRICE_MULTIPLIER", "1.5"))
    gas_price = int(w3.eth.gas_price * gas_multiplier)
    logger.info("Gas price: %.4f gwei", float(Web3.from_wei(gas_price, "gwei")))

    nonce = w3.eth.get_transaction_count(account.address)
    tx = contract.functions.register(AGENT_URI).build_transaction(
        {
            "from": account.address,
            "nonce": nonce,
            "gas": 300_000,
            "gasPrice": gas_price,
        }
    )

    signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    logger.info("Registration tx sent: %s", tx_hash.hex())

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] == 1:
        logger.info("Registration successful! Receipt: %s", receipt.transactionHash.hex())
        logger.info("View your agent at https://8004scan.io or the Base Sepolia explorer.")
    else:
        logger.error("Transaction reverted. Check contract address and ABI.")
        sys.exit(1)


if __name__ == "__main__":
    main()
