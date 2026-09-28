# AetherChain API Reference

AetherChain is a zero-dependency Python node built around **UTXO state**, **secp256k1 ECDSA**, **Proof of Work**, a thread-safe mempool, newline-delimited TCP peers, JSON-RPC, and validated JSON snapshots.

> **Scope:** This reference documents the current implementation in `aetherchain/`. It is an educational and development node, not a production custody or consensus library.

## Quick start

```python
from aetherchain import Blockchain, RPCNode, UTXOWallet

chain = Blockchain(difficulty=1, block_reward=5_000)
miner = UTXOWallet()
chain.mine_pending_transactions(miner.address)

rpc = RPCNode(chain, host="127.0.0.1", port=8545)
rpc.start()
```

Run the built-in example:

```bash
python3 -m aetherchain.demo
```

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

## Units and invariants

- Monetary values are **integer base units**. Floats are rejected.
- Addresses are `AETH` plus the first 36 hexadecimal characters of SHA-256 over an uncompressed public key.
- A transaction ID is SHA-256 over canonical JSON of `sender`, ordered `inputs`, and ordered `outputs`.
- A block commits both a transaction Merkle root and a deterministic UTXO `state_root`.
- Every non-genesis block contains exactly one first-position coinbase transaction.
- Coinbase value may not exceed the configured subsidy plus included transaction fees.
- Block admission is atomic: failed validation leaves the state unchanged.

## Package exports

`aetherchain.__init__` exports:

| Symbol | Module | Purpose |
|---|---|---|
| `Block` | `blockchain` | PoW block data structure and header hashing |
| `Blockchain` | `blockchain` | Chain, mining, validation, difficulty, and fork choice |
| `Mempool` | `blockchain` | Thread-safe pending transaction pool |
| `Node` | `node` | Unified blockchain, RPC, P2P, mining, and snapshot lifecycle |
| `Wallet` | `crypto` | secp256k1 keypair and address |
| `UTXOWallet` | `ledger` | Wallet with UTXO selection and change creation |
| `UTXOState` | `ledger` | Live UTXO set and state-root computation |
| `UTXOTransaction` | `ledger` | Signed transfer or system coinbase transaction |
| `TransactionInput` | `ledger` | Reference to a prior output |
| `TransactionOutput` | `ledger` | New spendable output |
| `UTXO` | `ledger` | A live unspent output |
| `P2PNode` | `p2p` | TCP transport and validated chain/block admission |
| `RPCNode` | `rpc` | JSON-RPC 2.0 HTTP server |
| `save_chain`, `load_chain` | `storage` | Atomic JSON snapshot persistence |

## `crypto` API

### `Wallet()`

Creates a random private key in `[1, N-1]`, derives the secp256k1 public point, and exposes:

```python
wallet.private_key  # int; keep secret
wallet.public_key   # tuple[int, int]
wallet.address      # str, e.g. AETH...
```

### `generate_keypair() -> tuple[int, Point]`

Returns a cryptographically random private key and its public point. `Point` is `None` for infinity or `(x, y)` for a curve point.

### `sign_hash(private_key: int, digest: bytes) -> tuple[int, int]`

Signs a digest using deterministic RFC6979 nonces and low-`s` normalization. Raises `ValueError` for an invalid private key.

### `verify_signature(public_key, digest, signature) -> bool`

Verifies `(r, s)` against a secp256k1 public point. Invalid points, malformed ranges, and high-`s` signatures return `False`.

### `point_add`, `point_mul`, `is_on_curve`

Low-level elliptic-curve primitives. `point_mul(k, point)` rejects negative scalars and points outside secp256k1.

## `ledger` API

### `TransactionInput(tx_id: str, output_index: int)`

References the output at `tx_id:output_index`.

- `utxo_id -> str`: canonical lookup key.

### `TransactionOutput(recipient: str, amount: int)`

Defines a new output. Validation requires a non-empty recipient and a positive integer amount.

### `UTXO(tx_id, output_index, recipient, amount)`

Immutable representation of a live output.

- `id -> str`: canonical `tx_id:output_index` key.

### `UTXOTransaction(inputs, outputs, sender)`

Creates an unsigned transaction. The transaction ID is computed immediately from the canonical payload.

