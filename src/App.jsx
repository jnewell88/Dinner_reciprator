import { useEffect, useState } from "react";
import { ExternalLink, LoaderCircle, MapPin, Plus, Search, ShoppingBasket, ShoppingCart, Trash2, X } from "lucide-react";

const STARTER_INGREDIENTS = [
  { name: "whole milk", amount: "" },
  { name: "large eggs", amount: "" },
  { name: "boneless chicken breast", amount: "" },
  { name: "rolled oats", amount: "" },
];

function formatPrice(price) {
  if (price === null || price === undefined || price === "") return "Price unavailable";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(price));
}

export default function App() {
  const [ingredients, setIngredients] = useState(() => {
    try {
      const savedIngredients = JSON.parse(window.localStorage.getItem("shopping-ingredients"));
      if (Array.isArray(savedIngredients)) {
        return savedIngredients.map((ingredient) => typeof ingredient === "string"
          ? { name: ingredient, amount: "" }
          : { name: ingredient.name || "", amount: ingredient.amount || "" })
          .filter((ingredient) => ingredient.name);
      }
    } catch {
      return STARTER_INGREDIENTS;
    }
    return STARTER_INGREDIENTS;
  });
  const [newIngredient, setNewIngredient] = useState("");
  const [selectedStore, setSelectedStore] = useState(() => {
    try {
      const savedStore = JSON.parse(window.localStorage.getItem("my-store"));
      if (savedStore?.id && savedStore?.name) return savedStore;
    } catch {
      // Ignore unavailable or malformed browser storage and use the starter store.
    }
    return { id: "01400943", name: "Dillons Market" };
  });
  const [zipCode, setZipCode] = useState("");
  const [stores, setStores] = useState(null);
  const [isFindingStores, setIsFindingStores] = useState(false);
  const [storeError, setStoreError] = useState("");
  const [results, setResults] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [krogerBrandsOnly, setKrogerBrandsOnly] = useState(() => window.localStorage.getItem("kroger-brands-only") === "true");
  const [organicMeatOnly, setOrganicMeatOnly] = useState(() => window.localStorage.getItem("organic-meat-only") === "true");
  const [organicVegetablesOnly, setOrganicVegetablesOnly] = useState(() => window.localStorage.getItem("organic-vegetables-only") === "true");
  const [nonGmoOnly, setNonGmoOnly] = useState(() => window.localStorage.getItem("non-gmo-only") === "true");
  const [activeTab, setActiveTab] = useState("shopping");
  const [savedRecipes, setSavedRecipes] = useState([]);
  const [isLoadingSavedRecipes, setIsLoadingSavedRecipes] = useState(false);
  const [savedRecipesError, setSavedRecipesError] = useState("");
  const [mealIdeas, setMealIdeas] = useState(() => {
    try {
      const savedMeals = JSON.parse(window.localStorage.getItem("meal-list"));
      if (Array.isArray(savedMeals)) {
        return savedMeals.filter((meal) => typeof meal === "string" && meal.trim()).slice(0, 10);
      }
    } catch {
      return [];
    }
    return [];
  });
  const [newMeal, setNewMeal] = useState("");
  const [recipeSources, setRecipeSources] = useState(["themealdb", "budgetbytes"]);
  const [recipeResults, setRecipeResults] = useState(null);
  const [isCheckingRecipes, setIsCheckingRecipes] = useState(false);
  const [recipeError, setRecipeError] = useState("");
  const [mealError, setMealError] = useState("");
  const [importingRecipe, setImportingRecipe] = useState("");

  useEffect(() => {
    window.localStorage.setItem("my-store", JSON.stringify(selectedStore));
  }, [selectedStore]);

  useEffect(() => {
    window.localStorage.setItem("kroger-brands-only", String(krogerBrandsOnly));
  }, [krogerBrandsOnly]);

  useEffect(() => {
    window.localStorage.setItem("organic-meat-only", String(organicMeatOnly));
  }, [organicMeatOnly]);

  useEffect(() => {
    window.localStorage.setItem("organic-vegetables-only", String(organicVegetablesOnly));
  }, [organicVegetablesOnly]);

  useEffect(() => {
    window.localStorage.setItem("non-gmo-only", String(nonGmoOnly));
  }, [nonGmoOnly]);

  useEffect(() => {
    if (activeTab !== "recipes") return undefined;
    let isCurrent = true;
    setIsLoadingSavedRecipes(true);
    setSavedRecipesError("");

    fetch("/api/recipes/saved")
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Saved recipes could not be loaded.");
        if (isCurrent) setSavedRecipes(data.recipes);
      })
      .catch((requestError) => {
        if (isCurrent) setSavedRecipesError(requestError.message || "Could not reach the recipe library.");
      })
      .finally(() => {
        if (isCurrent) setIsLoadingSavedRecipes(false);
      });

    return () => { isCurrent = false; };
  }, [activeTab]);

  useEffect(() => {
    window.localStorage.setItem("meal-list", JSON.stringify(mealIdeas));
  }, [mealIdeas]);

  useEffect(() => {
    window.localStorage.setItem("shopping-ingredients", JSON.stringify(ingredients));
  }, [ingredients]);

  function addIngredient(event) {
    event.preventDefault();
    const value = newIngredient.trim();
    if (!value) return;
    setIngredients((current) => [...current, { name: value, amount: "" }]);
    setNewIngredient("");
    setResults(null);
  }

  function removeIngredient(indexToRemove) {
    setIngredients((current) => current.filter((_, index) => index !== indexToRemove));
    setResults(null);
  }

  function clearIngredients() {
    setIngredients([]);
    setResults(null);
    setError("");
  }

  function addMeal(event) {
    event.preventDefault();
    const meal = newMeal.trim();
    if (!meal) return;
    if (mealIdeas.some((savedMeal) => savedMeal.toLowerCase() === meal.toLowerCase())) {
      setMealError("That meal is already on your list.");
      return;
    }
    if (mealIdeas.length >= 10) {
      setMealError("Your list can hold up to 10 meals.");
      return;
    }
    setMealIdeas((current) => [...current, meal]);
    setNewMeal("");
    setMealError("");
    setRecipeResults(null);
  }

  function removeMeal(indexToRemove) {
    setMealIdeas((current) => current.filter((_, index) => index !== indexToRemove));
    setRecipeResults(null);
  }

  function toggleRecipeSource(source) {
    setRecipeSources((current) => current.includes(source)
      ? current.filter((selectedSource) => selectedSource !== source)
      : [...current, source]);
    setRecipeResults(null);
  }

  async function findStores(event) {
    event.preventDefault();
    setStoreError("");
    setStores(null);
    setIsFindingStores(true);

    try {
      const response = await fetch("/api/stores", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zip_code: zipCode.trim() }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Store search could not be completed.");
      setStores(data.stores);
    } catch (requestError) {
      setStoreError(requestError.message || "Could not reach the store locator.");
    } finally {
      setIsFindingStores(false);
    }
  }

  function chooseStore(store) {
    setSelectedStore(store);
    setStores(null);
    setStoreError("");
    setResults(null);
  }

  async function findMatches(event) {
    event.preventDefault();
    setError("");
    setResults(null);
    setIsLoading(true);

    try {
      const response = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ingredients,
          store_id: selectedStore.id,
          kroger_brands_only: krogerBrandsOnly,
          organic_meat_only: organicMeatOnly,
          organic_vegetables_only: organicVegetablesOnly,
          non_gmo_only: nonGmoOnly,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Search could not be completed.");
      setResults(data.results);
    } catch (requestError) {
      setError(requestError.message || "Could not reach the product search service.");
    } finally {
      setIsLoading(false);
    }
  }

  async function checkRecipes(event) {
    event.preventDefault();
    setRecipeError("");
    setRecipeResults(null);
    setIsCheckingRecipes(true);

    try {
      const response = await fetch("/api/recipes/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ meals: mealIdeas, sources: recipeSources }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Recipe search could not be completed.");
      setRecipeResults(data.results);
    } catch (requestError) {
      setRecipeError(requestError.message || "Could not reach the recipe search service.");
    } finally {
      setIsCheckingRecipes(false);
    }
  }

  async function importRecipe(source, recipe) {
    const importKey = `${source.source}-${recipe.id || recipe.url}`;
    setRecipeError("");
    setImportingRecipe(importKey);

    try {
      const response = await fetch("/api/recipes/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: source.source,
          recipe_id: source.source === "themealdb" ? recipe.id : recipe.url,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Recipe ingredients could not be imported.");
      if (ingredients.length + data.ingredients.length > 30) {
        throw new Error("The shopping list supports up to 30 ingredients. Remove some items before importing this recipe.");
      }
      setIngredients((current) => [...current, ...data.ingredients]);
      setSavedRecipes((current) => [
        data.saved_recipe,
        ...current.filter((savedRecipe) => savedRecipe.id !== data.saved_recipe.id),
      ]);
      setResults(null);
    } catch (requestError) {
      setRecipeError(requestError.message || "Could not import recipe ingredients.");
    } finally {
      setImportingRecipe("");
    }
  }

  async function removeSavedRecipe(recipeId) {
    setSavedRecipesError("");
    try {
      const response = await fetch(`/api/recipes/saved/${recipeId}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Recipe could not be removed.");
      setSavedRecipes((current) => current.filter((recipe) => recipe.id !== recipeId));
    } catch (requestError) {
      setSavedRecipesError(requestError.message || "Could not update the recipe library.");
    }
  }

  const matchedProductCount = results?.filter((result) => result.product).length ?? 0;
  const unmatchedProductCount = results ? results.length - matchedProductCount : 0;
  const estimatedListTotal = results?.reduce((total, result) => {
    if (!result.product) return total;
    const itemCost = Number(result.product.estimated_cost ?? result.product.price);
    return Number.isFinite(itemCost) ? total + itemCost : total;
  }, 0) ?? 0;

  return (
    <div className="app-frame">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Supper Run home">
          <span className="brand-mark"><ShoppingBasket size={19} strokeWidth={2.2} /></span>
          <span className="brand-name">SUPPER <b>RUN</b></span>
        </a>
        <div className="topbar-store"><MapPin size={15} /><span>{selectedStore.name}</span><i /> <span>{selectedStore.id}</span></div>
      </header>

      <main className="page" id="top">
        <div className="page-heading">
          <div>
            <p className="eyebrow">WEEKLY SHOP <span> / </span> 01</p>
            <h1>Dinner starts at the market.</h1>
          </div>
          <p className="heading-note">PRODUCT FINDER <span className="heading-dot" /></p>
        </div>

        <nav className="view-tabs" role="tablist" aria-label="Application views">
          <button
            id="shopping-tab"
            className={activeTab === "shopping" ? "view-tab active" : "view-tab"}
            type="button"
            role="tab"
            aria-selected={activeTab === "shopping"}
            aria-controls="shopping-view"
            onClick={() => setActiveTab("shopping")}
          >
            Shopping <span>{String(ingredients.length).padStart(2, "0")}</span>
          </button>
          <button
            id="saved-recipes-tab"
            className={activeTab === "recipes" ? "view-tab active" : "view-tab"}
            type="button"
            role="tab"
            aria-selected={activeTab === "recipes"}
            aria-controls="saved-recipes-view"
            onClick={() => setActiveTab("recipes")}
          >
            Saved recipes <span>{String(savedRecipes.length).padStart(2, "0")}</span>
          </button>
        </nav>

        {activeTab === "shopping" ? (
          <>
        <div className="workspace-grid">
          <section className="list-panel" aria-labelledby="list-title">
            <div className="panel-heading">
              <div>
                <p className="panel-kicker">YOUR PLAN</p>
                <h2 id="list-title">Shopping list</h2>
              </div>
              <div className="panel-tools">
                <span className="item-count">{String(ingredients.length).padStart(2, "0")}</span>
                <button
                  className="clear-list-button"
                  type="button"
                  title="Clear ingredients"
                  aria-label="Clear ingredients"
                  onClick={clearIngredients}
                  disabled={ingredients.length === 0}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>

            <section className="store-locator" aria-labelledby="store-locator-title">
              <div className="locator-heading">
                <h3 id="store-locator-title">Find a store</h3>
                <span>BY ZIP CODE</span>
              </div>
              <form className="locator-form" onSubmit={findStores}>
                <label className="sr-only" htmlFor="store-zip">ZIP code</label>
                <input
                  id="store-zip"
                  value={zipCode}
                  onChange={(event) => setZipCode(event.target.value)}
                  placeholder="Enter ZIP code"
                  inputMode="numeric"
                  pattern="[0-9]{5}(-[0-9]{4})?"
                  maxLength={10}
                  required
                />
                <button className="locator-button" type="submit" disabled={isFindingStores} aria-label="Search stores">
                  {isFindingStores ? <LoaderCircle className="spin" size={17} /> : <Search size={17} />}
                </button>
              </form>
              {storeError && <p className="store-error" role="alert">{storeError}</p>}
              {isFindingStores && <p className="store-search-status">Looking for nearby stores…</p>}
              {stores && (
                <div className="store-results" aria-live="polite">
                  {stores.length ? stores.map((store) => (
                    <button
                      className="store-option"
                      key={store.id}
                      type="button"
                      aria-label={`Set ${store.name || store.chain || "Kroger store"} as My Store`}
                      onClick={() => chooseStore(store)}
                    >
                      <MapPin size={16} />
                      <span className="store-option-copy">
                        <strong>{store.name || store.chain || "Kroger store"}</strong>
                        <span>{[store.address, store.city, store.state, store.zip_code].filter(Boolean).join(", ")}</span>
                      </span>
                      <span className="store-option-id">{selectedStore.id === store.id ? "MY STORE" : "USE STORE"}</span>
                    </button>
                  )) : <p className="store-search-status">No stores found for that ZIP code.</p>}
                </div>
              )}
              <div className="selected-store">
                <MapPin size={15} />
                <div>
                  <span>MY STORE</span>
                  <strong>{selectedStore.name}</strong>
                  <small>{selectedStore.id}</small>
                </div>
              </div>
            </section>

            <form className="add-form" onSubmit={addIngredient}>
              <label className="sr-only" htmlFor="new-ingredient">Add an ingredient</label>
              <input
                id="new-ingredient"
                value={newIngredient}
                onChange={(event) => setNewIngredient(event.target.value)}
                placeholder="Add an ingredient"
                maxLength={100}
              />
              <button className="icon-button add-button" type="submit" title="Add ingredient" aria-label="Add ingredient">
                <Plus size={19} />
              </button>
            </form>

            {ingredients.length ? (
              <ul className="ingredient-list">
                {ingredients.map((ingredient, index) => (
                  <li className="ingredient-row" key={`${ingredient.name}-${ingredient.amount}-${index}`}>
                    <span className="ingredient-index">{String(index + 1).padStart(2, "0")}</span>
                    <span className="ingredient-name">
                      {ingredient.amount && <span className="ingredient-amount">{ingredient.amount}</span>}
                      {ingredient.name}
                    </span>
                    <button
                      className="icon-button remove-button"
                      type="button"
                      title={`Remove ${ingredient.name}`}
                      aria-label={`Remove ${ingredient.name}`}
                      onClick={() => removeIngredient(index)}
                    >
                      <X size={16} />
                    </button>
                  </li>
                ))}
              </ul>
            ) : <p className="ingredient-empty">Your shopping list is empty.</p>}

            <form className="search-form" onSubmit={findMatches}>
              <label className="brand-toggle">
                <input
                  type="checkbox"
                  checked={krogerBrandsOnly}
                  onChange={(event) => {
                    setKrogerBrandsOnly(event.target.checked);
                    setResults(null);
                  }}
                />
                <span>Kroger packaged brands only (fresh produce exempt)</span>
              </label>
              <label className="brand-toggle">
                <input
                  type="checkbox"
                  checked={organicMeatOnly}
                  onChange={(event) => {
                    setOrganicMeatOnly(event.target.checked);
                    setResults(null);
                  }}
                />
                <span>Organic meat only</span>
              </label>
              <label className="brand-toggle">
                <input
                  type="checkbox"
                  checked={organicVegetablesOnly}
                  onChange={(event) => {
                    setOrganicVegetablesOnly(event.target.checked);
                    setResults(null);
                  }}
                />
                <span>Organic vegetables only</span>
              </label>
              <label className="brand-toggle">
                <input
                  type="checkbox"
                  checked={nonGmoOnly}
                  onChange={(event) => {
                    setNonGmoOnly(event.target.checked);
                    setResults(null);
                  }}
                />
                <span>Non-GMO claim only</span>
              </label>
              <button className="search-button" type="submit" disabled={isLoading || ingredients.length === 0 || ingredients.length > 30 || !selectedStore.id}>
                {isLoading ? <LoaderCircle className="spin" size={18} /> : <Search size={18} />}
                <span>{isLoading ? "Searching" : "Find matches"}</span>
              </button>
            </form>
          </section>

          <section className="matches-panel" aria-labelledby="matches-title" aria-live="polite">
            <div className="panel-heading matches-heading">
              <div>
                <p className="panel-kicker">KROGER <span> / </span> QUANTITY-ADJUSTED ESTIMATES</p>
                <h2 id="matches-title">Product matches</h2>
              </div>
              {results && <span className="match-count">{results.filter((result) => result.product).length} FOUND</span>}
            </div>

            {error ? (
              <div className="message-state error-state" role="alert">
                <span className="state-marker">!</span>
                <div><strong>Search unavailable</strong><p>{error}</p></div>
              </div>
            ) : isLoading ? (
              <div className="message-state loading-state"><LoaderCircle className="spin" size={22} /><span>Checking local products</span></div>
            ) : results ? (
              <div className="results-list">
                {results.map((result, index) => (
                  <article className={`result-row ${result.product ? "" : "no-match"}`} key={`${result.ingredient}-${index}`}>
                    <span className="result-index">{String(index + 1).padStart(2, "0")}</span>
                    <div className="result-copy">
                      <p className="result-ingredient">{result.ingredient}</p>
                      {result.product ? (
                        <>
                          <h3>{result.product.description || "Product description unavailable"}</h3>
                          <p className="product-meta">
                            {[
                              result.product.brand,
                              result.product.size,
                              result.product.purchase_note,
                              result.product.upc ? `UPC ${result.product.upc}` : null,
                            ]
                              .filter(Boolean).join(" · ") || " "}
                          </p>
                        </>
                      ) : <p className="not-found">
                        {result.non_gmo_only
                          ? "No explicit non-GMO claim found"
                          : result.organic_filter
                          ? `No organic ${result.organic_filter} match found`
                          : krogerBrandsOnly ? "No Kroger-brand match found" : "No product match found"}
                      </p>}
                    </div>
                    <div className="price-cell">
                      <span>EST. COST</span>
                      <strong className="product-price">{result.product ? formatPrice(result.product.estimated_cost ?? result.product.price) : "—"}</strong>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <span className="empty-icon"><ShoppingBasket size={26} strokeWidth={1.5} /></span>
                <span className="empty-caption">READY WHEN YOU ARE</span>
                <span className="empty-rule" />
                <p>—</p>
              </div>
            )}

            {results && results.length > 0 && (
              <div className="cart-summary">
                <div className="list-total">
                  <span>ESTIMATED LIST TOTAL</span>
                  <strong>{matchedProductCount ? formatPrice(estimatedListTotal) : "—"}</strong>
                  <small>
                    {unmatchedProductCount
                      ? `${matchedProductCount} matched; ${unmatchedProductCount} unmatched excluded`
                      : `${matchedProductCount} matched items`}
                  </small>
                </div>
                <div className="cart-action">
                  <button className="cart-button" type="button" disabled title="Dillons account linking is not set up yet">
                    <ShoppingCart size={17} />
                    <span>Add to cart</span>
                  </button>
                  <small>Dillons account not connected</small>
                </div>
              </div>
            )}

            {results && results.length > 0 && (
              <p className="results-footnote">Prices and availability may vary by store.</p>
            )}
          </section>
        </div>

        <section className="recipe-panel" aria-labelledby="recipe-panel-title">
          <div className="recipe-panel-heading">
            <div>
              <p className="panel-kicker">RECIPE SOURCES</p>
              <h2 id="recipe-panel-title">Meal list</h2>
            </div>
            <span className="item-count">{String(mealIdeas.length).padStart(2, "0")}</span>
          </div>
          <div className="recipe-panel-grid">
            <div className="meal-editor">
              <form className="add-form meal-add-form" onSubmit={addMeal}>
                <label className="sr-only" htmlFor="new-meal">Add a meal</label>
                <input
                  id="new-meal"
                  value={newMeal}
                  onChange={(event) => setNewMeal(event.target.value)}
                  placeholder="Add a meal to check"
                  maxLength={100}
                  disabled={mealIdeas.length >= 10}
                />
                <button className="icon-button add-button" type="submit" title="Add meal" aria-label="Add meal" disabled={mealIdeas.length >= 10}>
                  <Plus size={19} />
                </button>
              </form>
              {mealError && <p className="store-error" role="alert">{mealError}</p>}
              {mealIdeas.length ? (
                <ul className="meal-list">
                  {mealIdeas.map((meal, index) => (
                    <li className="meal-row" key={`${meal}-${index}`}>
                      <span className="ingredient-index">{String(index + 1).padStart(2, "0")}</span>
                      <span>{meal}</span>
                      <button className="icon-button remove-button" type="button" aria-label={`Remove ${meal}`} title={`Remove ${meal}`} onClick={() => removeMeal(index)}>
                        <X size={16} />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : <p className="meal-empty">No meals added yet.</p>}

              <fieldset className="recipe-source-picker">
                <legend>CHECK THESE SITES</legend>
                <label>
                  <input type="checkbox" checked={recipeSources.includes("themealdb")} onChange={() => toggleRecipeSource("themealdb")} />
                  <span>TheMealDB</span>
                </label>
                <label>
                  <input type="checkbox" checked={recipeSources.includes("budgetbytes")} onChange={() => toggleRecipeSource("budgetbytes")} />
                  <span>Budget Bytes</span>
                </label>
              </fieldset>
              <button className="recipe-search-button" type="button" onClick={checkRecipes} disabled={isCheckingRecipes || !mealIdeas.length || !recipeSources.length}>
                {isCheckingRecipes ? <LoaderCircle className="spin" size={18} /> : <Search size={18} />}
                <span>{isCheckingRecipes ? "Checking sites" : "Check recipe sites"}</span>
              </button>
              {recipeError && <p className="store-error" role="alert">{recipeError}</p>}
            </div>

            <div className="recipe-check-results" aria-live="polite">
              {isCheckingRecipes ? (
                <div className="recipe-empty-state"><LoaderCircle className="spin" size={21} /><span>Searching recipe sources</span></div>
              ) : recipeResults ? (
                <div className="meal-check-list">
                  {recipeResults.map((mealResult) => (
                    <article className="meal-check-row" key={mealResult.meal}>
                      <h3>{mealResult.meal}</h3>
                      {mealResult.sources.map((source) => (
                        <div className="source-check-row" key={source.source}>
                          <div className="source-check-heading">
                            <strong>{source.name}</strong>
                            <span className={`source-status status-${source.status}`}>
                              {source.status === "found" ? "FOUND" : source.status === "not_found" ? "NOT FOUND" : "UNAVAILABLE"}
                            </span>
                          </div>
                          {source.recipes.length ? (
                            <ul className="recipe-match-list">
                              {source.recipes.map((recipe) => (
                                <li className="recipe-match" key={recipe.url}>
                                  <a href={recipe.url} target="_blank" rel="noreferrer">{recipe.title}<ExternalLink size={13} /></a>
                                  <button
                                    className="import-recipe-button"
                                    type="button"
                                    onClick={() => importRecipe(source, recipe)}
                                    disabled={Boolean(importingRecipe)}
                                  >
                                    {importingRecipe === `${source.source}-${recipe.id || recipe.url}`
                                      ? <LoaderCircle className="spin" size={14} />
                                      : <Plus size={14} />}
                                    <span>Add ingredients</span>
                                  </button>
                                </li>
                              ))}
                            </ul>
                          ) : source.status !== "found" && source.status !== "not_found" ? (
                            <p className="source-no-match">Search unavailable</p>
                          ) : <p className="source-no-match">No recipe found</p>}
                        </div>
                      ))}
                    </article>
                  ))}
                </div>
              ) : (
                <div className="recipe-empty-state"><span>Add meals to check recipe sites.</span></div>
              )}
            </div>
          </div>
        </section>
          </>
        ) : (
          <section className="saved-recipes-view" id="saved-recipes-view" role="tabpanel" aria-labelledby="saved-recipes-tab">
            <div className="saved-recipes-heading">
              <div>
                <p className="panel-kicker">YOUR COOKBOOK</p>
                <h2>Saved recipes</h2>
              </div>
              <span className="item-count">{String(savedRecipes.length).padStart(2, "0")}</span>
            </div>

            {savedRecipesError && <p className="store-error" role="alert">{savedRecipesError}</p>}
            {isLoadingSavedRecipes ? (
              <div className="recipe-empty-state"><LoaderCircle className="spin" size={21} /><span>Loading saved recipes</span></div>
            ) : savedRecipes.length ? (
              <div className="saved-recipes-list">
                {savedRecipes.map((recipe) => (
                  <article className="saved-recipe" key={recipe.id}>
                    <div className="saved-recipe-topline">
                      <div>
                        <h3>{recipe.title}</h3>
                        <p>{recipe.source === "themealdb" ? "TheMealDB" : "Budget Bytes"} · {recipe.ingredients.length} ingredients · {recipe.instructions.length} steps</p>
                      </div>
                      <button
                        className="icon-button remove-button"
                        type="button"
                        title={`Remove ${recipe.title} from saved recipes`}
                        aria-label={`Remove ${recipe.title} from saved recipes`}
                        onClick={() => removeSavedRecipe(recipe.id)}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                    <details className="saved-recipe-details">
                      <summary>Open recipe for cooking</summary>
                      <a className="saved-source-link" href={recipe.url} target="_blank" rel="noreferrer">
                        View original recipe <ExternalLink size={13} />
                      </a>
                      <div className="saved-recipe-content">
                        <section>
                          <h4>Ingredients</h4>
                          <ul>
                            {recipe.ingredients.map((ingredient, index) => (
                              <li key={`${ingredient.name}-${index}`}>
                                {ingredient.amount && <strong>{ingredient.amount} </strong>}{ingredient.name}
                              </li>
                            ))}
                          </ul>
                        </section>
                        <section>
                          <h4>Instructions</h4>
                          {recipe.instructions.length ? (
                            <ol>
                              {recipe.instructions.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}
                            </ol>
                          ) : <p className="source-no-match">No structured instructions are available. Use the original recipe link.</p>}
                        </section>
                      </div>
                    </details>
                  </article>
                ))}
              </div>
            ) : (
              <div className="saved-recipes-empty">
                <span className="empty-icon"><ShoppingBasket size={25} strokeWidth={1.5} /></span>
                <h3>No saved recipes yet</h3>
                <p>Use “Add ingredients” on a recipe match to save it here for cooking.</p>
                <button className="recipe-search-button" type="button" onClick={() => setActiveTab("shopping")}>Find recipes</button>
              </div>
            )}
          </section>
        )}

        <footer className="page-footer"><span>SUPPER RUN</span><span>PLAN WELL. EAT WELL.</span></footer>
      </main>
    </div>
  );
}