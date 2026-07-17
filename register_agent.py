import os
import json
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

# Config - UPDATE THESE
RPC_URL = os.getenv("RPC_URL", "https://sepolia.base.org")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # NEVER commit this!
AGENT_URI = "ipfs://YOUR_CID_HERE/agent_card.json"  # Replace after uploading JSON to IPFS/Filecoin

# Identity Registry (Base Sepolia example - confirm latest!)
IDENTITY_REGISTRY = "0x7177a6867296406881E20d6647232314736Dd09A"

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    print("Failed to connect to RPC")
    exit(1)

account = w3.eth.account.from_key(PRIVATE_KEY)
print(f"Using account: {account.address}")

# Minimal ABI for register function (expand from official repo ABIs)
abi = [
    {
        "inputs": [{"internalType": "string", "name": "agentURI", "type": "string"}],
        "name": "register",
        "outputs": [{"internalType": "uint256", "name": "agentId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function"
    }
]

contract = w3.eth.contract(address=IDENTITY_REGISTRY, abi=abi)

# Build tx
nonce = w3.eth.get_transaction_count(account.address)
tx = contract.functions.register(AGENT_URI).build_transaction({
    'from': account.address,
    'nonce': nonce,
    'gas': 300000,
    'gasPrice': w3.to_wei('1', 'gwei')
})

signed_tx = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
print(f"Registration tx sent: {tx_hash.hex()}")

receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
print("Success! Receipt:", receipt)
print("Check 8004scan.io or explorer for your agentId")