```python
tx = UTXOTransaction(inputs, outputs, sender=wallet.address)
tx.sign(wallet)
valid, fee, reason = tx.validate(state.utxo_pool)
```

Methods:

- `calculate_hash() -> bytes`: canonical SHA-256 digest.
- `calculate_hash_hex() -> str`: transaction ID.
- `sign(wallet) -> None`: attaches the public key and signature; rejects a sender mismatch.
- `is_coinbase() -> bool`: true when `sender == "SYSTEM"`.
- `validate(pool, max_coinbase=None) -> tuple[bool, int, str]`: returns validity, computed fee, and a human-readable reason.

### `UTXOState(pool=None)`

Maintains `utxo_pool: dict[str, UTXO]`.

- `clone() -> UTXOState`: shallow-copy immutable UTXO state for staging.
- `get_balance(address) -> int`: sum live outputs owned by an address.
- `get_user_utxos(address) -> list[UTXO]`: deterministic ID-sorted selection candidates.
- `state_root() -> str`: SHA-256 commitment over sorted `id|recipient|amount` leaves.
- `apply(tx, max_coinbase=None) -> int`: validate and apply one transaction; returns its fee or raises `ValueError`.
- `apply_block(transactions, max_coinbase) -> int`: validates and atomically applies a complete block; returns total fees.

### `UTXOWallet.create_transaction(recipient, amount, fee, state)`

Selects deterministic UTXOs until `amount + fee` is covered, creates payment and change outputs, signs the transaction, and returns it. Raises `ValueError` for invalid amounts or insufficient funds.

## `blockchain` API

### `Block`

```python
Block(index, previous_hash, transactions, difficulty, timestamp,
      state_root="", nonce=0, hash="")
```

Properties and methods:

- `merkle_root -> str`: duplicate-last-node Merkle construction over transaction IDs.
- `header() -> dict`: canonical header fields.
- `calculate_hash() -> str`: SHA-256 of canonical header JSON.
- `mine() -> None`: increments `nonce` until the hash begins with `"0" * difficulty`.

### `Blockchain(...)`

Constructor defaults:

```python
Blockchain(
    difficulty=1,
    block_reward=500_000,
    target_block_time=2.0,
    adjustment_interval=5,
    genesis_allocation=1_000_000,
                 genesis_address="AETH_GENESIS_RESERVE",
                 max_block_transactions=10,
)
```

Public state:

- `chain: list[Block]`
- `state: UTXOState`
- `mempool: Mempool`
- `difficulty: int`
- `initial_difficulty`, `block_reward`, `target_block_time`, `adjustment_interval`

Methods:

- `latest_block() -> Block`
- `get_block(index) -> Block | None`
- `block_work(difficulty) -> int`: `16 ** difficulty` work estimate.
- `chain_work(chain=None) -> int`: cumulative work used by fork choice.
- `expected_difficulty(next_index, previous_chain=None) -> int`: historical epoch-based difficulty.
- `next_difficulty() -> int`: difficulty for the next locally mined block.
- `mine_pending_transactions(miner_address, max_transactions=10) -> Block | None`: selects valid mempool transactions, collects fees, creates coinbase, commits the state root, mines, and removes included transactions.
- `get_transaction(tx_id) -> UTXOTransaction | None`: searches pending and confirmed transactions.
- `stats() -> dict`: returns height, difficulty, cumulative work, state root, UTXO count, and mempool size.
- `validate_chain(candidate) -> tuple[bool, UTXOState | None, str]`: replay-validates links, hashes, PoW, difficulty, state transitions, roots, and rewards.
- `replace_chain(candidate) -> bool`: adopts only a valid candidate with strictly greater cumulative work.
- `is_chain_valid() -> bool`: validates the current chain and compares replayed state to live state.

### `Mempool`

```python
Mempool(max_size=10_000)
```

- `add_transaction(tx, state) -> bool`: admits a valid, non-conflicting transaction.
- `get_batch(limit=10) -> list[UTXOTransaction]`
- `remove_transactions(transactions) -> None`
- `clear() -> None`

All operations are protected by an `RLock`.

`max_block_transactions` bounds the number of non-coinbase transactions selected for a block.

## `node` API

