# Claude Code Handoff

## Project

Supper Run is a local React/Vite frontend with a FastAPI backend. The backend calls Kroger for nearby stores and product matches, and checks recipe sources (TheMealDB, Budget Bytes, and Allrecipes). The app runs as a single-user local tool; it does not yet have accounts or multi-user data isolation.

## Run and Validate

From the repository root:

```bash
npm run dev -- --host 127.0.0.1
.venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
npm run build
```

Vite runs at `http://127.0.0.1:5173/`; it proxies `/api` to FastAPI on port `8000`. The project interpreter is `.venv/bin/python`. The app does not use a `.env` file; do not add one or reintroduce `python-dotenv` loading. Never print or commit credential values.

## Current Worktree State

There are uncommitted changes. Inspect `git status` before editing. The original CLI `main.py` was removed because it is not used by the web app. `.gitignore` excludes local environment files, caches, build output, editor settings, and SQLite runtime databases. Preserve these changes.

The React app currently includes shopping-list/product matching, quantity-aware cost estimates, recipe search/import, saved recipes and cooking view, enhancements roadmap, and Kroger credential settings. Product filters include Kroger packaged brands (fresh-produce exemption), organic meat, organic vegetables, and explicit non-GMO claim. Pantry seasonings can be skipped from the shopping list while remaining in the saved recipe. Recipe-imported shopping lists replace the current list. Seasonings skipped at import are kept (with amounts, `excluded_ingredients`) as "+ Add" chips under the list; skipped rows in results offer "I'm out — find it". Either marks the list item `restock: true`, which `/api/search` always searches even when skipping pantry seasonings. Each product-match row has a remove (×) button that marks it `removed` (collapsed with Undo); removed rows are excluded from the match count and estimated total, and are not sent to the cart.

## Credential Security Design

- Kroger client ID and secret are submitted through the Settings tab to `/api/settings/kroger`.
- `src/secure_credentials.py` encrypts the credential JSON using Fernet and stores ciphertext in the `encrypted_settings` table of the app database.
- The Fernet key is kept separately in the operating-system keyring using service `DinnerReciprator` and account `kroger-credentials-encryption-key`.
- All app data lives in one SQLite file, `data/app.sqlite3`, owned by `src/database.py` (`database.connect()` creates the schema, migrates, and sets 0700/0600 permissions). Tables: `saved_recipes`, `encrypted_settings`. On first use it moves rows from the legacy `recipes.sqlite3` / `kroger_credentials.sqlite3` files, verifies them, and deletes the old files. `.gitignore` excludes it.
- Tests redirect `src.database` paths to a temp directory in `tests/__init__.py`; never let tests touch `data/`.
- `config.py` holds only non-secret constants; `KrogerAPI.authenticate_client()` reads credentials from the secure store.
- Status API returns only whether credentials are configured. Save API never echoes credentials. Do not add a secret-retrieval endpoint or persist credentials in browser storage/logs.
- `cryptography` and `keyring` are in `requirements.txt`. The project venv required a direct pip install after the VS Code package installer reported success without changing that interpreter. Check imports in `.venv` if setup seems inconsistent.
- Credentials are entered only through Settings. These are Kroger developer app credentials, not personal Dillons login credentials.
- `.env` was scrubbed from git history on 2026-11-08 (main was rewritten and force-pushed). `.gitignore` still excludes `.env*` as a safeguard.

## Recipe Storage and Sources

