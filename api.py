import gzip
import html
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from fractions import Fraction
from html.parser import HTMLParser
from math import ceil, isfinite
from pathlib import Path
from typing import Any
from typing import Literal
from urllib.parse import urljoin, urlparse

import secrets
from urllib.parse import urlencode

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from config import KROGER_REDIRECT_URI
from src import database
from src.kroger_client import (
    KrogerAccountAuthorizationError,
    KrogerAPI,
    KrogerAuthenticationError,
    account_authorization_url,
    exchange_account_code,
    refresh_account_token,
)
from src.secure_credentials import (
    SecureCredentialError,
    clear_kroger_account_tokens,
    clear_kroger_credentials,
    load_kroger_account_tokens,
    load_kroger_credentials,
    save_kroger_account_tokens,
    save_kroger_credentials,
)

app = FastAPI(title="Dinner Reciprator API")
def serialize_saved_recipe(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source": row["source"],
        "title": row["title"],
        "url": row["url"],
        "ingredients": json.loads(row["ingredients_json"]),
        "instructions": json.loads(row["instructions_json"]),
        "servings": row["servings"] if "servings" in row.keys() else "",
        "saved_at": row["saved_at"],
    }


def save_recipe_record(source: str, source_recipe_id: str, recipe: dict[str, Any]) -> dict[str, Any]:
    saved_at = datetime.now(timezone.utc).isoformat()
    with database.connect() as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """INSERT INTO saved_recipes
                (source, source_recipe_id, title, url, ingredients_json, instructions_json, servings, saved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_recipe_id) DO UPDATE SET
                    title = excluded.title,
                    url = excluded.url,
                    ingredients_json = excluded.ingredients_json,
                    instructions_json = excluded.instructions_json,
                    servings = excluded.servings""",
            (
                source,
                source_recipe_id,
                recipe["title"],
                recipe["url"],
                json.dumps(recipe["ingredients"]),
                json.dumps(recipe["instructions"]),
                recipe.get("servings") or "",
                saved_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM saved_recipes WHERE source = ? AND source_recipe_id = ?",
            (source, source_recipe_id),
        ).fetchone()
        return serialize_saved_recipe(row)


def normalize_recipe_yield(value: Any) -> str:
    """JSON-LD recipeYield ("4", ["4", "4 servings"], 6) -> "4 servings"."""
    candidates = value if isinstance(value, list) else [value]
    texts = [" ".join(html.unescape(str(item)).split()) for item in candidates if item not in (None, "")]
    if not texts:
        return ""
    worded = next((text for text in texts if re.search(r"[a-z]", text, flags=re.IGNORECASE)), None)
    text = worded or texts[0]
    if re.fullmatch(r"\d+(?:\s*-\s*\d+)?", text):
        text = f"{text} servings"
    return text[:60]


SCALE_QUANTITY_PATTERN = re.compile(
    r"^\s*(\d+\s+\d+\s*/\s*\d+|\d+\s*[¼½¾⅓⅔⅛⅜⅝⅞]|\d+\s*/\s*\d+|\d+(?:\.\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞])"
)


def quantity_value(text: str) -> float:
    text = " ".join(text.split())
    mixed_unicode = re.fullmatch(r"(\d+)\s*([¼½¾⅓⅔⅛⅜⅝⅞])", text)
    if mixed_unicode:
        return float(mixed_unicode.group(1)) + FRACTION_VALUES[mixed_unicode.group(2)]
    if text in FRACTION_VALUES:
        return FRACTION_VALUES[text]
    if "/" in text:
        whole = 0.0
        if " " in text.split("/")[0].strip():
            whole_text, text = text.split(" ", 1)
            whole = float(whole_text)
        numerator, denominator = (part.strip() for part in text.split("/"))
        return whole + float(numerator) / float(denominator)
    return float(text)


def scale_amount(amount: str, factor: float) -> str:
    """Scale the leading quantity of each "+"-joined amount ("1 1/2 cups" x2 -> "3 cups")."""
    if factor == 1 or not amount:
        return amount

    def scale_part(part: str) -> str:
        match = SCALE_QUANTITY_PATTERN.match(part)
        if not match:
            return part
        value = quantity_value(match.group(1)) * factor
        rest = part[match.end():]
        number = f"{value:.4f}".rstrip("0").rstrip(".")
        return format_recipe_amount(f"{number}{rest}")

    return " + ".join(scale_part(part.strip()) for part in amount.split("+"))


def scale_servings(servings: str, factor: float) -> str:
    if factor == 1 or not servings:
        return servings
    return re.sub(r"\d+", lambda match: f"{int(match.group()) * factor:g}", servings)


def normalize_recipe_instructions(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.sub(r"<[^>]+>", " ", html.unescape(value))
        return [" ".join(step.split()) for step in re.split(r"\r?\n+", value) if step.strip()]
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
    "c": ("volume", 236.588),
    "cup": ("volume", 236.588),
    "cups": ("volume", 236.588),
    "pt": ("volume", 473.176),
    "pint": ("volume", 473.176),
    "pints": ("volume", 473.176),
    "qt": ("volume", 946.353),
    "qts": ("volume", 946.353),
    "quart": ("volume", 946.353),
    "quarts": ("volume", 946.353),
    "ct": ("count", 1.0),
    "count": ("count", 1.0),
    "each": ("count", 1.0),
    "ea": ("count", 1.0),
    "dozen": ("count", 12.0),
    "piece": ("count", 1.0),
    "pieces": ("count", 1.0),
}


PACKAGE_UNITS = r"(?:cans?|jars?|packages?|pkgs?\.?|boxes|box|bags?|cartons?|bottles?|containers?|tubs?)"
SIZED_PACKAGE_PATTERN = re.compile(
    rf"^\s*(\d+(?:\.\d+)?)\s*\(?\s*(\d+(?:\.\d+)?)\s*-?\s*(ounces?|oz\.?|pounds?|lbs?\.?|grams?|g)\s*\)?\s*{PACKAGE_UNITS}\b",
    re.IGNORECASE,
)


def parse_amount(value: str, ingredient_name: str = "") -> tuple[str, float] | None:
    # "2 15-ounce cans" / "2 (15 oz.) cans" -> total net weight.
    sized = SIZED_PACKAGE_PATTERN.match(value)
    if sized:
        factor = UNIT_FACTORS.get(sized.group(3).lower().rstrip("."))
        if factor:
            return factor[0], float(sized.group(1)) * float(sized.group(2)) * factor[1]
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
    elif not unit and re.search(r"\b(?:eggs?|tortillas?)\b", ingredient_name.lower()):
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
    if required and required[0] == "count" and (not package_size or package_size[0] != "count"):
        # Count-sold goods (tortillas, buns) often list weight as size and the count in the description.
        pack_count = re.search(
            r"\b(\d+)\s*(?:ct|count|pk|pack)\b",
            f"{item.get('size') or ''} {product.get('description') or ''}",
            flags=re.IGNORECASE,
        )
        if pack_count and int(pack_count.group(1)) > 0:
            package_size = ("count", float(pack_count.group(1)))
    package_only = re.match(rf"^\s*(\d+)\s+{PACKAGE_UNITS}\b", amount, flags=re.IGNORECASE)
    if not required and package_only:
        # "1 can" with no size: buy that many packages.
        package_count = max(1, int(package_only.group(1)))
        package_size_text = str(item.get("size") or "package")
        return price * package_count, f"{package_count} pack{'s' if package_count != 1 else ''} ({package_size_text} each)"
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


