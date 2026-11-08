import { useEffect, useState } from "react";
import { LoaderCircle, Plus, RefreshCw, Search, ShoppingCart, X } from "lucide-react";
import { SCALE_OPTIONS, scaleServings } from "./scaling.js";

const DEFAULT_FILTERS = {
  kroger_brands_only: false,
  organic_meat_only: false,
  organic_vegetables_only: false,
  non_gmo_only: false,
  prefer_block_cheese: false,
  skip_pantry_seasonings: true,
  exclude_text: "",
  max_package_price: "",
};

const FILTER_TOGGLES = [
  ["kroger_brands_only", "Kroger packaged brands only (fresh produce exempt)"],
  ["organic_meat_only", "Organic meat only"],
  ["organic_vegetables_only", "Organic vegetables only"],
  ["non_gmo_only", "Non-GMO claim only"],
  ["prefer_block_cheese", "Block cheese instead of shredded"],
  ["skip_pantry_seasonings", "Skip common pantry seasonings"],
];

function readStored(key, fallback) {
  try {
    const value = JSON.parse(window.localStorage.getItem(key));
    return value ?? fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage unavailable; settings last for this session only.
  }
}

function formatPrice(price) {
  if (price === null || price === undefined || price === "") return "Price unavailable";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(price));
}

function itemCost(product) {
  const cost = Number(product?.estimated_cost ?? product?.price);
  return Number.isFinite(cost) ? cost : 0;
}

