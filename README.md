# v4 Exit Quote

A local command-line tool and Python library for quoting a standard Uniswap v4 PositionManager decrease using the actual simulated PoolManager return values.

**MEASURED:** Implemented behavior and local test results are recorded in `evidence/unit-tests.txt`, `evidence/local-demo-complete/summary.json`, and the source under `v4_exit_quote/`. Live independent adoption and mainnet compatibility have not been established.

The application reads a position, simulates its withdrawal with zero discovery minimums, extracts `callerDelta` and `feesAccrued` from the call trace, calculates the component PositionManager checks, and verifies a second transaction with the corrected minimums. It returns an unsigned transaction and structured evidence. It does not sign or broadcast.

## Run the included working demo

Requires Python 3.10+ and Foundry's `anvil` executable. No RPC account, wallet key, network deployment or Python dependency installation is needed.

```sh
python3 examples/run_local_demo.py
```

The demo starts its own Anvil node on `127.0.0.1`, loads bundled test pools, runs the real CLI, and executes full, partial, mature-position, hookless and native-currency withdrawals on that local chain. It also checks that an unadjusted minimum reverts and a wrong-chain request fails. It shuts down its node on exit. Transactions are sent only by the demonstration script to the private node that script started.

To retain fresh output in a new directory:

```sh
python3 examples/run_local_demo.py --output-dir demo-output
```

The saved fixture uses retrieved HaloHook runtime with a fresh locally compiled PoolManager/PositionManager and mock-token balances. It is not a fork of an existing user's position. `examples/young-position-quote.json` is an executed example of the CLI response, not a live mainnet quote.

## Quote an existing supported position

The RPC must support historical state at the selected block and `debug_traceCall` with Geth-style `callTracer`. A normal free RPC may not expose tracing. Unsupported tracing produces an explicit error and no transaction output.

Set the RPC URL in `V4_EXIT_RPC_URL`, then supply the expected chain, contract addresses, owner and position ID:

```sh
python3 -m v4_exit_quote quote \
  --chain-id CHAIN_ID \
  --position-manager POSITION_MANAGER_ADDRESS \
  --pool-manager POOL_MANAGER_ADDRESS \
  --owner OWNER_ADDRESS \
  --token-id POSITION_ID \
  --slippage-bps 50 \
  --evidence-dir quote-evidence
```

Replace the uppercase placeholders. Use official deployment records for the addresses. The tool verifies the RPC chain ID and PositionManager's PoolManager getter, but does not claim that arbitrary supplied contract bytecode is canonical or audited.

Additional options:

| Option | Meaning |
| --- | --- |
| `--liquidity N` | Raw liquidity units to remove; defaults to the entire position. |
| `--block N` | Quote a particular block. Otherwise latest is resolved to a block number. |
| `--hook-data 0x...` | Caller-supplied hook data, passed unchanged. |
| `--ttl N` | Deadline seconds after the quoted block's timestamp; default 300. |
| `--gas N` | Simulation gas cap, included in the generated transaction; default 8,000,000. |
| `--rpc-url URL` | Alternative to the environment variable. Prefer the environment for credentials. |

Output is JSON on stdout. Errors are JSON on stderr with exit code 2. Evidence directories must be new; the tool refuses to overwrite an existing directory. RPC URLs are not included in result JSON, stored traces or error messages.

The latest block is pinned for all position reads and simulations. Its hash is checked again before returning a quote. Historical quotes can have deadlines that are already past: refresh a quote before using its calldata. The emitted gas value is a simulation cap, not an estimate of required gas or an execution-cost quote.

## What the amounts mean

All monetary and liquidity amounts are decimal strings representing raw units. No USD conversion, token-price estimate or decimal normalization is performed.

```text
slippage_component[i] = caller_delta[i] - fees_accrued[i]
minimum[i] = floor(slippage_component[i] × (10000 - slippage_bps) / 10000)
```

`caller_delta` is the credit returned to PositionManager by PoolManager after hook accounting. It is not an independent measurement of final wallet receipts. For the supported ordinary ERC-20/native settlement path, the generated `TAKE_PAIR` requests those credits for the position owner. Transfer-tax, rebasing or other nonstandard token behavior is outside this release's support.

The example young position returns this token0 result:

```json
{
  "caller_delta": "5981737760509662",
  "fees_accrued": "999999999999999",
  "slippage_component": "4981737760509663",
  "minimum": "4956829071707114"
}
```

In that fixture the hook withholds all accrued fees at the quote block. A minimum derived from wallet receipts instead of the slippage component would be too high. Mining the eventual transaction advances the block, which can reduce the age-based penalty; later receipts can consequently exceed the earlier quote.

## Python API

```python
from v4_exit_quote import QuoteRequest, Rpc, quote

request = QuoteRequest(
    chain_id=1,
    position_manager="0x...",  # replace with full official addresses
    pool_manager="0x...",
    owner="0x...",
    token_id=123,
    slippage_bps=50,
)
result = quote(Rpc(rpc_url), request)
```

Run from this directory, or optionally install the package with `pip install .` to get the `v4-exit-quote` console command. Runtime code uses the Python standard library only.

## Support boundary

- Standard PositionManager `DECREASE_LIQUIDITY` followed by `TAKE_PAIR`, with a fixed owner recipient.
- EOA owners with no deployed or EIP-7702-delegated code; owner equality is checked.
- Positive full or partial decreases; the position NFT is retained, even when all liquidity is removed.
- Unsubscribed positions only. PermissionedPositionManager and custom wallet execution paths are not supported.
- Nonnegative accrued-fee and withdrawal deltas. Negative or overflowing slippage components are rejected.
- Hooks that successfully execute through this path and yield one identifiable PositionManager-to-PoolManager decrease. There is no universal hook-compatibility guarantee.
- The RPC and supplied contract addresses are trusted inputs. Raw traces may reveal position details and hook data; store or share them deliberately.

The tool verifies the requested operation's exact PoolKey, ticks, salt, liquidity amount and hook data in the trace, rather than accepting an arbitrary call with a matching selector. Discovery and corrected simulations must yield identical deltas at the pinned block, and corrected calldata must also pass `eth_call`.

Simulation at one block does not guarantee later execution, unchanged hook behavior, fee protection after simulation, solvency, or token transfer semantics. This release is unaudited and has been exercised on local fixtures. No claim of live independent adoption or grant approval is made.

## Tests and evidence

```sh
python3 -m unittest discover -s tests -v
python3 examples/run_local_demo.py
```

`evidence/unit-tests.txt` contains the unit-test run. `evidence/local-demo-complete/summary.json` records the end-to-end local execution; each case directory contains the quote and raw traces. `evidence/build-manifest.json` records hashes and source revisions. The local demo's successful transactions are test-chain transactions only.

## Protocol references

- PositionManager decrease and slippage logic: https://github.com/Uniswap/v4-periphery/blob/c6e4754dab11bd38c6f08ac51c3c868569e93d64/src/PositionManager.sol
- SlippageCheck: https://github.com/Uniswap/v4-periphery/blob/c6e4754dab11bd38c6f08ac51c3c868569e93d64/src/libraries/SlippageCheck.sol
- PositionInfo packing: https://github.com/Uniswap/v4-periphery/blob/c6e4754dab11bd38c6f08ac51c3c868569e93d64/src/libraries/PositionInfoLibrary.sol
- Call tracing: https://geth.ethereum.org/docs/developers/evm-tracing/built-in-tracers

The Python tool's license is MIT. Bundled test fixtures include third-party bytecode with its own licenses; see `THIRD_PARTY.md`.
