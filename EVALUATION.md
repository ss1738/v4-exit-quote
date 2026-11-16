# Independent developer evaluation

This is an unaudited prototype for standard Uniswap v4 PositionManager withdrawal quoting. Please evaluate whether it fills a gap in your own development workflow. A negative finding or incompatibility report is useful; no endorsement is requested.

## Reproduce the local behavior

Requires Python 3.10+ and Foundry's `anvil` on PATH.

```sh
git clone https://github.com/ss1738/v4-exit-quote.git
cd v4-exit-quote
git rev-parse HEAD
python3 -m unittest discover -s tests -v
python3 examples/run_local_demo.py --output-dir evaluation-output
```

The demo starts a private local node, uses mock tokens and executes only on that node. No wallet key, funded wallet or external RPC is needed. See README.md for the accounting, source provenance and support boundary.

## Evaluate relevance to your workflow

The local demo establishes reproducibility only. If your team implements standard PositionManager withdrawals, compare the tool's `caller_delta`, `fees_accrued`, `slippage_component`, minimums and calldata with your existing quoting flow. Explain which part you actually used and whether it adds anything to your current implementation.

Optionally evaluate a supported position with a tracing-capable RPC using the README's quote command. This CLI performs read-only RPC calls and produces an unsigned transaction. Do not execute the output with real funds as part of this evaluation. Never send wallet keys or credential-bearing RPC URLs in a report.

Unsupported cases include smart-wallet owners, subscribed positions, custom PositionManagers and nonstandard token settlement. A successful simulation does not guarantee later execution.

## Report template

Open an issue in this repository, or share a private report with the maintainer first if it contains confidential information. Redact RPC URLs, private hook data and other sensitive inputs.

- Team/project and your role:
- Team's relationship to this project's maintainer:
- Version or commit evaluated:
- Python/Anvil versions and operating system:
- What you actually ran or integrated:
- Existing withdrawal workflow and the comparison you made:
- Results, reproducible errors, or unsupported requirements:
- Did this add useful functionality? If not, why not?
- Public issue, integration commit or other evidence, if available:
- May the maintainer name your team and link this report in a grant application? Yes / No:

An installation, star, signup or promise to integrate should not be described as independent adoption. Reproduction of the demo alone should be reported as demo reproduction. Grant eligibility and funding decisions belong to the funder.
