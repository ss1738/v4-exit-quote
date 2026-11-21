"""ABI encoding and quoting without signing, broadcasting, or runtime dependencies."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

ZERO = "0x" + "00" * 20
MAX128 = (1 << 128) - 1
MAX_INT128 = (1 << 127) - 1
SELECTORS = {
    "owner": "6352211e",
    "pool_info": "7ba03aad",
    "liquidity": "1efeed33",
    "manager": "dc4c90d3",
    "modify": "dd46508f",
    "core_modify": "5a6bcfda",
}
READ_METHODS = frozenset(
    {
        "eth_chainId",
        "eth_getBlockByNumber",
        "eth_getCode",
        "eth_call",
        "debug_traceCall",
    }
)


class QuoteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise QuoteError(code, message)


def address(value: str) -> str:
    require(
        isinstance(value, str)
        and re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is not None,
        "INVALID_ADDRESS",
        "Expected a 20-byte hexadecimal address.",
    )
    return value.lower()


def raw_hex(value: str) -> bytes:
    require(
        isinstance(value, str)
        and re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value) is not None,
        "INVALID_HEX",
        "Expected even-length 0x-prefixed hexadecimal bytes.",
    )
    return bytes.fromhex(value[2:])


def quantity(value: Any, field: str) -> int:
    """Parse a JSON-RPC hex quantity string, failing closed on any other shape.

    Requires the 0x prefix and hex digits only. Leading zeros are tolerated on
    purpose for node compatibility, so this does not enforce canonical encoding.
    """
    require(
        isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]+", value) is not None,
        "INVALID_RPC_RESPONSE",
        f"RPC returned an invalid {field}.",
    )
    return int(value, 16)


def uint(value: int, bits: int = 256) -> int:
    require(
        type(value) is int and 0 <= value < (1 << bits),
        "INVALID_INTEGER",
        f"Expected an unsigned {bits}-bit integer.",
    )
    return value


def word(value: int) -> bytes:
    return uint(value).to_bytes(32, "big")


def signed(value: int, bits: int) -> int:
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def signed_word(value: int) -> bytes:
    require(
        -(1 << 255) <= value < (1 << 255), "INVALID_INTEGER", "int256 out of range."
    )
    return (value % (1 << 256)).to_bytes(32, "big")


def address_word(value: str) -> bytes:
    return bytes(12) + raw_hex(address(value))


def blob(value: bytes) -> bytes:
    return word(len(value)) + value + bytes((-len(value)) % 32)


def words(value: str, count: int) -> list[int]:
    data = raw_hex(value)
    require(
        len(data) == count * 32, "INVALID_ABI", f"Expected exactly {count} ABI words."
    )
    return [int.from_bytes(data[i : i + 32], "big") for i in range(0, len(data), 32)]


def decode_address(value: str) -> str:
    n = words(value, 1)[0]
    uint(n, 160)
    return "0x" + n.to_bytes(20, "big").hex()


def selector_call(name: str, argument: int | None = None) -> str:
    return (
        "0x" + SELECTORS[name] + (word(argument).hex() if argument is not None else "")
    )


@dataclass(frozen=True)
class Pool:
    currency0: str
    currency1: str
    fee: int
    tick_spacing: int
    hook: str
    tick_lower: int
    tick_upper: int
    subscriber: bool

    @classmethod
    def decode(cls, data: str) -> Pool:
        a, b, fee, spacing_word, hook, info = words(data, 6)
        uint(a, 160)
        uint(b, 160)
        uint(hook, 160)
        uint(fee, 24)
        spacing = signed(spacing_word, 24)
        require(
            signed_word(spacing) == word(spacing_word),
            "INVALID_ABI",
            "Invalid int24 encoding.",
        )
        lower, upper = signed(info >> 8, 24), signed(info >> 32, 24)
        require(
            0 < spacing <= 32767 and -887272 <= lower < upper <= 887272,
            "UNSUPPORTED_POOL",
            "Invalid pool tick configuration.",
        )
        require(
            a < b, "UNSUPPORTED_POOL", "Pool currencies must be distinct and sorted."
        )
        return cls(
            "0x" + a.to_bytes(20, "big").hex(),
            "0x" + b.to_bytes(20, "big").hex(),
            fee,
            spacing,
            "0x" + hook.to_bytes(20, "big").hex(),
            lower,
            upper,
            bool(info & 255),
        )

    def encoded(self) -> bytes:
        return (
            address_word(self.currency0)
            + address_word(self.currency1)
            + word(self.fee)
            + signed_word(self.tick_spacing)
            + address_word(self.hook)
        )


def manager_input(pool: Pool, token_id: int, liquidity: int, hook_data: bytes) -> str:
    # PoolKey is five words; ModifyLiquidityParams is four; bytes offset is the tenth.
    data = (
        pool.encoded()
        + signed_word(pool.tick_lower)
        + signed_word(pool.tick_upper)
        + signed_word(-liquidity)
        + word(token_id)
        + word(320)
        + blob(hook_data)
    )
    return "0x" + SELECTORS["core_modify"] + data.hex()


def decrease_calldata(
    pool: Pool,
    token_id: int,
    liquidity: int,
    minimum0: int,
    minimum1: int,
    owner: str,
    deadline: int,
    hook_data: bytes,
) -> str:
    uint(token_id)
    uint(liquidity, 128)
    uint(minimum0, 128)
    uint(minimum1, 128)
    uint(deadline)
    decrease = (
        word(token_id)
        + word(liquidity)
        + word(minimum0)
        + word(minimum1)
        + word(160)
        + blob(hook_data)
    )
    take = (
        address_word(pool.currency0)
        + address_word(pool.currency1)
        + address_word(owner)
    )
    # abi.encode(bytes actions, bytes[] params), actions = DECREASE_LIQUIDITY, TAKE_PAIR.
    actions = blob(bytes.fromhex("0111"))
    first, second = blob(decrease), blob(take)
    params = word(2) + word(64) + word(64 + len(first)) + first + second
    unlock_data = word(64) + word(64 + len(actions)) + actions + params
    return (
        "0x"
        + SELECTORS["modify"]
        + (word(64) + word(deadline) + blob(unlock_data)).hex()
    )


def unpack_delta(value: int) -> tuple[int, int]:
    return signed(value >> 128, 128), signed(value, 128)


def extract_deltas(
    trace: dict[str, Any],
    position_manager: str,
    pool_manager: str,
    expected_input: str,
    tx: dict[str, str],
) -> tuple[tuple[int, int], tuple[int, int]]:
    require(
        isinstance(trace, dict)
        and all(isinstance(trace.get(k), str) for k in ("type", "from", "to", "input")),
        "TRACE_UNAVAILABLE",
        "RPC did not return a callTracer frame.",
    )
    require(
        not trace.get("error"),
        "SIMULATION_REVERTED",
        "Withdrawal simulation reverted; no quote produced.",
    )
    require(
        trace.get("type", "").upper() == "CALL"
        and trace.get("from", "").lower() == tx["from"]
        and trace.get("to", "").lower() == tx["to"]
        and trace.get("input", "").lower() == tx["data"].lower(),
        "TRACE_MISMATCH",
        "Trace root does not match the requested withdrawal.",
    )
    matches = []

    def walk(frame: dict[str, Any], failed_parent: bool = False) -> None:
        require(isinstance(frame, dict), "INVALID_TRACE", "Malformed call frame.")
        require(
            all(
                isinstance(frame.get(k, ""), str)
                for k in ("type", "from", "to", "input")
            ),
            "INVALID_TRACE",
            "Malformed call frame fields.",
        )
        failed = failed_parent or bool(frame.get("error"))
        if (
            frame.get("from", "").lower() == position_manager
            and frame.get("to", "").lower() == pool_manager
            and frame.get("input", "")
            .lower()
            .startswith("0x" + SELECTORS["core_modify"])
        ):
            require(
                not failed and frame.get("type", "").upper() == "CALL",
                "UNSUPPORTED_TRACE",
                "PoolManager operation reverted or used a non-CALL frame.",
            )
            require(
                frame["input"].lower() == expected_input.lower(),
                "TRACE_MISMATCH",
                "PoolManager operation differs from the requested position decrease.",
            )
            matches.append(frame)
        children = frame.get("calls", [])
        require(isinstance(children, list), "INVALID_TRACE", "Malformed child calls.")
        for child in children:
            walk(child, failed)

    walk(trace)
    require(
        len(matches) == 1,
        "AMBIGUOUS_TRACE",
        "Expected exactly one matching PositionManager-to-PoolManager decrease.",
    )
    delta, fees = words(matches[0].get("output", "0x"), 2)
    return unpack_delta(delta), unpack_delta(fees)


def minima(
    delta: tuple[int, int], fees: tuple[int, int], slippage_bps: int
) -> tuple[tuple[int, int], tuple[int, int]]:
    uint(slippage_bps)
    require(
        slippage_bps < 10000,
        "INVALID_SLIPPAGE",
        "Slippage must be between 0 and 9999 basis points.",
    )
    principal = tuple(delta[i] - fees[i] for i in range(2))
    require(
        all(0 <= n <= MAX_INT128 for n in principal),
        "UNSUPPORTED_DELTA",
        "Negative or overflowing slippage component: standard PositionManager cannot support this decrease.",
    )
    require(
        all(n >= 0 for n in delta) and all(n >= 0 for n in fees),
        "UNSUPPORTED_DELTA",
        "This release supports nonnegative withdrawal and accrued-fee deltas only.",
    )
    minimum = tuple(n * (10000 - slippage_bps) // 10000 for n in principal)
    return principal, minimum


class Rpc:
    """Read-only JSON-RPC client. RPC URL is never included in output or exceptions."""

    def __init__(self, url: str, timeout: float = 30):
        parts = urllib.parse.urlsplit(url)
        require(
            parts.scheme in ("http", "https") and bool(parts.hostname),
            "INVALID_RPC",
            "RPC URL must be HTTP or HTTPS.",
        )
        self.url, self.timeout, self.sequence = url, timeout, 0

    def call(self, method: str, params: list[Any]) -> Any:
        require(
            method in READ_METHODS,
            "METHOD_REFUSED",
            "Only read-only RPC methods are supported.",
        )
        self.sequence += 1
        body = {
            "jsonrpc": "2.0",
            "id": self.sequence,
            "method": method,
            "params": params,
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read(32_000_001)
            require(
                len(raw) <= 32_000_000, "RPC_TOO_LARGE", "RPC response exceeded 32 MB."
            )
            result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise QuoteError(
                "RPC_HTTP_ERROR",
                f"RPC returned HTTP {exc.code}; endpoint credentials are not logged.",
            ) from None
        except (OSError, ValueError) as exc:
            raise QuoteError(
                "RPC_UNAVAILABLE",
                f"RPC transport or JSON failure ({type(exc).__name__}).",
            ) from None
        require(
            isinstance(result, dict) and result.get("id") == self.sequence,
            "INVALID_RPC_RESPONSE",
            "Malformed JSON-RPC response or mismatched id.",
        )
        if result.get("error"):
            code = (
                result["error"].get("code")
                if isinstance(result["error"], dict)
                else None
            )
            kind = "TRACE_UNAVAILABLE" if method == "debug_traceCall" else "RPC_ERROR"
            raise QuoteError(
                kind,
                f"{method} failed (RPC code {code}); no quote produced. Check RPC tracing/archive support and transaction validity.",
            )
        require(
            "result" in result, "INVALID_RPC_RESPONSE", "RPC response omitted result."
        )
        return result["result"]


@dataclass(frozen=True)
class QuoteRequest:
    chain_id: int
    position_manager: str
    pool_manager: str
    owner: str
    token_id: int
    liquidity: int | None = None
    slippage_bps: int = 50
    ttl: int = 300
    block: int | None = None
    hook_data: str = "0x"
    gas: int = 8_000_000


def quote(
    rpc: Rpc, request: QuoteRequest, evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    uint(request.chain_id)
    uint(request.token_id)
    uint(request.slippage_bps)
    require(
        request.slippage_bps < 10000,
        "INVALID_SLIPPAGE",
        "Slippage must be between 0 and 9999 basis points.",
    )
    require(
        type(request.ttl) is int and 1 <= request.ttl <= 86400,
        "INVALID_TTL",
        "TTL must be 1 to 86400 seconds.",
    )
    require(
        type(request.gas) is int and 21000 <= request.gas <= 100_000_000,
        "INVALID_GAS",
        "Simulation gas must be between 21000 and 100000000.",
    )
    if request.block is not None:
        uint(request.block)
    posm, manager, owner = map(
        address, (request.position_manager, request.pool_manager, request.owner)
    )
    require(
        ZERO not in (posm, manager, owner),
        "INVALID_ADDRESS",
        "Manager and owner addresses cannot be zero.",
    )
    hook_data = raw_hex(request.hook_data)
    require(len(hook_data) <= 65536, "INVALID_HOOK_DATA", "Hook data exceeds 64 KiB.")
    require(
        quantity(rpc.call("eth_chainId", []), "chain id") == request.chain_id,
        "WRONG_CHAIN",
        "RPC chain id differs from requested chain.",
    )
    block = rpc.call(
        "eth_getBlockByNumber",
        [hex(request.block) if request.block is not None else "latest", False],
    )
    require(
        isinstance(block, dict)
        and block.get("number")
        and block.get("hash")
        and block.get("timestamp"),
        "BLOCK_UNAVAILABLE",
        "Requested block is unavailable.",
    )
    tag, block_hash = block["number"], block["hash"]
    require(len(raw_hex(block_hash)) == 32, "INVALID_BLOCK", "Invalid block hash.")
    block_number = quantity(tag, "block number")
    block_timestamp = quantity(block["timestamp"], "block timestamp")
    deadline = block_timestamp + request.ttl

    def call(to: str, data: str) -> str:
        return rpc.call("eth_call", [{"to": to, "data": data}, tag])

    codes = {}
    for label, target in (
        ("position_manager", posm),
        ("pool_manager", manager),
        ("owner", owner),
    ):
        codes[label] = raw_hex(rpc.call("eth_getCode", [target, tag]))
    require(
        codes["position_manager"] and codes["pool_manager"],
        "NO_CONTRACT",
        "Manager addresses must have code.",
    )
    require(
        not codes["owner"],
        "UNSUPPORTED_OWNER",
        "Version 0.1 supports EOA owners without delegated code; smart-wallet execution requires its own simulation path.",
    )
    require(
        decode_address(call(posm, selector_call("manager"))) == manager,
        "WRONG_POOL_MANAGER",
        "PositionManager does not point to the supplied PoolManager.",
    )
    require(
        decode_address(call(posm, selector_call("owner", request.token_id))) == owner,
        "WRONG_OWNER",
        "The supplied account does not own this position.",
    )
    pool = Pool.decode(call(posm, selector_call("pool_info", request.token_id)))
    require(
        not pool.subscriber,
        "UNSUPPORTED_SUBSCRIBER",
        "Subscribed positions are outside version 0.1 support.",
    )
    if pool.hook != ZERO:
        codes["hook"] = raw_hex(rpc.call("eth_getCode", [pool.hook, tag]))
        require(bool(codes["hook"]), "NO_HOOK_CODE", "Pool hook has no code.")
    current_liquidity = words(
        call(posm, selector_call("liquidity", request.token_id)), 1
    )[0]
    uint(current_liquidity, 128)
    liquidity = (
        current_liquidity if request.liquidity is None else uint(request.liquidity, 128)
    )
    require(
        0 < liquidity <= current_liquidity,
        "INVALID_LIQUIDITY",
        "Decrease must be positive and no greater than the position liquidity.",
    )
    expected = manager_input(pool, request.token_id, liquidity, hook_data)

    def transaction(min0: int, min1: int) -> dict[str, str]:
        return {
            "from": owner,
            "to": posm,
            "value": "0x0",
            "gas": hex(request.gas),
            "data": decrease_calldata(
                pool,
                request.token_id,
                liquidity,
                min0,
                min1,
                owner,
                deadline,
                hook_data,
            ),
        }

    def trace(tx: dict[str, str]) -> dict[str, Any]:
        return rpc.call(
            "debug_traceCall", [tx, tag, {"tracer": "callTracer", "timeout": "20s"}]
        )

    discovery_tx = transaction(0, 0)
    discovery_trace = trace(discovery_tx)
    delta, fees = extract_deltas(discovery_trace, posm, manager, expected, discovery_tx)
    principal, minimum = minima(delta, fees, request.slippage_bps)
    final_tx = transaction(*minimum)
    final_trace = trace(final_tx)
    final_delta, final_fees = extract_deltas(
        final_trace, posm, manager, expected, final_tx
    )
    require(
        (delta, fees) == (final_delta, final_fees),
        "UNSTABLE_SIMULATION",
        "Deltas changed between discovery and final verification at the same block.",
    )
    require(
        rpc.call("eth_call", [final_tx, tag]) == "0x",
        "INVALID_EXECUTION_RESULT",
        "Unexpected PositionManager return data.",
    )
    recheck = rpc.call("eth_getBlockByNumber", [tag, False])
    require(
        isinstance(recheck, dict) and recheck.get("hash") == block_hash,
        "BLOCK_CHANGED",
        "Block hash changed during quoting; retry.",
    )
    if evidence is not None:
        evidence.update(
            {
                "discovery_transaction": discovery_tx,
                "discovery_trace": discovery_trace,
                "final_transaction": final_tx,
                "final_trace": final_trace,
                "block": block,
            }
        )

    amounts = []
    for i, currency in enumerate((pool.currency0, pool.currency1)):
        amounts.append(
            {
                "currency": currency,
                "unit": "raw_token_units",
                "caller_delta": str(delta[i]),
                "fees_accrued": str(fees[i]),
                "slippage_component": str(principal[i]),
                "minimum": str(minimum[i]),
            }
        )
    return {
        "schema_version": "0.1",
        "status": "simulated",
        "chain_id": str(request.chain_id),
        "block": {
            "number": str(block_number),
            "hash": block_hash,
            "timestamp": str(block_timestamp),
        },
        "position_manager": posm,
        "pool_manager": manager,
        "owner": owner,
        "token_id": str(request.token_id),
        "liquidity_to_remove": str(liquidity),
        "hook": pool.hook,
        "slippage_bps": request.slippage_bps,
        "deadline": str(deadline),
        "amounts": amounts,
        "transaction": final_tx,
        "verification": {
            "corrected_trace_succeeded": True,
            "corrected_eth_call_succeeded": True,
            "block_hash_rechecked": True,
            "runtime_sha256": {
                k: hashlib.sha256(v).hexdigest()
                for k, v in codes.items()
                if k != "owner"
            },
        },
        "limits": [
            "Simulation is at the recorded block, not a guarantee of future execution.",
            "caller_delta is PoolManager accounting credit, not independently verified wallet receipts; transfer-tax/rebasing tokens are unsupported.",
            "Addresses and RPC are caller-trusted; interface compatibility is not a code audit or canonical-deployment check.",
            "EOA-owned, unsubscribed standard PositionManager positions only. No signing or broadcasting.",
        ],
    }