### `Node(blockchain=None, host="127.0.0.1", p2p_port=5001, rpc_port=8545)`

Provides one lifecycle object for local operation:

```python
node = Node(Blockchain(difficulty=1), p2p_port=5001, rpc_port=8545)
node.start()
node.connect_peer("127.0.0.1", 5002)
node.mine_once(miner.address)
node.snapshot("snapshots/node.json")
node.stop()
```

- `running -> bool`
- `start()` starts P2P then RPC and rolls back P2P if RPC startup fails.
- `stop()` shuts down both services.
- `connect_peer(host, port) -> bool`
- `mine_once(miner_address, max_transactions=None) -> Block | None`: mines and broadcasts a block.
- `snapshot(path) -> None`

## `p2p` API

### `P2PNode(blockchain, host, port)`

```python
node = P2PNode("127.0.0.1", 5001, chain)
node.start()
# node.connect("127.0.0.1", 5002)
node.stop()
```

The wire format is one JSON object per newline:

```json
{"type":"NEW_TRANSACTION","payload":{...}}
```

Message types:

- `HANDSHAKE`: announces the peer’s listening port.
- `GET_CHAIN`: requests serialized chain data.
- `CHAIN_RESPONSE`: replay-validates and may adopt a higher-work chain.
- `NEW_TRANSACTION`: validates before mempool admission.
- `NEW_BLOCK`: validates against the current chain before appending.

Frames are limited to 1 MiB and unknown message types or non-object payloads are rejected. Serialized blocks include their hash and deserialization verifies it.

Serialization helpers:

- `serialize_tx` / `deserialize_tx`
- `serialize_block` / `deserialize_block`

The transport has no peer authentication or encryption and should be bound to localhost for development.

## `rpc` API

### `RPCNode(blockchain, host="127.0.0.1", port=8545)`

- `start() -> None`: launches a daemon `ThreadingHTTPServer`.
- `stop() -> None`: shuts it down cleanly.
- `handle(request) -> tuple[result, error]`: direct in-process dispatcher useful for tests.

### JSON-RPC methods

Requests use JSON-RPC 2.0:

```json
{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}
```

| Method | Parameters | Result |
|---|---|---|
| `eth_blockNumber` | `[]` | Hex chain height, e.g. `"0x3"` |
| `aether_blockNumber` | `[]` | Same as above |
| `eth_getBalance` | `[address]` | Hex integer balance |
| `aether_getStateRoot` | `[]` | Current state-root hex string |
| `aether_getChainStats` | `[]` | Height, difficulty, work, root, UTXO count, mempool size |
| `aether_getUtxos` | `[address]` | Live UTXO records owned by an address |
| `aether_getMempool` | `[]` | Transaction ID list |
| `aether_getTransactionByHash` | `[tx_id]` | Serialized pending or confirmed transaction |
| `eth_getBlockByNumber` | `[number]` | Serialized block or `null` |
| `aether_getBlockByNumber` | `[number]` | Same as above |
| `eth_sendRawTransaction` | `[serialized_tx_object]` | Transaction ID or error |
| `aether_sendTransaction` | `[serialized_tx_object]` | Same as above |

RPC errors use `-32601` for unknown methods, `-32602` for malformed parameters, and `-32000` for transaction rejection.

`net_version` returns `"1"`, `web3_clientVersion` returns `"AetherChain/0.2"`, and `GET /health` returns `{"status":"ok"}` for process probes.

## `storage` API

### `save_chain(blockchain, path) -> None`

Writes a versioned JSON snapshot through a temporary file, flush + `fsync`, and `os.replace` for atomic replacement. Parent directories are created automatically.

### `load_chain(path) -> Blockchain`

Recreates the configured chain, deserializes all blocks, validates the complete replay, and returns a restored node. Invalid versions or state raise `ValueError`.

## Error handling and operational guidance

- Treat all network payloads as untrusted and expect `ValueError`/malformed JSON at boundaries.
- Never expose `Wallet.private_key` through RPC or P2P serialization.
- Use a real constant-time cryptographic library, authenticated peer transport, access control, rate limiting, and durable append-only storage before handling real value.
- The current PoW target is represented by leading hexadecimal zeroes; difficulty/work is educational rather than Bitcoin-compatible.