def normalize_shopping_item_name(name: str) -> str:
    without_homemade = re.sub(r"\bhomemade\b", "", name, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", without_homemade).strip(" ,-/")


def ingredient_label(name: str, amount: str) -> str:
    display_name = normalize_shopping_item_name(normalize_ingredient_name(name, amount))
    amount = format_recipe_amount(amount)
    required = parse_amount(amount, display_name)
    if "egg" in display_name.lower() and required and required == ("count", 1.0):
        display_name = re.sub(r"eggs\b", "egg", display_name, flags=re.IGNORECASE)
    return " ".join(part for part in (amount, display_name) if part)


def format_recipe_amount(value: str) -> str:
    match = re.match(r"^\s*(\d+\.\d+)(?=\s|$)", value)
    if not match:
        return value.strip()

    exact_fraction = Fraction(match.group(1))
    common_fraction = exact_fraction.limit_denominator(16)
    fraction = (
        common_fraction
        if abs(float(common_fraction - exact_fraction)) <= 0.001
        else exact_fraction
    )

    whole, numerator = divmod(fraction.numerator, fraction.denominator)
    if numerator == 0:
        formatted = str(whole)
    elif whole:
        formatted = f"{whole} {numerator}/{fraction.denominator}"
    else:
        formatted = f"{numerator}/{fraction.denominator}"
    return f"{formatted}{value[match.end():]}".strip()


PREPARED_CHICKEN_WORDS = r"(?:cooked|shredded|diced|chopped|cubed|pulled|leftover|roasted|grilled|boiled|poached)"


def chicken_product_term(name: str) -> str | None:
    """Recipes calling for cooked chicken are bought as raw meat; rotisserie and canned chicken are bought as-is."""
    lowered = name.lower().split(",", 1)[0]
    if not re.search(r"\bchicken\b", lowered) or re.search(r"\b(?:broth|stock|soup|bouillon|base)\b", lowered):
        return None
    if re.search(r"\brotisserie\b", lowered):
        return "rotisserie chicken"
    if re.search(r"\b(?:canned|can|tinned)\b", lowered):
        return "canned chicken breast"
    if not re.search(rf"\b{PREPARED_CHICKEN_WORDS}\b", lowered):
        return None
    cut = "thighs" if re.search(r"\bthighs?\b", lowered) else "breasts"
    return f"boneless skinless chicken {cut}"


# Kroger sells flour tortillas by named size rather than diameter.
TORTILLA_SIZES = ("street taco", "fajita", "soft taco", "burrito", "extra large burrito")
TORTILLA_SIZE_WORDS = (
    (r"\bstreet\s+taco|\bmini\b", "street taco"),
    (r"\bfajita", "fajita"),
    (r"\b(?:extra[\s-]+large|xl|grande|super\s+size)\b", "extra large burrito"),
    (r"\bburrito|\blarge\b", "burrito"),
    (r"\b(?:soft\s+)?taco|\bmedium\b|\bsmall\b", "soft taco"),
)


def tortilla_size(text: str) -> str:
    inches = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|\s)?\s*(?:inch|in\b|\")", text, flags=re.IGNORECASE)
    if inches:
        diameter = float(inches.group(1))
        if diameter <= 5:
            return "street taco"
        if diameter <= 7:
            return "fajita"
        if diameter <= 8.5:
            return "soft taco"
        if diameter <= 10.5:
            return "burrito"
        return "extra large burrito"
    for pattern, size in TORTILLA_SIZE_WORDS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return size
    return "soft taco"


def tortilla_product_term(name: str, amount: str = "") -> str | None:
    lowered = name.lower()
    if not re.search(r"\btortillas?\b", lowered) or re.search(r"\b(?:chips?|strips?|soup)\b", lowered):
        return None
    if re.search(r"\bcorn\b", lowered):
        return "white corn tortillas" if "white" in lowered else "corn tortillas"
    kind = "whole wheat" if re.search(r"\bwhole\s+wheat\b", lowered) else "flour"
    if re.search(r"\blow\s+carb\b", lowered):
        kind = f"low carb {kind}"
    return f"{tortilla_size(f'{name} {amount}')} {kind} tortillas"


# Preparation steps never change what is bought.
PREPARATION_WORDS = (
    r"cooked|uncooked|drained|undrained|rinsed|peeled|seeded|deseeded|cored|pitted|trimmed|stemmed|"
    r"softened|melted|beaten|whisked|warmed|thawed|defrosted|divided|sifted|packed|lightly|loosely|"
    r"chopped|finely|roughly|coarsely|thinly|freshly|torn|halved|quartered|"
    r"cut|into|pieces|bite-sized|bite\s+size|optional"
)
# Cut words describe a product form for packaged goods (diced tomatoes, shredded cheese)
# but only preparation for fresh produce and meat.
CUT_WORDS = r"diced|sliced|minced|cubed|julienned|shredded|grated|crushed|mashed"
CUT_WORD_PRODUCTS = (r"\b(?:crushed|stewed)\s+tomato", r"\bcrushed\s+red\s+pepper", r"\bmashed\s+potato")
CANNED_CONTEXT_PATTERN = r"\b(?:cans?|canned|tinned|jars?|jarred|ounces?|oz)\b|\d+-ounce"


def is_canned_diced_tomato(name: str, amount: str = "") -> bool:
    """Diced tomatoes are the canned product only when the recipe signals a can; otherwise they are fresh."""
    return bool(re.search(r"\bdiced\s+tomato", name, flags=re.IGNORECASE)) and bool(
        re.search(CANNED_CONTEXT_PATTERN, f"{name} {amount}", flags=re.IGNORECASE)
    )


def strip_preparation_words(name: str, amount: str = "") -> str:
    stripped = re.sub(r"\bat\s+room\s+temperature\b|\bto\s+taste\b|\bfor\s+(?:serving|garnish|topping)\b", " ", name, flags=re.IGNORECASE)
    stripped = re.sub(rf"\b(?:{PREPARATION_WORDS})\b", " ", stripped, flags=re.IGNORECASE)
    tokens = set(search_tokens(stripped))
    is_fresh = bool(tokens & (VEGETABLE_INGREDIENT_TOKENS | FRUIT_INGREDIENT_TOKENS | MEAT_INGREDIENT_TOKENS))
    # In a can or jar, the cut is the product form (diced green chiles, sliced jalapenos).
    keeps_form = bool(re.search(CANNED_CONTEXT_PATTERN, f"{name} {amount}", flags=re.IGNORECASE)) or any(
        re.search(pattern, stripped, flags=re.IGNORECASE) for pattern in CUT_WORD_PRODUCTS
    )
    if is_fresh and not keeps_form:
        stripped = re.sub(rf"\b(?:{CUT_WORDS})\b", " ", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\b(?:cans?|jars?)(?:\s+of)?\b", " ", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s+", " ", stripped).strip(" ,-/")
    stripped = re.sub(r"^(?:and|or)\s+|\s+(?:and|or)$", "", stripped, flags=re.IGNORECASE).strip()
    return stripped or name


LEADING_ADJECTIVES = {
    "boneless", "skinless", "large", "medium", "small", "fresh", "lean", "ripe", "firm", "plain",
    "unsalted", "salted", "low-sodium", "extra-virgin", "hot", "mild", "sweet", "dry", "whole",
}


