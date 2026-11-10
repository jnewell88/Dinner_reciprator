import unittest

import api


class ChickenRuleTests(unittest.TestCase):
    def test_cooked_chicken_bought_raw(self):
        for name in ("cooked chicken", "shredded cooked chicken breast", "shredded chicken", "diced chicken"):
            self.assertEqual(api.product_search_term(name), "boneless skinless chicken breasts", name)
        self.assertEqual(api.product_search_term("diced cooked chicken thighs"), "boneless skinless chicken thighs")

    def test_rotisserie_and_canned_kept(self):
        self.assertEqual(api.product_search_term("shredded rotisserie chicken"), "rotisserie chicken")
        self.assertEqual(api.product_search_term("canned chicken"), "canned chicken breast")
        self.assertNotIn("chicken breasts", api.alternative_search_terms("shredded rotisserie chicken"))
        self.assertNotIn("chicken breasts", api.alternative_search_terms("canned chicken"))

    def test_other_chicken_items_untouched(self):
        for name in ("chicken broth", "chicken stock", "chicken breasts"):
            self.assertEqual(api.product_search_term(name), name)


class PreparationWordTests(unittest.TestCase):
    def test_preparation_dropped_from_lookup(self):
        cases = {
            "cooked rice": "rice",
            "drained and rinsed black beans": "black beans",
            "peeled and cubed potatoes": "potatoes",
            "finely diced celery": "celery",
            "thinly sliced green onions": "green onions",
            "cream cheese, softened": "cream cheese",
            "medium carrots, peeled and finely diced": "carrots",
            "chopped fresh parsley": "fresh parsley",
        }
        for name, term in cases.items():
            self.assertEqual(api.product_search_term(name), term, name)

    def test_generic_oil_is_vegetable_oil(self):
        self.assertEqual(api.product_search_term("cooking oil"), "vegetable oil")
        self.assertEqual(api.product_search_term("olive oil"), "olive oil")

    def test_product_forms_kept(self):
        for name in ("crushed tomatoes", "crushed red pepper",
                     "shredded cheddar cheese", "grated parmesan", "sliced almonds", "large eggs"):
            self.assertEqual(api.product_search_term(name), name, name)


class DicedTomatoTests(unittest.TestCase):
    def test_fresh_unless_can_signalled(self):
        for name, amount in (("diced tomato, for serving", ""), ("diced tomatoes", "1 cup"), ("tomatoes, diced", "2")):
            self.assertEqual(api.product_search_term(name, amount), "tomato" + ("es" if "tomatoes" in name else ""), name)
            self.assertTrue(api.is_fresh_produce_ingredient(name, amount), name)

    def test_canned_when_can_signalled(self):
        for name, amount in (("diced tomatoes", "1 14.5-ounce can"), ("canned diced tomatoes", "1"),
                             ("diced tomatoes", "2 cans"), ("diced tomatoes", "28 oz")):
            self.assertIn("diced tomatoes", api.product_search_term(name, amount), (name, amount))
            self.assertFalse(api.is_fresh_produce_ingredient(name, amount), (name, amount))


class GreenChileTests(unittest.TestCase):
    chiles = {"description": "Kroger Diced Green Chile Peppers", "upc": "c",
              "items": [{"size": "4 oz", "price": {"regular": 0.99}}]}
    rotel = {"description": "Kroger Diced Tomatoes with Green Chilies Original", "upc": "r",
             "items": [{"size": "14.5 oz", "price": {"regular": 1.0}}]}

    def test_canned_form_kept_in_term(self):
        self.assertEqual(api.product_search_term("diced green chilies", "1 4-ounce can"), "diced green chilies")
        self.assertEqual(api.product_search_term("can diced green chilies", "1"), "diced green chilies")

    def test_chile_spellings_match(self):
        self.assertEqual(api.search_tokens("chilies chiles chili chile"), ["chile"] * 4)

    def test_tomato_blend_not_chosen_for_plain_chiles(self):
        ranked = api.rank_products([self.rotel, self.chiles], "diced green chilies", "1 can", api.ProductFilters())
        self.assertEqual(ranked[0]["upc"], "c")

    def test_tomato_blend_chosen_when_asked(self):
        ranked = api.rank_products([self.chiles, self.rotel], "diced tomatoes and green chilies", "1 can",
                                   api.ProductFilters())
        self.assertEqual(ranked[0]["upc"], "r")