- Saved recipes are persisted in the `saved_recipes` table of `data/app.sqlite3`.
- `/api/recipes/import` fetches source recipe details, persists the full recipe, and returns filtered shopping ingredients separately.
- TheMealDB and Budget Bytes provide structured ingredients and instructions.
- Allrecipes search works at `/search?q=...`; recipe pages expose Recipe JSON-LD. Imports keep ingredients, instructions, and the canonical source URL (the saved view always links back to the original). `POST /api/recipes/saved/{id}/refresh` re-fetches a saved recipe; the UI offers it as "Load instructions from source" for recipes saved before instructions were kept.
- The Pioneer Woman: `robots.txt` disallows `/search`, so search runs against a 24-hour in-memory cache of the public content sitemap (`/food-cooking/recipes/a<id>/<slug>/` URLs, titles from slugs). Imports use the page's Recipe JSON-LD (ingredients and instructions, with the source link).
- Servings come from JSON-LD `recipeYield` (`normalize_recipe_yield()`, stored in the `servings` column, added by migration in `src/database.py`); TheMealDB has none. Recipes saved earlier get servings via "Load from source" (refresh endpoint).
- Scaling (1×/2×/4×): `scale_amount()`/`scale_servings()` in `api.py` and their mirror in `src/scaling.js` (keep in sync). Saved-recipe view scales display only; `/api/plan` takes `recipe_scales` and scales ingredient amounts before merging.
- `format_recipe_amount()` renders decimal measurements as fractions: approximate common fractions when close, otherwise exact rational fractions.

## Meal Plan Tab

- `src/MealPlan.jsx` + `POST /api/plan`: pick saved recipes (max 14), set tab-specific strict filters (stored in localStorage `plan-filters`), and get a report: matched (cheapest of the closest matches), needs-your-choice (unmatched or removed, with alternatives), and skipped pantry seasonings ("I'm out" searches with `restock`).
- `plan_items()` merges ingredients across meals by `product_search_term()` and sums amounts with a shared dimension (`combine_plan_amounts()`); searches run 4-way parallel.
- `ProductFilters.exclude_terms` (whole-word match on description/brand) and `max_package_price` apply everywhere `search_ingredient_products()` is used.
- The Meal Plan report has the same cart controls as the Shopping tab: per-item QTY, remove (×) with Undo, and Add to cart. The report is kept in sessionStorage `plan-report` so it survives tab switches and the Dillons sign-in redirect. Shared cart helpers live in `src/cart.js`.

## Dillons Cart (Kroger shopper account)

