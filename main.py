from src.kroger_client import KrogerAPI

WEEKLY_INGREDIENTS = [
    "whole milk",
    "large eggs",
    "boneless chicken breast",
    "rolled oats"
]

DILLONS_STORE_ID = "01400943"

def run():
    client = KrogerAPI()
    print("Authenticating with Kroger API...")
    client.authenticate_client()

    print(f"\nFinding products for store: {DILLONS_STORE_ID}\n" + "-"*40)
    matched_cart = []

    for ingredient in WEEKLY_INGREDIENTS:
        results = client.search_product(term=ingredient, location_id=DILLONS_STORE_ID, limit=1)
        if results:
            product = results[0]
            upc = product.get("upc")
            description = product.get("description")
            price_info = product.get("items", [{}])[0].get("price", {}).get("regular", "N/A")

            print(f"✓ {ingredient.capitalize()} -> {description} (${price_info}) | UPC: {upc}")
            matched_cart.append({"upc": upc, "quantity": 1})
        else:
            print(f"✗ Could not find match for: {ingredient}")

    print("\nMatched payload ready for Cart API:")
    print(matched_cart)

if __name__ == "__main__":
    run()
