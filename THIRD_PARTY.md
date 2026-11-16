# Third-party test fixture provenance

The Python application and demonstration script are original code under `LICENSE`. That license does not replace the licenses of third-party bytecode included in `examples/local-genesis.json`.

The bundled genesis is for a private local test chain. It contains:

| Component | Source | License indicated by inspected source |
| --- | --- | --- |
| PoolManager | Uniswap/v4-core, `59d3ecf53afa9264a16bba0e38f4c5d2231f80bc` | BUSL-1.1; preserve upstream conditions. |
| PositionManager and dependencies | Uniswap/v4-periphery, `c6e4754dab11bd38c6f08ac51c3c868569e93d64` | MIT, with dependency notices retained upstream. |
| Permit2 precompiled fixture | Uniswap/permit2, `cc56ad0f3439c502c246fc5cfcc3db92bb8b7219` | MIT. |
| MockERC20 / ERC20 | Solmate submodule pinned by the Permit2 revision | AGPL-3.0-only. |
| HaloHook | Verified Ethereum runtime at `0x6965238926a727a4ad6f5836cca94741efffc541` | Verified source header indicates MIT. |

Included notices are in `evidence/third-party-licenses/`. Corresponding sources and fixture-export test are provided in `evidence/fixture-source/`; upstream dependency revisions are in `evidence/build-manifest.json`. All source files retain their original license headers. The demo uses existing bytecode locally and never broadcasts a deployment to a public network.

Upstream source locations:

- https://github.com/Uniswap/v4-core/tree/59d3ecf53afa9264a16bba0e38f4c5d2231f80bc
- https://github.com/Uniswap/v4-periphery/tree/c6e4754dab11bd38c6f08ac51c3c868569e93d64
- https://github.com/Uniswap/permit2/tree/cc56ad0f3439c502c246fc5cfcc3db92bb8b7219
- https://etherscan.io/address/0x6965238926a727a4ad6f5836cca94741efffc541#code
