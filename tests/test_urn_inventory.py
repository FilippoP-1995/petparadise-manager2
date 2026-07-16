import unittest

from urn_inventory import DEFAULT_URNS


class UrnInventoryTests(unittest.TestCase):
    def test_default_urns_have_valid_structure(self):
        self.assertTrue(DEFAULT_URNS)
        for name, material, quantity, price in DEFAULT_URNS:
            self.assertIsInstance(name, str)
            self.assertTrue(name.strip())
            self.assertIsInstance(material, str)
            self.assertTrue(material.strip())
            self.assertIsInstance(quantity, int)
            self.assertGreaterEqual(quantity, 0)
            self.assertIsInstance(price, str)
            self.assertGreater(float(price), 0)

    def test_default_urns_have_no_duplicate_names(self):
        names = [name for name, *_ in DEFAULT_URNS]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
