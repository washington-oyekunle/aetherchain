"""Minimal JSON-RPC 2.0 HTTP interface for local AetherChain nodes."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from .blockchain import Blockchain
from .p2p import P2PNode


def quantity(value: int) -> str:
    return hex(value)


class RPCNode:
    def __init__(self, blockchain: Blockchain, host: str = "127.0.0.1", port: int = 8545):
        self.blockchain, self.host, self.port = blockchain, host, port
        self.server: ThreadingHTTPServer | None = None
        self.thread: Thread | None = None

    def start(self) -> None:
        if self.server:
            return
        node = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path != "/health":
                    self.send_error(404)
                    return
                encoded = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def do_POST(self) -> None:  # noqa: N802
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    request = json.loads(self.rfile.read(length).decode("utf-8"))
                    result, error = node.handle(request)
                    body = {"jsonrpc": "2.0", "id": request.get("id"), "result": result} if error is None else {
                        "jsonrpc": "2.0", "id": request.get("id"), "error": error}
                    encoded = json.dumps(body, separators=(",", ":")).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                    self.send_error(400, str(exc))

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.server = None
        self.thread = None

    def handle(self, request: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
        method, params = request.get("method"), request.get("params", [])
        try:
            if request.get("jsonrpc") not in (None, "2.0") or not isinstance(method, str) or not isinstance(params, list):
                return None, {"code": -32600, "message": "invalid JSON-RPC request"}
            if method in {"eth_blockNumber", "aether_blockNumber"}:
                return quantity(len(self.blockchain.chain) - 1), None
            if method == "net_version":
                return "1", None
            if method == "web3_clientVersion":
                return "AetherChain/0.2", None
            if method == "eth_getBalance":
                address = params[0]
                return quantity(self.blockchain.state.get_balance(address)), None
            if method == "aether_getStateRoot":
                return self.blockchain.state.state_root(), None
            if method == "aether_getChainStats":
                return self.blockchain.stats(), None
            if method == "aether_getUtxos":
                address = params[0]
                return [{"id": u.id, "tx_id": u.tx_id, "output_index": u.output_index,
                         "recipient": u.recipient, "amount": u.amount}
                        for u in self.blockchain.state.get_user_utxos(address)], None
            if method == "aether_getMempool":
                return list(self.blockchain.mempool.pending_transactions), None
            if method in {"aether_getBlockByNumber", "eth_getBlockByNumber"}:
                index = int(params[0], 16) if isinstance(params[0], str) else int(params[0])
                block = self.blockchain.get_block(index)
                return P2PNode.serialize_block(block) if block else None, None
            if method == "aether_getTransactionByHash":
                tx = self.blockchain.get_transaction(params[0])
                if tx is None:
                    return None, None
                return P2PNode.serialize_tx(tx) | {"tx_id": tx.tx_id}, None
            if method in {"eth_sendRawTransaction", "aether_sendTransaction"}:
                tx = P2PNode.deserialize_tx(params[0])
                if not self.blockchain.mempool.add_transaction(tx, self.blockchain.state):
                    return None, {"code": -32000, "message": "transaction rejected"}
                return tx.tx_id, None
            return None, {"code": -32601, "message": "method not found"}
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            return None, {"code": -32602, "message": str(exc)}
