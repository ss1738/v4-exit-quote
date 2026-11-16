"""Command-line interface; all large amounts are serialized as decimal strings."""

import argparse
import json
import os
import sys
from pathlib import Path

from .core import QuoteError, QuoteRequest, Rpc, quote


def integer(value: str) -> int:
    try:
        return int(value, 16 if value.startswith("0x") else 10)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "Expected a decimal or 0x hexadecimal integer."
        ) from None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Uniswap v4 withdrawal quotes using callTracer."
    )
    parser.add_argument("--version", action="version", version="v4-exit-quote 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser(
        "quote",
        help="Simulate one position decrease and print verified calldata as JSON.",
    )
    p.add_argument(
        "--rpc-url",
        default=os.environ.get("V4_EXIT_RPC_URL"),
        help="Defaults to V4_EXIT_RPC_URL; never printed.",
    )
    p.add_argument("--chain-id", required=True, type=integer)
    p.add_argument("--position-manager", required=True)
    p.add_argument("--pool-manager", required=True)
    p.add_argument("--owner", required=True)
    p.add_argument("--token-id", required=True, type=integer)
    p.add_argument(
        "--liquidity",
        type=integer,
        help="Raw liquidity units; defaults to all position liquidity.",
    )
    p.add_argument("--slippage-bps", type=integer, default=50)
    p.add_argument(
        "--ttl",
        type=integer,
        default=300,
        help="Deadline seconds after the quoted block timestamp.",
    )
    p.add_argument(
        "--block",
        type=integer,
        help="Block number; latest is resolved and pinned by default.",
    )
    p.add_argument("--hook-data", default="0x")
    p.add_argument("--gas", type=integer, default=8_000_000)
    p.add_argument(
        "--evidence-dir",
        type=Path,
        help="Create a new directory containing the quote and raw traces.",
    )
    args = parser.parse_args()
    try:
        if not args.rpc_url:
            raise QuoteError("RPC_REQUIRED", "Provide --rpc-url or V4_EXIT_RPC_URL.")
        if args.evidence_dir and args.evidence_dir.exists():
            raise QuoteError(
                "OUTPUT_EXISTS",
                "Evidence directory already exists; choose a new directory.",
            )
        request = QuoteRequest(
            **{k: getattr(args, k) for k in QuoteRequest.__dataclass_fields__}
        )
        evidence = {}
        result = quote(Rpc(args.rpc_url), request, evidence)
        if args.evidence_dir:
            args.evidence_dir.mkdir(parents=True, exist_ok=False)
            (args.evidence_dir / "quote.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
            (args.evidence_dir / "traces.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
        print(json.dumps(result, indent=2))
        return 0
    except QuoteError as exc:
        print(
            json.dumps({"status": "error", "code": exc.code, "message": str(exc)}),
            file=sys.stderr,
        )
        return 2
    except OSError:
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": "OUTPUT_ERROR",
                    "message": "Could not write evidence files.",
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
