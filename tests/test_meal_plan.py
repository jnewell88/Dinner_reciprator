import unittest
from unittest import mock

import api
from tests.test_product_search import FakeClient, product


class CombineAmountTests(unittest.TestCase):
    def test_same_dimension_summed(self):
        self.assertEqual(api.combine_plan_amounts(["1 lb", "8 oz"], "ground beef"), "680.4 g")
        self.assertEqual(api.combine_plan_amounts(["1 c.", "2 tbsp."], "flour"), "266.2 ml")

    def test_packages_summed(self):
        self.assertEqual(api.combine_plan_amounts(["1 can", "2 cans"], "pinto beans"), "3 packages")

    def test_mixed_listed(self):
        self.assertEqual(api.combine_plan_amounts(["2 cloves", "1 tbsp."], "garlic"), "2 cloves + 1 tbsp.")

    def test_single_and_empty(self):
        self.assertEqual(api.combine_plan_amounts(["", "2"], "onion"), "2")
        self.assertEqual(api.combine_plan_amounts(["", ""], "onion"), "")


class PlanItemTests(unittest.TestCase):
    recipes = [
        {"id": 1, "title": "Chili", "ingredients": [
            {"name": "ground beef", "amount": "1 lb"},
            {"name": "onion, diced", "amount": "1"},
            {"name": "kosher salt", "amount": "1 tsp"},
        ]},
        {"id": 2, "title": "Tacos", "ingredients": [
            {"name": "ground beef", "amount": "8 oz"},
            {"name": "diced onion", "amount": "1"},
        ]},
    ]

    def test_merges_across_meals_and_skips_seasonings(self):
        items, skipped = api.plan_items(self.recipes, skip_pantry_seasonings=True)
        by_name = {item["name"]: item for item in items}
        self.assertEqual(set(by_name), {"ground beef", "onion, diced"})
        self.assertEqual(by_name["ground beef"]["meals"], ["Chili", "Tacos"])
        self.assertEqual(by_name["ground beef"]["amount"], "680.4 g")
        self.assertEqual(by_name["onion, diced"]["amount_text"], "1 + 1")
        self.assertEqual([s["name"] for s in skipped], ["kosher salt"])

    def test_seasonings_kept_when_not_skipping(self):
        items, skipped = api.plan_items(self.recipes, skip_pantry_seasonings=False)
        self.assertIn("kosher salt", [item["name"] for item in items])
        self.assertEqual(skipped, [])


class ExclusionFilterTests(unittest.TestCase):
    def test_exclude_terms_whole_word(self):
        filters = api.ProductFilters(exclude_terms=["Great Value", "light"])
        self.assertTrue(api.is_excluded_product(product("Light Sour Cream", "1"), filters))
        self.assertFalse(api.is_excluded_product(product("Lightly Salted Butter", "2"), filters))
        self.assertTrue(api.is_excluded_product(product("Salsa", "3", brand="Great Value"), filters))

    def test_max_package_price(self):
        filters = api.ProductFilters(max_package_price=3.0)
        self.assertTrue(api.is_excluded_product(product("Steak", "1", price=12.0), filters))
        self.assertFalse(api.is_excluded_product(product("Onion", "2", price=1.0), filters))

    def test_search_applies_exclusions(self):
        client = FakeClient({"sour cream": [product("Light Sour Cream", "l", price=1.0),
                                            product("Sour Cream", "s", price=2.0)]})
        products, _ = api.search_ingredient_products(
            client, "sour cream", "1 c.", "S1", api.ProductFilters(exclude_terms=["light"])
        )
        self.assertEqual([p["upc"] for p in products], ["s"])


class PlanEndpointTests(unittest.TestCase):
    def test_report_splits_matched_unmatched_skipped(self):
        catalog = {"ground beef": [product("Ground Beef 80/20", "b", size="1 lb", price=5.0)]}
        client = FakeClient(catalog)
        rows = [{"id": r["id"], "title": r["title"], "ingredients": r["ingredients"]} for r in PlanItemTests.recipes]
        with mock.patch.object(api, "KrogerAPI", return_value=client), \
             mock.patch.object(api, "load_saved_recipes", return_value=rows):
            report = api.build_meal_plan(api.MealPlanRequest(recipe_ids=[1, 2], store_id="S1"))
        self.assertEqual([m["name"] for m in report["matched"]], ["ground beef"])
        self.assertEqual([u["name"] for u in report["unmatched"]], ["onion, diced"])
        self.assertEqual([s["name"] for s in report["skipped_seasonings"]], ["kosher salt"])
        self.assertAlmostEqual(report["estimated_total"], 10.0)


if __name__ == "__main__":
    unittest.main()
