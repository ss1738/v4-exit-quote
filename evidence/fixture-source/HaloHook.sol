// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IHooks} from "v4-core/interfaces/IHooks.sol";
import {IPoolManager} from "v4-core/interfaces/IPoolManager.sol";
import {Hooks} from "v4-core/libraries/Hooks.sol";
import {PoolKey} from "v4-core/types/PoolKey.sol";
import {PoolId, PoolIdLibrary} from "v4-core/types/PoolId.sol";
import {StateLibrary} from "v4-core/libraries/StateLibrary.sol";
import {BalanceDelta, toBalanceDelta} from "v4-core/types/BalanceDelta.sol";
import {Currency, CurrencyLibrary} from "v4-core/types/Currency.sol";
import {BeforeSwapDelta} from "v4-core/types/BeforeSwapDelta.sol";

/// @title  HALO — fees belong to the liquidity that stayed
///
/// @notice A Uniswap v4 pool where fees are released to a position by TENURE.
///         Liquidity that arrives, collects on one trade and leaves hands those
///         fees to the liquidity that was already there.
///
/// @dev    THE PROBLEM
///
///         Just-in-time liquidity is the cleanest extraction in AMMs. See a large
///         swap in the mempool, add an enormous position one block ahead of it,
///         take almost the whole fee on that trade because you now dwarf everyone
///         else in range, remove in the next block. No inventory risk, twelve
///         seconds of exposure — and the people who had been quoting that range
///         for weeks are diluted to nothing on the one trade that actually paid.
///
///         Nothing in the AMM objects, because the AMM cannot see time. A position
///         one block old and a position one month old are the same row of storage.
///         Halo gives the pool that missing dimension.
///
///         WHAT IT CAN TOUCH
///
///         ┌────────────────────────────────────────────────────────────────┐
///         │  THE TOLL IS TAKEN FROM FEES ONLY. PRINCIPAL IS NEVER TOUCHED. │
///         └────────────────────────────────────────────────────────────────┘
///
///         v4 hands `afterRemoveLiquidity` the accrued fees as a BalanceDelta
///         separate from the principal. So this is not a policy the contract
///         promises to respect — it is the only quantity the contract is ever
///         given. A withdrawal returns every wei of capital whatever the toll
///         says, because the toll is arithmetically incapable of reaching it.
///
///         THE RULE
///
///             toll = feesAccrued × (TENURE_BLOCKS − age) ÷ TENURE_BLOCKS
///
///         Same block: the entire fee goes back. One hour: nothing is taken.
///         Linear rather than clever, on purpose — this contract takes money from
///         people, and a rule that can be checked by hand beats a rule that is
///         better tuned.
///
///         WHERE IT GOES
///
///         Nowhere. The toll is donated, in the same call, to the liquidity
///         currently in range — the positions that were there to be diluted.
///         Nothing accrues to a treasury, a deployer, or this contract: there is
///         no withdraw function anywhere in this file, and no owner to call one.
///
///         THE ATTACK THIS MUST SURVIVE
///
///         Age is stored per position. If adding to an old position kept its old
///         age, the whole thing collapses in one line: open a position, wait an
///         hour, then JIT by adding a hundred million to that same position and
///         removing it toll-free, because the row says "one hour old". So an add
///         does not inherit an age, it DILUTES it, weighted by liquidity:
///
///             age ← oldAge × oldLiquidity ÷ (oldLiquidity + addedLiquidity)
///
///         Ten thousand units of JIT landing on one unit of tenured liquidity
///         inherits one ten-thousandth of that tenure. The loophole closes on the
///         same arithmetic that opened it.
contract HaloHook is IHooks {
    using PoolIdLibrary for PoolKey;
    using StateLibrary for IPoolManager;
    using CurrencyLibrary for Currency;

    error NotPoolManager();
    error HookNotImplemented();
    error BadConfig();

    event TollTaken(bytes32 indexed position, uint256 age, uint256 bps, uint128 amount0, uint128 amount1);
    /// @dev Emitted when the toll had nowhere to land because no liquidity was in
    ///      range. It is held and flushed on a later swap rather than reverting
    ///      somebody's withdrawal.
    event TollDeferred(uint128 amount0, uint128 amount1);
    event TollFlushed(uint128 amount0, uint128 amount1);

    /* ── the rule: compiled in, no owner, no setter ────────────────── */

    /// @notice Blocks of tenure after which a position keeps all of its fees.
    /// @dev ~1 hour on Ethereum. JIT is a single-block attack, so the threshold
    ///      only has to be long enough that renting liquidity for a moment stops
    ///      paying — not long enough to trap anyone.
    uint256 public constant TENURE_BLOCKS = 300;

    uint256 internal constant BPS = 10_000;

    IPoolManager public immutable poolManager;

    struct Position {
        uint128 liquidity;
        uint64 ageBlock; // liquidity-weighted average block of entry
    }

    mapping(bytes32 => Position) public positions;

    uint128 public pending0;
    uint128 public pending1;

    constructor(IPoolManager _pm) {
        if (address(_pm) == address(0)) revert BadConfig();
        poolManager = _pm;
        Hooks.validateHookPermissions(IHooks(address(this)), getHookPermissions());
    }

    modifier onlyManager() {
        if (msg.sender != address(poolManager)) revert NotPoolManager();
        _;
    }

    function getHookPermissions() public pure returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: false,
            afterInitialize: false,
            beforeAddLiquidity: false,
            afterAddLiquidity: true, // stamp and dilute the age
            beforeRemoveLiquidity: false,
            afterRemoveLiquidity: true, // read the age, take the toll
            beforeSwap: false,
            afterSwap: true, // flush a deferred donation
            beforeDonate: false,
            afterDonate: false,
            beforeSwapReturnDelta: false,
            afterSwapReturnDelta: false,
            afterAddLiquidityReturnDelta: false,
            afterRemoveLiquidityReturnDelta: true // the toll itself
        });
    }

    /* ── reads ─────────────────────────────────────────────────────── */

    function positionKey(PoolId id, address owner, int24 tickLower, int24 tickUpper, bytes32 salt)
        public
        pure
        returns (bytes32)
    {
        return keccak256(abi.encodePacked(PoolId.unwrap(id), owner, tickLower, tickUpper, salt));
    }

    /// @notice What share of a position's fees would be handed back right now.
    /// @dev Exposed so the number is checkable before a withdrawal, not after.
    function tollBps(bytes32 key) public view returns (uint256) {
        Position memory p = positions[key];
        if (p.ageBlock == 0) return BPS; // unknown position: treat as brand new
        uint256 age = block.number - uint256(p.ageBlock);
        if (age >= TENURE_BLOCKS) return 0;
        return ((TENURE_BLOCKS - age) * BPS) / TENURE_BLOCKS;
    }

    /* ── add: stamp, and dilute what was there ─────────────────────── */

    function afterAddLiquidity(
        address sender,
        PoolKey calldata key,
        IPoolManager.ModifyLiquidityParams calldata params,
        BalanceDelta,
        BalanceDelta,
        bytes calldata
    ) external override onlyManager returns (bytes4, BalanceDelta) {
        if (params.liquidityDelta > 0) {
            bytes32 k = positionKey(key.toId(), sender, params.tickLower, params.tickUpper, params.salt);
            Position storage p = positions[k];

            uint128 added = uint128(uint256(params.liquidityDelta));
            uint128 old = p.liquidity;

            if (old == 0 || p.ageBlock == 0) {
                p.ageBlock = uint64(block.number);
            } else {
                // Blend as an AGE rather than as a timestamp, so the arithmetic
                // can never place the entry block in the future.
                uint256 oldAge = block.number - uint256(p.ageBlock);
                uint256 blended = (oldAge * uint256(old)) / (uint256(old) + uint256(added));
                p.ageBlock = uint64(block.number - blended);
            }
            p.liquidity = old + added;
        }
        return (IHooks.afterAddLiquidity.selector, toBalanceDelta(0, 0));
    }

    /* ── remove: the toll, and where it goes ───────────────────────── */

    function afterRemoveLiquidity(
        address sender,
        PoolKey calldata key,
        IPoolManager.ModifyLiquidityParams calldata params,
        BalanceDelta,
        BalanceDelta feesAccrued,
        bytes calldata
    ) external override onlyManager returns (bytes4, BalanceDelta) {
        bytes32 k = positionKey(key.toId(), sender, params.tickLower, params.tickUpper, params.salt);
        Position storage p = positions[k];

        uint256 bps = tollBps(k);
        uint256 age = p.ageBlock == 0 ? 0 : block.number - uint256(p.ageBlock);

        // Book the exit before touching money.
        if (params.liquidityDelta < 0) {
            uint128 removed = uint128(uint256(-params.liquidityDelta));
            p.liquidity = removed >= p.liquidity ? 0 : p.liquidity - removed;
            if (p.liquidity == 0) p.ageBlock = 0;
        }

        if (bps == 0) return (IHooks.afterRemoveLiquidity.selector, toBalanceDelta(0, 0));

        // Fees owed on a removal are non-negative; anything else is not a fee.
        int128 f0 = feesAccrued.amount0();
        int128 f1 = feesAccrued.amount1();
        uint128 toll0 = f0 > 0 ? uint128((uint256(uint128(f0)) * bps) / BPS) : 0;
        uint128 toll1 = f1 > 0 ? uint128((uint256(uint128(f1)) * bps) / BPS) : 0;
        if (toll0 == 0 && toll1 == 0) {
            return (IHooks.afterRemoveLiquidity.selector, toBalanceDelta(0, 0));
        }

        emit TollTaken(k, age, bps, toll0, toll1);

        // Claiming the toll gives this hook a positive delta; donating it in the
        // same call gives an equal negative one. They cancel, nothing moves out
        // of the manager, and the fees land on the liquidity still in range.
        _payOut(key, toll0, toll1);

        return (IHooks.afterRemoveLiquidity.selector, toBalanceDelta(int128(toll0), int128(toll1)));
    }

    /// @dev Claiming the toll leaves this hook with a POSITIVE delta, and every
    ///      delta must be resolved before the manager's lock closes. There are
    ///      exactly two ways to resolve it and both are used here:
    ///
    ///        · donate()  — gives an equal negative delta. They cancel, nothing
    ///                      moves, and the fees land on the liquidity in range.
    ///        · mint()    — turns the delta into ERC-6909 claim tokens held by
    ///                      this contract, to be donated later.
    ///
    ///      The second branch exists because donate() reverts when nothing is in
    ///      range to receive it, and that must never bubble up into somebody's
    ///      withdrawal. Returning the toll without settling it either way is what
    ///      the tests caught: the manager rejected the whole removal with
    ///      CurrencyNotSettled, so an empty-range pool could not be exited at all.
    function _payOut(PoolKey calldata key, uint128 a0, uint128 a1) internal {
        if (poolManager.getLiquidity(key.toId()) != 0) {
            poolManager.donate(key, uint256(a0), uint256(a1), "");
            return;
        }
        if (a0 != 0) poolManager.mint(address(this), key.currency0.toId(), uint256(a0));
        if (a1 != 0) poolManager.mint(address(this), key.currency1.toId(), uint256(a1));
        pending0 += a0;
        pending1 += a1;
        emit TollDeferred(a0, a1);
    }

    /* ── swap: flush anything that had nowhere to go ───────────────── */

    function afterSwap(address, PoolKey calldata key, IPoolManager.SwapParams calldata, BalanceDelta, bytes calldata)
        external
        override
        onlyManager
        returns (bytes4, int128)
    {
        uint128 a0 = pending0;
        uint128 a1 = pending1;
        if ((a0 != 0 || a1 != 0) && poolManager.getLiquidity(key.toId()) != 0) {
            pending0 = 0;
            pending1 = 0;
            // donate() debits the hook; the claim tokens minted when the toll was
            // parked are burned to pay for it, so again nothing moves in or out.
            poolManager.donate(key, uint256(a0), uint256(a1), "");
            if (a0 != 0) poolManager.burn(address(this), key.currency0.toId(), uint256(a0));
            if (a1 != 0) poolManager.burn(address(this), key.currency1.toId(), uint256(a1));
            emit TollFlushed(a0, a1);
        }
        return (IHooks.afterSwap.selector, int128(0));
    }

    /* ── unused entry points ───────────────────────────────────────── */

    function beforeInitialize(address, PoolKey calldata, uint160) external pure override returns (bytes4) {
        revert HookNotImplemented();
    }

    function afterInitialize(address, PoolKey calldata, uint160, int24) external pure override returns (bytes4) {
        revert HookNotImplemented();
    }

    function beforeAddLiquidity(address, PoolKey calldata, IPoolManager.ModifyLiquidityParams calldata, bytes calldata)
        external
        pure
        override
        returns (bytes4)
    {
        revert HookNotImplemented();
    }

    function beforeRemoveLiquidity(
        address,
        PoolKey calldata,
        IPoolManager.ModifyLiquidityParams calldata,
        bytes calldata
    ) external pure override returns (bytes4) {
        revert HookNotImplemented();
    }

    function beforeSwap(address, PoolKey calldata, IPoolManager.SwapParams calldata, bytes calldata)
        external
        pure
        override
        returns (bytes4, BeforeSwapDelta, uint24)
    {
        revert HookNotImplemented();
    }

    function beforeDonate(address, PoolKey calldata, uint256, uint256, bytes calldata)
        external
        pure
        override
        returns (bytes4)
    {
        revert HookNotImplemented();
    }

    function afterDonate(address, PoolKey calldata, uint256, uint256, bytes calldata)
        external
        pure
        override
        returns (bytes4)
    {
        revert HookNotImplemented();
    }
}
