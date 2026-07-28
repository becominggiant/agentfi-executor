"""
ProfitPilot agent core.

Uses LangGraph's prebuilt ReAct agent with real Web3 tools for on-chain
data analysis and cross-DEX arbitrage detection on Base.

Usage:
    python agent_core.py
    python agent_core.py "Your custom task here"
"""

import logging
import os
import sys

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from tools.arbitrage import detect_arbitrage_opportunity
from tools.executor import execute_flash_arbitrage
from tools.web3_tools import (
    get_aerodrome_price,
    get_chainlink_price,
    get_uniswap_v3_price,
    get_wallet_balance,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

if not os.getenv("OPENAI_API_KEY"):
    logger.error("OPENAI_API_KEY not set. Add it to .env or export it.")
    sys.exit(1)

SYSTEM_PROMPT = """You are ProfitPilot, an autonomous DeFi intelligence agent on Base.

Your mission: monitor on-chain prices, detect cross-DEX arbitrage opportunities,
and execute them via flash loans — safety and testnet-first operation always.

Available tools:
- get_wallet_balance           — check ETH and token balances for any address
- get_uniswap_v3_price         — live swap quote from Uniswap V3 on Base mainnet
- get_aerodrome_price          — live swap quote from Aerodrome on Base mainnet
- get_chainlink_price          — latest Chainlink oracle price on Base mainnet
- detect_arbitrage_opportunity — full cross-DEX scan (Uniswap V3 + Aerodrome)
- execute_flash_arbitrage      — execute arbitrage via Aave V3 flash loan (Base Sepolia)

Rules:
1. Use detect_arbitrage_opportunity for any broad market scan.
2. Always cross-reference DEX prices with Chainlink to validate data quality.
3. Flag stale oracle data (age > 3600 s) as unreliable.
4. Include spread_pct and net_profit_usd_estimate in every opportunity report.
5. Only call execute_flash_arbitrage after detect_arbitrage_opportunity confirms
   a profitable opportunity AND the user explicitly authorises execution.
6. Always remind the user that execute_flash_arbitrage targets Base Sepolia
   (testnet) — no real funds are at risk.
7. Never instruct the user to execute a mainnet trade without explicit confirmation.
"""

_llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0)
_tools = [
    get_wallet_balance,
    get_uniswap_v3_price,
    get_aerodrome_price,
    get_chainlink_price,
    detect_arbitrage_opportunity,
    execute_flash_arbitrage,
]

# Build the ReAct agent (tool-calling loop handled automatically by LangGraph)
agent = create_react_agent(_llm, _tools)


def run_agent(task: str) -> str:
    """Run the agent on a task string and return the final response text."""
    logger.info("Agent task: %.120s", task)
    result = agent.invoke(
        {
            "messages": [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=task),
            ]
        }
    )
    final_message = result["messages"][-1]
    return final_message.content


if __name__ == "__main__":
    default_task = (
        "Scan for arbitrage opportunities between Uniswap V3 and Aerodrome "
        "for the WETH/USDC pair using 1 WETH. Report the best opportunity found, "
        "including spread % and estimated net profit in USD."
    )
    task = sys.argv[1] if len(sys.argv) > 1 else default_task
    print(run_agent(task))