def drop_trailing_notes(name: str) -> str:
    """Drop ", finely diced"-style notes, but keep adjective lists like "boneless, skinless chicken"."""
    parts = [part.strip() for part in name.split(",")]
    head = parts[0]
    index = 1
    while index < len(parts) and head and head.split()[-1].lower() in LEADING_ADJECTIVES:
        head = f"{head} {parts[index]}"
        index += 1
    # "whole" is a count word ("2 whole chicken breasts") except in product names.
    return re.sub(r"\bwhole\s+(?!milk|wheat|grain|chicken\b(?!\s+breasts?)|bean|kernel|foods|fryer)", "", head, flags=re.IGNORECASE).strip()


def product_search_term(ingredient_name: str, amount: str = "") -> str:
    # Preparation notes after a comma ("celery, finely diced") only confuse product search.
    normalized_name = normalize_shopping_item_name(drop_trailing_notes(ingredient_name))
    chicken_term = chicken_product_term(normalized_name)
    if chicken_term:
        return chicken_term
    tortilla_term = tortilla_product_term(normalized_name, amount)
    if tortilla_term:
        return tortilla_term
    normalized_name = strip_preparation_words(normalized_name, amount)
    # Generic oils are sold as vegetable oil; "cooking oil" otherwise matches cooking sprays.
    normalized_name = re.sub(r"^(?:cooking|neutral|frying)\s+oil$", "vegetable oil", normalized_name, flags=re.IGNORECASE)
    if not re.search(r"\beggs?\b", normalized_name, flags=re.IGNORECASE):
        normalized_name = re.sub(r"^(?:large|medium|small)\s+", "", normalized_name, flags=re.IGNORECASE)

    if not re.search(r"\bmince\b", normalized_name, flags=re.IGNORECASE):
        return normalized_name

    meat = re.sub(r"\bmince\b", "", normalized_name, flags=re.IGNORECASE).strip()
    return f"ground {meat}" if meat else "ground meat"


ALTERNATIVE_SYNONYMS = {
    "courgette": "zucchini",
    "aubergine": "eggplant",
    "scallions": "green onions",
    "scallion": "green onion",
    "spring onions": "green onions",
    "spring onion": "green onion",
    "coriander": "cilantro",
    "confectioners sugar": "powdered sugar",
    "icing sugar": "powdered sugar",
    "breadcrumbs": "bread crumbs",
}
DESCRIPTIVE_PRODUCT_WORDS = {
    "boneless", "skinless", "fresh", "frozen", "shredded", "cooked", "diced",
    "chopped", "sliced", "minced", "grated", "large", "medium", "small", "whole",
    "homemade", "organic", "ground",
}


def alternative_search_terms(ingredient_name: str, amount: str = "") -> list[str]:
    canonical = product_search_term(ingredient_name, amount)
    terms = []
    tortilla = re.fullmatch(rf"({'|'.join(TORTILLA_SIZES)}) (.+ tortillas)", canonical)
    if tortilla:
        # Neighbouring sizes first, then any size.
        position = TORTILLA_SIZES.index(tortilla.group(1))
        neighbours = sorted(
            (size for size in TORTILLA_SIZES if size != tortilla.group(1)),
            key=lambda size: abs(TORTILLA_SIZES.index(size) - position),
        )
        terms.extend(f"{size} {tortilla.group(2)}" for size in neighbours[:2])
        terms.append(tortilla.group(2))
    for source, alternative in ALTERNATIVE_SYNONYMS.items():
        if source in canonical.lower():
            terms.append(re.sub(re.escape(source), alternative, canonical, flags=re.IGNORECASE))

    simplified = " ".join(
        token for token in canonical.split()
        if token.lower() not in DESCRIPTIVE_PRODUCT_WORDS
    )
    if simplified and simplified.lower() != canonical.lower():
        terms.append(simplified)
    raw_chicken = re.fullmatch(r"boneless skinless (chicken (?:breasts|thighs))", canonical)
    if raw_chicken:
        terms.append(raw_chicken.group(1))
    return list(dict.fromkeys(term.strip() for term in terms if term.strip()))[:4]


# Words that make a product a different thing from what a recipe asked for, unless the recipe says so.
ALTERED_PRODUCT_PATTERNS = (
    r"refried", r"whipped", r"spread", r"neufchatel", r"reduced\s+fat", r"low\s+fat", r"fat\s+free",
    r"\blight\b", r"dairy[\s-]+free", r"non[\s-]+dairy", r"plant[\s-]+based", r"vegan", r"alternative",
    r"flavou?red", r"seasoned", r"ranch\s+style", r"chili\s+beans", r"sugar[\s-]+free", r"instant", r"\bmix\b", r"\bdip\b", r"\bsoup\b",
    r"\bbroth\b", r"\bbaby\b", r"\bsauce\b", r"\bspray\b", r"\bchips\b", r"\bsnacks?\b",
    r"\bnoodles?\b", r"\bramen\b", r"\bshaker\b",
)


def altered_product_penalty(product: dict[str, Any], query: str) -> int:
    description = str(product.get("description") or "")
    return sum(
        1 for pattern in ALTERED_PRODUCT_PATTERNS
        if re.search(pattern, description, flags=re.IGNORECASE)
        and not re.search(pattern, query, flags=re.IGNORECASE)
    )


def product_sort_key(product: dict[str, Any], amount: str, ingredient_name: str) -> tuple[int, int, bool, int, float]:
    estimated_cost, _ = product_cost(product, amount, ingredient_name)
    query = product_search_term(ingredient_name, amount)
    # Package-form words help the Kroger query but are rarely in product names.
    query_tokens = set(search_tokens(query)) - {"canned", "can", "tinned", "jarred"}
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
    altered = altered_product_penalty(product, f"{ingredient_name} {query}")
    # A product built around another main ingredient (tomatoes in "Tomatoes and Green Chiles") is a different item.
    named_tokens = query_tokens | set(search_tokens(ingredient_name))
    # Synonyms (scallions -> green onions) name the same ingredient, not an extra one.
    for source, alternative in ALTERNATIVE_SYNONYMS.items():
        if re.search(rf"\b{re.escape(source)}\b", query, flags=re.IGNORECASE):
            named_tokens |= set(search_tokens(alternative))
    extra_ingredients = (product_tokens & MAIN_INGREDIENT_TOKENS) - named_tokens
    altered += len(extra_ingredients)
    return missing_terms, altered, estimated_cost is None, egg_carton_preference, estimated_cost or 0.0


def search_tokens(value: str) -> list[str]:
    normalized = re.sub(r"\bbread\s+crumbs\b", "breadcrumbs", value.lower())
    tokens = re.findall(r"[a-z0-9]+", normalized)
    normalized_tokens = []
    for token in tokens:
        if token.endswith("ies") and len(token) > 4:
            token = f"{token[:-3]}y"
        elif token.endswith("oes") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            token = token[:-1]
        if token in {"chili", "chily", "chilli", "chile"}:
            token = "chile"
        normalized_tokens.append(token)
    return normalized_tokens


