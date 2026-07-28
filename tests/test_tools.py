"""
Unit tests for ProfitPilot tools.

All Web3 / RPC calls are mocked — no live network required.
Run with:  pytest tests/
"""

import os
import time
from unittest.mock import MagicMock, patch

import pytest


# ── get_wallet_balance ────────────────────────────────────────────────────────

class TestGetWalletBalance:
    def test_invalid_address_returns_error(self):
        from tools.web3_tools import get_wallet_balance

        result = get_wallet_balance.invoke({"address": "not_an_address"})
        assert "error" in result

    def test_valid_address_returns_balances(self):
        from tools.web3_tools import get_wallet_balance, w3

        mock_contract = MagicMock()
        mock_contract.functions.balanceOf.return_value.call.return_value = 1_000_000
        mock_contract.functions.decimals.return_value.call.return_value = 6

        with patch.object(w3.eth, "get_balance", return_value=10**18), \
             patch.object(w3.eth, "contract", return_value=mock_contract):
            result = get_wallet_balance.invoke(
                {"address": "0x000000000000000000000000000000000000dEaD"}
            )

        assert "balances" in result
        assert result["balances"]["ETH"] == 1.0
        assert result["network"] == "base-mainnet"


# ── get_chainlink_price ───────────────────────────────────────────────────────

class TestGetChainlinkPrice:
    def test_returns_price(self):
        from tools.web3_tools import get_chainlink_price, w3

        mock_feed = MagicMock()
        mock_feed.functions.decimals.return_value.call.return_value = 8
        mock_feed.functions.latestRoundData.return_value.call.return_value = (
            1,
            350_000_000_000,  # $3 500.00 with 8 decimals
            0,
            int(time.time()),
            1,
        )

        with patch.object(w3.eth, "contract", return_value=mock_feed):
            result = get_chainlink_price.invoke({})

        assert "price_usd" in result
        assert result["price_usd"] == 3500.0
        assert result["stale"] is False

    def test_stale_data_flagged(self):
        from tools.web3_tools import get_chainlink_price, w3

        stale_ts = int(time.time()) - 7200  # 2 hours ago
        mock_feed = MagicMock()
        mock_feed.functions.decimals.return_value.call.return_value = 8
        mock_feed.functions.latestRoundData.return_value.call.return_value = (
            1, 350_000_000_000, 0, stale_ts, 1
        )

        with patch.object(w3.eth, "contract", return_value=mock_feed):
            result = get_chainlink_price.invoke({})

        assert result["stale"] is True

    def test_invalid_feed_address_returns_error(self):
        from tools.web3_tools import get_chainlink_price

        result = get_chainlink_price.invoke({"feed_address": "0xinvalid"})
        assert "error" in result


# ── get_uniswap_v3_price ──────────────────────────────────────────────────────

class TestGetUniswapV3Price:
    def test_returns_price(self):
        from tools.web3_tools import get_uniswap_v3_price, w3, WETH, USDC

        mock_token = MagicMock()
        mock_token.functions.decimals.return_value.call.side_effect = [18, 6]
        mock_quoter = MagicMock()
        # amountOut, sqrtPriceX96After, initializedTicksCrossed, gasEstimate
        mock_quoter.functions.quoteExactInputSingle.return_value.call.return_value = (
            3_500_000_000,
            0,
            0,
            120_000,
        )

        def _contract_factory(address, abi):
            if address in (WETH, USDC):
                return mock_token
            return mock_quoter

        with patch.object(w3.eth, "contract", side_effect=_contract_factory):
            result = get_uniswap_v3_price.invoke(
                {"token_in": WETH, "token_out": USDC, "amount_in_human": 1.0, "fee_tier": 500}
            )

        assert "effective_price" in result
        assert result["effective_price"] == pytest.approx(3500.0, rel=1e-3)
        assert result["dex"] == "uniswap_v3"

    def test_invalid_token_returns_error(self):
        from tools.web3_tools import get_uniswap_v3_price, USDC

        result = get_uniswap_v3_price.invoke(
            {"token_in": "bad", "token_out": USDC, "amount_in_human": 1.0}
        )
        assert "error" in result


