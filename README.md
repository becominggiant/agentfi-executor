# Agentfi-executor — ProfitPilot

ERC-8004 autonomous DeFi intelligence agent for cross-DEX arbitrage detection and on-chain profit exploration on Base.

## Architecture

```
agent_core.py          LangGraph ReAct agent with real Web3 tools
server.py              FastAPI A2A server (ERC-8004 compliant)
monitoring.py          Health checks: RPC, oracle freshness, wallet balance, gas
register_agent.py      One-time ERC-8004 registration on Base Sepolia
deploy_contract.py     Compile (py-solc-x) + deploy FlashArbitrage to Base Sepolia
contracts/
  FlashArbitrage.sol   Aave V3 flash-loan arbitrage contract (single flat file)
tools/
  web3_tools.py        get_wallet_balance, get_uniswap_v3_price,
                       get_aerodrome_price, get_chainlink_price
  arbitrage.py         detect_arbitrage_opportunity (cross-DEX scan)
  executor.py          execute_flash_arbitrage (Aave V3 flash-loan execution)
tests/
  test_tools.py        Unit tests (all RPC calls mocked)
```

**Profit strategy:** Cross-DEX price comparison between Uniswap V3 (Base, 0.05 % and 0.3 % pools) and Aerodrome volatile pools, cross-referenced against Chainlink oracles. Flags any spread > 0.15 % that yields positive net profit after estimated gas. Confirmed opportunities are executed via Aave V3 flash loans.

**Data sources (Base mainnet, read-only):**
- Uniswap V3 QuoterV2 — `0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a`
- Aerodrome Router — `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43`
- Chainlink ETH/USD — `0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70`

**Execution targets (Base Sepolia, writes):**
- Aave V3 Pool — `0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27`
- Aave V3 PoolAddressesProvider — `0xE4C23309117Aa30342BFaae6c95c6478e0A4Ad00`
- Uniswap V3 SwapRouter02 — `0x94cC0AaC535CCDB3C01d6787D6413C739ae12bc4`

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env: add OPENAI_API_KEY, PRIVATE_KEY (testnet only), RPC URLs

# 3. Run health check
python monitoring.py

# 4. Deploy the FlashArbitrage contract to Base Sepolia
python deploy_contract.py
# Prints the deployed address and writes FLASH_ARBITRAGE_CONTRACT to .env

# 5. Run the agent (single task)
python agent_core.py
# or pass a custom task:
python agent_core.py "Scan for WETH/USDC arbitrage using 0.5 WETH"

# 6. Start the A2A server
python server.py
# Server listens on http://0.0.0.0:8000
# POST /a2a   — send tasks
# GET  /health — health probe
# GET  /agent_card — ERC-8004 card

# 7. Register on Base Sepolia (one-time)
# a) Upload agent_card.json to IPFS, note the CID
# b) Set AGENT_URI=ipfs://<CID>/agent_card.json in .env
# c) python register_agent.py
```

## Running tests

```bash
pytest tests/ -v
```

## FlashArbitrage contract

`contracts/FlashArbitrage.sol` is a single flat file (no npm/forge required) that:

1. **Borrows** `asset` from Aave V3 via `flashLoanSimple`.
2. **Buys** `tokenOut` on the cheaper DEX (Uniswap V3 or Aerodrome).
3. **Sells** `tokenOut` back to `asset` on the more expensive DEX.
4. **Repays** the flash loan + 0.05 % Aave premium.
5. **Forwards** profit to the contract owner.

DEX type codes: `0` = Uniswap V3, `1` = Aerodrome.

Compile and deploy:
```bash
python deploy_contract.py
```
This installs `solc 0.8.20` via py-solc-x on first run, compiles with optimisation enabled, deploys to Base Sepolia, and writes the address to `.env`.

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
- Replace public RPC URLs with a private provider (Alchemy, Infura, QuickNode) before production use.
- The FlashArbitrage contract is `onlyOwner` gated; only the deploying wallet can trigger execution.

## Next steps toward mainnet

1. Host the A2A server (Railway, Fly.io, AWS) and update `agent_card.json` endpoint URL.
2. Upload `agent_card.json` to IPFS; run `register_agent.py`.
3. Add slippage simulation and liquidity-depth checks before executing.
4. Set up alerting (Telegram, PagerDuty, etc.) wired to `monitoring.check_system_health()`.
5. Audit the FlashArbitrage contract; run a full testnet simulation before any mainnet funds.
6. Switch `AAVE_ADDRESSES_PROVIDER` to the Base mainnet value and fund the contract with gas.

See official ERC-8004 spec: https://github.com/erc-8004/erc-8004-contracts

**Safety first — testnets only until the full pipeline is audited.**