MEAT_INGREDIENT_TOKENS = {
    "bacon", "beef", "bison", "breast", "chicken", "drumstick", "ham", "lamb",
    "meat", "mince", "pork", "rib", "roast", "sausage", "steak", "tenderloin",
    "thigh", "turkey", "veal", "wing",
}
VEGETABLE_INGREDIENT_TOKENS = {
    "asparagus", "bean", "beet", "bell", "broccoli", "brussels", "cabbage",
    "carrot", "cauliflower", "celery", "chile", "corn", "cucumber", "eggplant", "garlic",
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


GENERIC_PRODUCE_WORDS = {"green", "red", "yellow", "white", "sweet", "bell", "pepper", "vegetable"}
MAIN_INGREDIENT_TOKENS = (
    VEGETABLE_INGREDIENT_TOKENS | FRUIT_INGREDIENT_TOKENS | MEAT_INGREDIENT_TOKENS
    | {"salmon", "tuna", "shrimp", "rice", "pasta", "cheese", "queso", "egg"}
) - GENERIC_PRODUCE_WORDS


def is_organic_product(product: dict[str, Any]) -> bool:
    searchable_text = " ".join(
        str(value or "") for value in (product.get("description"), product.get("brand"))
    )
    return bool(re.search(r"\borganic\b", searchable_text, flags=re.IGNORECASE))


def is_non_gmo_product(product: dict[str, Any]) -> bool:
    claim = str(product.get("nonGmoClaimName") or "").upper()
    return product.get("nonGmo") is True and "PRODUCT LABELED WITH NON-GMO CLAIM" in claim


PACKAGED_PRODUCE_PATTERN = (
    r"\b(?:cans?|canned|jars?|jarred|frozen|dried|dry|sauce|paste|puree|soup|juice|salsa|chips|powder|"
    r"refried|(?:crushed|stewed)\s+tomato(?:es)?|"
    r"(?:pinto|black|kidney|navy|cannellini|garbanzo|great\s+northern|lima|white|baked|butter)\s+beans?)\b"
)


def is_fresh_produce_ingredient(ingredient_name: str, amount: str = "") -> bool:
    """Fresh fruit and vegetables; canned, frozen, dried and dry-legume products are packaged goods."""
    if re.search(PACKAGED_PRODUCE_PATTERN, f"{ingredient_name} {amount}", flags=re.IGNORECASE):
        return False
    if is_canned_diced_tomato(ingredient_name, amount):
        return False
    tokens = set(search_tokens(ingredient_name))
    return bool(tokens & (VEGETABLE_INGREDIENT_TOKENS | FRUIT_INGREDIENT_TOKENS))


# Kroger's own labels; the API's brand filter matches only one exact name, so filter locally.
KROGER_OWN_BRANDS = {
    "kroger", "simple truth", "simple truth organic", "simple truth plant based", "private selection",
    "heritage farm", "smart way", "psst", "bakery fresh goodness", "kroger deli",
}


def is_kroger_brand(product: dict[str, Any]) -> bool:
    return str(product.get("brand") or "").strip().lower() in KROGER_OWN_BRANDS


CHEESE_INGREDIENT_TOKENS = {
    "asiago", "brie", "cheddar", "cheese", "colby", "feta", "fontina", "gouda",
    "gruyere", "havarti", "jack", "manchego", "mozzarella", "parmesan", "pecorino",
    "provolone", "ricotta", "romano", "swiss",
}


def is_cheese_ingredient(ingredient_name: str) -> bool:
    tokens = set(search_tokens(ingredient_name))
    return bool(tokens & CHEESE_INGREDIENT_TOKENS) and not bool(tokens & {"sauce", "dip", "powder"})


def is_block_cheese_product(product: dict[str, Any]) -> bool:
    description = str(product.get("description") or "").lower()
    return (
        bool(re.search(r"\b(?:block|brick|chunk)\b", description))
        and not bool(re.search(r"\b(?:shredded|grated|sliced)\b", description))
    )


SOFT_CHEESE_TOKENS = {"brie", "cottage", "cream", "feta", "goat", "mascarpone", "ricotta"}
PRE_CUT_CHEESE_PATTERN = r"\b(?:shredded|grated|sliced|crumbled)\b"


def prefers_block_for_ingredient(ingredient_name: str, prefer_block_cheese: bool) -> bool:
    return (
        prefer_block_cheese
        and is_cheese_ingredient(ingredient_name)
        and not set(search_tokens(ingredient_name)) & SOFT_CHEESE_TOKENS
    )


def strip_pre_cut_words(term: str) -> str:
    return re.sub(r"\s+", " ", re.sub(PRE_CUT_CHEESE_PATTERN, "", term, flags=re.IGNORECASE)).strip()


def block_cheese_rank(product: dict[str, Any]) -> int:
    if is_block_cheese_product(product):
        return 0
    description = str(product.get("description") or "")
    return 2 if re.search(PRE_CUT_CHEESE_PATTERN, description, flags=re.IGNORECASE) else 1


class ProductFilters(BaseModel):
    kroger_brands_only: bool = False
    organic_meat_only: bool = False
    organic_vegetables_only: bool = False
    non_gmo_only: bool = False
    prefer_block_cheese: bool = False
    # Words or brands that must never be bought (matched as whole words in description or brand).
    exclude_terms: list[str] = Field(default_factory=list, max_length=20)
    max_package_price: float | None = Field(default=None, gt=0, le=1000)


def is_excluded_product(product: dict[str, Any], filters: ProductFilters) -> bool:
    text = f"{product.get('description') or ''} {product.get('brand') or ''}"
    for term in filters.exclude_terms:
        term = term.strip()[:40]
        if term and re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags=re.IGNORECASE):
            return True
    if filters.max_package_price is not None:
        price = product_price(product)
        if price is None or price > filters.max_package_price:
            return True
    return False


def rank_products(
    products: list[dict[str, Any]], name: str, amount: str, filters: ProductFilters
) -> list[dict[str, Any]]:
    block_preferred = prefers_block_for_ingredient(name, filters.prefer_block_cheese)
    sort_name = strip_pre_cut_words(name) if block_preferred else name

    def key(candidate: dict[str, Any]) -> tuple:
        rank = block_cheese_rank(candidate) if block_preferred else 0
        missing, altered, *rest = product_sort_key(candidate, amount, sort_name)
        return (missing, altered, rank, *rest)

    return sorted(products, key=key)


