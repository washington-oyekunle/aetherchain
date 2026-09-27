# AetherChain

A zero-dependency Python implementation of a compact, auditable blockchain node assembled from the supplied components.

## Protocol design

AetherChain deliberately selects one coherent branch: **UTXO state + secp256k1 ECDSA + Proof of Work**. The earlier account/PBFT ideas are not mixed into the same state machine because they have different transaction, finality, validator, and reward semantics.

### Included

- Integer base units; floating-point monetary values are rejected
- Canonical transaction IDs and deterministic RFC6979 ECDSA signatures
- Public-key ownership checks and low-`s` signature normalization
- UTXO conservation, duplicate-input, ownership, replay, and double-spend protection
- Deterministic `state_root` commitment in every block header
- Merkle transaction roots and PoW validation
- Atomic staged block application; invalid blocks cannot partially mutate state
- Fee collection with strict coinbase subsidy-plus-fees enforcement
- Historical difficulty validation and cumulative-work fork choice
- Thread-safe mempool and chain state
- Newline-delimited JSON TCP P2P transport with validated block/chain admission
- JSON-RPC 2.0 HTTP node interface
- Atomic JSON snapshots with validated restore
- Standard-library-only runtime dependencies

## Run the demo

```bash
python3 -m aetherchain.demo
```

## Run tests

```bash
python3 -m unittest discover -s tests -v
```

## JSON-RPC

```python
from aetherchain import Blockchain, RPCNode

rpc = RPCNode(Blockchain(difficulty=1), host="127.0.0.1", port=8545)
rpc.start()
```

Supported methods include `eth_blockNumber`, `eth_getBalance`, `eth_getBlockByNumber`,
`eth_sendRawTransaction`, `aether_getStateRoot`, and `aether_getMempool`.

## Snapshots

```python
from aetherchain import load_chain, save_chain
save_chain(blockchain, "data/chain.json")
blockchain = load_chain("data/chain.json")
```

The P2P layer is intentionally a transport and admission layer, not a complete internet-scale network. Production deployment should add peer authentication, encrypted transport, rate limits, persistent append-only storage, chain-download pagination, and a finalized consensus protocol.