- OAuth authorization-code flow: `POST /api/kroger/account/connect` (body `return_to`, must be http localhost/127.0.0.1) returns Kroger's authorize URL with a single-use `state` (in-memory, 10-minute TTL). Kroger redirects to `GET /api/kroger/callback`, which exchanges the code and 303-redirects to the app with `?kroger_account=connected|denied|error`.
- Redirect URI is `KROGER_REDIRECT_URI` in `config.py` (`http://localhost:8000/api/kroger/callback`); it must be registered exactly on the Kroger developer app. Scope `cart.basic:write`.
- Shopper tokens (access, refresh, expires_at) are Fernet-encrypted in `encrypted_settings` row `kroger_account` (`save/load/clear_kroger_account_tokens`). `GET /api/kroger/account` returns only `connected` and the redirect URI; never return tokens. Changing the client ID or clearing app credentials clears the shopper tokens.
- `POST /api/cart/add` (`items: [{upc, quantity}]`, modality PICKUP) calls `PUT /v1/cart/add`. `account_access_token()` refreshes ~60s before expiry; a 401/403 from the cart retries once after a forced refresh, then clears tokens and returns 401 ("reconnect").
- Products carry `quantity` (packages from the cost estimate's "N packs", else 1); the UI has a per-row QTY field and sends only non-removed matches with a UPC. The full-page Kroger sign-in would lose React state, so the UI stashes `results` + tab in sessionStorage `kroger-connect-stash` and restores them on return.
- The Kroger public cart API can only add items; it cannot read or clear the cart.

## Product Search Pipeline

- `search_ingredient_products()` in `api.py` is the single place that builds a Kroger query for one ingredient and applies organic, non-GMO, Kroger-brand (fresh-produce exempt), and block-cheese rules. Both `/api/search` and `/api/search/alternatives` use it; add new filters there.
- Organic and produce classification considers the ingredient name plus the search terms, so synonyms such as courgette -> zucchini keep the user's rules.
- Block cheese (`prefer_block_cheese`): applies to hard cheeses only (soft cheeses such as ricotta, feta, and cream cheese are skipped). It strips shredded/grated/sliced/crumbled from the term, searches `"<term> block"` first with a plain-term fallback, and ranks block > other > pre-cut.
- `/api/search/alternatives` tries `alternative_search_terms()` plus the canonical term, keeps the original amount for cost estimates, dedupes by UPC, and returns up to 8 candidates tagged with `search_term`. The UI shows "Find alternatives" on unmatched rows, and "Use" swaps the product into the result so it counts toward the total and cart.
- `product_search_term(name, amount)` builds the Kroger lookup term:
  - Text after a comma and preparation words are dropped (cooked, drained, chopped, softened…). Cut words (diced, sliced, shredded…) are dropped only for fresh produce/meat, so "diced tomatoes", "shredded cheese", "sliced almonds" stay.
  - Cooked/shredded/diced chicken is bought raw (boneless skinless breasts, or thighs). Rotisserie and canned chicken are searched as such.
  - Flour tortillas map diameter or size words to Kroger's named sizes (street taco ≤5", fajita ≤7", soft taco ≤8.5", burrito ≤10.5", else extra large burrito; default soft taco). Alternatives try neighbouring sizes. Tortillas are costed by count, using "N ct" from the description when the size field is a weight.
- Kroger-brands filter: Kroger's API brand filter matches one exact label, so the app searches up to 50 results and keeps Kroger-owned labels (`KROGER_OWN_BRANDS`: Kroger, Simple Truth, Simple Truth Organic, Private Selection, Heritage Farm…), falling back to `brand=Kroger`. The fresh-produce exemption excludes canned/frozen/dried goods, sauces, and dry legumes (pinto, black beans…).
- Diced tomatoes are fresh produce unless the recipe signals a can (can/jar/ounce/"canned"); crushed and stewed tomatoes are always canned.
- Amounts like "2 15-ounce cans" are costed by total weight; "3 cans" buys 3 packages. Ranking penalises altered products (refried, whipped, spread, reduced fat, dairy-free, seasoned…) unless the ingredient names them.
- In a can/jar context, cut words stay in the term (diced green chilies). chili/chile/chilies/chiles normalise to one token. Ranking penalises products containing another main ingredient the recipe didn't name (tomatoes in Rotel-style blends).
- Kroger token rejections (400/401/403) raise `KrogerAuthenticationError` and produce a 502 telling the user to check their Settings credentials.
- Tests: `.venv/bin/python -m unittest discover -s tests -t .` (stdlib unittest with a fake Kroger client; no network). `starlette.testclient` is unavailable because `httpx2` is not installed.

## Next Checks and Known Gaps

Verified 2026-11-08: secure credential save/load/clear against the real Secret Service keyring with dummy values (ciphertext only, 0600/0700 perms, test key removed). Settings GET/PUT/DELETE never echo secrets. Product and store search return 503 without credentials, and recipe search works without them.

1. Settings tab UI in a browser has not been exercised (secret input clears after save, status updates). The API behind it is verified.
2. Block-cheese and alternatives behavior is verified only against a fake catalog. Spot-check with real Kroger credentials whether `"<cheese> block"` queries return good results.
3. Dillons account linking and cart add are verified only with mocked Kroger responses. First real run: register the redirect URI, connect, and add one item.

## Style and Safety

- Keep product-filter decisions server-side in `api.py` and Kroger request construction in `src/kroger_client.py`.
- Avoid logging credential payloads or access tokens.
- Keep recipe content source-linked and for the user's personal use; respect robots.txt and do not bypass site access controls.
- Prefer focused tests/builds after each change. Do not revert unrelated user changes or commit unless requested.