import unittest

import api

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.thepioneerwoman.com/food-cooking/recipes/a11980/veggie-chili/</loc>
    <image:image><image:loc>https://hips.hearstapps.com/veggie.jpg</image:loc></image:image></url>
  <url><loc>https://www.thepioneerwoman.com/food-cooking/recipes/a32450737/slow-cooker-white-chicken-chili-recipe/</loc></url>
  <url><loc>https://www.thepioneerwoman.com/food-cooking/recipes/a94428/sweet-and-creamy-mango-chili-paletas/</loc></url>
  <url><loc>https://www.thepioneerwoman.com/ree-drummond-life/a7073/i-want-to-go-to-chilis/</loc></url>
  <url><loc>https://evil.example.com/food-cooking/recipes/a1/chili/</loc></url>
  <url><loc>http://www.thepioneerwoman.com/food-cooking/recipes/a2/chili/</loc></url>
</urlset>"""


class PioneerWomanSitemapTests(unittest.TestCase):
    def setUp(self):
        self.recipes = api.parse_pioneer_woman_sitemap(SITEMAP)

    def test_only_https_recipe_pages_on_site_are_indexed(self):
        self.assertEqual(
            [recipe["title"] for recipe in self.recipes],
            ["Veggie Chili", "Slow Cooker White Chicken Chili", "Sweet And Creamy Mango Chili Paletas"],
        )
        self.assertEqual(self.recipes[0]["image"], "https://hips.hearstapps.com/veggie.jpg")

    def test_ranking_prefers_closest_slug_and_requires_all_words(self):
        titles = [r["title"] for r in api.rank_pioneer_woman_recipes("chili", self.recipes)]
        self.assertEqual(titles[0], "Veggie Chili")
        self.assertEqual(len(titles), 3)
        titles = [r["title"] for r in api.rank_pioneer_woman_recipes("chicken chili recipe", self.recipes)]
        self.assertEqual(titles, ["Slow Cooker White Chicken Chili"])
        self.assertEqual(api.rank_pioneer_woman_recipes("lasagna", self.recipes), [])

    def test_results_omit_internal_tokens(self):
        result = api.rank_pioneer_woman_recipes("veggie chili", self.recipes)[0]
        self.assertEqual(set(result), {"id", "title", "url", "image"})

    def test_import_rejects_other_hosts(self):
        request = api.RecipeImportRequest(source="pioneerwoman", recipe_id="https://www.allrecipes.com/recipe/1/x/")
        with self.assertRaises(api.HTTPException) as ctx:
            api.import_recipe_ingredients(request)
        self.assertEqual(ctx.exception.status_code, 422)



class InstructionTests(unittest.TestCase):
    def test_howto_steps_and_entities(self):
        raw = [{"@type": "HowToSection", "itemListElement": [
            {"@type": "HowToStep", "text": "Mix flour &amp; water."},
            {"@type": "HowToStep", "text": "Bake <b>30</b> minutes."},
        ]}]
        self.assertEqual(api.normalize_recipe_instructions(raw), ["Mix flour & water.", "Bake 30 minutes."])


if __name__ == "__main__":
    unittest.main()
