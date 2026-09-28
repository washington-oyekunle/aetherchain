import unittest

from aetherchain import Blockchain, ContractStore, RPCNode, VM, VMError


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


if __name__ == "__main__":
    unittest.main()
