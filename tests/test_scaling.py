import unittest

import api


class ScaleAmountTests(unittest.TestCase):
    def test_scaling(self):
        cases = {
            ("1 1/2 cups", 2): "3 cups",
            ("¾ cup", 2): "1 1/2 cup",
            ("1/3 c.", 4): "1 1/3 c.",
            ("2 ½ cups", 2): "5 cups",
            ("2 (9-inch)", 2): "4 (9-inch)",
            ("¼ tsp + ¼ tsp", 4): "1 tsp + 1 tsp",
            ("Pinch", 4): "Pinch",
            ("", 2): "",
            ("8 oz.", 1): "8 oz.",
        }
        for (amount, factor), expected in cases.items():
            self.assertEqual(api.scale_amount(amount, factor), expected, (amount, factor))


class YieldTests(unittest.TestCase):
    def test_normalize_yield(self):
        self.assertEqual(api.normalize_recipe_yield("4"), "4 servings")
        self.assertEqual(api.normalize_recipe_yield(["6", "6 servings"]), "6 servings")
        self.assertEqual(api.normalize_recipe_yield(8), "8 servings")
        self.assertEqual(api.normalize_recipe_yield(None), "")

    def test_scale_servings(self):
        self.assertEqual(api.scale_servings("Serves 4-6", 2), "Serves 8-12")
        self.assertEqual(api.scale_servings("4 servings", 1), "4 servings")


if __name__ == "__main__":
    unittest.main()
