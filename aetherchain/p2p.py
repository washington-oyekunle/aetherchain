"""Small threaded TCP peer used for local AetherChain demonstrations.

The protocol is newline-delimited JSON. Networking is deliberately separated from
consensus: every received transaction/block is validated by the blockchain before
it changes local state.
"""
from __future__ import annotations

import json
import socket
import threading
from typing import Any, Optional

from .blockchain import Block, Blockchain
from .ledger import TransactionInput, TransactionOutput, UTXOTransaction

HANDSHAKE = "HANDSHAKE"
GET_CHAIN = "GET_CHAIN"
CHAIN_RESPONSE = "CHAIN_RESPONSE"
NEW_TRANSACTION = "NEW_TRANSACTION"
NEW_BLOCK = "NEW_BLOCK"
MAX_FRAME_BYTES = 1_048_576
MESSAGE_TYPES = {HANDSHAKE, GET_CHAIN, CHAIN_RESPONSE, NEW_TRANSACTION, NEW_BLOCK}


class P2PNode:
    def __init__(self, host: str, port: int, blockchain: Blockchain):
        self.host, self.port, self.blockchain = host, port, blockchain
        self.peers: set[tuple[str, int]] = set()
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._running:
            return
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.host, self.port))
        self._server.listen(16)
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._server:
            self._server.close()
        self._server = None

    def connect(self, host: str, port: int) -> bool:
        if not self._running or (host, port) == (self.host, self.port):
            return False
        try:
            sock = socket.create_connection((host, port), timeout=2)
            self.peers.add((host, port))
            self._send(sock, HANDSHAKE, {"listen_port": self.port})
            self._send(sock, GET_CHAIN, {})
            threading.Thread(target=self._read_loop, args=(sock, (host, port)), daemon=True).start()
            return True
        except OSError:
            return False

    def broadcast_transaction(self, tx: UTXOTransaction) -> None:
        self._broadcast(NEW_TRANSACTION, self.serialize_tx(tx))

    def broadcast_block(self, block: Block) -> None:
        self._broadcast(NEW_BLOCK, self.serialize_block(block))

    def _accept_loop(self) -> None:
        assert self._server is not None
        while self._running:
            try:
                sock, address = self._server.accept()
                threading.Thread(target=self._read_loop, args=(sock, address), daemon=True).start()
            except OSError:
                break

    def _read_loop(self, sock: socket.socket, address: tuple[str, int]) -> None:
        buffer = ""
        try:
            while self._running:
                data = sock.recv(8192)
                if not data:
                    return
                buffer += data.decode()
                if len(buffer.encode()) > MAX_FRAME_BYTES:
                    return
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        if len(line.encode()) > MAX_FRAME_BYTES:
                            return
                        address = self._process(sock, json.loads(line), address)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return
        finally:
            sock.close()
            self.peers.discard(address)

    def _process(self, sock: socket.socket, message: dict[str, Any], address: tuple[str, int]) -> tuple[str, int]:
        kind, payload = message.get("type"), message.get("payload", {})
        if kind not in MESSAGE_TYPES or not isinstance(payload, dict):
            raise ValueError("unsupported or malformed peer message")
        if kind == HANDSHAKE:
            peer = (address[0], int(payload["listen_port"]))
            self.peers.add(peer)
            return peer
        if kind == GET_CHAIN:
            self._send(sock, CHAIN_RESPONSE, {"chain": [self.serialize_block(b) for b in self.blockchain.chain]})
        elif kind == CHAIN_RESPONSE:
            self._adopt_longer_chain(payload.get("chain", []))
            return address
        elif kind == NEW_TRANSACTION:
            tx = self.deserialize_tx(payload)
            self.blockchain.mempool.add_transaction(tx, self.blockchain.state)
        elif kind == NEW_BLOCK:
            self._accept_block(self.deserialize_block(payload))
        return address

    def _accept_block(self, block: Block) -> bool:
        with self.blockchain._lock:
            if block.index != len(self.blockchain.chain) or block.previous_hash != self.blockchain.latest_block().hash:
                return False
            valid, state, _ = self.blockchain.validate_chain(self.blockchain.chain + [block])
            if not valid or state is None:
                return False
            self.blockchain.state = state
            self.blockchain.chain.append(block)
            self.blockchain.mempool.remove_transactions(block.transactions[1:])
            return True

    def _adopt_longer_chain(self, raw_chain: list[dict[str, Any]]) -> bool:
        """Replay and adopt a strictly longer valid chain atomically."""
        with self.blockchain._lock:
            if len(raw_chain) <= len(self.blockchain.chain):
                return False
            try:
                candidate = [self.deserialize_block(item) for item in raw_chain]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                return False
            return self.blockchain.replace_chain(candidate)

    def _broadcast(self, kind: str, payload: dict[str, Any]) -> None:
        for host, port in list(self.peers):
            try:
                with socket.create_connection((host, port), timeout=1) as sock:
                    self._send(sock, kind, payload)
            except OSError:
                self.peers.discard((host, port))

    @staticmethod
    def _send(sock: socket.socket, kind: str, payload: dict[str, Any]) -> None:
        if kind not in MESSAGE_TYPES:
            raise ValueError("unsupported peer message")
        encoded = (json.dumps({"type": kind, "payload": payload}, separators=(",", ":")) + "\n").encode()
        if len(encoded) > MAX_FRAME_BYTES:
            raise ValueError("peer message exceeds frame limit")
        sock.sendall(encoded)

    @staticmethod
    def serialize_tx(tx: UTXOTransaction) -> dict[str, Any]:
        return {"sender": tx.sender, "inputs": [{"tx_id": i.tx_id, "output_index": i.output_index} for i in tx.inputs],
                "outputs": [{"recipient": o.recipient, "amount": o.amount} for o in tx.outputs],
                "pubkey": list(tx.sender_pubkey) if tx.sender_pubkey else None,
                "signature": list(tx.signature) if tx.signature else None}

    @staticmethod
    def deserialize_tx(data: dict[str, Any]) -> UTXOTransaction:
        tx = UTXOTransaction([TransactionInput(i["tx_id"], int(i["output_index"])) for i in data["inputs"]],
                             [TransactionOutput(o["recipient"], int(o["amount"])) for o in data["outputs"]], data["sender"])
        if data.get("pubkey"):
            tx.sender_pubkey = (int(data["pubkey"][0]), int(data["pubkey"][1]))
        if data.get("signature"):
            tx.signature = (int(data["signature"][0]), int(data["signature"][1]))
        return tx

    @classmethod
    def serialize_block(cls, block: Block) -> dict[str, Any]:
        return {"index": block.index, "previous_hash": block.previous_hash, "difficulty": block.difficulty,
                "timestamp": block.timestamp, "state_root": block.state_root, "nonce": block.nonce,
                "hash": block.hash,
                "transactions": [cls.serialize_tx(t) for t in block.transactions]}

    @classmethod
    def deserialize_block(cls, data: dict[str, Any]) -> Block:
        block = Block(int(data["index"]), data["previous_hash"], [cls.deserialize_tx(t) for t in data["transactions"]],
                      int(data["difficulty"]), float(data["timestamp"]), data["state_root"], int(data["nonce"]))
        block.hash = block.calculate_hash()
        if data.get("hash") not in (None, block.hash):
            raise ValueError("serialized block hash mismatch")
        return block
