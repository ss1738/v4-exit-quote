import copy
import unittest

from v4_exit_quote.core import (
    MAX_INT128,
    Pool,
    QuoteError,
    QuoteRequest,
    Rpc,
    SELECTORS,
    ZERO,
    address,
    decrease_calldata,
    extract_deltas,
    manager_input,
    minima,
    quantity,
    quote,
    unpack_delta,
    word,
)

OWNER = "0x1111222233334444555566667777888899990000"
POSM = "0x" + "22" * 20
MANAGER = "0x" + "33" * 20
TOKEN0 = "0x" + "44" * 20
TOKEN1 = "0x" + "55" * 20
POOL = Pool(TOKEN0, TOKEN1, 3000, 60, ZERO, -120, 120, False)


def pack(a, b):
    return ((a % (1 << 128)) << 128) | (b % (1 << 128))


class FakeRpc:
    def __init__(self):
        self.calls = []
        self.delta = (1100, 2200)
        self.fees = (100, 200)
        self.owner = OWNER
        self.chain = 31337
        self.subscriber = False
        self.trace_missing = False
        self.revert = False
        self.reorg = False
        self.unstable = False
        self.block_reads = 0
        self.trace_count = 0
        self.owner_code = "0x"

    def call(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "eth_chainId":
            return hex(self.chain)
        if method == "eth_getBlockByNumber":
            self.block_reads += 1
            return {
                "number": "0x64",
                "hash": "0x"
                + ("bb" if self.reorg and self.block_reads > 1 else "aa") * 32,
                "timestamp": "0x3e8",
            }
        if method == "eth_getCode":
            return self.owner_code if params[0] == OWNER else "0x6000"
        if method == "eth_call":
            data = params[0]["data"]
            selector = data[2:10]
            if selector == SELECTORS["owner"]:
                return "0x" + word(int(self.owner, 16)).hex()
            if selector == SELECTORS["manager"]:
                return "0x" + word(int(MANAGER, 16)).hex()
            if selector == SELECTORS["liquidity"]:
                return "0x" + word(1000).hex()
            if selector == SELECTORS["pool_info"]:
                info = (
                    ((120 & 0xFFFFFF) << 32)
                    | ((-120 & 0xFFFFFF) << 8)
                    | int(self.subscriber)
                )
                return "0x" + (POOL.encoded() + word(info)).hex()
            if selector == SELECTORS["modify"]:
                return "0x"
            raise AssertionError(data)
        if method == "debug_traceCall":
            self.trace_count += 1
            if self.trace_missing:
                return {"structLogs": []}
            tx = params[0]
            # The positive path uses the full position, id 1, no hookData.
            frame = {
                "type": "CALL",
                "from": POSM,
                "to": MANAGER,
                "input": manager_input(POOL, 1, 1000, b""),
                "output": "0x"
                + (word(pack(*self.delta)) + word(pack(*self.fees))).hex(),
            }
            if self.unstable and self.trace_count > 1:
                frame["output"] = (
                    "0x" + (word(pack(1101, 2200)) + word(pack(*self.fees))).hex()
                )
            result = {
                "type": "CALL",
                "from": tx["from"],
                "to": tx["to"],
                "input": tx["data"],
                "calls": [frame],
            }
            if self.revert:
                result["error"] = "execution reverted"
            return result
        raise AssertionError(method)


def request(**kwargs):
    return QuoteRequest(
        **(
            {
                "chain_id": 31337,
                "position_manager": POSM,
                "pool_manager": MANAGER,
                "owner": OWNER,
                "token_id": 1,
            }
            | kwargs
        )
    )


class QuoteTests(unittest.TestCase):
    def expect_code(self, code, fn):
        with self.assertRaises(QuoteError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def test_signed_packed_deltas(self):
        for pair in [(-1, 2), (1, -2), (-(1 << 127), MAX_INT128), (0, 0)]:
            self.assertEqual(unpack_delta(pack(*pair)), pair)

    def test_known_penalty_case_and_rounding(self):
        principal, minimum = minima((5981737760509662,) * 2, (999999999999999,) * 2, 50)
        self.assertEqual(principal, (4981737760509663,) * 2)
        self.assertEqual(minimum, (4956829071707114,) * 2)
        self.assertEqual(minima((1, 99), (0, 0), 50)[1], (0, 98))

    def test_partial_and_asymmetric_amounts(self):
        self.assertEqual(minima((1234, 0), (12, 0), 25), ((1222, 0), (1218, 0)))

    def test_negative_component_refused(self):
        self.expect_code("UNSUPPORTED_DELTA", lambda: minima((10, 20), (11, 0), 50))

    def test_signed_overflow_refused(self):
        self.expect_code(
            "UNSUPPORTED_DELTA", lambda: minima((MAX_INT128 + 1, 0), (0, 0), 50)
        )

    def test_invalid_slippage(self):
        for value in [-1, 10000, 1.5, True]:
            with self.subTest(value=value):
                self.assertRaises(QuoteError, minima, (1, 1), (0, 0), value)

    def test_full_quote_and_block_pinning(self):
        rpc, evidence = FakeRpc(), {}
        result = quote(rpc, request(), evidence)
        self.assertEqual(result["amounts"][0]["minimum"], "995")
        self.assertEqual(result["amounts"][1]["minimum"], "1990")
        self.assertEqual(result["transaction"]["from"], OWNER)
        self.assertEqual(result["status"], "simulated")
        self.assertIn("final_trace", evidence)
        for method, params in rpc.calls:
            if method in ("eth_getCode", "eth_call", "debug_traceCall"):
                self.assertEqual(params[1], "0x64")
        self.assertEqual(sum(method == "debug_traceCall" for method, _ in rpc.calls), 2)

    def test_wrong_chain(self):
        rpc = FakeRpc()
        rpc.chain = 1
        self.expect_code("WRONG_CHAIN", lambda: quote(rpc, request()))

    def test_wrong_owner(self):
        rpc = FakeRpc()
        rpc.owner = POSM
        self.expect_code("WRONG_OWNER", lambda: quote(rpc, request()))

    def test_subscribed_position_refused(self):
        rpc = FakeRpc()
        rpc.subscriber = True
        self.expect_code("UNSUPPORTED_SUBSCRIBER", lambda: quote(rpc, request()))

    def test_smart_wallet_owner_refused(self):
        rpc = FakeRpc()
        rpc.owner_code = "0xef0100" + "aa" * 20
        self.expect_code("UNSUPPORTED_OWNER", lambda: quote(rpc, request()))

    def test_invalid_liquidity(self):
        for value in (0, 1001, -1):
            with self.subTest(value=value):
                self.assertRaises(
                    QuoteError, quote, FakeRpc(), request(liquidity=value)
                )

    def test_missing_trace_refused(self):
        rpc = FakeRpc()
        rpc.trace_missing = True
        self.expect_code("TRACE_UNAVAILABLE", lambda: quote(rpc, request()))

    def test_reverted_trace_refused(self):
        rpc = FakeRpc()
        rpc.revert = True
        self.expect_code("SIMULATION_REVERTED", lambda: quote(rpc, request()))

    def test_unstable_simulation_refused(self):
        rpc = FakeRpc()
        rpc.unstable = True
        self.expect_code("UNSTABLE_SIMULATION", lambda: quote(rpc, request()))

    def test_reorg_refused(self):
        rpc = FakeRpc()
        rpc.reorg = True
        self.expect_code("BLOCK_CHANGED", lambda: quote(rpc, request()))

    def test_mismatched_root_refused(self):
        rpc = FakeRpc()
        quote(rpc, request())
        tx = next(p[0] for m, p in rpc.calls if m == "debug_traceCall")
        trace = rpc.call("debug_traceCall", [tx, "0x64", {}])
        trace["from"] = POSM
        self.expect_code(
            "TRACE_MISMATCH",
            lambda: extract_deltas(
                trace, POSM, MANAGER, manager_input(POOL, 1, 1000, b""), tx
            ),
        )

    def test_multiple_or_wrong_manager_calls_refused(self):
        for duplicate in (False, True):
            rpc = FakeRpc()
            tx = {"from": OWNER, "to": POSM, "data": "0x1234"}
            trace = rpc.call("debug_traceCall", [tx, "0x64", {}])
            if duplicate:
                trace["calls"].append(copy.deepcopy(trace["calls"][0]))
            else:
                trace["calls"][0]["input"] = manager_input(POOL, 2, 1000, b"")
            self.expect_code(
                "AMBIGUOUS_TRACE" if duplicate else "TRACE_MISMATCH",
                lambda: extract_deltas(
                    trace, POSM, MANAGER, manager_input(POOL, 1, 1000, b""), tx
                ),
            )

    def test_bad_addresses(self):
        for value in ("0x123", "0x" + "gg" * 20, "not-an-address", None):
            self.expect_code("INVALID_ADDRESS", lambda: address(value))

    def test_malformed_rpc_quantities_are_quote_errors(self):
        # A syntactically valid JSON-RPC reply with a bad hex quantity must fail
        # closed with a QuoteError, which the CLI reports as JSON with exit code 2.
        good_block = {"number": "0x64", "hash": "0x" + "aa" * 32, "timestamp": "0x3e8"}
        cases = [
            ("eth_chainId", None),
            ("eth_chainId", 31337),
            ("eth_chainId", "0xzz"),
            ("eth_chainId", "0x"),
            ("eth_chainId", "7a69"),
            ("eth_getBlockByNumber", good_block | {"timestamp": "zz"}),
            ("eth_getBlockByNumber", good_block | {"timestamp": 1000}),
            ("eth_getBlockByNumber", good_block | {"number": 100}),
            ("eth_getBlockByNumber", good_block | {"number": "0xzz"}),
        ]
        for method, value in cases:
            with self.subTest(method=method, value=value):
                rpc = FakeRpc()
                original = rpc.call

                def call(name, params, original=original, method=method, value=value):
                    result = original(name, params)
                    return value if name == method else result

                rpc.call = call
                self.expect_code(
                    "INVALID_RPC_RESPONSE", lambda rpc=rpc: quote(rpc, request())
                )

    def test_quantity_parses_valid_hex_strings(self):
        cases = [
            ("0x0", 0),
            ("0x64", 100),
            ("0xFF", 255),
            ("0xAbC", 2748),
            # Leading zeros are accepted on purpose for node compatibility. The
            # helper checks shape and digits; it does not enforce canonical form.
            ("0x0064", 100),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(quantity(text, "value"), expected)

    def test_write_methods_unavailable(self):
        rpc = Rpc("http://127.0.0.1:1")
        self.expect_code("METHOD_REFUSED", lambda: rpc.call("eth_sendTransaction", []))

    def test_dynamic_hook_data_changes_encoded_operation(self):
        raw = bytes.fromhex("ab" * 33)
        data = decrease_calldata(POOL, 1, 1000, 995, 1990, OWNER, 1300, raw)
        self.assertIn(raw.hex(), data)
        self.assertNotEqual(
            data, decrease_calldata(POOL, 1, 1000, 995, 1990, OWNER, 1300, b"")
        )


if __name__ == "__main__":
    unittest.main()
