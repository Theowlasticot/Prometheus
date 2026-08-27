import unittest
from utils.vehicle_manager import VehicleManager

class TestVehicleManager(unittest.TestCase):
    def setUp(self):
        self.vm = VehicleManager(code="us")

    def test_vehicle_manager_initialization(self):
        self.assertEqual(self.vm.code, "us")
        self.assertIsInstance(self.vm.index, dict)

    def test_get_valid_ids(self):
        # Type 1 Engine -> generic type id 0
        ids = self.vm.get_valid_ids("Type 1 Engine")
        self.assertIsInstance(ids, list)

    def test_get_ids_with_capability(self):
        water_ids = self.vm.get_ids_with_capability("WATER")
        self.assertIsInstance(water_ids, list)

    def test_is_trailer(self):
        res = self.vm.is_trailer(7)
        self.assertIsInstance(res, bool)

if __name__ == "__main__":
    unittest.main()