# ── get_aerodrome_price ───────────────────────────────────────────────────────

class TestGetAerodromePrice:
    def test_returns_price(self):
        from tools.web3_tools import get_aerodrome_price, w3, WETH, USDC

        mock_token = MagicMock()
        mock_token.functions.decimals.return_value.call.side_effect = [18, 6]
        mock_router = MagicMock()
        mock_router.functions.getAmountsOut.return_value.call.return_value = [
            10**18,
            3_490_000_000,
        ]

        def _contract_factory(address, abi):
            from tools.web3_tools import AERODROME_ROUTER
            if address == AERODROME_ROUTER:
                return mock_router
            return mock_token

        with patch.object(w3.eth, "contract", side_effect=_contract_factory):
            result = get_aerodrome_price.invoke(
                {"token_in": WETH, "token_out": USDC, "amount_in_human": 1.0}
            )

        assert "effective_price" in result
        assert result["dex"] == "aerodrome"
        assert result["pool_type"] == "volatile"


# ── detect_arbitrage_opportunity ──────────────────────────────────────────────

class TestDetectArbitrageOpportunity:
    def _mock_prices(self, univ3_price: float, aero_price: float, cl_price: float):
        """Build mock return values for the three internal price functions."""
        return (
            {
                "dex": "uniswap_v3",
                "effective_price": univ3_price,
                "amount_in": 1.0,
                "amount_out": univ3_price,
                "gas_estimate": 120_000,
            },
            {
                "dex": "aerodrome",
                "effective_price": aero_price,
                "amount_in": 1.0,
                "amount_out": aero_price,
            },
            {
                "source": "chainlink",
                "price_usd": cl_price,
                "age_seconds": 30,
                "stale": False,
            },
        )

    def test_opportunity_detected_when_spread_sufficient(self):
        from tools.arbitrage import detect_arbitrage_opportunity
        from tools.web3_tools import WETH, USDC

        univ3, aero, cl = self._mock_prices(3520.0, 3500.0, 3510.0)

        with patch("tools.arbitrage._get_uniswap_v3_price") as mu3, \
             patch("tools.arbitrage._get_aerodrome_price", return_value=aero), \
             patch("tools.arbitrage._get_chainlink_price", return_value=cl):
            mu3.side_effect = [univ3, Exception("no 0.3% pool")]

            result = detect_arbitrage_opportunity.invoke(
                {"token_in": WETH, "token_out": USDC, "amount_in_human": 1.0}
            )

        assert result["opportunity"] is True
        assert result["spread_pct"] > 0.15
        assert result["net_profit_usd_estimate"] > 0
        assert "warning" in result

    def test_no_opportunity_when_spread_tiny(self):
        from tools.arbitrage import detect_arbitrage_opportunity
        from tools.web3_tools import WETH, USDC

        univ3, aero, cl = self._mock_prices(3500.02, 3500.0, 3500.0)

        with patch("tools.arbitrage._get_uniswap_v3_price") as mu3, \
             patch("tools.arbitrage._get_aerodrome_price", return_value=aero), \
             patch("tools.arbitrage._get_chainlink_price", return_value=cl):
            mu3.side_effect = [univ3, Exception("no 0.3% pool")]

            result = detect_arbitrage_opportunity.invoke(
                {"token_in": WETH, "token_out": USDC, "amount_in_human": 1.0}
            )

        assert result["opportunity"] is False

    def test_insufficient_data_handled(self):
        from tools.arbitrage import detect_arbitrage_opportunity
        from tools.web3_tools import WETH, USDC

        with patch("tools.arbitrage._get_uniswap_v3_price", side_effect=Exception("rpc down")), \
             patch("tools.arbitrage._get_aerodrome_price", side_effect=Exception("rpc down")), \
             patch("tools.arbitrage._get_chainlink_price", side_effect=Exception("rpc down")):
            result = detect_arbitrage_opportunity.invoke(
                {"token_in": WETH, "token_out": USDC, "amount_in_human": 1.0}
            )

        assert result["opportunity"] is False
        assert "reason" in result or "error" in result


