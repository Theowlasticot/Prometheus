import unittest
from utils.building_data import has_expansion, load_building_data

class TestBuildingData(unittest.TestCase):
    def test_has_expansion_empty(self):
        bdata = {
            "123": {"type": "Fire Station", "expansions": ["HazMat Extension"]}
        }
        self.assertTrue(has_expansion(bdata, ""))
        self.assertTrue(has_expansion(bdata, "hazmat"))
        self.assertFalse(has_expansion(bdata, "airport"))

    def test_load_building_data(self):
        data = load_building_data()
        self.assertIsInstance(data, dict)

if __name__ == "__main__":
    unittest.main()