class TortillaTests(unittest.TestCase):
    def test_sizes_map_to_store_names(self):
        cases = {
            ("flour tortillas", ""): "soft taco flour tortillas",
            ("flour tortillas", "8 (8-inch)"): "soft taco flour tortillas",
            ("10-inch flour tortillas", "6"): "burrito flour tortillas",
            ("large flour tortillas", "4"): "burrito flour tortillas",
            ("burrito-size flour tortillas", ""): "burrito flour tortillas",
            ("6-inch flour tortillas", ""): "fajita flour tortillas",
            ("12-inch flour tortillas", ""): "extra large burrito flour tortillas",
            ("corn tortillas", "12"): "corn tortillas",
            ("tortilla chips", ""): "tortilla chips",
        }
        for (name, amount), term in cases.items():
            self.assertEqual(api.product_search_term(name, amount), term, name)

    def test_alternatives_try_neighbouring_sizes(self):
        self.assertEqual(
            api.alternative_search_terms("10-inch flour tortillas")[:3],
            ["soft taco flour tortillas", "extra large burrito flour tortillas", "flour tortillas"],
        )

    def test_cost_uses_pack_count_from_description(self):
        product = {"description": "Burrito Size Flour Tortillas 8 ct",
                   "items": [{"size": "20 oz", "price": {"regular": 3.49}}]}
        cost, note = api.product_cost(product, "12", "flour tortillas")
        self.assertAlmostEqual(cost, 6.98)
        self.assertIn("2 packs", note)


class IngredientParserTests(unittest.TestCase):
    def test_pioneer_woman_formats(self):
        cases = {
            "1 c. grated cheddar cheese (about 4 oz.)": ("grated cheddar cheese", "1 c."),
            "2 (9-inch) refrigerated pie crusts": ("refrigerated pie crusts", "2 (9-inch)"),
            "2 8-ounce cans tomato sauce": ("tomato sauce", "2 8-ounce cans"),
            "1/4 c. all-purpose flour": ("all-purpose flour", "1/4 c."),
            "2 cups flour": ("flour", "2 cups"),
        }
        for raw, (name, amount) in cases.items():
            parsed = api.parse_budget_bytes_ingredient(raw)
            self.assertEqual((parsed["name"], parsed["amount"]), (name, amount), raw)

    def test_cup_abbreviation_costs(self):
        self.assertEqual(api.parse_amount("1 c."), ("volume", 236.588))
        self.assertAlmostEqual(api.parse_amount("1 quart")[1], 946.353)

    def test_pantry_seasonings_with_notes(self):
        for name in ("kosher salt, plus more to taste", "Kosher salt and black pepper, to taste",
                     "chili powder, plus more to taste"):
            self.assertTrue(api.is_generic_pantry_seasoning(name), name)
        for name in ("taco seasoning", "seasoned salt"):
            self.assertFalse(api.is_generic_pantry_seasoning(name), name)

    def test_pantry_staples(self):
        for name in ("crushed red pepper flakes", "baking soda", "apple cider vinegar", "soy sauce",
                     "grated fresh ginger", "ground ginger", "vegetable oil", "cooking oil"):
            self.assertTrue(api.is_generic_pantry_seasoning(name), name)

    def test_noodle_products_not_matched_to_plain_ingredient(self):
        scallions = {"description": "Green Onions Scallions", "items": [{"price": {"regular": 0.99}, "size": "1 bunch"}]}
        noodles = {"description": "Scallion Noodles", "items": [{"price": {"regular": 0.50}, "size": "1 ct"}]}
        ranked = api.rank_products([noodles, scallions], "scallions, thinly sliced", "2", api.ProductFilters())
        self.assertIs(ranked[0], scallions)
        flakes = {"description": "Crushed Red Pepper Flakes", "items": [{"price": {"regular": 2.0}, "size": "1 oz"}]}
        shaker = {"description": "Parmesan & Red Pepper Flakes Shaker", "items": [{"price": {"regular": 1.0}, "size": "1 oz"}]}
        ranked = api.rank_products([shaker, flakes], "crushed red pepper flakes", "1/4 tsp", api.ProductFilters())
        self.assertIs(ranked[0], flakes)


if __name__ == "__main__":
    unittest.main()