export default function MealPlan({ savedRecipes, isLoadingSavedRecipes, selectedStore }) {
  const [selectedIds, setSelectedIds] = useState(() => readStored("plan-recipe-ids", []));
  const [scales, setScales] = useState(() => readStored("plan-recipe-scales", {}));
  const [filters, setFilters] = useState(() => ({ ...DEFAULT_FILTERS, ...readStored("plan-filters", {}) }));
  const [report, setReport] = useState(null);
  const [isBuilding, setIsBuilding] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => writeStored("plan-recipe-ids", selectedIds), [selectedIds]);
  useEffect(() => writeStored("plan-filters", filters), [filters]);
  useEffect(() => writeStored("plan-recipe-scales", scales), [scales]);

  const availableIds = new Set(savedRecipes.map((recipe) => recipe.id));
  const chosenIds = selectedIds.filter((id) => availableIds.has(id));

  function toggleRecipe(id) {
    setSelectedIds((current) => (current.includes(id) ? current.filter((value) => value !== id) : [...current, id]));
    setReport(null);
  }

  function setFilter(key, value) {
    setFilters((current) => ({ ...current, [key]: value }));
    setReport(null);
  }

  function requestFilters() {
    const maxPrice = Number.parseFloat(filters.max_package_price);
    return {
      store_id: selectedStore.id,
      kroger_brands_only: filters.kroger_brands_only,
      organic_meat_only: filters.organic_meat_only,
      organic_vegetables_only: filters.organic_vegetables_only,
      non_gmo_only: filters.non_gmo_only,
      prefer_block_cheese: filters.prefer_block_cheese,
      exclude_terms: filters.exclude_text.split(",").map((term) => term.trim()).filter(Boolean).slice(0, 20),
      max_package_price: Number.isFinite(maxPrice) && maxPrice > 0 ? maxPrice : null,
    };
  }

  async function postJson(url, body, fallbackMessage) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail;
      throw new Error(detail || fallbackMessage);
    }
    return data;
  }

  async function buildPlan(event) {
    event.preventDefault();
    setError("");
    setReport(null);
    setIsBuilding(true);
    try {
      const data = await postJson(
        "/api/plan",
        {
          ...requestFilters(),
          recipe_ids: chosenIds,
          recipe_scales: Object.fromEntries(chosenIds.map((id) => [id, scales[id] || 1])),
          skip_pantry_seasonings: filters.skip_pantry_seasonings,
        },
        "The meal plan could not be built.",
      );
      setReport({
        meals: data.meals,
        matched: data.matched,
        attention: data.unmatched.map((item) => ({ ...item, reason: "No product matched your filters" })),
        skipped: data.skipped_seasonings,
      });
    } catch (requestError) {
      setError(requestError.message || "Could not reach the meal plan service.");
    } finally {
      setIsBuilding(false);
    }
  }

  function updateAttention(index, changes) {
    setReport((current) => ({
      ...current,
      attention: current.attention.map((item, position) => (position === index ? { ...item, ...changes } : item)),
    }));
  }

  async function findAlternatives(index) {
    const item = report.attention[index];
    updateAttention(index, { alternativesLoading: true, alternativesError: "", alternatives: null });
    try {
      const data = await postJson(
        "/api/search/alternatives",
        { ...requestFilters(), ingredient_name: item.name, amount: item.amount || "" },
        "Alternative search could not be completed.",
      );
      updateAttention(index, { alternativesLoading: false, alternatives: data.alternatives });
    } catch (requestError) {
      updateAttention(index, { alternativesLoading: false, alternativesError: requestError.message });
    }
  }

  function chooseAlternative(index, alternative) {
    setReport((current) => {
      const item = current.attention[index];
      const { alternatives, alternativesLoading, alternativesError, reason, ...rest } = item;
      return {
        ...current,
        attention: current.attention.filter((_, position) => position !== index),
        matched: [...current.matched, { ...rest, product: alternative, chosenByYou: true }],
      };
    });
  }

  function changeMatched(index) {
    setReport((current) => {
      const { product, chosenByYou, ...item } = current.matched[index];
      return {
        ...current,
        matched: current.matched.filter((_, position) => position !== index),
        attention: [...current.attention, { ...item, reason: `You removed ${product.description || "the match"}` }],
      };
    });
  }

  function dropAttention(index) {
    setReport((current) => ({ ...current, attention: current.attention.filter((_, position) => position !== index) }));
  }

  async function restockSeasoning(index) {
    const seasoning = report.skipped[index];
    setReport((current) => ({
      ...current,
      skipped: current.skipped.map((item, position) => (position === index ? { ...item, loading: true, error: "" } : item)),
    }));
    try {
      const data = await postJson(
        "/api/search",
        {
          ...requestFilters(),
          skip_pantry_seasonings: true,
          ingredients: [{ name: seasoning.name, amount: seasoning.amount || "", restock: true }],
        },
        "Search could not be completed.",
      );
      const product = data.results[0]?.product;
      const { loading, error: _error, ...item } = seasoning;
      setReport((current) => ({
        ...current,
        skipped: current.skipped.filter((_, position) => position !== index),
        matched: product ? [...current.matched, { ...item, product, chosenByYou: true }] : current.matched,
        attention: product ? current.attention : [...current.attention, { ...item, reason: "No product matched your filters" }],
      }));
    } catch (requestError) {
      setReport((current) => ({
        ...current,
        skipped: current.skipped.map((item, position) => (
          position === index ? { ...item, loading: false, error: requestError.message } : item
        )),
      }));
    }
  }

  const total = report ? report.matched.reduce((sum, item) => sum + itemCost(item.product), 0) : 0;
  const canBuild = chosenIds.length > 0 && chosenIds.length <= 14 && selectedStore.id && !isBuilding;

  return (
    <section className="plan-view" id="plan-view" role="tabpanel" aria-labelledby="plan-tab">
      <div className="plan-grid">
        <form className="plan-setup" onSubmit={buildPlan}>
          <div className="enhancements-heading">
            <div>
              <p className="panel-kicker">PICK MEALS · STRICT FILTERS</p>
              <h2>Meal plan</h2>
            </div>
            <span className="item-count">{String(chosenIds.length).padStart(2, "0")}</span>
          </div>

          <fieldset className="plan-fieldset">
            <legend>MEALS</legend>
            {isLoadingSavedRecipes ? (
              <p className="plan-muted"><LoaderCircle className="spin" size={14} /> Loading saved recipes…</p>
            ) : savedRecipes.length ? (
              <ul className="plan-meal-list">
                {savedRecipes.map((recipe) => (
                  <li key={recipe.id}>
                    <label>
                      <input type="checkbox" checked={chosenIds.includes(recipe.id)} onChange={() => toggleRecipe(recipe.id)} />
                      <span>{recipe.title}</span>
                      <small>
                        {recipe.servings ? `Makes ${scaleServings(recipe.servings, scales[recipe.id] || 1)}` : `${recipe.ingredients.length} ingredients`}
                      </small>
                    </label>
                    {chosenIds.includes(recipe.id) && (
                      <div className="scale-picker" role="group" aria-label={`Scale ${recipe.title}`}>
                        {SCALE_OPTIONS.map((factor) => (
                          <button
                            key={factor}
                            type="button"
                            className={(scales[recipe.id] || 1) === factor ? "active" : ""}
                            aria-pressed={(scales[recipe.id] || 1) === factor}
                            onClick={() => {
                              setScales((current) => ({ ...current, [recipe.id]: factor }));
                              setReport(null);
                            }}
                          >
                            {factor}×
                          </button>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="plan-muted">No saved recipes yet. Import recipes from the Shopping tab first.</p>
            )}
          </fieldset>

          <fieldset className="plan-fieldset">
            <legend>FILTERS</legend>
            {FILTER_TOGGLES.map(([key, label]) => (
              <label className="brand-toggle" key={key}>
                <input type="checkbox" checked={filters[key]} onChange={(event) => setFilter(key, event.target.checked)} />
                <span>{label}</span>
              </label>
            ))}
            <label className="plan-field">
              <span>Never buy (words or brands, comma-separated)</span>
              <input
                value={filters.exclude_text}
                onChange={(event) => setFilter("exclude_text", event.target.value)}
                placeholder="e.g. light, reduced fat, Great Value"
                maxLength={400}
              />
            </label>
            <label className="plan-field">
              <span>Max price per package ($)</span>
              <input
                value={filters.max_package_price}
                onChange={(event) => setFilter("max_package_price", event.target.value)}
                placeholder="No limit"
                inputMode="decimal"
                maxLength={7}
              />
            </label>
          </fieldset>

          <p className="plan-muted">Store: {selectedStore.name || "choose a store on the Shopping tab"}</p>
          <button className="search-button" type="submit" disabled={!canBuild}>
            {isBuilding ? <LoaderCircle className="spin" size={18} /> : <Search size={18} />}
            <span>{isBuilding ? "Finding cheapest options" : "Find cheapest options"}</span>
          </button>
          {chosenIds.length > 14 && <p className="store-error">Select at most 14 meals.</p>}
        </form>

        <div className="plan-report" aria-live="polite">
          {error ? (
            <div className="message-state error-state" role="alert">
              <span className="state-marker">!</span>
              <div><strong>Plan unavailable</strong><p>{error}</p></div>
            </div>
          ) : isBuilding ? (
            <div className="message-state loading-state"><LoaderCircle className="spin" size={22} /><span>Searching every ingredient</span></div>
          ) : report ? (
            <>
              <div className="plan-summary">
                <div>
                  <span>ESTIMATED TOTAL</span>
                  <strong>{formatPrice(total)}</strong>
                  <small>{report.meals.map((meal) => (meal.servings ? `${meal.title}, makes ${meal.servings}` : meal.title)).join(" · ")}</small>
                </div>
                <div className="cart-action">
                  <button className="cart-button" type="button" disabled title="Kroger account linking is not set up yet">
                    <ShoppingCart size={17} />
                    <span>Add {report.matched.length} items to cart</span>
                  </button>
                  <small>Cart linking not set up yet</small>
                </div>
              </div>

              {report.attention.length > 0 && (
                <section className="plan-section attention">
                  <h3>Needs your choice <span>{report.attention.length}</span></h3>
                  <ul>
                    {report.attention.map((item, index) => (
                      <li key={`${item.name}-${index}`}>
                        <div className="plan-item-head">
                          <div>
                            <strong>{item.ingredient}</strong>
                            <small>{item.reason} · {item.meals.join(", ")}</small>
                          </div>
                          <button type="button" className="plan-icon-button" title="Leave this off the list" aria-label={`Leave ${item.name} off the list`} onClick={() => dropAttention(index)}>
                            <X size={15} />
                          </button>
                        </div>
                        {item.alternatives ? (
                          item.alternatives.length ? (
                            <ul className="alternative-list">
                              {item.alternatives.map((alternative) => (
                                <li key={alternative.upc || alternative.description}>
                                  <div>
                                    <span className="alternative-name">{alternative.description}</span>
                                    <span className="alternative-meta">
                                      {[alternative.brand, alternative.size, formatPrice(alternative.estimated_cost ?? alternative.price)].filter(Boolean).join(" · ")}
                                    </span>
                                  </div>
                                  <button type="button" className="alternative-use" onClick={() => chooseAlternative(index, alternative)}>Use</button>
                                </li>
                              ))}
                            </ul>
                          ) : <p className="alternative-empty">No alternatives match your filters. Loosen a filter or leave it off.</p>
                        ) : (
                          <button type="button" className="alternative-trigger" disabled={item.alternativesLoading} onClick={() => findAlternatives(index)}>
                            {item.alternativesLoading ? <LoaderCircle className="spin" size={14} /> : <Search size={14} />}
                            <span>{item.alternativesLoading ? "Searching alternatives" : "Find options"}</span>
                          </button>
                        )}
                        {item.alternativesError && <p className="alternative-error" role="alert">{item.alternativesError}</p>}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {report.skipped.length > 0 && (
                <section className="plan-section">
                  <h3>Pantry seasonings skipped <span>{report.skipped.length}</span></h3>
                  <ul className="plan-chip-list">
                    {report.skipped.map((seasoning, index) => (
                      <li key={`${seasoning.name}-${index}`}>
                        <button type="button" disabled={seasoning.loading} onClick={() => restockSeasoning(index)} title="I'm out of this; find it">
                          {seasoning.loading ? <LoaderCircle className="spin" size={13} /> : <Plus size={13} />}
                          <span>{seasoning.ingredient}</span>
                        </button>
                        {seasoning.error && <small className="alternative-error">{seasoning.error}</small>}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <section className="plan-section">
                <h3>Matched <span>{report.matched.length}</span></h3>
                <ul>
                  {report.matched.map((item, index) => (
                    <li key={`${item.name}-${index}`} className="plan-item-head">
                      <div>
                        <small className="result-ingredient">{item.ingredient}</small>
                        <strong>{item.product.description}</strong>
                        <small>
                          {[item.product.brand, item.product.size, item.product.purchase_note, item.chosenByYou ? "chosen by you" : null, item.meals.join(", ")]
                            .filter(Boolean).join(" · ")}
                        </small>
                      </div>
                      <div className="plan-item-price">
                        <strong className="product-price">{formatPrice(item.product.estimated_cost ?? item.product.price)}</strong>
                        <button type="button" className="plan-icon-button" title="Choose a different product" aria-label={`Change ${item.name}`} onClick={() => changeMatched(index)}>
                          <RefreshCw size={14} />
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            </>
          ) : (
            <div className="empty-state">
              <span className="empty-caption">PICK MEALS, SET FILTERS</span>
              <span className="empty-rule" />
              <p>The report lists the cheapest match for every ingredient, plus anything that needs your choice.</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
