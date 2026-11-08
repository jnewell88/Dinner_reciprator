import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from math import ceil, isfinite
from pathlib import Path
from typing import Any
from typing import Literal
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.kroger_client import KrogerAPI

app = FastAPI(title="Dinner Reciprator API")
RECIPE_DATABASE_PATH = Path(__file__).resolve().parent / "data" / "recipes.sqlite3"


def initialize_recipe_database() -> None:
    RECIPE_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(RECIPE_DATABASE_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS saved_recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_recipe_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                ingredients_json TEXT NOT NULL,
                instructions_json TEXT NOT NULL,
                saved_at TEXT NOT NULL,
                UNIQUE(source, source_recipe_id)
            )"""
        )


def serialize_saved_recipe(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source": row["source"],
        "title": row["title"],
        "url": row["url"],
        "ingredients": json.loads(row["ingredients_json"]),
        "instructions": json.loads(row["instructions_json"]),
        "saved_at": row["saved_at"],
    }


def save_recipe_record(source: str, source_recipe_id: str, recipe: dict[str, Any]) -> dict[str, Any]:
    initialize_recipe_database()
    saved_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(RECIPE_DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """INSERT INTO saved_recipes
                (source, source_recipe_id, title, url, ingredients_json, instructions_json, saved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_recipe_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    ingredients_json = excluded.ingredients_json,
                    instructions_json = excluded.instructions_json""",
            (
                source,
                source_recipe_id,
                recipe["title"],
                recipe["url"],
                json.dumps(recipe["ingredients"]),
                json.dumps(recipe["instructions"]),
                saved_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM saved_recipes WHERE source = ? AND source_recipe_id = ?",
            (source, source_recipe_id),
        ).fetchone()
        return serialize_saved_recipe(row)


def normalize_recipe_instructions(value: Any) -> list[str]:
    if isinstance(value, str):
        return [step.strip() for step in re.split(r"\r?\n+", value) if step.strip()]
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return normalize_recipe_instructions(value["text"])
        return normalize_recipe_instructions(value.get("itemListElement", []))
    if isinstance(value, list):
        return [step for item in value for step in normalize_recipe_instructions(item)]
    return []


def product_price(product: dict[str, Any]) -> float | None:
    items = product.get("items") or []
    item = items[0] if items else {}
    prices = item.get("price") or {}

    for value in (prices.get("promo"), prices.get("regular")):
        try:
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if isfinite(amount) and amount >= 0:
            return amount
    return None


FRACTION_VALUES = {
    "¼": 0.25,
    "½": 0.5,
    "¾": 0.75,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "⅛": 0.125,
    "⅜": 0.375,
    "⅝": 0.625,
    "⅞": 0.875,
}
AMOUNT_PATTERN = re.compile(
    r"^\s*(\d+(?:\s+\d+\s*/\s*\d+|\s*[¼½¾⅓⅔⅛⅜⅝⅞]|\s*/\s*\d+|\.\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞])\s*([a-zA-Z.]+)?"
)
UNIT_FACTORS = {
    "g": ("mass", 1.0),
    "gram": ("mass", 1.0),
    "grams": ("mass", 1.0),
    "kg": ("mass", 1000.0),
    "kilogram": ("mass", 1000.0),
    "kilograms": ("mass", 1000.0),
    "oz": ("mass", 28.3495),
    "ounce": ("mass", 28.3495),
    "ounces": ("mass", 28.3495),
    "lb": ("mass", 453.592),
    "lbs": ("mass", 453.592),
    "pound": ("mass", 453.592),
    "pounds": ("mass", 453.592),
    "ml": ("volume", 1.0),
    "milliliter": ("volume", 1.0),
    "milliliters": ("volume", 1.0),
    "l": ("volume", 1000.0),
    "liter": ("volume", 1000.0),
    "liters": ("volume", 1000.0),
    "tsp": ("volume", 4.92892),
    "teaspoon": ("volume", 4.92892),
    "teaspoons": ("volume", 4.92892),
    "tbsp": ("volume", 14.7868),
    "tblsp": ("volume", 14.7868),
    "tblspn": ("volume", 14.7868),
    "tablespoon": ("volume", 14.7868),
    "tablespoons": ("volume", 14.7868),
    "cup": ("volume", 236.588),
    "cups": ("volume", 236.588),
    "ct": ("count", 1.0),
    "count": ("count", 1.0),
    "each": ("count", 1.0),
    "ea": ("count", 1.0),
    "dozen": ("count", 12.0),
    "piece": ("count", 1.0),
    "pieces": ("count", 1.0),
}


def parse_amount(value: str, ingredient_name: str = "") -> tuple[str, float] | None:
    match = AMOUNT_PATTERN.match(value)
    if not match:
        return None

    number_text, unit_text = match.groups()
    number_text = number_text.strip()
    mixed_fraction = re.fullmatch(r"(\d+)\s*([¼½¾⅓⅔⅛⅜⅝⅞])", number_text)
    if mixed_fraction:
        quantity = float(mixed_fraction.group(1)) + FRACTION_VALUES[mixed_fraction.group(2)]
    elif " " in number_text and "/" in number_text:
        whole, fraction = number_text.split(maxsplit=1)
        numerator, denominator = fraction.split("/")
        quantity = float(whole) + float(numerator) / float(denominator)
    elif "/" in number_text:
        numerator, denominator = number_text.split("/")
        quantity = float(numerator) / float(denominator)
    elif number_text in FRACTION_VALUES:
        quantity = FRACTION_VALUES[number_text]
    else:
        quantity = float(number_text)

    unit = (unit_text or "").lower().rstrip(".")
    if unit in {"large", "medium", "small"} and "egg" in ingredient_name.lower():
        unit = "each"
    elif not unit and "egg" in ingredient_name.lower():
        unit = "each"
    factor = UNIT_FACTORS.get(unit)
    if not factor:
        return None
    dimension, multiplier = factor
    return dimension, quantity * multiplier


def product_cost(product: dict[str, Any], amount: str, ingredient_name: str) -> tuple[float | None, str | None]:
    price = product_price(product)
    if price is None:
        return None, None

    items = product.get("items") or []
    item = items[0] if items else {}
    required = parse_amount(amount, ingredient_name)
    package_size = parse_amount(str(item.get("size") or ""))
    if not required or not package_size or required[0] != package_size[0] or package_size[1] <= 0:
        return price, "package price; quantity not directly comparable"

    if str(item.get("soldBy") or "").upper() == "WEIGHT" and required[0] == "mass":
        cost = price * required[1] / package_size[1]
        return cost, f"{amount} by weight"

    package_count = max(1, ceil(required[1] / package_size[1]))
    package_size_text = str(item.get("size") or "package")
    return price * package_count, f"{package_count} pack{'s' if package_count != 1 else ''} ({package_size_text} each)"


def normalize_ingredient_name(name: str, amount: str) -> str:
    amount_words = set(re.findall(r"[a-z]+", amount.lower()))
    for size in ("large", "medium", "small"):
        prefix = f"{size} "
        if size in amount_words and name.lower().startswith(prefix) and "egg" in name.lower():
            return name[len(prefix):].strip()
    return name.strip()


def ingredient_label(name: str, amount: str) -> str:
    display_name = normalize_ingredient_name(name, amount)
    required = parse_amount(amount, display_name)
    if "egg" in display_name.lower() and required and required == ("count", 1.0):
        display_name = re.sub(r"eggs\b", "egg", display_name, flags=re.IGNORECASE)
    return " ".join(part for part in (amount, display_name) if part)


def product_search_term(ingredient_name: str) -> str:
    if not re.search(r"\bmince\b", ingredient_name, flags=re.IGNORECASE):
        return ingredient_name.strip()

    meat = re.sub(r"\bmince\b", "", ingredient_name, flags=re.IGNORECASE).strip()
    return f"ground {meat}" if meat else "ground meat"


def product_sort_key(product: dict[str, Any], amount: str, ingredient_name: str) -> tuple[int, bool, int, float]:
    estimated_cost, _ = product_cost(product, amount, ingredient_name)
    query = product_search_term(ingredient_name)
    query_tokens = set(search_tokens(query))
    product_tokens = set(search_tokens(str(product.get("description") or "")))
    missing_terms = len(query_tokens - product_tokens)
    egg_carton_preference = 0
    required = parse_amount(amount, ingredient_name)
    if "egg" in ingredient_name.lower() and required and required[0] == "count" and required[1] <= 12:
        items = product.get("items") or []
        item = items[0] if items else {}
        package = parse_amount(str(item.get("size") or ""))
        if not package or package[0] != "count" or package[1] < 12:
            egg_carton_preference = 1
    return missing_terms, estimated_cost is None, egg_carton_preference, estimated_cost or 0.0


def search_tokens(value: str) -> list[str]:
    normalized = re.sub(r"\bbread\s+crumbs\b", "breadcrumbs", value.lower())
    tokens = re.findall(r"[a-z0-9]+", normalized)
    normalized_tokens = []
    for token in tokens:
        if token.endswith("ies") and len(token) > 4:
            token = f"{token[:-3]}y"
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            token = token[:-1]
        normalized_tokens.append(token)
    return normalized_tokens


MEAT_INGREDIENT_TOKENS = {
    "bacon", "beef", "bison", "breast", "chicken", "drumstick", "ham", "lamb",
    "meat", "mince", "pork", "rib", "roast", "sausage", "steak", "tenderloin",
    "thigh", "turkey", "veal", "wing",
}
VEGETABLE_INGREDIENT_TOKENS = {
    "asparagus", "bean", "beet", "bell", "broccoli", "brussels", "cabbage",
    "carrot", "cauliflower", "celery", "corn", "cucumber", "eggplant", "garlic",
    "green", "kale", "lettuce", "mushroom", "onion", "pea", "pepper", "potato",
    "pumpkin", "radish", "spinach", "squash", "tomato", "vegetable", "zucchini",
}
FRUIT_INGREDIENT_TOKENS = {
    "apple", "apricot", "avocado", "banana", "berry", "blueberry", "cherry",
    "citrus", "grape", "lemon", "lime", "mango", "melon", "orange", "peach",
    "pear", "pineapple", "plum", "raspberry", "strawberry", "watermelon",
}


def organic_filter_for_ingredient(
    ingredient_name: str,
    organic_meat_only: bool,
    organic_vegetables_only: bool,
) -> str | None:
    tokens = set(search_tokens(ingredient_name))
    if organic_meat_only and tokens & MEAT_INGREDIENT_TOKENS:
        return "meat"
    if organic_vegetables_only and tokens & VEGETABLE_INGREDIENT_TOKENS:
        return "vegetables"
    return None


def is_organic_product(product: dict[str, Any]) -> bool:
    searchable_text = " ".join(
        str(value or "") for value in (product.get("description"), product.get("brand"))
    )
    return bool(re.search(r"\borganic\b", searchable_text, flags=re.IGNORECASE))


def is_non_gmo_product(product: dict[str, Any]) -> bool:
    claim = str(product.get("nonGmoClaimName") or "").upper()
    return product.get("nonGmo") is True and "PRODUCT LABELED WITH NON-GMO CLAIM" in claim


def is_fresh_produce_ingredient(ingredient_name: str) -> bool:
    tokens = set(search_tokens(ingredient_name))
    return bool(tokens & (VEGETABLE_INGREDIENT_TOKENS | FRUIT_INGREDIENT_TOKENS))


class ProductSearchRequest(BaseModel):
    ingredients: list[dict[str, str | None]] = Field(min_length=1, max_length=30)
    store_id: str = Field(min_length=1, max_length=20)
    kroger_brands_only: bool = False
    organic_meat_only: bool = False
    organic_vegetables_only: bool = False
    non_gmo_only: bool = False


class StoreSearchRequest(BaseModel):
    zip_code: str = Field(min_length=5, max_length=10)


class RecipeSearchRequest(BaseModel):
    meals: list[str] = Field(min_length=1, max_length=10)
    sources: list[Literal["themealdb", "budgetbytes"]] = Field(
        default_factory=lambda: ["themealdb", "budgetbytes"], min_length=1
    )


class RecipeImportRequest(BaseModel):
    source: Literal["themealdb", "budgetbytes"]
    recipe_id: str = Field(min_length=1, max_length=500)


class RecipeJSONLDParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_jsonld = False
        self.blocks: list[str] = []
        self._current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self._in_jsonld = (
            tag == "script"
            and (attributes.get("type") or "").split(";")[0].strip().lower() == "application/ld+json"
        )
        if self._in_jsonld:
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self.blocks.append("".join(self._current))
            self._in_jsonld = False
            self._current = []


def recipe_jsonld_nodes(value: Any):
    if isinstance(value, dict):
        recipe_type = value.get("@type", [])
        if recipe_type == "Recipe" or (isinstance(recipe_type, list) and "Recipe" in recipe_type):
            yield value
        for nested in value.values():
            yield from recipe_jsonld_nodes(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from recipe_jsonld_nodes(nested)


def parse_budget_bytes_ingredient(value: str) -> dict[str, str]:
    unit_pattern = (
        r"(?:lbs?\.?|pounds?|oz\.?|ounces?|kg\.?|kilograms?|g|grams?|ml|milliliters?|l|liters?|"
        r"cups?|tbsp\.?|tablespoons?|tsp\.?|teaspoons?|cloves?|cans?|jars?|slices?|"
        r"pieces?|breasts?|stalks?|sprigs?|leaves?|bunches?|pinches?|packages?|pkgs?)"
    )
    amount_pattern = re.compile(
        rf"^\s*((?:\d+\s+)?(?:\d+\s*/\s*\d+|\d+(?:\s*[¼½¾⅓⅔⅛⅜⅝⅞]|\.\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞])"
        rf"(?:\s+{unit_pattern})?)\s+(.+?)\s*$",
        re.IGNORECASE,
    )
    match = amount_pattern.match(value)
    if not match:
        return {"name": value.strip(), "amount": ""}

    amount, name = match.groups()
    name = re.sub(r"\s+\$[\d.]+\*?\s*$", "", name).split("(", 1)[0].strip(" ,.;")
    return {"name": name or value.strip(), "amount": amount.strip()}


def import_recipe_ingredients(request: RecipeImportRequest) -> dict[str, Any]:
    try:
        if request.source == "themealdb":
            if not request.recipe_id.isdigit():
                raise HTTPException(status_code=422, detail="Invalid TheMealDB recipe ID.")
            response = requests.get(
                "https://www.themealdb.com/api/json/v1/1/lookup.php",
                params={"i": request.recipe_id},
                timeout=10,
            )
            response.raise_for_status()
            meals = response.json().get("meals") or []
            if not meals:
                raise HTTPException(status_code=404, detail="Recipe was not found on TheMealDB.")

            meal = meals[0]
            ingredients = [
                {
                    "name": normalize_ingredient_name(
                        str(meal.get(f"strIngredient{index}") or "").strip(),
                        str(meal.get(f"strMeasure{index}") or "").strip(),
                    ),
                    "amount": str(meal.get(f"strMeasure{index}") or "").strip(),
                }
                for index in range(1, 21)
                if str(meal.get(f"strIngredient{index}") or "").strip()
            ]
            return {
                "title": meal.get("strMeal") or "Recipe",
                "url": meal.get("strSource") or f"https://www.themealdb.com/meal/{request.recipe_id}",
                "ingredients": ingredients,
                "instructions": normalize_recipe_instructions(meal.get("strInstructions")),
            }

        recipe_url = request.recipe_id
        parsed_url = urlparse(recipe_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "www.budgetbytes.com":
            raise HTTPException(status_code=422, detail="Only Budget Bytes recipe links can be imported.")

        response = requests.get(
            recipe_url,
            headers={"User-Agent": "DinnerReciprator/0.1", "Accept": "text/html"},
            timeout=10,
        )
        response.raise_for_status()
        parser = RecipeJSONLDParser()
        parser.feed(response.text)
        recipe_nodes = []
        for block in parser.blocks:
            try:
                recipe_nodes.extend(recipe_jsonld_nodes(json.loads(block)))
            except ValueError:
                continue

        if not recipe_nodes:
            raise HTTPException(status_code=404, detail="A structured recipe card was not found on this page.")

        recipe = recipe_nodes[0]
        ingredients = [
            parse_budget_bytes_ingredient(value)
            for value in recipe.get("recipeIngredient", [])
            if isinstance(value, str) and value.strip()
        ]
        if not ingredients:
            raise HTTPException(status_code=404, detail="No recipe ingredients were found on this page.")
        return {
            "title": recipe.get("name") or "Recipe",
            "url": recipe_url,
            "ingredients": ingredients,
            "instructions": normalize_recipe_instructions(recipe.get("recipeInstructions")),
        }
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail="The recipe source could not be reached.") from error


def search_recipe_source(meal: str, source: str) -> dict[str, Any]:
    source_name = "TheMealDB" if source == "themealdb" else "Budget Bytes"

    try:
        if source == "themealdb":
            response = requests.get(
                "https://www.themealdb.com/api/json/v1/1/search.php",
                params={"s": meal},
                timeout=8,
            )
            response.raise_for_status()
            entries = response.json().get("meals") or []
            recipes = [
                {
                    "id": entry.get("idMeal"),
                    "title": entry.get("strMeal"),
                    "url": entry.get("strSource")
                    or f"https://www.themealdb.com/meal/{entry.get('idMeal')}",
                    "image": entry.get("strMealThumb"),
                }
                for entry in entries[:5]
            ]
        else:
            response = requests.get(
                "https://www.budgetbytes.com/wp-json/wp/v2/search",
                params={"search": meal, "per_page": 5},
                headers={"User-Agent": "DinnerReciprator/0.1", "Accept": "application/json"},
                timeout=8,
            )
            response.raise_for_status()
            entries = response.json()
            recipes = [
                {"id": entry.get("id"), "title": entry.get("title"), "url": entry.get("url")}
                for entry in entries
                if entry.get("subtype") == "post" and entry.get("title") and entry.get("url")
            ][:5]
    except (requests.RequestException, ValueError):
        return {
            "source": source,
            "name": source_name,
            "status": "error",
            "recipes": [],
        }

    return {
        "source": source,
        "name": source_name,
        "status": "found" if recipes else "not_found",
        "recipes": recipes,
    }


@app.post("/api/stores")
def search_stores(request: StoreSearchRequest) -> dict[str, list[dict[str, Any]]]:
    zip_code = request.zip_code.strip()
    valid_zip = (
        (zip_code.isdigit() and len(zip_code) in (5, 9))
        or (
            len(zip_code) == 10
            and zip_code[:5].isdigit()
            and zip_code[5] == "-"
            and zip_code[6:].isdigit()
        )
    )
    if not valid_zip:
        raise HTTPException(status_code=422, detail="Enter a valid US ZIP code.")

    try:
        locations = KrogerAPI().search_locations(zip_code=zip_code, limit=10)
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="Kroger could not find stores right now. Check your ZIP code and try again.",
        ) from error

    stores = []
    for location in locations:
        address = location.get("address") or {}
        stores.append(
            {
                "id": location.get("locationId"),
                "name": location.get("name") or location.get("chain"),
                "chain": location.get("chain"),
                "address": address.get("addressLine1"),
                "city": address.get("city"),
                "state": address.get("state"),
                "zip_code": address.get("zipCode"),
            }
        )

    return {"stores": stores}


@app.post("/api/recipes/search")
def search_recipes(request: RecipeSearchRequest) -> dict[str, list[dict[str, Any]]]:
    meals = list(dict.fromkeys(meal.strip() for meal in request.meals if meal.strip()))
    sources = list(dict.fromkeys(request.sources))
    if not meals:
        raise HTTPException(status_code=422, detail="Add at least one meal to search.")

    matches: dict[str, dict[str, dict[str, Any]]] = {meal: {} for meal in meals}
    with ThreadPoolExecutor(max_workers=8) as executor:
        tasks = {
            executor.submit(search_recipe_source, meal, source): (meal, source)
            for meal in meals
            for source in sources
        }
        for task in as_completed(tasks):
            meal, source = tasks[task]
            matches[meal][source] = task.result()

    return {
        "results": [
            {
                "meal": meal,
                "sources": [matches[meal][source] for source in sources],
            }
            for meal in meals
        ]
    }


@app.post("/api/recipes/import")
def import_recipe(request: RecipeImportRequest) -> dict[str, Any]:
    recipe = import_recipe_ingredients(request)
    saved_recipe = save_recipe_record(request.source, request.recipe_id, recipe)
    return {**recipe, "saved_recipe": saved_recipe}


@app.get("/api/recipes/saved")
def list_saved_recipes() -> dict[str, list[dict[str, Any]]]:
    initialize_recipe_database()
    with sqlite3.connect(RECIPE_DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM saved_recipes ORDER BY saved_at DESC, id DESC"
        ).fetchall()
    return {"recipes": [serialize_saved_recipe(row) for row in rows]}


@app.delete("/api/recipes/saved/{recipe_id}")
def delete_saved_recipe(recipe_id: int) -> dict[str, bool]:
    initialize_recipe_database()
    with sqlite3.connect(RECIPE_DATABASE_PATH) as connection:
        cursor = connection.execute("DELETE FROM saved_recipes WHERE id = ?", (recipe_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Saved recipe was not found.")
    return {"deleted": True}


@app.post("/api/search")
def search_products(request: ProductSearchRequest) -> dict[str, list[dict[str, Any]]]:
    client = KrogerAPI()
    results = []

    try:
        client.authenticate_client()
        for ingredient in request.ingredients:
            name = str(ingredient.get("name") or "").strip()
            amount = str(ingredient.get("amount") or "").strip()
            if not name:
                continue

            organic_filter = organic_filter_for_ingredient(
                name,
                request.organic_meat_only,
                request.organic_vegetables_only,
            )
            term = product_search_term(name)
            if organic_filter and not re.search(r"\borganic\b", term, flags=re.IGNORECASE):
                term = f"organic {term}"
            if request.non_gmo_only:
                term = f"non gmo {term}"

            products = client.search_product(
                term=term,
                location_id=request.store_id.strip(),
                limit=10,
                brand=(
                    "Kroger"
                    if request.kroger_brands_only and not is_fresh_produce_ingredient(name)
                    else None
                ),
            )
            if organic_filter:
                products = [candidate for candidate in products if is_organic_product(candidate)]
            if request.non_gmo_only:
                products = [candidate for candidate in products if is_non_gmo_product(candidate)]
            product = min(
                products,
                key=lambda candidate: product_sort_key(candidate, amount, name),
            ) if products else None
            item = (product or {}).get("items") or [{}]
            estimated_cost, purchase_note = product_cost(product, amount, name) if product else (None, None)

            results.append(
                {
                    "ingredient": ingredient_label(name, amount),
                    "organic_filter": organic_filter,
                    "non_gmo_only": request.non_gmo_only,
                    "product": {
                        "description": product.get("description"),
                        "brand": product.get("brand"),
                        "size": item[0].get("size"),
                        "upc": product.get("upc"),
                        "price": product_price(product),
                        "estimated_cost": estimated_cost,
                        "purchase_note": purchase_note,
                    }
                    if product
                    else None,
                }
            )
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail="Kroger could not complete the product search. Check the store and try again.",
        ) from error

    return {"results": results}