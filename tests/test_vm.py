import unittest
import tempfile
from pathlib import Path

from aetherchain import Blockchain, ContractOperation, ContractStore, RPCNode, UTXOWallet, VM, VMError, load_chain_sqlite, save_chain_sqlite
from aetherchain.p2p import P2PNode


class VMTests(unittest.TestCase):
    def test_arithmetic_and_return_are_deterministic(self):
        # PUSH1 2, PUSH1 3, ADD, PUSH1 0, MSTORE, PUSH1 32, PUSH1 0, RETURN
        code = bytes.fromhex("600260030160005260206000f3")
        first = VM().execute(code)
        second = VM().execute(code)
        self.assertTrue(first.success)
        self.assertEqual(first.return_data, (5).to_bytes(32, "big"))
        self.assertEqual(first.return_data, second.return_data)
        self.assertEqual(first.gas_used, second.gas_used)

    def test_storage_and_revert_are_atomic(self):
        store = ContractStore()
        # PUSH1 7, PUSH1 1, SSTORE, STOP
        contract, deployed = store.deploy("AETHcreator", bytes.fromhex("600760155500"))
        self.assertTrue(deployed.success)
        assert contract is not None
        self.assertEqual(contract.storage[0x15], 7)
        # Inject an invalid runtime opcode; failure must not change existing storage.
        contract.code = bytes.fromhex("6008601555fe")
        result = store.call(contract.address, gas_limit=10_000, calldata=b"")
        self.assertFalse(result.success)
        self.assertEqual(store.get(contract.address).storage[0x15], 7)

    def test_out_of_gas_and_invalid_jump_fail(self):
        self.assertFalse(VM(1).execute(bytes.fromhex("6001600101")).success)
        self.assertFalse(VM().execute(bytes.fromhex("600156")).success)

    def test_contract_addresses_and_rpc(self):
        store = ContractStore()
        contract, result = store.deploy("AETHcreator", "600060005260206000f3")
        self.assertTrue(result.success)
        assert contract is not None
        again, _ = store.deploy("AETHcreator", "600060005260206000f3")
        self.assertNotEqual(contract.address, again.address)
        rpc = RPCNode(Blockchain(difficulty=0), contracts=store)
        response, error = rpc.handle({"jsonrpc": "2.0", "id": 1, "method": "aether_contractGet", "params": [contract.address]})
        self.assertIsNone(error)
        self.assertEqual(response["address"], contract.address)
        response, error = rpc.handle({"id": 2, "method": "aether_vmExecute", "params": ["6002600301"]})
        self.assertIsNone(error)
        self.assertTrue(response["success"])

    def test_signed_operations_are_consensus_replayed(self):
        chain = Blockchain(difficulty=0)
        wallet = UTXOWallet()
        deploy = ContractOperation("deploy", wallet.address, 0, code=bytes.fromhex("600060005260206000f3"))
        deploy.sign(wallet)
        self.assertTrue(chain.contract_mempool.add_operation(deploy, chain.contracts))
        block = chain.mine_pending_transactions(wallet.address)
        self.assertIsNotNone(block)
        self.assertTrue(chain.is_chain_valid())
        self.assertEqual(chain.contracts.state_root(), block.contract_root)
        round_trip = P2PNode.deserialize_block(P2PNode.serialize_block(block))
        self.assertEqual(round_trip.contract_operations[0].tx_id, deploy.tx_id)
        call = ContractOperation("call", wallet.address, 1, address=next(iter(chain.contracts.contracts)))
        call.sign(wallet)
        self.assertTrue(chain.contract_mempool.add_operation(call, chain.contracts))

        bad_nonce = ContractOperation("call", wallet.address, 1, address=next(iter(chain.contracts.contracts)))
        bad_nonce.sign(wallet)
        self.assertFalse(chain.contract_mempool.add_operation(bad_nonce, chain.contracts))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chain.sqlite3"
            save_chain_sqlite(chain, path)
            restored = load_chain_sqlite(path)
            self.assertTrue(restored.is_chain_valid())
            self.assertEqual(restored.contracts.state_root(), chain.contracts.state_root())


if __name__ == "__main__":
    unittest.main()
