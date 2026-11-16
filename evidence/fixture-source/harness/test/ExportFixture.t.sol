// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {WithdrawalValidationTest} from "./WithdrawalValidation.t.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {PositionConfig} from "v4-periphery/test/shared/PositionConfig.sol";

interface TransferNFT {
    function transferFrom(address from, address to, uint256 tokenId) external;
}

/// @notice Export a controlled local pool for testing the real JSON-RPC CLI.
contract ExportFixtureTest is WithdrawalValidationTest {
    function setUp() public override {}

    function test_export() public {
        super.setUp();
        address eoa = address(0x1111222233334444555566667777888899990000);
        TransferNFT(address(lpm)).transferFrom(address(this), eoa, tokenId);
        (PoolKey memory vanilla,) = initPool(currency0, currency1, IHooks(address(0)), 3000, SQRT_PRICE_1_1);
        uint256 vanillaId = lpm.nextTokenId();
        mint(PositionConfig({poolKey: vanilla, tickLower: -120, tickUpper: 120}), 1e18, address(this), "");
        donateRouter.donate(vanilla, 1e15, 1e15, "");
        TransferNFT(address(lpm)).transferFrom(address(this), eoa, vanillaId);
        (PoolKey memory nativePool,) = initPool(Currency.wrap(address(0)), currency1, IHooks(address(0)), 3000, SQRT_PRICE_1_1);
        uint256 nativeId = lpm.nextTokenId();
        vm.deal(address(this), 100 ether);
        mintWithNative(SQRT_PRICE_1_1, PositionConfig({poolKey: nativePool, tickLower: -120, tickUpper: 120}), 1e18, address(this), "");
        TransferNFT(address(lpm)).transferFrom(address(this), eoa, nativeId);
        vm.deal(eoa, 100 ether);
        vm.createDir("fixture", true);
        vm.dumpState("fixture/state.json");
        vm.serializeAddress("fixture", "owner", eoa);
        vm.serializeAddress("fixture", "position_manager", address(lpm));
        vm.serializeAddress("fixture", "pool_manager", MAINNET_MANAGER);
        vm.serializeAddress("fixture", "currency0", Currency.unwrap(currency0));
        vm.serializeAddress("fixture", "currency1", Currency.unwrap(currency1));
        vm.serializeUint("fixture", "token_id", tokenId);
        vm.serializeUint("fixture", "vanilla_token_id", vanillaId);
        vm.serializeUint("fixture", "native_token_id", nativeId);
        vm.serializeUint("fixture", "block_number", block.number);
        vm.serializeUint("fixture", "timestamp", block.timestamp);
        string memory json = vm.serializeUint("fixture", "liquidity", 1e18);
        vm.writeJson(json, "fixture/context.json");
    }
}
