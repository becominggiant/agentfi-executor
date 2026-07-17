# Agentfi-executor

ERC-8004 AI Agent starter for on-chain profit exploration and execution.

## Setup
1. `cp .env.example .env` and fill PRIVATE_KEY + RPC_URL
2. `pip install -r requirements.txt`
3. Upload agent_card.json to IPFS/Filecoin, update AGENT_URI
4. `python register_agent.py`
5. `python agent_core.py`

Extend with real tools (trading, payments), host as server, build reputation, tokenize via Virtuals, etc.

See official: https://github.com/erc-8004/erc-8004-contracts

Safety first - testnets only!