import unittest
from unittest import mock

from fastapi import HTTPException

import api
from src.secure_credentials import SecureCredentialError


def product(description, upc, size="8 oz", price=3.0, brand="Kroger", **extra):
    return {
        "description": description,
        "upc": upc,
        "brand": brand,
        "items": [{"size": size, "price": {"regular": price}}],
        **extra,
    }


class FakeClient:
    def __init__(self, catalog):
        self.catalog = catalog
        self.calls = []

    def authenticate_client(self):
        return "token"

    def search_product(self, term, location_id, limit=5, brand=None):
        self.calls.append({"term": term, "location_id": location_id, "brand": brand, "limit": limit})
        return list(self.catalog.get(term, []))


class BlockCheeseTests(unittest.TestCase):
    def test_hard_cheese_prefers_block(self):
        self.assertTrue(api.prefers_block_for_ingredient("shredded cheddar cheese", True))
        self.assertFalse(api.prefers_block_for_ingredient("shredded cheddar cheese", False))

    def test_soft_cheese_not_forced_to_block(self):
        for name in ("ricotta cheese", "crumbled feta", "cream cheese"):
            self.assertFalse(api.prefers_block_for_ingredient(name, True), name)

    def test_non_cheese_ignored(self):
        self.assertFalse(api.prefers_block_for_ingredient("chicken breasts", True))

    def test_block_search_strips_shredded_and_ranks_block_first(self):
        client = FakeClient({
            "cheddar cheese block": [
                product("Kroger Shredded Sharp Cheddar Cheese", "1", price=2.0),
                product("Kroger Sharp Cheddar Cheese Block", "2", price=3.5),
            ],
        })
        filters = api.ProductFilters(prefer_block_cheese=True)
        products, _ = api.search_ingredient_products(client, "shredded cheddar cheese", "8 oz", "S1", filters)
        self.assertEqual(client.calls[0]["term"], "cheddar cheese block")
        self.assertEqual(len(client.calls), 1, "should stop after a block result is found")
        self.assertEqual(products[0]["upc"], "2")

    def test_block_falls_back_to_plain_term(self):
        client = FakeClient({"cheddar cheese": [product("Cheddar Cheese Sliced", "3")]})
        filters = api.ProductFilters(prefer_block_cheese=True)
        products, _ = api.search_ingredient_products(client, "shredded cheddar cheese", "8 oz", "S1", filters)
        self.assertEqual([c["term"] for c in client.calls], ["cheddar cheese block", "cheddar cheese"])
        self.assertEqual(products[0]["upc"], "3")

    def test_without_preference_term_unchanged(self):
        client = FakeClient({})
        api.search_ingredient_products(client, "shredded cheddar cheese", "8 oz", "S1", api.ProductFilters())
        self.assertEqual(client.calls[0]["term"], "shredded cheddar cheese")


class FilterPreservationTests(unittest.TestCase):
    def test_organic_non_gmo_and_brand_applied(self):
        client = FakeClient({
            "non gmo organic ground beef": [
                product("Organic Ground Beef", "a", nonGmo=True,
                        nonGmoClaimName="PRODUCT LABELED WITH NON-GMO CLAIM"),
                product("Organic Ground Beef", "b"),
                product("Ground Beef", "c", nonGmo=True,
                        nonGmoClaimName="PRODUCT LABELED WITH NON-GMO CLAIM"),
                product("Organic Ground Beef", "d", brand="Simple Truth Organic", nonGmo=True,
                        nonGmoClaimName="PRODUCT LABELED WITH NON-GMO CLAIM"),
                product("Organic Ground Beef", "e", brand="Laura's Lean", nonGmo=True,
                        nonGmoClaimName="PRODUCT LABELED WITH NON-GMO CLAIM"),
            ],
        })
        filters = api.ProductFilters(kroger_brands_only=True, organic_meat_only=True, non_gmo_only=True)
        products, organic = api.search_ingredient_products(
            client, "beef mince", "1 lb", "S1", filters, base_term="ground beef"
        )
        self.assertEqual(organic, "meat")
        self.assertEqual(sorted(p["upc"] for p in products), ["a", "d"])

    def test_brand_filter_falls_back_to_kroger_label(self):
        client = FakeClient({"cream cheese": [product("Cream Cheese Brick", "p", brand="Philadelphia")]})
        api.search_ingredient_products(
            client, "cream cheese", "8 oz", "S1", api.ProductFilters(kroger_brands_only=True)
        )
        self.assertEqual([c["brand"] for c in client.calls], [None, "Kroger"])

    def test_fresh_produce_exempt_from_brand_filter(self):
        client = FakeClient({"zucchini": [product("Zucchini", "z", brand="")]})
        products, _ = api.search_ingredient_products(
            client, "zucchini", "1", "S1", api.ProductFilters(kroger_brands_only=True)
        )
        self.assertEqual([p["upc"] for p in products], ["z"])

    def test_canned_and_dry_beans_are_not_fresh_produce(self):
        self.assertFalse(api.is_fresh_produce_ingredient("pinto beans"))
        self.assertFalse(api.is_fresh_produce_ingredient("corn", "1 15-ounce can"))
        self.assertFalse(api.is_fresh_produce_ingredient("tomato sauce"))
        self.assertTrue(api.is_fresh_produce_ingredient("green beans"))
        self.assertTrue(api.is_fresh_produce_ingredient("tomatoes"))