# ── monitoring ────────────────────────────────────────────────────────────────

class TestMonitoring:
    def test_healthy_system_returns_no_alerts(self):
        from monitoring import check_system_health

        healthy_rpc = {"rpc": "test", "connected": True, "latest_block": 1_000_000}
        healthy_oracle = {"oracle": "chainlink_eth_usd", "price_usd": 3500.0, "age_seconds": 30, "stale": False}
        healthy_wallet = {"wallet": "0x1234...", "balance_eth": 1.0, "low_balance_alert": False}
        healthy_gas = {"gas_price_gwei": 0.001, "high_gas_alert": False}

        with patch("monitoring._check_rpc", return_value=healthy_rpc), \
             patch("monitoring._check_oracle", return_value=healthy_oracle), \
             patch("monitoring._check_wallet", return_value=healthy_wallet), \
             patch("monitoring._check_gas", return_value=healthy_gas):
            result = check_system_health()

        assert result["healthy"] is True
        assert result["alerts"] == []

    def test_rpc_failure_produces_alert(self):
        from monitoring import check_system_health

        down_rpc = {"rpc": "base-mainnet", "connected": False}
        healthy_rpc = {"rpc": "base-sepolia", "connected": True, "latest_block": 1}
        healthy_oracle = {"oracle": "chainlink_eth_usd", "stale": False}
        healthy_wallet = {"wallet": "not_configured"}
        healthy_gas = {"gas_price_gwei": 0.001, "high_gas_alert": False}

        with patch("monitoring._check_rpc") as mock_rpc, \
             patch("monitoring._check_oracle", return_value=healthy_oracle), \
             patch("monitoring._check_wallet", return_value=healthy_wallet), \
             patch("monitoring._check_gas", return_value=healthy_gas):
            mock_rpc.side_effect = [down_rpc, healthy_rpc]
            result = check_system_health()

        assert result["healthy"] is False
        assert any("mainnet" in a.lower() for a in result["alerts"])

    def test_stale_oracle_produces_alert(self):
        from monitoring import check_system_health

        healthy_rpc = {"rpc": "test", "connected": True, "latest_block": 1}
        stale_oracle = {"oracle": "chainlink_eth_usd", "stale": True}
        healthy_wallet = {"wallet": "not_configured"}
        healthy_gas = {"gas_price_gwei": 0.001, "high_gas_alert": False}

        with patch("monitoring._check_rpc", return_value=healthy_rpc), \
             patch("monitoring._check_oracle", return_value=stale_oracle), \
             patch("monitoring._check_wallet", return_value=healthy_wallet), \
             patch("monitoring._check_gas", return_value=healthy_gas):
            result = check_system_health()

        assert result["healthy"] is False
        assert any("stale" in a.lower() for a in result["alerts"])


# ── execute_flash_arbitrage ───────────────────────────────────────────────────

