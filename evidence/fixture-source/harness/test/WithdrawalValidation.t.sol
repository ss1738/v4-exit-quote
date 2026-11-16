// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Deployers} from "@uniswap/v4-core/test/utils/Deployers.sol";
import {PoolManager} from "@uniswap/v4-core/src/PoolManager.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {BalanceDelta} from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {TickMath} from "@uniswap/v4-core/src/libraries/TickMath.sol";
import {LiquidityAmounts} from "@uniswap/v4-core/test/utils/LiquidityAmounts.sol";
import {PositionManager} from "v4-periphery/src/PositionManager.sol";
import {IPositionManager} from "v4-periphery/src/interfaces/IPositionManager.sol";
import {IPositionDescriptor} from "v4-periphery/src/interfaces/IPositionDescriptor.sol";
import {IWETH9} from "v4-periphery/src/interfaces/external/IWETH9.sol";
import {SlippageCheck} from "v4-periphery/src/libraries/SlippageCheck.sol";
import {LiquidityOperations} from "v4-periphery/test/shared/LiquidityOperations.sol";
import {PositionConfig} from "v4-periphery/test/shared/PositionConfig.sol";
import {FeeMath} from "v4-periphery/test/shared/FeeMath.sol";
import {DeployPermit2} from "permit2/test/utils/DeployPermit2.sol";
import {IAllowanceTransfer} from "permit2/src/interfaces/IAllowanceTransfer.sol";
import {IERC20} from "forge-std/interfaces/IERC20.sol";

/// @notice LOCAL integration, not a fork: real retrieved hook runtime, fresh mock-token pool.
contract WithdrawalValidationTest is Deployers, DeployPermit2, LiquidityOperations {
    using FeeMath for IPositionManager;
    address constant MAINNET_MANAGER = 0x000000000004444c5dc75cB358380D2e3dE08A90;
    address constant HALO = 0x6965238926a727a4ad6F5836CcA94741eFfFC541;
    PositionConfig config;
    uint256 tokenId;
    uint128 p0;
    uint128 p1;
    uint128 f0;
    uint128 f1;

    function deployFreshManager() internal override {
        deployCodeTo(
            "../v4-periphery/lib/v4-core/src/PoolManager.sol:PoolManager", abi.encode(address(this)), MAINNET_MANAGER
        );
        manager = IPoolManager(MAINNET_MANAGER);
    }

    function setUp() public virtual {
        vm.roll(1000);
        deployFreshManagerAndRouters();
        deployMintAndApprove2Currencies();
        vm.etch(HALO, vm.parseJsonBytes(vm.readFile("../halo-initial-rpc.json"), ".hook_code.result"));
        (key,) = initPool(currency0, currency1, IHooks(HALO), 3000, SQRT_PRICE_1_1);
        IAllowanceTransfer permit = IAllowanceTransfer(deployPermit2());
        lpm = IPositionManager(
            address(new PositionManager(manager, permit, 100_000, IPositionDescriptor(address(0)), IWETH9(address(0))))
        );
        IERC20(Currency.unwrap(currency0)).approve(address(permit), type(uint256).max);
        IERC20(Currency.unwrap(currency1)).approve(address(permit), type(uint256).max);
        permit.approve(Currency.unwrap(currency0), address(lpm), type(uint160).max, type(uint48).max);
        permit.approve(Currency.unwrap(currency1), address(lpm), type(uint160).max, type(uint48).max);
        config = PositionConfig({poolKey: key, tickLower: -120, tickUpper: 120});
        tokenId = lpm.nextTokenId();
        mint(config, 1e18, address(this), "");
        donateRouter.donate(key, 1e15, 1e15, "");
        (uint256 a0, uint256 a1) = LiquidityAmounts.getAmountsForLiquidity(
            SQRT_PRICE_1_1, TickMath.getSqrtPriceAtTick(-120), TickMath.getSqrtPriceAtTick(120), 1e18
        );
        p0 = uint128(a0);
        p1 = uint128(a1);
        BalanceDelta fees = lpm.getFeesOwed(manager, config, tokenId);
        f0 = uint128(fees.amount0());
        f1 = uint128(fees.amount1());
    }

    function test_naivePrincipalQuoteRevertsWithHalfPercentSlippage() public {
        uint128 m0 = uint128(uint256(p0) * 9950 / 10000);
        uint128 m1 = uint128(uint256(p1) * 9950 / 10000);
        bytes memory calls = getDecreaseEncoded(tokenId, config, 1e18, m0, m1, "");
        vm.expectRevert(abi.encodeWithSelector(SlippageCheck.MinimumAmountInsufficient.selector, m0, p0 - f0));
        lpm.modifyLiquidities(calls, _deadline);
    }

    function test_hookAwarePrincipalQuoteExecutesAndReturnsPrincipal() public {
        uint128 m0 = uint128(uint256(p0 - f0) * 9950 / 10000);
        uint128 m1 = uint128(uint256(p1 - f1) * 9950 / 10000);
        uint256 b0 = currency0.balanceOfSelf();
        uint256 b1 = currency1.balanceOfSelf();
        lpm.modifyLiquidities(getDecreaseEncoded(tokenId, config, 1e18, m0, m1, ""), _deadline);
        assertEq(currency0.balanceOfSelf() - b0, p0);
        assertEq(currency1.balanceOfSelf() - b1, p1);
        emit log_named_uint("gross principal token0", p0);
        emit log_named_uint("accrued fees token0", f0);
        emit log_named_uint("slippage-checked component token0", p0 - f0);
        emit log_named_uint("corrected minimum token0", m0);
        emit log_named_uint("actual wallet receipt token0", currency0.balanceOfSelf() - b0);
    }

    function test_maturePositionNeedsNoFeePenaltyAdjustment() public {
        vm.roll(block.number + 300);
        uint256 b0 = currency0.balanceOfSelf();
        uint256 b1 = currency1.balanceOfSelf();
        lpm.modifyLiquidities(
            getDecreaseEncoded(
                tokenId, config, 1e18, uint128(uint256(p0) * 9950 / 10000), uint128(uint256(p1) * 9950 / 10000), ""
            ),
            _deadline
        );
        assertEq(currency0.balanceOfSelf() - b0, uint256(p0) + f0);
        assertEq(currency1.balanceOfSelf() - b1, uint256(p1) + f1);
    }
}
