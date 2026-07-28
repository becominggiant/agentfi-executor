// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ─────────────────────────────────────────────────────────────────────────────
// Minimal interfaces — all external dependencies inlined to keep this a single
// flat file that compiles without npm/forge tooling.
// ─────────────────────────────────────────────────────────────────────────────

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @dev Minimal Aave V3 PoolAddressesProvider interface.
interface IPoolAddressesProvider {
    function getPool() external view returns (address);
}

/// @dev Minimal Aave V3 Pool — only flashLoanSimple is needed.
interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes calldata params,
        uint16 referralCode
    ) external;
}

/// @dev Uniswap V3 SwapRouter02 — exactInputSingle (no deadline).
interface ISwapRouterV3 {
    struct ExactInputSingleParams {
        address tokenIn;
        address tokenOut;
        uint24  fee;
        address recipient;
        uint256 amountIn;
        uint256 amountOutMinimum;
        uint160 sqrtPriceLimitX96;
    }
    function exactInputSingle(ExactInputSingleParams calldata params)
        external returns (uint256 amountOut);
}

/// @dev Aerodrome / Velodrome-style router.
interface IAerodromeRouter {
    struct Route {
        address from;
        address to;
        bool    stable;
        address factory;
    }
    function swapExactTokensForTokens(
        uint256     amountIn,
        uint256     amountOutMin,
        Route[]     calldata routes,
        address     to,
        uint256     deadline
    ) external returns (uint256[] memory amounts);
}

