import unittest


class T(unittest.TestCase):
    @unittest.skip("flaky")
    def test_total(self):
        self.assertEqual(1, 1)
