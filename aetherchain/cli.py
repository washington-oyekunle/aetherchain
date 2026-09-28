"""Command-line interface for AetherChain developer nodes."""
from __future__ import annotations

import argparse
import getpass
import json
import signal
import sys
import time
from pathlib import Path

from .config import NodeConfig
from .crypto import Wallet
from .database import load_chain_sqlite
from .keystore import load_keystore, save_keystore
from .node import Node


def _config(args: argparse.Namespace) -> NodeConfig:
    return NodeConfig.load(args.config)


def cmd_config_init(args: argparse.Namespace) -> int:
    config = NodeConfig()
    config.save(args.config)
    print(f"Wrote {args.config}")
    return 0


def cmd_wallet_create(args: argparse.Namespace) -> int:
    wallet = Wallet()
    password = args.password or getpass.getpass("Keystore password: ")
    save_keystore(wallet, args.output, password)
    print(json.dumps({"address": wallet.address, "keystore": str(args.output)}, indent=2))
    return 0


def cmd_wallet_show(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Keystore password: ")
    wallet = load_keystore(args.keystore, password)
    print(wallet.address)
    return 0


def _node_from_config(args: argparse.Namespace) -> Node:
    config = _config(args)
    return Node.from_config(config)


def cmd_node_status(args: argparse.Namespace) -> int:
    config = _config(args)
    database = config.data_dir / "chain.sqlite3"
    if not database.exists():
        print(json.dumps({"status": "not_initialized", "data_dir": str(config.data_dir)}, indent=2))
        return 0
    chain = load_chain_sqlite(database)
    print(json.dumps(chain.stats() | {"status": "ok", "data_dir": str(config.data_dir)}, indent=2))
    return 0


def cmd_node_validate(args: argparse.Namespace) -> int:
    config = _config(args)
    database = config.data_dir / "chain.sqlite3"
    chain = load_chain_sqlite(database)
    print(json.dumps({"valid": chain.is_chain_valid(), "height": len(chain.chain) - 1}, indent=2))
    return 0


def cmd_mine(args: argparse.Namespace) -> int:
    node = _node_from_config(args)
    block = node.mine_once(args.address)
    if block is None:
        print("No block mined", file=sys.stderr)
        return 1
    print(json.dumps({"height": block.index, "hash": block.hash, "state_root": block.state_root}, indent=2))
    return 0


def cmd_node_start(args: argparse.Namespace) -> int:
    node = _node_from_config(args)
    node.start()
    print(f"AetherChain running: RPC {node.rpc.host}:{node.rpc.port}, P2P {node.p2p.host}:{node.p2p.port}")
    stop = False
    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        while not stop:
            time.sleep(0.25)
    finally:
        node.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aether", description="AetherChain developer-preview node tools")
    parser.add_argument("--config", type=Path, default=Path("aether.toml"), help="TOML configuration path")
    sub = parser.add_subparsers(dest="command", required=True)
    config = sub.add_parser("config-init", help="write a starter TOML configuration")
    config.set_defaults(func=cmd_config_init)
    wallet = sub.add_parser("wallet", help="manage password-protected developer wallets")
    wallet_sub = wallet.add_subparsers(dest="wallet_command", required=True)
    create = wallet_sub.add_parser("create")
    create.add_argument("--output", type=Path, default=Path("wallet.json"))
    create.add_argument("--password")
    create.set_defaults(func=cmd_wallet_create)
    show = wallet_sub.add_parser("show")
    show.add_argument("keystore", type=Path)
    show.add_argument("--password")
    show.set_defaults(func=cmd_wallet_show)
    node = sub.add_parser("node", help="inspect or run a node")
    node_sub = node.add_subparsers(dest="node_command", required=True)
    for name, func in (("status", cmd_node_status), ("validate", cmd_node_validate), ("start", cmd_node_start)):
        command = node_sub.add_parser(name)
        command.set_defaults(func=func)
    mine = sub.add_parser("mine", help="mine one block using a wallet address")
    mine.add_argument("address")
    mine.set_defaults(func=cmd_mine)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