class TestExecuteFlashArbitrage:
    """All blockchain calls are mocked — no live testnet required."""

    _WETH = "0x4200000000000000000000000000000000000006"
    _USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    _CONTRACT = "0xDeAdBeEf00000000000000000000000000000001"

    def _env(self):
        return {
            "PRIVATE_KEY": "0x" + "a" * 64,
            "FLASH_ARBITRAGE_CONTRACT": self._CONTRACT,
        }

    def test_missing_private_key_returns_error(self):
        from tools.executor import execute_flash_arbitrage

        with patch.dict("os.environ", {}, clear=True):
            # remove PRIVATE_KEY and FLASH_ARBITRAGE_CONTRACT
            import os
            os.environ.pop("PRIVATE_KEY", None)
            os.environ.pop("FLASH_ARBITRAGE_CONTRACT", None)
            result = execute_flash_arbitrage.invoke(
                {
                    "token_in": self._USDC,
                    "token_out": self._WETH,
                    "amount_in_human": 500.0,
                    "buy_venue": "uniswap_v3_500",
                    "sell_venue": "uniswap_v3_3000",
                    "expected_token_out_human": 0.143,
                    "min_profit_usd": 0.50,
                    "chainlink_price_usd": 3500.0,
                }
            )
        assert "error" in result

    def test_missing_contract_address_returns_error(self):
        from tools.executor import execute_flash_arbitrage

        env = {"PRIVATE_KEY": "0x" + "a" * 64}
        with patch.dict("os.environ", env):
            import os
            os.environ.pop("FLASH_ARBITRAGE_CONTRACT", None)
            result = execute_flash_arbitrage.invoke(
                {
                    "token_in": self._USDC,
                    "token_out": self._WETH,
                    "amount_in_human": 500.0,
                    "buy_venue": "uniswap_v3_500",
                    "sell_venue": "uniswap_v3_3000",
                    "expected_token_out_human": 0.143,
                    "min_profit_usd": 0.50,
                    "chainlink_price_usd": 3500.0,
                }
            )
        assert "error" in result
        assert "FLASH_ARBITRAGE_CONTRACT" in result["error"]

    def test_unknown_venue_returns_error(self):
        from tools.executor import execute_flash_arbitrage

        with patch.dict("os.environ", self._env()):
            result = execute_flash_arbitrage.invoke(
                {
                    "token_in": self._USDC,
                    "token_out": self._WETH,
                    "amount_in_human": 500.0,
                    "buy_venue": "unknown_dex",
                    "sell_venue": "uniswap_v3_500",
                    "expected_token_out_human": 0.143,
                    "min_profit_usd": 0.50,
                    "chainlink_price_usd": 3500.0,
                }
            )
        assert "error" in result
        assert "unknown_dex" in result["error"]

    def test_successful_execution(self):
        from tools.executor import execute_flash_arbitrage

        mock_contract_fn = MagicMock()
        mock_contract_fn.estimate_gas.return_value = 350_000
        mock_contract_fn.build_transaction.return_value = {
            "to": self._CONTRACT,
            "data": "0x",
            "gas": 420_000,
            "gasPrice": 1_000_000_000,
            "nonce": 0,
            "value": 0,
            "chainId": 84532,
        }

        mock_contract = MagicMock()
        mock_contract.functions.executeArbitrage.return_value = mock_contract_fn
        mock_contract.events.ArbitrageExecuted.return_value.process_receipt.return_value = [
            {"args": {"profit": 100_000, "borrowed": 500_000_000}}
        ]

        mock_receipt = MagicMock()
        mock_receipt.__getitem__ = lambda s, k: {
            "status": 1, "blockNumber": 12345, "gasUsed": 380_000
        }[k]

        tx_hash_bytes = bytes.fromhex("abcd" * 16)

        mock_w3 = MagicMock()
        mock_w3.eth.gas_price = 1_000_000_000
        mock_w3.eth.get_transaction_count.return_value = 0
        mock_w3.eth.contract.return_value = mock_contract
        mock_w3.eth.account.sign_transaction.return_value = MagicMock(
            raw_transaction=b"\x00" * 64
        )
        mock_w3.eth.send_raw_transaction.return_value = tx_hash_bytes
        mock_w3.eth.wait_for_transaction_receipt.return_value = mock_receipt

        with patch.dict("os.environ", self._env()), \
             patch("tools.executor.w3_test", mock_w3):
            result = execute_flash_arbitrage.invoke(
                {
                    "token_in": self._USDC,
                    "token_out": self._WETH,
                    "amount_in_human": 500.0,
                    "buy_venue": "uniswap_v3_500",
                    "sell_venue": "aerodrome_volatile",
                    "expected_token_out_human": 0.143,
                    "min_profit_usd": 0.05,
                    "chainlink_price_usd": 3500.0,
                }
            )

        assert result.get("status") == "success"
        assert "tx_hash" in result
        assert "explorer" in result
        assert "warning" in result

