import json
import tempfile
import unittest
from pathlib import Path

from aetherchain import Blockchain, Node, NodeConfig, UTXOWallet, load_chain_sqlite, load_keystore, save_chain_sqlite, save_keystore
from aetherchain.cli import main


class AdoptionTests(unittest.TestCase):
    def test_keystore_round_trip_and_wrong_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wallet.json"
            wallet = UTXOWallet()
            save_keystore(wallet, path, "correct horse")
            restored = load_keystore(path, "correct horse")
            self.assertEqual(restored.address, wallet.address)
            with self.assertRaises(ValueError):
                load_keystore(path, "wrong password")

    def test_sqlite_restart_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chain.sqlite3"
            chain = Blockchain(difficulty=0, block_reward=100)
            miner = UTXOWallet()
            chain.mine_pending_transactions(miner.address)
            save_chain_sqlite(chain, path)
            restored = load_chain_sqlite(path)
            self.assertTrue(restored.is_chain_valid())
            self.assertEqual(restored.stats(), chain.stats())

    def test_config_and_node_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "aether.toml"
            data_dir = Path(directory) / "data"
            config = NodeConfig(data_dir=data_dir, difficulty=0, block_reward=100, p2p_port=0, rpc_port=0)
            config.save(config_path)
            loaded = NodeConfig.load(config_path)
            node = Node.from_config(loaded)
            miner = UTXOWallet()
            node.mine_once(miner.address)
            self.assertTrue((data_dir / "chain.sqlite3").exists())
            restarted = Node.from_config(loaded)
            self.assertEqual(len(restarted.blockchain.chain), 2)

    def test_cli_config_and_wallet_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "aether.toml"
            wallet_path = Path(directory) / "wallet.json"
            self.assertEqual(main(["--config", str(config_path), "config-init"]), 0)
            self.assertTrue(config_path.exists())
            self.assertEqual(main(["wallet", "create", "--output", str(wallet_path), "--password", "test-pass"]), 0)
            self.assertTrue(json.loads(wallet_path.read_text())["address"].startswith("AETH"))
            self.assertEqual(main(["wallet", "show", str(wallet_path), "--password", "test-pass"]), 0)


if __name__ == "__main__":
    unittest.main()