def search_ingredient_products(
    client: KrogerAPI,
    name: str,
    amount: str,
    store_id: str,
    filters: ProductFilters,
    base_term: str | None = None,
    limit: int = 10,
    related_terms: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Search Kroger for one ingredient, applying every user filter. Returns ranked products."""
    term = base_term or product_search_term(name, amount)
    # Classify on both the ingredient and the search term so synonyms (courgette -> zucchini)
    # keep the user's organic and fresh-produce rules.
    classify_text = " ".join([name, term, *(related_terms or [])])
    organic_filter = organic_filter_for_ingredient(
        classify_text, filters.organic_meat_only, filters.organic_vegetables_only
    )
    block_preferred = prefers_block_for_ingredient(name, filters.prefer_block_cheese)
    if block_preferred:
        term = strip_pre_cut_words(term)
    if organic_filter and not re.search(r"\borganic\b", term, flags=re.IGNORECASE):
        term = f"organic {term}"
    if filters.non_gmo_only:
        term = f"non gmo {term}"
    kroger_brands_only = filters.kroger_brands_only and not is_fresh_produce_ingredient(classify_text, amount)

    terms = [f"{term} block", term] if block_preferred else [term]
    products: list[dict[str, Any]] = []
    for search_term in terms:
        if kroger_brands_only:
            found = [
                candidate
                for candidate in client.search_product(term=search_term, location_id=store_id, limit=50)
                if is_kroger_brand(candidate)
            ] or client.search_product(term=search_term, location_id=store_id, limit=limit, brand="Kroger")
        else:
            found = client.search_product(term=search_term, location_id=store_id, limit=limit)
        if organic_filter:
            found = [candidate for candidate in found if is_organic_product(candidate)]
        if filters.non_gmo_only:
            found = [candidate for candidate in found if is_non_gmo_product(candidate)]
        if filters.exclude_terms or filters.max_package_price is not None:
            found = [candidate for candidate in found if not is_excluded_product(candidate, filters)]
        products.extend(found)
        if block_preferred and any(is_block_cheese_product(candidate) for candidate in found):
            break

    unique: dict[str, dict[str, Any]] = {}
    for candidate in products:
        unique.setdefault(str(candidate.get("upc") or candidate.get("productId") or id(candidate)), candidate)
    return rank_products(list(unique.values()), name, amount, filters), organic_filter


def serialize_product(product: dict[str, Any], amount: str, name: str) -> dict[str, Any]:
    item = (product.get("items") or [{}])[0]
    estimated_cost, purchase_note = product_cost(product, amount, name)
    return {
        "description": product.get("description"),
        "brand": product.get("brand"),
        "size": item.get("size"),
        "upc": product.get("upc"),
        "price": product_price(product),
        "estimated_cost": estimated_cost,
        "purchase_note": purchase_note,
        "quantity": purchase_quantity(purchase_note),
    }


def purchase_quantity(purchase_note: str | None) -> int:
    """Packages to put in the cart: the "N packs" from the cost estimate, else one."""
    match = re.match(r"^(\d+) packs?\b", purchase_note or "")
    return max(1, int(match.group(1))) if match else 1


def raise_kroger_http_error(error: Exception, action: str) -> None:
    if isinstance(error, SecureCredentialError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    if isinstance(error, KrogerAuthenticationError):
        raise HTTPException(status_code=502, detail=str(error)) from error
    raise HTTPException(
        status_code=502,
        detail=f"Kroger could not complete the {action}. Check the store and try again.",
    ) from error


class ProductSearchRequest(ProductFilters):
    # Each ingredient: {"name", "amount", optional "restock": true to buy a pantry seasoning anyway}.
    ingredients: list[dict[str, str | bool | None]] = Field(min_length=1, max_length=30)
    store_id: str = Field(min_length=1, max_length=20)
    skip_pantry_seasonings: bool = False


class KrogerCredentialsRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=500)
    client_secret: str = Field(min_length=1, max_length=2000)


class ProductAlternativeRequest(ProductFilters):
    ingredient_name: str = Field(min_length=1, max_length=100)
    amount: str = Field(default="", max_length=100)
    store_id: str = Field(min_length=1, max_length=20)


class StoreSearchRequest(BaseModel):
    zip_code: str = Field(min_length=5, max_length=10)


RecipeSource = Literal["themealdb", "budgetbytes", "allrecipes", "pioneerwoman"]
RECIPE_PAGE_HOSTS = {
    "budgetbytes": "www.budgetbytes.com",
    "allrecipes": "www.allrecipes.com",
    "pioneerwoman": "www.thepioneerwoman.com",
}


class RecipeSearchRequest(BaseModel):
    meals: list[str] = Field(min_length=1, max_length=10)
    sources: list[RecipeSource] = Field(
        default_factory=lambda: ["themealdb", "budgetbytes", "allrecipes", "pioneerwoman"], min_length=1
    )


class RecipeImportRequest(BaseModel):
    source: RecipeSource
    recipe_id: str = Field(min_length=1, max_length=500)
    skip_pantry_seasonings: bool = True


@app.get("/api/settings/kroger")
def get_kroger_settings() -> dict[str, bool]:
    try:
        credentials = load_kroger_credentials()
    except SecureCredentialError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"configured": credentials is not None}


@app.put("/api/settings/kroger")
def put_kroger_settings(request: KrogerCredentialsRequest) -> dict[str, bool]:
    client_id = request.client_id.strip()
    try:
        previous = load_kroger_credentials()
        if previous is None or previous[0] != client_id:
            # Account tokens belong to the old Kroger app; they cannot be refreshed with new credentials.
            clear_kroger_account_tokens()
        save_kroger_credentials(client_id, request.client_secret)
    except SecureCredentialError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"configured": True}


@app.delete("/api/settings/kroger")
def delete_kroger_settings() -> dict[str, bool]:
    try:
        clear_kroger_account_tokens()
        clear_kroger_credentials()
    except SecureCredentialError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"configured": False}


class StorePreference(BaseModel):
    id: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    name: str = Field(min_length=1, max_length=200)


@app.get("/api/preferences/store")
def get_store_preference() -> dict[str, Any]:
    with database.connect() as connection:
        row = connection.execute("SELECT value FROM preferences WHERE name = 'store'").fetchone()
    if row is None:
        return {"store": None}
    try:
        return {"store": StorePreference(**json.loads(row["value"])).model_dump()}
    except (ValueError, TypeError):
        return {"store": None}


@app.put("/api/preferences/store")
def put_store_preference(store: StorePreference) -> dict[str, Any]:
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO preferences(name, value) VALUES('store', ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            (json.dumps(store.model_dump()),),
        )
    return {"store": store.model_dump()}


# --- Linked Dillons/Kroger shopper account (OAuth authorization-code flow) ---

ACCOUNT_STATE_TTL_SECONDS = 600
ACCOUNT_TOKEN_REFRESH_MARGIN_SECONDS = 60
LOCAL_APP_HOSTS = {"localhost", "127.0.0.1"}
_pending_account_states: dict[str, tuple[str, float]] = {}
_account_lock = threading.Lock()


class KrogerAccountConnectRequest(BaseModel):
    return_to: str = Field(default="http://127.0.0.1:5173/", max_length=200)


class CartItem(BaseModel):
    upc: str = Field(pattern=r"^\d{8,14}$")
    quantity: int = Field(default=1, ge=1, le=99)


class CartAddRequest(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=100)
    modality: Literal["PICKUP", "DELIVERY"] = "PICKUP"


def local_return_url(value: str) -> str:
    """Only send the browser back to this local app, never to an arbitrary site."""
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in LOCAL_APP_HOSTS:
        raise HTTPException(status_code=400, detail="The return address must be this local app.")
    return f"{parsed.scheme}://{parsed.netloc}/"


def account_access_token(force_refresh: bool = False) -> str:
    """Return a valid shopper access token, refreshing it when needed. Raises 401 when not linked."""
    with _account_lock:
        tokens = load_kroger_account_tokens()
        if not tokens or not tokens.get("access_token"):
            raise HTTPException(status_code=401, detail="Connect your Dillons account in Settings first.")
        fresh = float(tokens.get("expires_at") or 0) - ACCOUNT_TOKEN_REFRESH_MARGIN_SECONDS > time.time()
        if fresh and not force_refresh:
            return tokens["access_token"]
        if not tokens.get("refresh_token"):
            clear_kroger_account_tokens()
            raise HTTPException(status_code=401, detail="Your Dillons sign-in expired. Reconnect it in Settings.")
        try:
            tokens = refresh_account_token(tokens["refresh_token"])
        except KrogerAccountAuthorizationError as error:
            clear_kroger_account_tokens()
            raise HTTPException(
                status_code=401, detail="Your Dillons sign-in expired. Reconnect it in Settings."
            ) from error
        save_kroger_account_tokens(tokens)
        return tokens["access_token"]


@app.get("/api/kroger/account")
def get_kroger_account() -> dict[str, Any]:
    try:
        connected = bool(load_kroger_account_tokens())
    except SecureCredentialError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"connected": connected, "redirect_uri": KROGER_REDIRECT_URI}


@app.post("/api/kroger/account/connect")
def connect_kroger_account(request: KrogerAccountConnectRequest) -> dict[str, str]:
    return_to = local_return_url(request.return_to)
    state = secrets.token_urlsafe(24)
    now = time.time()
    try:
        url = account_authorization_url(state)
    except SecureCredentialError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    with _account_lock:
        for key, (_, created) in list(_pending_account_states.items()):
            if now - created > ACCOUNT_STATE_TTL_SECONDS:
                del _pending_account_states[key]
        _pending_account_states[state] = (return_to, now)
    return {"authorize_url": url}


@app.get("/api/kroger/callback")
def kroger_account_callback(state: str = "", code: str = "", error: str = "") -> RedirectResponse:
    with _account_lock:
        pending = _pending_account_states.pop(state, None) if state else None
    if pending is None or time.time() - pending[1] > ACCOUNT_STATE_TTL_SECONDS:
        raise HTTPException(status_code=400, detail="This sign-in link expired. Start again from Settings.")
    return_to = pending[0]

    def back(result: str) -> RedirectResponse:
        return RedirectResponse(f"{return_to}?{urlencode({'kroger_account': result})}", status_code=303)

    if error or not code:
        return back("denied" if error == "access_denied" else "error")
    try:
        tokens = exchange_account_code(code)
        save_kroger_account_tokens(tokens)
    except (KrogerAccountAuthorizationError, SecureCredentialError, requests.RequestException):
        return back("error")
    return back("connected")


@app.delete("/api/kroger/account")
def disconnect_kroger_account() -> dict[str, bool]:
    try:
        clear_kroger_account_tokens()
    except SecureCredentialError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"connected": False}


@app.post("/api/cart/add")
def add_to_cart(request: CartAddRequest) -> dict[str, int]:
    items = [
        {"upc": item.upc, "quantity": item.quantity, "modality": request.modality}
        for item in request.items
    ]
    client = KrogerAPI()
    try:
        try:
            client.add_items_to_cart(account_access_token(), items)
        except KrogerAccountAuthorizationError:
            # The token may have been revoked early; refresh once before giving up.
            client.add_items_to_cart(account_access_token(force_refresh=True), items)
    except KrogerAccountAuthorizationError as error:
        clear_kroger_account_tokens()
        raise HTTPException(
            status_code=401, detail="Dillons rejected your sign-in. Reconnect your account in Settings."
        ) from error
    except SecureCredentialError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail="Dillons could not add the items to your cart.") from error
    return {"added": len(items), "packages": sum(item["quantity"] for item in items)}


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


class RecipeSearchLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._anchor_text: list[str] = []
        self._heading_text: list[str] = []
        self._inside_heading = False
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._anchor_text = []
            self._heading_text = []
            self._inside_heading = False
        elif tag == "h3" and self._href:
            self._inside_heading = True

    def handle_data(self, data: str) -> None:
        if self._href:
            text = data.strip()
            if text:
                self._anchor_text.append(text)
                if self._inside_heading:
                    self._heading_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h3":
            self._inside_heading = False
        elif tag == "a" and self._href:
            title = " ".join(self._heading_text or self._anchor_text)
            self.links.append((self._href, title))
            self._href = None


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
        r"cups?|c\.|qts?\.?|quarts?|pts?\.?|pints?|"
        r"tbsp\.?|tablespoons?|tsp\.?|teaspoons?|cloves?|cans?|jars?|slices?|"
        r"pieces?|breasts?|stalks?|sprigs?|leaves?|bunches?|pinches?|packages?|pkgs?)"
    )
    # Package sizes such as "(9-inch)" or "8-ounce" in "2 8-ounce cans tomato sauce".
    package_size_pattern = (
        r"(?:\(\s*[\d./]+\s*-?\s*(?:inch|in\.?|ounces?|oz\.?|pounds?|lbs?\.?)\s*\)"
        r"|[\d./]+-(?:inch|ounces?|oz|pounds?|lbs?)\b)"
    )
    amount_pattern = re.compile(
        rf"^\s*((?:\d+\s+)?(?:\d+\s*/\s*\d+|\d+(?:\s*[¼½¾⅓⅔⅛⅜⅝⅞]|\.\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞])"
        rf"(?:\s+{package_size_pattern})?(?:\s+{unit_pattern})?)\s+(.+?)\s*$",
        re.IGNORECASE,
    )
    match = amount_pattern.match(value)
    if not match:
        return {"name": value.strip(), "amount": ""}

    amount, name = match.groups()
    name = re.sub(r"\s+\$[\d.]+\*?\s*$", "", name).split("(", 1)[0].strip(" ,.;")
    return {"name": name or value.strip(), "amount": format_recipe_amount(amount)}


GENERIC_PANTRY_SEASONING_PATTERNS = (
    r"(?:kosher |sea |table )?salt(?: and (?:freshly (?:cracked |ground )?)?(?:black |white )?pepper)?(?: to taste)?",
    r"(?:freshly cracked |ground )?(?:black|white) pepper",
    r"olive oil",
    r"cooking spray",
    r"extra virgin olive oil",
    r"(?:crushed )?red pepper(?: flakes)?",
    r"(?:vegetable|canola|neutral|cooking) oil",
    r"baking (?:soda|powder)",
    r"(?:apple cider|white|distilled white|rice|red wine|white wine|balsamic) vinegar",
    r"(?:low sodium |reduced sodium |light |dark )?soy sauce",
    r"(?:grated |minced |fresh |grated fresh |minced fresh |fresh grated )?ginger(?: root)?",
    r"(?:dried )?(?:oregano|thyme|rosemary|marjoram|sage)",
    r"italian seasoning",
    r"garlic powder",
    r"onion powder",
    r"(?:smoked )?paprika",
    r"ground cumin",
    r"cumin",
    r"chili powder",
    r"cayenne pepper",
    r"bay leaves?",
)


def is_generic_pantry_seasoning(name: str) -> bool:
    # Drop trailing notes such as ", plus more to taste" or ", for serving".
    name = name.split(",", 1)[0].split("(", 1)[0]
    normalized = re.sub(r"[^a-z ]", " ", name.lower())
    normalized = " ".join(normalized.split())
    normalized = re.sub(r"^(?:freshly cracked|ground|dried)\s+", "", normalized)
    if re.search(r"\b(?:taco seasoning|saffron|garam masala|zaatar|za atar|cajun seasoning|five spice)\b", normalized):
        return False
    return any(re.fullmatch(pattern, normalized) for pattern in GENERIC_PANTRY_SEASONING_PATTERNS)


def split_recipe_shopping_ingredients(
    ingredients: list[dict[str, str]],
    skip_pantry_seasonings: bool,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if not skip_pantry_seasonings:
        return ingredients, []

    shopping_ingredients = []
    excluded_seasonings = []
    for ingredient in ingredients:
        if is_generic_pantry_seasoning(ingredient["name"]):
            excluded_seasonings.append(ingredient)
        else:
            shopping_ingredients.append(ingredient)
    return shopping_ingredients, excluded_seasonings


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
                    "amount": format_recipe_amount(
                        str(meal.get(f"strMeasure{index}") or "").strip()
                    ),
                }
                for index in range(1, 21)
                if str(meal.get(f"strIngredient{index}") or "").strip()
            ]
            return {
                "title": meal.get("strMeal") or "Recipe",
                "url": meal.get("strSource") or f"https://www.themealdb.com/meal/{request.recipe_id}",
                "ingredients": ingredients,
                "instructions": normalize_recipe_instructions(meal.get("strInstructions")),
                "servings": "",
            }

        recipe_url = request.recipe_id
        parsed_url = urlparse(recipe_url)
        allowed_host = RECIPE_PAGE_HOSTS[request.source]
        if parsed_url.scheme != "https" or parsed_url.hostname != allowed_host:
            raise HTTPException(status_code=422, detail=f"Only {allowed_host} recipe links can be imported.")

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
            # Saved for the user's personal cooking view; the source URL is kept and shown alongside.
            "instructions": normalize_recipe_instructions(recipe.get("recipeInstructions")),
            "servings": normalize_recipe_yield(recipe.get("recipeYield")),
        }
    except requests.RequestException as error:
        raise HTTPException(status_code=502, detail="The recipe source could not be reached.") from error


PIONEER_WOMAN_HOST = "www.thepioneerwoman.com"
PIONEER_WOMAN_SITEMAP_INDEX = f"https://{PIONEER_WOMAN_HOST}/sitemap_index.xml"
PIONEER_WOMAN_INDEX_TTL_SECONDS = 24 * 60 * 60
PIONEER_WOMAN_SLUG_NOISE = {"recipe", "recipes", "best", "easy", "how", "make", "to"}
_pioneer_woman_index: dict[str, Any] = {"loaded_at": 0.0, "recipes": []}
_pioneer_woman_index_lock = threading.Lock()


def parse_pioneer_woman_sitemap(xml_text: str) -> list[dict[str, Any]]:
    """Extract recipe pages from the content sitemap. Regex keeps us clear of XML entity expansion."""
    recipes = []
    for block in re.findall(r"<url>(.*?)</url>", xml_text, flags=re.DOTALL):
        loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
        if not loc:
            continue
        url = html.unescape(loc.group(1))
        parsed = urlparse(url)
        match = re.fullmatch(r"/food-cooking/recipes/a\d+/([a-z0-9-]+)/?", parsed.path)
        if parsed.scheme != "https" or parsed.hostname != PIONEER_WOMAN_HOST or not match:
            continue
        slug_words = [word for word in match.group(1).split("-") if word]
        if slug_words and slug_words[-1] == "recipe":
            slug_words = slug_words[:-1]
        if not slug_words:
            continue
        image = re.search(r"<image:loc>\s*([^<\s]+)\s*</image:loc>", block)
        recipes.append(
            {
                "id": url,
                "title": " ".join(word.capitalize() for word in slug_words),
                "url": url,
                "image": html.unescape(image.group(1)) if image else None,
                "tokens": set(search_tokens(" ".join(slug_words))),
            }
        )
    return recipes


def load_pioneer_woman_index() -> list[dict[str, Any]]:
    # The site's /search path is disallowed in robots.txt, so search a cached copy of the public sitemap.
    with _pioneer_woman_index_lock:
        if (
            _pioneer_woman_index["recipes"]
            and time.monotonic() - _pioneer_woman_index["loaded_at"] < PIONEER_WOMAN_INDEX_TTL_SECONDS
        ):
            return _pioneer_woman_index["recipes"]

        headers = {"User-Agent": "DinnerReciprator/0.1 (+local recipe search)"}
        index_response = requests.get(PIONEER_WOMAN_SITEMAP_INDEX, headers=headers, timeout=10)
        index_response.raise_for_status()
        sitemap_urls = [
            html.unescape(url)
            for url in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", index_response.text)
            if "/sitemaps/content." in url and urlparse(html.unescape(url)).hostname == PIONEER_WOMAN_HOST
        ]
        recipes: list[dict[str, Any]] = []
        for sitemap_url in sitemap_urls:
            response = requests.get(sitemap_url, headers=headers, timeout=20)
            response.raise_for_status()
            body = response.content
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            recipes.extend(parse_pioneer_woman_sitemap(body.decode("utf-8", errors="replace")))
        if not recipes:
            raise ValueError("No Pioneer Woman recipes were found in the sitemap.")
        _pioneer_woman_index.update(loaded_at=time.monotonic(), recipes=recipes)
        return recipes


def rank_pioneer_woman_recipes(meal: str, recipes: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    query_tokens = set(search_tokens(meal)) - PIONEER_WOMAN_SLUG_NOISE
    if not query_tokens:
        return []
    matches = [recipe for recipe in recipes if query_tokens <= recipe["tokens"]]
    matches.sort(key=lambda recipe: (len(recipe["tokens"] - query_tokens), recipe["title"]))
    return [
        {key: recipe[key] for key in ("id", "title", "url", "image")}
        for recipe in matches[:limit]
    ]


def search_pioneer_woman_recipes(meal: str) -> list[dict[str, Any]]:
    return rank_pioneer_woman_recipes(meal, load_pioneer_woman_index())


def search_recipe_source(meal: str, source: str) -> dict[str, Any]:
    source_name = {
        "themealdb": "TheMealDB",
        "budgetbytes": "Budget Bytes",
        "allrecipes": "Allrecipes",
        "pioneerwoman": "The Pioneer Woman",
    }[source]

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
        elif source == "budgetbytes":
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
        elif source == "pioneerwoman":
            recipes = search_pioneer_woman_recipes(meal)
        else:
            response = requests.get(
                "https://www.allrecipes.com/search",
                params={"q": meal},
                headers={"User-Agent": "DinnerReciprator/0.1 (+local recipe search)", "Accept": "text/html"},
                timeout=10,
            )
            response.raise_for_status()
            parser = RecipeSearchLinkParser()
            parser.feed(response.text)
            recipes = []
            seen_urls = set()
            for href, title in parser.links:
                recipe_url = urljoin("https://www.allrecipes.com", href)
                parsed_url = urlparse(recipe_url)
                is_recipe_path = "/recipe/" in parsed_url.path or bool(
                    re.search(r"-recipe-\d+(?:/|$)", parsed_url.path)
                )
                if parsed_url.hostname != "www.allrecipes.com" or not is_recipe_path or recipe_url in seen_urls:
                    continue
                title = re.sub(r"\s+\d[\d,]*\s+Ratings?$", "", title, flags=re.IGNORECASE).strip()
                title = re.sub(r"^Save Recipe\s+", "", title, flags=re.IGNORECASE).strip()
                if not title:
                    continue
                recipes.append({"id": recipe_url, "title": title, "url": recipe_url})
                seen_urls.add(recipe_url)
                if len(recipes) == 5:
                    break
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
    except SecureCredentialError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except KrogerAuthenticationError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
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
    shopping_ingredients, excluded_seasonings = split_recipe_shopping_ingredients(
        recipe["ingredients"], request.skip_pantry_seasonings
    )
    return {
        **recipe,
        "saved_recipe": saved_recipe,
        "shopping_ingredients": shopping_ingredients,
        "excluded_seasonings": [ingredient["name"] for ingredient in excluded_seasonings],
        "excluded_ingredients": excluded_seasonings,
    }


@app.get("/api/recipes/saved")
def list_saved_recipes() -> dict[str, list[dict[str, Any]]]:
    with database.connect() as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM saved_recipes ORDER BY saved_at DESC, id DESC"
        ).fetchall()
    return {"recipes": [serialize_saved_recipe(row) for row in rows]}


@app.post("/api/recipes/saved/{recipe_id}/refresh")
def refresh_saved_recipe(recipe_id: int) -> dict[str, Any]:
    """Re-fetch a saved recipe from its source (e.g. to fill in instructions saved before they were kept)."""
    with database.connect() as connection:
        row = connection.execute(
            "SELECT source, source_recipe_id FROM saved_recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Saved recipe was not found.")
    recipe = import_recipe_ingredients(RecipeImportRequest(source=row[0], recipe_id=row[1]))
    return {"saved_recipe": save_recipe_record(row[0], row[1], recipe)}


@app.delete("/api/recipes/saved/{recipe_id}")
def delete_saved_recipe(recipe_id: int) -> dict[str, bool]:
    with database.connect() as connection:
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
            restock = ingredient.get("restock") is True
            if request.skip_pantry_seasonings and not restock and is_generic_pantry_seasoning(name):
                results.append(
                    {
                        "ingredient": ingredient_label(name, amount),
                        "name": name,
                        "amount": amount,
                        "pantry_skipped": True,
                        "organic_filter": None,
                        "non_gmo_only": request.non_gmo_only,
                        "product": None,
                    }
                )
                continue

            products, organic_filter = search_ingredient_products(
                client, name, amount, request.store_id.strip(), request
            )
            product = products[0] if products else None
            results.append(
                {
                    "ingredient": ingredient_label(name, amount),
                    "name": name,
                    "amount": amount,
                    "organic_filter": organic_filter,
                    "non_gmo_only": request.non_gmo_only,
                    "product": serialize_product(product, amount, name) if product else None,
                }
            )
    except (SecureCredentialError, KrogerAuthenticationError, requests.RequestException) as error:
        raise_kroger_http_error(error, "product search")

    return {"results": results}


@app.post("/api/search/alternatives")
def search_product_alternatives(request: ProductAlternativeRequest) -> dict[str, Any]:
    name = request.ingredient_name.strip()
    amount = request.amount.strip()
    terms = alternative_search_terms(name, amount)
    canonical = product_search_term(name, amount)
    if canonical not in terms:
        terms.append(canonical)

    client = KrogerAPI()
    alternatives: list[dict[str, Any]] = []
    seen: set[str] = set()
    organic_filter = None
    try:
        client.authenticate_client()
        for term in terms:
            products, organic_filter = search_ingredient_products(
                client, name, amount, request.store_id.strip(), request,
                base_term=term, limit=25, related_terms=terms,
            )
            for product in products:
                key = str(product.get("upc") or product.get("productId") or "")
                if key in seen:
                    continue
                seen.add(key)
                alternatives.append({**serialize_product(product, amount, name), "search_term": term})
    except (SecureCredentialError, KrogerAuthenticationError, requests.RequestException) as error:
        raise_kroger_http_error(error, "alternative search")

    alternatives = [
        alt for _, alt in sorted(
            enumerate(alternatives),
            key=lambda pair: (pair[1]["estimated_cost"] is None, pair[0]),
        )
    ][:8]
    return {
        "ingredient": ingredient_label(name, amount),
        "search_terms": terms,
        "organic_filter": organic_filter,
        "alternatives": alternatives,
    }


class MealPlanRequest(ProductFilters):
    recipe_ids: list[int] = Field(min_length=1, max_length=14)
    # Optional per-recipe multiplier, e.g. {"4": 2} doubles recipe 4.
    recipe_scales: dict[int, Literal[1, 2, 3, 4]] = Field(default_factory=dict)
    store_id: str = Field(min_length=1, max_length=20)
    skip_pantry_seasonings: bool = True


def combine_plan_amounts(amounts: list[str], name: str) -> str:
    """Sum amounts that share a dimension ("1 c." + "2 tbsp." -> ml); otherwise list them."""
    present = [amount for amount in amounts if amount]
    if not present:
        return ""
    if len(present) == 1:
        return present[0]
    parsed = [parse_amount(amount, name) for amount in present]
    if all(parsed) and len({dimension for dimension, _ in parsed}) == 1:
        dimension = parsed[0][0]
        total = sum(value for _, value in parsed)
        unit = {"mass": "g", "volume": "ml", "count": "each"}[dimension]
        return f"{round(total, 1):g} {unit}"
    packages = [re.match(rf"^\s*(\d+)\s+{PACKAGE_UNITS}\b", amount, flags=re.IGNORECASE) for amount in present]
    if all(packages):
        return f"{sum(int(match.group(1)) for match in packages)} packages"
    return " + ".join(present)


def plan_items(recipes: list[dict[str, Any]], skip_pantry_seasonings: bool) -> tuple[list[dict], list[dict]]:
    """Merge ingredients across recipes by product search term; split out pantry seasonings."""
    grouped: dict[str, dict[str, Any]] = {}
    skipped: dict[str, dict[str, Any]] = {}
    for recipe in recipes:
        for ingredient in recipe["ingredients"]:
            name = str(ingredient.get("name") or "").strip()
            amount = str(ingredient.get("amount") or "").strip()
            if not name:
                continue
            target = skipped if skip_pantry_seasonings and is_generic_pantry_seasoning(name) else grouped
            key = product_search_term(name, amount).lower()
            entry = target.setdefault(key, {"name": name, "amounts": [], "meals": []})
            entry["amounts"].append(amount)
            if recipe["title"] not in entry["meals"]:
                entry["meals"].append(recipe["title"])
    for entry in [*grouped.values(), *skipped.values()]:
        entry["amount"] = combine_plan_amounts(entry["amounts"], entry["name"])
        entry["amount_text"] = " + ".join(amount for amount in entry["amounts"] if amount)
        entry["ingredient"] = ingredient_label(entry["name"], entry["amount_text"])
        del entry["amounts"]
    return list(grouped.values()), list(skipped.values())


def load_saved_recipes(recipe_ids: list[int]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in recipe_ids)
    with database.connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM saved_recipes WHERE id IN ({placeholders})", recipe_ids
        ).fetchall()
    return [serialize_saved_recipe(row) for row in rows]


@app.post("/api/plan")
def build_meal_plan(request: MealPlanRequest) -> dict[str, Any]:
    recipes = load_saved_recipes(request.recipe_ids)
    if not recipes:
        raise HTTPException(status_code=404, detail="None of the selected recipes were found.")
    for recipe in recipes:
        factor = request.recipe_scales.get(recipe["id"], 1)
        recipe["scale"] = factor
        if factor != 1:
            recipe["ingredients"] = [
                {**ingredient, "amount": scale_amount(str(ingredient.get("amount") or ""), factor)}
                for ingredient in recipe["ingredients"]
            ]
            recipe["title"] = f"{recipe['title']} (×{factor})"

    items, skipped = plan_items(recipes, request.skip_pantry_seasonings)
    if len(items) > 120:
        raise HTTPException(status_code=422, detail="Too many ingredients; select fewer meals.")

    client = KrogerAPI()
    store_id = request.store_id.strip()

    def match(item: dict[str, Any]) -> dict[str, Any]:
        products, organic_filter = search_ingredient_products(
            client, item["name"], item["amount"], store_id, request
        )
        product = products[0] if products else None
        return {
            **item,
            "organic_filter": organic_filter,
            "product": serialize_product(product, item["amount"], item["name"]) if product else None,
        }

    try:
        client.authenticate_client()
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(match, items))
    except (SecureCredentialError, KrogerAuthenticationError, requests.RequestException) as error:
        raise_kroger_http_error(error, "meal plan search")

    matched = [result for result in results if result["product"]]
    unmatched = [result for result in results if not result["product"]]
    total = sum(
        result["product"]["estimated_cost"] or result["product"]["price"] or 0 for result in matched
    )
    return {
        "meals": [
            {"id": recipe["id"], "title": recipe["title"], "scale": recipe["scale"],
             "servings": scale_servings(recipe.get("servings") or "", recipe["scale"])}
            for recipe in recipes
        ],
        "matched": matched,
        "unmatched": unmatched,
        "skipped_seasonings": skipped,
        "estimated_total": round(total, 2),
    }
