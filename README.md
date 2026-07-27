# Agentfi-executor — ProfitPilot

ERC-8004 autonomous DeFi intelligence agent for cross-DEX arbitrage detection and on-chain profit exploration on Base.

## Architecture

```
agent_core.py          LangGraph ReAct agent with real Web3 tools
server.py              FastAPI A2A server (ERC-8004 compliant)
monitoring.py          Health checks: RPC, oracle freshness, wallet balance, gas
register_agent.py      One-time ERC-8004 registration on Base Sepolia
tools/
  web3_tools.py        get_wallet_balance, get_uniswap_v3_price,
                       get_aerodrome_price, get_chainlink_price
  arbitrage.py         detect_arbitrage_opportunity (cross-DEX scan)
tests/
  test_tools.py        Unit tests (all RPC calls mocked)
```

**Profit strategy:** Cross-DEX price comparison between Uniswap V3 (Base, 0.05 % and 0.3 % pools) and Aerodrome volatile pools, cross-referenced against Chainlink oracles. Flags any spread > 0.15 % that yields positive net profit after estimated gas.

**Data sources (Base mainnet, read-only):**
- Uniswap V3 QuoterV2 — `0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a`
- Aerodrome Router — `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43`
- Chainlink ETH/USD — `0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70`

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env: add OPENAI_API_KEY, PRIVATE_KEY (testnet only), RPC URLs

# 3. Run health check
python monitoring.py

# 4. Run the agent (single task)
python agent_core.py
# or pass a custom task:
python agent_core.py "Scan for WETH/USDC arbitrage using 0.5 WETH"

# 5. Start the A2A server
python server.py
# Server listens on http://0.0.0.0:8000
# POST /a2a   — send tasks
# GET  /health — health probe
# GET  /agent_card — ERC-8004 card

# 6. Register on Base Sepolia (one-time)
# a) Upload agent_card.json to IPFS, note the CID
# b) Set AGENT_URI=ipfs://<CID>/agent_card.json in .env
# c) python register_agent.py
```

## Running tests

```bash
pytest tests/ -v
```

## A2A API

**POST /a2a**
```json
{
  "task_id": "optional-uuid",
  "message": "Scan for WETH/USDC arbitrage. Report spread and estimated profit."
}
```
Returns:
```json
{
  "task_id": "optional-uuid",
  "status": "success",
  "response": "...",
  "timestamp": 1720000000.0
}
```

## Security notes

- `PRIVATE_KEY` is for the testnet wallet **only**. Never use a mainnet key with real funds.
- All market data reads are from Base mainnet via public RPCs (no key required).
- All on-chain writes target Base Sepolia exclusively.
- Replace public RPC URLs with a private provider (Alchemy, Infra, QuickNode) before production use.

## Next steps toward mainnet

1. Host the A2A server (Railway, Fly.io, AWS) and update `agent_card.json` endpoint URL.
2. Upload `agent_card.json` to IPFS; run `register_agent.py`.
3. Implement execution path: atomic arbitrage via a flash-loan contract or pre-funded wallet.
4. Add slippage simulation and liquidity-depth checks before flagging opportunities.
5. Set up alerting (Telegram, PagerDuty, etc.) wired to `monitoring.check_system_health()`.
6. Audit the execution contract; run a full testnet simulation before any mainnet funds.

See official ERC-8004 spec: https://github.com/erc-8004/erc-8004-contracts

**Safety first — testnets only until the full pipeline is audited.**
