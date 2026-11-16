#!/usr/bin/env python3
"""Exercise the real CLI and execute its transactions on a private local Anvil only."""

import argparse
import json
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v4_exit_quote.core import Pool, decrease_calldata, word  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path)
    args = parser.parse_args()
    if not shutil.which("anvil"):
        raise SystemExit("Install Foundry/Anvil to run this local integration demo.")
    out = args.output_dir or pathlib.Path(tempfile.mkdtemp(prefix="v4-exit-demo-"))
    if args.output_dir:
        out.mkdir(parents=True, exist_ok=False)
    out = out.resolve()
    context = json.loads((ROOT / "examples/local-context.json").read_text())
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    node = subprocess.Popen(
        [
            "anvil",
            "--init",
            str(ROOT / "examples/local-genesis.json"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--hardfork",
            "cancun",
            "--silent",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def rpc(method, params):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    def call(method, params):
        response = rpc(method, params)
        if "error" in response:
            raise AssertionError(f"Local RPC {method} failed: {response['error']}")
        return response["result"]

    def balances(currencies=None):
        result = []
        for currency in currencies or (context["currency0"], context["currency1"]):
            if int(currency, 16) == 0:
                result.append(
                    int(call("eth_getBalance", [context["owner"], "latest"]), 16)
                )
                continue
            tx = {
                "to": currency,
                "data": "0x70a08231" + word(int(context["owner"], 16)).hex(),
            }
            result.append(int(call("eth_call", [tx, "latest"]), 16))
        return result

    base = [
        sys.executable,
        "-m",
        "v4_exit_quote",
        "quote",
        "--rpc-url",
        url,
        "--chain-id",
        "31337",
        "--position-manager",
        context["position_manager"],
        "--pool-manager",
        context["pool_manager"],
        "--owner",
        context["owner"],
        "--token-id",
        str(context["token_id"]),
    ]

    def get_quote(name, *extra):
        r = subprocess.run(
            base + list(extra) + ["--evidence-dir", str(out / name)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode:
            raise AssertionError(r.stderr)
        return json.loads(r.stdout)

    def execute(result):
        currencies = [row["currency"] for row in result["amounts"]]
        before = balances(currencies)
        call("anvil_impersonateAccount", [context["owner"]])
        tx_hash = call("eth_sendTransaction", [result["transaction"]])
        receipt = None
        for _ in range(100):
            receipt = call("eth_getTransactionReceipt", [tx_hash])
            if receipt:
                break
            time.sleep(0.05)
        assert receipt and receipt["status"] == "0x1", receipt
        after = balances(currencies)
        actual = [after[i] - before[i] for i in range(2)]
        gas_paid = int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16)
        for i, currency in enumerate(currencies):
            if int(currency, 16) == 0:
                actual[i] += gas_paid
        expected = [int(row["caller_delta"]) for row in result["amounts"]]
        # Mining advances the block: Halo's time penalty can shrink after the quote.
        assert all(actual[i] >= expected[i] for i in range(2)), (actual, expected)
        return {
            "transaction_hash": tx_hash,
            "receipt_status": receipt["status"],
            "actual_receipts_excluding_transaction_gas": [str(n) for n in actual],
            "quoted_caller_delta": [str(n) for n in expected],
            "note": "Executed in a later block; the fixture's age-based penalty may decrease.",
        }

    try:
        for _ in range(100):
            if node.poll() is not None:
                raise AssertionError("Local Anvil exited before becoming ready.")
            try:
                if call("eth_chainId", []) == "0x7a69":
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("Local Anvil failed to become ready.")
        call("anvil_mine", [hex(context["block_number"])])
        state = call("evm_snapshot", [])
        before = balances()
        block_before = call("eth_blockNumber", [])
        young = get_quote("young")
        assert balances() == before
        assert call("eth_blockNumber", []) == block_before
        # Young fixture forfeits all accrued fees; its caller delta equals gross principal.
        pool = Pool(
            context["currency0"].lower(),
            context["currency1"].lower(),
            3000,
            60,
            young["hook"],
            -120,
            120,
            False,
        )
        naive = dict(young["transaction"])
        naive["data"] = decrease_calldata(
            pool,
            context["token_id"],
            context["liquidity"],
            int(young["amounts"][0]["caller_delta"]) * 9950 // 10000,
            int(young["amounts"][1]["caller_delta"]) * 9950 // 10000,
            context["owner"],
            int(young["deadline"]),
            b"",
        )
        assert "error" in rpc("eth_call", [naive, "latest"]), (
            "Naive quote unexpectedly executed"
        )
        full_execution = execute(young)
        assert call("evm_revert", [state])
        state = call("evm_snapshot", [])
        partial = get_quote("partial", "--liquidity", str(context["liquidity"] // 2))
        partial_execution = execute(partial)
        assert call("evm_revert", [state])
        state = call("evm_snapshot", [])
        vanilla = get_quote("hookless", "--token-id", str(context["vanilla_token_id"]))
        assert int(vanilla["hook"], 16) == 0
        vanilla_execution = execute(vanilla)
        assert call("evm_revert", [state])
        state = call("evm_snapshot", [])
        native = get_quote("native", "--token-id", str(context["native_token_id"]))
        assert int(native["amounts"][0]["currency"], 16) == 0
        native_execution = execute(native)
        assert call("evm_revert", [state])
        call("anvil_mine", ["0x12c"])
        mature = get_quote("mature")
        for row in mature["amounts"]:
            assert int(row["slippage_component"]) + int(row["fees_accrued"]) == int(
                row["caller_delta"]
            )
        mature_execution = execute(mature)
        wrong = subprocess.run(
            base + ["--chain-id", "1"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (
            wrong.returncode == 2 and json.loads(wrong.stderr)["code"] == "WRONG_CHAIN"
        )
        summary = {
            "scope": "private local Anvil, mock tokens; not live-user adoption or mainnet execution",
            "checks": {
                "cli_quotes_without_state_changes": True,
                "naive_minimum_reverts": True,
                "corrected_full_withdrawal_executes": True,
                "partial_withdrawal_executes": True,
                "mature_withdrawal_executes": True,
                "hookless_withdrawal_executes": True,
                "native_currency_withdrawal_executes": True,
                "wrong_chain_refused": True,
            },
            "full_execution": full_execution,
            "partial_execution": partial_execution,
            "mature_execution": mature_execution,
            "hookless_execution": vanilla_execution,
            "native_execution": native_execution,
        }
        (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    finally:
        node.terminate()
        try:
            node.wait(timeout=5)
        except subprocess.TimeoutExpired:
            node.kill()
            node.wait()


if __name__ == "__main__":
    main()