class PackageAmountTests(unittest.TestCase):
    def test_sized_cans_cost_by_total_weight(self):
        can = product("Pinto Beans", "pb", size="15.5oz", price=1.0)
        cost, note = api.product_cost(can, "2 15-ounce cans", "pinto beans")
        self.assertEqual((cost, note[:7]), (2.0, "2 packs"))
        self.assertAlmostEqual(api.parse_amount("2 (15 oz.) cans")[1], 2 * 15 * 28.3495)

    def test_unsized_cans_buy_that_many(self):
        cost, note = api.product_cost(product("Pinto Beans", "pb", size="15.5oz", price=1.25), "3 cans", "pinto beans")
        self.assertAlmostEqual(cost, 3.75)

    def test_altered_products_ranked_below_plain(self):
        plain = product("Simple Truth Organic Low Sodium Pinto Beans", "1", size="15 oz", price=1.25)
        refried = product("Simple Truth Organic Refried Pinto Beans", "2", size="16 oz", price=1.19)
        ranked = api.rank_products([refried, plain], "pinto beans", "2 15-ounce cans", api.ProductFilters())
        self.assertEqual(ranked[0]["upc"], "1")
        ranked = api.rank_products([plain, refried], "refried beans", "1 can", api.ProductFilters())
        self.assertEqual(ranked[0]["upc"], "2")


class AlternativesEndpointTests(unittest.TestCase):
    def run_alternatives(self, catalog, **request_fields):
        client = FakeClient(catalog)
        with mock.patch.object(api, "KrogerAPI", return_value=client):
            request = api.ProductAlternativeRequest(store_id="S1", **request_fields)
            return api.search_product_alternatives(request), client

    def test_synonym_alternative_found_with_amount_preserved(self):
        response, client = self.run_alternatives(
            {"zucchini": [product("Fresh Zucchini", "z1", size="1 each", price=1.0, brand="")]},
            ingredient_name="courgette", amount="2", organic_vegetables_only=True,
        )
        self.assertIn("organic zucchini", [c["term"] for c in client.calls])
        self.assertTrue(all(c["term"].startswith("organic ") for c in client.calls), client.calls)
        self.assertEqual(response["ingredient"], api.ingredient_label("courgette", "2"))
        self.assertEqual(response["organic_filter"], "vegetables")

    def test_alternatives_deduplicated_and_tagged(self):
        shared = product("Boneless Skinless Chicken Breasts", "c1", size="1 lb", price=5.0)
        response, _ = self.run_alternatives(
            {"chicken breasts": [shared], "boneless skinless chicken breasts": [shared]},
            ingredient_name="shredded chicken", amount="1 lb",
        )
        self.assertEqual([alt["upc"] for alt in response["alternatives"]], ["c1"])
        self.assertIn("search_term", response["alternatives"][0])

    def test_missing_credentials_returns_503(self):
        client = FakeClient({})
        client.authenticate_client = mock.Mock(side_effect=SecureCredentialError("not configured"))
        with mock.patch.object(api, "KrogerAPI", return_value=client):
            with self.assertRaises(HTTPException) as ctx:
                api.search_product_alternatives(
                    api.ProductAlternativeRequest(store_id="S1", ingredient_name="onion")
                )
        self.assertEqual(ctx.exception.status_code, 503)



class PantrySkipTests(unittest.TestCase):
    def run_search(self, skip):
        client = FakeClient({"onion": [product("Yellow Onion", "o1", size="1 each", price=1.0)]})
        with mock.patch.object(api, "KrogerAPI", return_value=client):
            request = api.ProductSearchRequest(
                store_id="S1", skip_pantry_seasonings=skip,
                ingredients=[{"name": "salt", "amount": "1 tsp"}, {"name": "onion", "amount": "1"}],
            )
            return api.search_products(request)["results"], client

    def test_seasonings_skipped_without_kroger_call(self):
        results, client = self.run_search(skip=True)
        self.assertTrue(results[0]["pantry_skipped"])
        self.assertIsNone(results[0]["product"])
        self.assertEqual([c["term"] for c in client.calls], ["onion"])
        self.assertEqual(results[1]["product"]["upc"], "o1")

    def test_restocked_seasoning_searched_even_when_skipping(self):
        client = FakeClient({"salt": [product("Kroger Iodized Salt", "s1", size="26 oz", price=0.89)]})
        with mock.patch.object(api, "KrogerAPI", return_value=client):
            request = api.ProductSearchRequest(
                store_id="S1", skip_pantry_seasonings=True,
                ingredients=[{"name": "salt", "amount": "1 tsp", "restock": True}],
            )
            results = api.search_products(request)["results"]
        self.assertNotIn("pantry_skipped", results[0])
        self.assertEqual(results[0]["product"]["upc"], "s1")

    def test_import_split_keeps_amounts_of_skipped(self):
        shopping, skipped = api.split_recipe_shopping_ingredients(
            [{"name": "kosher salt", "amount": "2 tsp."}, {"name": "onion", "amount": "1"}], True
        )
        self.assertEqual(skipped, [{"name": "kosher salt", "amount": "2 tsp."}])
        self.assertEqual([i["name"] for i in shopping], ["onion"])

    def test_seasonings_searched_when_disabled(self):
        results, client = self.run_search(skip=False)
        self.assertNotIn("pantry_skipped", results[0])
        self.assertEqual([c["term"] for c in client.calls], ["salt", "onion"])

if __name__ == "__main__":
    unittest.main()
