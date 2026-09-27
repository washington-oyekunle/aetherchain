# AetherChain

A zero-dependency Python implementation of a small, coherent blockchain engine assembled from the supplied components.

## Architecture choice

This implementation uses **UTXO state + secp256k1 ECDSA + Proof of Work**. The supplied account/PBFT design is intentionally not mixed into the same state machine: PBFT and PoW have different finality, validator, and reward semantics.

- Integer base units; no floating-point money
- Canonical JSON transaction IDs
- Pure-Python secp256k1 signing with deterministic RFC6979 nonces and low-`s` signatures
- UTXO ownership, duplicate-input, conservation-of-value, and double-spend checks
- Atomic staged block application
- PoW blocks with Merkle roots and coinbase fee collection
- Dynamic difficulty at epoch boundaries
- Thread-safe mempool and chain state
- Newline-delimited JSON TCP peer transport for local demos

## Run

```bash
python3 -m aetherchain.demo
```

The package has no third-party runtime dependencies. The application repository's existing TypeScript site is unchanged.

## Test

```bash
python3 -m pytest -q
```

The P2P transport is deliberately a transport layer, not an unauthenticated fork-choice oracle. A production network should add peer authentication, rate limits, persistent storage, chain-work fork choice, and a formal wire-version scheme.