// ─────────────────────────────────────────────────────────────────────────────
// FlashArbitrage
//
// Borrows `asset` from Aave V3, swaps through two DEXes to capture a spread,
// repays the loan with premium, and forwards profit to the owner.
//
// DEX type codes
//   0 = Uniswap V3  (ISwapRouterV3.exactInputSingle)
//   1 = Aerodrome   (IAerodromeRouter.swapExactTokensForTokens, volatile or stable)
// ─────────────────────────────────────────────────────────────────────────────
contract FlashArbitrage {

    // ── Immutables ────────────────────────────────────────────────────────────
    IPoolAddressesProvider public immutable ADDRESSES_PROVIDER;
    IPool                  public immutable POOL;

    // ── State ─────────────────────────────────────────────────────────────────
    address public owner;

    // ── Arbitrage parameter struct (ABI-encoded in the flash-loan callback) ──
    struct ArbParams {
        address tokenIn;        // token borrowed = token to sell on buy DEX
        address tokenOut;       // intermediate token
        address buyDex;         // DEX address to buy tokenOut (sell tokenIn)
        address sellDex;        // DEX address to sell tokenOut (buy tokenIn)
        uint8   buyDexType;     // 0 = UniV3, 1 = Aerodrome
        uint8   sellDexType;    // 0 = UniV3, 1 = Aerodrome
        uint24  buyFeeTier;     // UniV3 fee tier for buy leg  (0 if Aerodrome)
        uint24  sellFeeTier;    // UniV3 fee tier for sell leg (0 if Aerodrome)
        bool    buyStable;      // Aerodrome pool type for buy leg
        bool    sellStable;     // Aerodrome pool type for sell leg
        address aeroFactory;    // Aerodrome pool factory (used in route encoding)
        uint256 amountOutMin;   // min tokenOut from buy leg  (slippage protection)
        uint256 minProfit;      // min tokenIn profit after repayment (reverts if not met)
    }

    // ── Events ────────────────────────────────────────────────────────────────
    event ArbitrageExecuted(
        address indexed tokenIn,
        address indexed tokenOut,
        uint256 borrowed,
        uint256 repaid,
        uint256 profit
    );
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    // ── Errors ────────────────────────────────────────────────────────────────
    error NotOwner();
    error NotPool();
    error BadInitiator();
    error Unprofitable(uint256 received, uint256 required);
    error BelowMinProfit(uint256 profit, uint256 minimum);
    error InvalidDexType(uint8 dexType);
    error ZeroAddress();
    error ETHTransferFailed();

    // ── Modifiers ─────────────────────────────────────────────────────────────
    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    // ── Constructor ───────────────────────────────────────────────────────────
    /// @param addressesProvider Aave V3 PoolAddressesProvider for this network.
    constructor(address addressesProvider) {
        if (addressesProvider == address(0)) revert ZeroAddress();
        ADDRESSES_PROVIDER = IPoolAddressesProvider(addressesProvider);
        POOL               = IPool(IPoolAddressesProvider(addressesProvider).getPool());
        owner              = msg.sender;
    }

    // ── Admin ─────────────────────────────────────────────────────────────────
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    // ── Core: initiate a flash-loan arbitrage ─────────────────────────────────
    /// @notice Request an Aave V3 flash loan and execute cross-DEX arbitrage.
    /// @param asset   Token to borrow (the base token of the pair, e.g. USDC).
    /// @param amount  Amount to borrow in token's native units.
    /// @param params  Encoded ArbParams describing the two swap legs.
    function executeArbitrage(
        address    asset,
        uint256    amount,
        ArbParams calldata params
    ) external onlyOwner {
        bytes memory encoded = abi.encode(params);
        POOL.flashLoanSimple(address(this), asset, amount, encoded, 0);
    }

    // ── Aave V3 flash-loan callback ───────────────────────────────────────────
    /// @notice Called by the Aave Pool after transferring `amount` of `asset`.
    ///         Must approve `amount + premium` to POOL before returning.
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external returns (bool) {
        if (msg.sender   != address(POOL)) revert NotPool();
        if (initiator    != address(this)) revert BadInitiator();

        ArbParams memory p  = abi.decode(params, (ArbParams));
        uint256 repayAmount = amount + premium;

        // Leg 1: tokenIn → tokenOut on the cheap venue (buy leg)
        uint256 tokenOutReceived = _swap(
            p.buyDex,
            p.buyDexType,
            asset,          // tokenIn
            p.tokenOut,
            amount,
            p.amountOutMin,
            p.buyFeeTier,
            p.buyStable,
            p.aeroFactory
        );

        // Leg 2: tokenOut → tokenIn on the expensive venue (sell leg)
        uint256 tokenInReceived = _swap(
            p.sellDex,
            p.sellDexType,
            p.tokenOut,
            asset,          // tokenIn (back to borrowed token)
            tokenOutReceived,
            repayAmount,    // must cover repayment at minimum
            p.sellFeeTier,
            p.sellStable,
            p.aeroFactory
        );

        // Profitability checks
        if (tokenInReceived < repayAmount)
            revert Unprofitable(tokenInReceived, repayAmount);

        uint256 profit = tokenInReceived - repayAmount;
        if (profit < p.minProfit)
            revert BelowMinProfit(profit, p.minProfit);

        // Approve Aave Pool to pull repayment (amount + premium)
        IERC20(asset).approve(address(POOL), repayAmount);

        // Forward profit to owner
        if (profit > 0) {
            IERC20(asset).transfer(owner, profit);
        }

        emit ArbitrageExecuted(asset, p.tokenOut, amount, repayAmount, profit);
        return true;
    }

    // ── Internal swap dispatcher ──────────────────────────────────────────────
    function _swap(
        address dex,
        uint8   dexType,
        address tokenIn,
        address tokenOut,
        uint256 amountIn,
        uint256 amountOutMin,
        uint24  feeTier,
        bool    stable,
        address aeroFactory
    ) internal returns (uint256 amountOut) {
        IERC20(tokenIn).approve(dex, amountIn);

        if (dexType == 0) {
            // ── Uniswap V3 ───────────────────────────────────────────────────
            amountOut = ISwapRouterV3(dex).exactInputSingle(
                ISwapRouterV3.ExactInputSingleParams({
                    tokenIn:           tokenIn,
                    tokenOut:          tokenOut,
                    fee:               feeTier,
                    recipient:         address(this),
                    amountIn:          amountIn,
                    amountOutMinimum:  amountOutMin,
                    sqrtPriceLimitX96: 0
                })
            );
        } else if (dexType == 1) {
            // ── Aerodrome ─────────────────────────────────────────────────────
            IAerodromeRouter.Route[] memory routes = new IAerodromeRouter.Route[](1);
            routes[0] = IAerodromeRouter.Route({
                from:    tokenIn,
                to:      tokenOut,
                stable:  stable,
                factory: aeroFactory
            });
            uint256[] memory amounts = IAerodromeRouter(dex).swapExactTokensForTokens(
                amountIn,
                amountOutMin,
                routes,
                address(this),
                block.timestamp + 300
            );
            amountOut = amounts[amounts.length - 1];
        } else {
            revert InvalidDexType(dexType);
        }
    }

    // ── Emergency recovery ────────────────────────────────────────────────────
    /// @notice Withdraw any ERC-20 token stranded in this contract.
    function withdrawToken(address token, uint256 amount) external onlyOwner {
        IERC20(token).transfer(owner, amount);
    }

    /// @notice Withdraw any ETH stranded in this contract.
    function withdrawETH() external onlyOwner {
        (bool ok, ) = owner.call{value: address(this).balance}("");
        if (!ok) revert ETHTransferFailed();
    }

    receive() external payable {}
}
