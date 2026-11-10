import { useEffect, useState } from "react";
import MealPlan from "./MealPlan.jsx";
import {
  CartError, DILLONS_CART_URL, addLinesToCart, cartAddedMessage, cartLines, clampQuantity, itemCost, itemQuantity,
} from "./cart.js";
import { SCALE_OPTIONS, scaleAmount, scaleServings } from "./scaling.js";
import { ExternalLink, LoaderCircle, MapPin, Plus, Search, ShoppingBasket, ShoppingCart, Trash2, Undo2, X } from "lucide-react";

// The sample list earlier versions started with; a saved list that is exactly this is treated as empty.
const FORMER_STARTER_INGREDIENTS = ["whole milk", "large eggs", "boneless chicken breast", "rolled oats"];

const FUTURE_ENHANCEMENTS = [
  {
    title: "Sales-informed recipe suggestions",
    description: "Recommend recipes to search for based on current deals at your selected store.",
  },
  {
    title: "Android app",
    description: "Plan meals and manage shopping lists from an Android device.",
  },
  {
    title: "iOS app",
    description: "Plan meals and manage shopping lists from an iPhone or iPad.",
  },
  {
    title: "Aldi comparison feature",
    description: "Compare prices for your shopping list at Aldi and Kroger stores.",
  }
];

const RECIPE_SOURCE_NAMES = {
  themealdb: "TheMealDB",
  budgetbytes: "Budget Bytes",
  allrecipes: "Allrecipes",
  pioneerwoman: "The Pioneer Woman",
};

function formatPrice(price) {
  if (price === null || price === undefined || price === "") return "Price unavailable";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(price));
}

const STARTER_STORE = { id: "01400943", name: "Dillons Market" };

export default function App() {
  const [ingredients, setIngredients] = useState(() => {
    try {
      const savedIngredients = JSON.parse(window.localStorage.getItem("shopping-ingredients"));
      if (Array.isArray(savedIngredients)) {
        const parsed = savedIngredients.map((ingredient) => typeof ingredient === "string"
          ? { name: ingredient, amount: "" }
          : { name: ingredient.name || "", amount: ingredient.amount || "", ...(ingredient.restock ? { restock: true } : {}) })
          .filter((ingredient) => ingredient.name);
        const isUntouchedSample = parsed.length === FORMER_STARTER_INGREDIENTS.length
          && parsed.every((ingredient, index) => ingredient.name === FORMER_STARTER_INGREDIENTS[index] && !ingredient.amount);
        return isUntouchedSample ? [] : parsed;
      }
    } catch {
      return [];
    }
    return [];
  });
  const [newIngredient, setNewIngredient] = useState("");
  const [skippedSeasonings, setSkippedSeasonings] = useState(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem("skipped-seasonings"));
      return Array.isArray(saved) ? saved.filter((item) => item && item.name) : [];
    } catch {
      return [];
    }
  });
  const [selectedStore, setSelectedStore] = useState(() => {
    try {
      const savedStore = JSON.parse(window.localStorage.getItem("my-store"));
      if (savedStore?.id && savedStore?.name) return savedStore;
    } catch {
      // Ignore unavailable or malformed browser storage and use the starter store.
    }
    return STARTER_STORE;
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
  const [preferBlockCheese, setPreferBlockCheese] = useState(() => window.localStorage.getItem("prefer-block-cheese") === "true");
  const [activeTab, setActiveTab] = useState("shopping");
  const [savedRecipes, setSavedRecipes] = useState([]);
  const [isLoadingSavedRecipes, setIsLoadingSavedRecipes] = useState(false);
  const [savedRecipesError, setSavedRecipesError] = useState("");
  const [refreshingRecipeId, setRefreshingRecipeId] = useState(null);
  const [recipeScales, setRecipeScales] = useState({});
  const [krogerConfigured, setKrogerConfigured] = useState(null);
  const [krogerClientId, setKrogerClientId] = useState("");
  const [krogerClientSecret, setKrogerClientSecret] = useState("");
  const [isSavingKrogerSettings, setIsSavingKrogerSettings] = useState(false);
  const [krogerSettingsError, setKrogerSettingsError] = useState("");
  const [krogerSettingsNotice, setKrogerSettingsNotice] = useState("");
  const [accountConnected, setAccountConnected] = useState(null);
  const [accountRedirectUri, setAccountRedirectUri] = useState("");
  const [isConnectingAccount, setIsConnectingAccount] = useState(false);
  const [accountError, setAccountError] = useState("");
  const [accountNotice, setAccountNotice] = useState("");
  const [isAddingToCart, setIsAddingToCart] = useState(false);
  const [cartAdded, setCartAdded] = useState(false);
  const [cartError, setCartError] = useState("");
  const [cartNotice, setCartNotice] = useState("");
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
  const [recipeSources, setRecipeSources] = useState(["themealdb", "budgetbytes", "allrecipes", "pioneerwoman"]);
  const [recipeResults, setRecipeResults] = useState(null);
  const [isCheckingRecipes, setIsCheckingRecipes] = useState(false);
  const [recipeError, setRecipeError] = useState("");
  const [recipeNotice, setRecipeNotice] = useState("");
  const [mealError, setMealError] = useState("");
  const [importingRecipe, setImportingRecipe] = useState("");
  const [skipPantrySeasonings, setSkipPantrySeasonings] = useState(() => window.localStorage.getItem("skip-pantry-seasonings") !== "false");

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
    window.localStorage.setItem("prefer-block-cheese", String(preferBlockCheese));
  }, [preferBlockCheese]);

  useEffect(() => {
    window.localStorage.setItem("skip-pantry-seasonings", String(skipPantrySeasonings));
  }, [skipPantrySeasonings]);

  useEffect(() => {
    if (activeTab !== "recipes" && activeTab !== "plan") return undefined;
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

  useEffect(() => {
    try {
      window.localStorage.setItem("skipped-seasonings", JSON.stringify(skippedSeasonings));
    } catch {
      // Storage unavailable; the list still works for this session.
    }
  }, [skippedSeasonings]);

  useEffect(() => {
    // Returning from the Kroger sign-in page: restore the matches stashed before leaving.
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("kroger_account");
    if (outcome) {
      window.history.replaceState(null, "", window.location.pathname);
      let returnTab = "settings";
      try {
        const stashed = window.sessionStorage.getItem("kroger-connect-stash");
        window.sessionStorage.removeItem("kroger-connect-stash");
        if (stashed) {
          const parsed = JSON.parse(stashed);
          if (Array.isArray(parsed.results)) setResults(parsed.results);
          if (typeof parsed.tab === "string") returnTab = parsed.tab;
        }
      } catch {
        // Nothing stashed; the matches can be searched again.
      }
      const message = outcome === "connected"
        ? "Dillons account connected."
        : outcome === "denied"
        ? "Dillons sign-in was cancelled."
        : "Dillons sign-in did not complete. Try connecting again.";
      if (outcome === "connected") {
        setAccountNotice(message);
        setCartNotice(message);
      } else {
        setAccountError(message);
        setCartError(message);
      }
      setActiveTab(returnTab);
    }
    loadAccountStatus();
    loadStorePreference();
  }, []);

  useEffect(() => {
    setCartAdded(false);
  }, [results]);

  useEffect(() => {
    if (activeTab !== "settings") return undefined;
    let isCurrent = true;
    fetch("/api/settings/kroger")
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Credential status could not be loaded.");
        if (isCurrent) setKrogerConfigured(data.configured);
      })
      .catch((requestError) => {
        if (isCurrent) setKrogerSettingsError(requestError.message || "Could not reach secure settings.");
      });
    return () => { isCurrent = false; };
  }, [activeTab]);

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

  function addSkippedSeasoning(indexToAdd) {
    if (ingredients.length >= 30) {
      setError("The shopping list is full (30 items). Remove something first.");
      return;
    }
    const seasoning = skippedSeasonings[indexToAdd];
    setIngredients((current) => [...current, { name: seasoning.name, amount: seasoning.amount || "", restock: true }]);
    setSkippedSeasonings((current) => current.filter((_, index) => index !== indexToAdd));
    setResults(null);
  }

  function clearIngredients() {
    setIngredients([]);
    setSkippedSeasonings([]);
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
    saveStorePreference(store);
    setStores(null);
    setStoreError("");
    setResults(null);
  }

  function searchFilters() {
    return {
      store_id: selectedStore.id,
      kroger_brands_only: krogerBrandsOnly,
      organic_meat_only: organicMeatOnly,
      organic_vegetables_only: organicVegetablesOnly,
      non_gmo_only: nonGmoOnly,
      prefer_block_cheese: preferBlockCheese,
      skip_pantry_seasonings: skipPantrySeasonings,
    };
  }

  async function restockSkippedResult(index) {
    const result = results[index];
    let marked = false;
    setIngredients((current) => current.map((ingredient) => {
      if (marked || ingredient.name !== result.name || (ingredient.amount || "") !== (result.amount || "")) return ingredient;
      marked = true;
      return { ...ingredient, restock: true };
    }));
    updateResult(index, { restockLoading: true, restockError: "" });
    try {
      const response = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...searchFilters(),
          ingredients: [{ name: result.name, amount: result.amount || "", restock: true }],
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Search could not be completed.");
      updateResult(index, { ...data.results[0], pantry_skipped: false, restockLoading: false });
    } catch (requestError) {
      updateResult(index, {
        restockLoading: false,
        restockError: requestError.message || "Could not reach the product search service.",
      });
    }
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
        body: JSON.stringify({ ...searchFilters(), ingredients }),
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

  function updateResult(indexToUpdate, changes) {
    setResults((current) => current && current.map((result, index) => (
      index === indexToUpdate ? { ...result, ...changes } : result
    )));
  }

  async function findAlternatives(index) {
    const result = results[index];
    updateResult(index, { alternativesLoading: true, alternativesError: "", alternatives: null });
    try {
      const response = await fetch("/api/search/alternatives", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ingredient_name: result.name,
          amount: result.amount || "",
          store_id: selectedStore.id,
          kroger_brands_only: krogerBrandsOnly,
          organic_meat_only: organicMeatOnly,
          organic_vegetables_only: organicVegetablesOnly,
          non_gmo_only: nonGmoOnly,
          prefer_block_cheese: preferBlockCheese,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Alternative search could not be completed.");
      updateResult(index, { alternativesLoading: false, alternatives: data.alternatives });
    } catch (requestError) {
      updateResult(index, {
        alternativesLoading: false,
        alternativesError: requestError.message || "Could not reach the product search service.",
      });
    }
  }

  function setResultRemoved(index, removed) {
    updateResult(index, { removed });
  }

  function chooseAlternative(index, alternative) {
    updateResult(index, { product: alternative, substitutedFrom: alternative.search_term, alternatives: null });
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
    setRecipeNotice("");
    setImportingRecipe(importKey);

    try {
      const response = await fetch("/api/recipes/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: source.source,
          recipe_id: source.source === "themealdb" ? recipe.id : recipe.url,
          skip_pantry_seasonings: skipPantrySeasonings,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Recipe ingredients could not be imported.");
      const shoppingIngredients = data.shopping_ingredients || data.ingredients;
      if (shoppingIngredients.length > 30) {
        throw new Error("This recipe has more than 30 shopping items and cannot fit on the list.");
      }
      setIngredients(shoppingIngredients);
      setSkippedSeasonings(data.excluded_ingredients || []);
      setSavedRecipes((current) => [
        data.saved_recipe,
        ...current.filter((savedRecipe) => savedRecipe.id !== data.saved_recipe.id),
      ]);
      setRecipeNotice(data.excluded_seasonings?.length
        ? `Shopping list replaced with ${shoppingIngredients.length} items; skipped pantry seasonings: ${data.excluded_seasonings.join(", ")} (add any you're out of from the shopping list). Full recipe saved for cooking.`
        : `Shopping list replaced with ${data.title}; full recipe saved for cooking.`);
      setResults(null);
    } catch (requestError) {
      setRecipeError(requestError.message || "Could not import recipe ingredients.");
    } finally {
      setImportingRecipe("");
    }
  }

  async function refreshSavedRecipe(recipeId) {
    setSavedRecipesError("");
    setRefreshingRecipeId(recipeId);
    try {
      const response = await fetch(`/api/recipes/saved/${recipeId}/refresh`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Recipe could not be refreshed.");
      setSavedRecipes((current) => current.map((recipe) => (recipe.id === recipeId ? data.saved_recipe : recipe)));
    } catch (requestError) {
      setSavedRecipesError(requestError.message || "Could not reach the recipe source.");
    } finally {
      setRefreshingRecipeId(null);
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

  async function saveKrogerSettings(event) {
    event.preventDefault();
    setKrogerSettingsError("");
    setKrogerSettingsNotice("");
    setIsSavingKrogerSettings(true);
    try {
      const response = await fetch("/api/settings/kroger", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: krogerClientId.trim(), client_secret: krogerClientSecret }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Credentials could not be saved.");
      setKrogerConfigured(data.configured);
      setKrogerClientId("");
      setKrogerClientSecret("");
      setKrogerSettingsNotice("Credentials encrypted and saved in this device's credential vault.");
    } catch (requestError) {
      setKrogerSettingsError(requestError.message || "Could not save credentials securely.");
    } finally {
      setIsSavingKrogerSettings(false);
    }
  }

  async function clearKrogerSettings() {
    setKrogerSettingsError("");
    setKrogerSettingsNotice("");
    setIsSavingKrogerSettings(true);
    try {
      const response = await fetch("/api/settings/kroger", { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Credentials could not be cleared.");
      setKrogerConfigured(data.configured);
      setKrogerClientId("");
      setKrogerClientSecret("");
      setKrogerSettingsNotice("Saved Kroger credentials were cleared.");
    } catch (requestError) {
      setKrogerSettingsError(requestError.message || "Could not clear saved credentials.");
    } finally {
      setIsSavingKrogerSettings(false);
    }
  }

  function saveStorePreference(store) {
    fetch("/api/preferences/store", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: store.id, name: store.name }),
    }).catch(() => {
      // The browser copy still remembers it; the server copy is a convenience.
    });
  }

  async function loadStorePreference() {
    try {
      const response = await fetch("/api/preferences/store");
      const data = await response.json();
      if (!response.ok) return;
      if (data.store?.id) {
        setSelectedStore(data.store);
      } else {
        // First run with server storage: keep a store this browser already chose.
        const savedStore = JSON.parse(window.localStorage.getItem("my-store") || "null");
        if (savedStore?.id && savedStore?.name && savedStore.id !== STARTER_STORE.id) saveStorePreference(savedStore);
      }
    } catch {
      // Keep the browser's saved store.
    }
  }

  async function loadAccountStatus() {
    try {
      const response = await fetch("/api/kroger/account");
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Account status could not be loaded.");
      setAccountConnected(data.connected);
      setAccountRedirectUri(data.redirect_uri || "");
    } catch (requestError) {
      setAccountConnected(false);
      setAccountError(requestError.message || "Could not check the Dillons account connection.");
    }
  }

  async function connectAccount() {
    setAccountError("");
    setAccountNotice("");
    setCartError("");
    setIsConnectingAccount(true);
    try {
      const response = await fetch("/api/kroger/account/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ return_to: window.location.origin }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not start the Dillons sign-in.");
      try {
        window.sessionStorage.setItem("kroger-connect-stash", JSON.stringify({ results, tab: activeTab }));
      } catch {
        // Storage unavailable; matches will need to be searched again after sign-in.
      }
      window.location.assign(data.authorize_url);
    } catch (requestError) {
      const message = requestError.message || "Could not start the Dillons sign-in.";
      if (activeTab === "settings") setAccountError(message);
      else setCartError(message);
      setIsConnectingAccount(false);
    }
  }

  async function disconnectAccount() {
    setAccountError("");
    setAccountNotice("");
    setIsConnectingAccount(true);
    try {
      const response = await fetch("/api/kroger/account", { method: "DELETE" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not disconnect the account.");
      setAccountConnected(data.connected);
      setAccountNotice("Dillons account disconnected from this app.");
    } catch (requestError) {
      setAccountError(requestError.message || "Could not disconnect the account.");
    } finally {
      setIsConnectingAccount(false);
    }
  }

  function setResultQuantity(index, value) {
    updateResult(index, { quantity: clampQuantity(value) });
  }

  async function addToCart() {
    if (!cartItems.length) return;
    setCartError("");
    setCartNotice("");
    setIsAddingToCart(true);
    try {
      const data = await addLinesToCart(cartItems);
      setCartAdded(true);
      setCartNotice(cartAddedMessage(data));
    } catch (requestError) {
      if (requestError instanceof CartError && requestError.status === 401) setAccountConnected(false);
      setCartError(requestError.message || "Could not reach the cart service.");
    } finally {
      setIsAddingToCart(false);
    }
  }

  const removedResultCount = results?.filter((result) => result.removed).length ?? 0;
  const matchedProductCount = results?.filter((result) => result.product && !result.removed).length ?? 0;
  const unmatchedProductCount = results ? results.length - removedResultCount - matchedProductCount : 0;
  const estimatedListTotal = results?.reduce((total, result) => {
    if (!result.product || result.removed) return total;
    const itemCost = Number(itemCost(result));
    return Number.isFinite(itemCost) ? total + itemCost : total;
  }, 0) ?? 0;
  const cartItems = cartLines(results || []);
  const matchedWithoutUpc = matchedProductCount - cartItems.length;

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
          <button
            id="plan-tab"
            className={activeTab === "plan" ? "view-tab active" : "view-tab"}
            type="button"
            role="tab"
            aria-selected={activeTab === "plan"}
            aria-controls="plan-view"
            onClick={() => setActiveTab("plan")}
          >
            Meal plan
          </button>
          <button
            id="enhancements-tab"
            className={activeTab === "enhancements" ? "view-tab active" : "view-tab"}
            type="button"
            role="tab"
            aria-selected={activeTab === "enhancements"}
            aria-controls="enhancements-view"
            onClick={() => setActiveTab("enhancements")}
          >
            Enhancements <span>{String(FUTURE_ENHANCEMENTS.length).padStart(2, "0")}</span>
          </button>
          <button
            id="settings-tab"
            className={activeTab === "settings" ? "view-tab active" : "view-tab"}
            type="button"
            role="tab"
            aria-selected={activeTab === "settings"}
            aria-controls="settings-view"
            onClick={() => {
              setKrogerSettingsError("");
              setKrogerSettingsNotice("");
              setActiveTab("settings");
            }}
          >
            Settings
          </button>
        </nav>

        {activeTab === "shopping" ? (
          <div id="shopping-view" role="tabpanel" aria-labelledby="shopping-tab">
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
                      {ingredient.restock && <span className="restock-badge" title="Pantry seasoning you're out of; it will be searched">restock</span>}
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

            {skippedSeasonings.length > 0 && (
              <div className="skipped-seasonings">
                <p>Skipped pantry seasonings. Out of one? Add it to the list.</p>
                <ul>
                  {skippedSeasonings.map((seasoning, index) => (
                    <li key={`${seasoning.name}-${index}`}>
                      <button type="button" onClick={() => addSkippedSeasoning(index)} title={`Add ${seasoning.name} to the shopping list`}>
                        <Plus size={13} />
                        <span>{[seasoning.amount, seasoning.name].filter(Boolean).join(" ")}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

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
              <label className="brand-toggle">
                <input
                  type="checkbox"
                  checked={preferBlockCheese}
                  onChange={(event) => {
                    setPreferBlockCheese(event.target.checked);
                    setResults(null);
                  }}
                />
                <span>Block cheese instead of shredded</span>
              </label>
              <label className="brand-toggle">
                <input
                  type="checkbox"
                  checked={skipPantrySeasonings}
                  onChange={(event) => {
                    setSkipPantrySeasonings(event.target.checked);
                    setResults(null);
                  }}
                />
                <span>Skip common pantry seasonings</span>
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
              {results && <span className="match-count">{matchedProductCount} FOUND</span>}
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
                {results.map((result, index) => result.removed ? (
                  <article className="result-row removed-row" key={`${result.ingredient}-${index}`}>
                    <span className="result-index">{String(index + 1).padStart(2, "0")}</span>
                    <div className="result-copy">
                      <p className="result-ingredient">{result.ingredient}</p>
                      <p className="removed-note">
                        {result.product ? `Removed — ${result.product.description || "product"}` : "Removed from list"}
                      </p>
                    </div>
                    <button type="button" className="alternative-trigger" onClick={() => setResultRemoved(index, false)}>
                      <Undo2 size={14} />
                      <span>Undo</span>
                    </button>
                  </article>
                ) : (
                  <article className={`result-row ${result.product ? "" : result.pantry_skipped ? "pantry-skipped" : "no-match"}`} key={`${result.ingredient}-${index}`}>
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
                          {result.substitutedFrom && (
                            <p className="alternative-note">Alternative via “{result.substitutedFrom}”</p>
                          )}
                        </>
                      ) : result.pantry_skipped ? (
                        <div className="pantry-skip">
                          <p className="not-found">Pantry seasoning — skipped</p>
                          <button
                            type="button"
                            className="alternative-trigger"
                            disabled={result.restockLoading}
                            onClick={() => restockSkippedResult(index)}
                          >
                            {result.restockLoading ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />}
                            <span>{result.restockLoading ? "Searching" : "I'm out — find it"}</span>
                          </button>
                          {result.restockError && <p className="alternative-error" role="alert">{result.restockError}</p>}
                        </div>
                      ) : <p className="not-found">
                        {result.non_gmo_only
                          ? "No explicit non-GMO claim found"
                          : result.organic_filter
                          ? `No organic ${result.organic_filter} match found`
                          : krogerBrandsOnly ? "No Kroger-brand match found" : "No product match found"}
                      </p>}
                      {!result.product && !result.pantry_skipped && result.name && (
                        <div className="alternatives">
                          {result.alternatives ? (
                            result.alternatives.length ? (
                              <ul className="alternative-list">
                                {result.alternatives.map((alternative) => (
                                  <li key={alternative.upc || alternative.description}>
                                    <div>
                                      <span className="alternative-name">{alternative.description}</span>
                                      <span className="alternative-meta">
                                        {[alternative.brand, alternative.size, formatPrice(alternative.estimated_cost ?? alternative.price)]
                                          .filter(Boolean).join(" · ")}
                                      </span>
                                    </div>
                                    <button type="button" className="alternative-use" onClick={() => chooseAlternative(index, alternative)}>
                                      Use
                                    </button>
                                  </li>
                                ))}
                              </ul>
                            ) : <p className="alternative-empty">No alternatives match your current filters.</p>
                          ) : (
                            <button
                              type="button"
                              className="alternative-trigger"
                              disabled={result.alternativesLoading}
                              onClick={() => findAlternatives(index)}
                            >
                              {result.alternativesLoading ? <LoaderCircle className="spin" size={14} /> : <Search size={14} />}
                              <span>{result.alternativesLoading ? "Searching alternatives" : "Find alternatives"}</span>
                            </button>
                          )}
                          {result.alternativesError && <p className="alternative-error" role="alert">{result.alternativesError}</p>}
                        </div>
                      )}
                    </div>
                    <div className="price-cell">
                      <span>EST. COST</span>
                      <strong className="product-price">{result.product ? formatPrice(itemCost(result)) : "—"}</strong>
                      {result.product?.upc && (
                        <label className="quantity-field">
                          <span>QTY</span>
                          <input
                            type="number"
                            min="1"
                            max="99"
                            value={itemQuantity(result)}
                            onChange={(event) => setResultQuantity(index, event.target.value)}
                            aria-label={`Packages of ${result.ingredient}`}
                          />
                        </label>
                      )}
                      <button
                        type="button"
                        className="remove-result"
                        aria-label={`Remove ${result.ingredient}`}
                        title="Remove from cart"
                        onClick={() => setResultRemoved(index, true)}
                      >
                        <X size={14} />
                      </button>
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
                    {[
                      unmatchedProductCount
                        ? `${matchedProductCount} matched; ${unmatchedProductCount} unmatched excluded`
                        : `${matchedProductCount} matched items`,
                      removedResultCount ? `${removedResultCount} removed` : null,
                    ].filter(Boolean).join("; ")}
                  </small>
                </div>
                <div className="cart-action">
                  {accountConnected ? (
                    <button
                      className="cart-button"
                      type="button"
                      onClick={addToCart}
                      disabled={isAddingToCart || cartAdded || cartItems.length === 0}
                    >
                      {isAddingToCart ? <LoaderCircle className="spin" size={17} /> : <ShoppingCart size={17} />}
                      <span>{isAddingToCart ? "Adding" : cartAdded ? "Added to cart" : `Add ${cartItems.length} to cart`}</span>
                    </button>
                  ) : (
                    <button
                      className="cart-button"
                      type="button"
                      onClick={connectAccount}
                      disabled={isConnectingAccount || accountConnected === null}
                    >
                      {isConnectingAccount ? <LoaderCircle className="spin" size={17} /> : <ShoppingCart size={17} />}
                      <span>Connect Dillons to add</span>
                    </button>
                  )}
                  <small>
                    {accountConnected
                      ? matchedWithoutUpc > 0 ? `${matchedWithoutUpc} without a UPC skipped · pickup` : "Pickup order"
                      : "Sign in with your Dillons account"}
                  </small>
                </div>
              </div>
            )}
            {results && results.length > 0 && cartError && <p className="store-error" role="alert">{cartError}</p>}
            {results && results.length > 0 && cartNotice && (
              <p className="recipe-notice" role="status">
                {cartNotice}{" "}
                {cartAdded && <a href={DILLONS_CART_URL} target="_blank" rel="noreferrer">Open Dillons cart</a>}
              </p>
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
                <label>
                  <input type="checkbox" checked={recipeSources.includes("allrecipes")} onChange={() => toggleRecipeSource("allrecipes")} />
                  <span>Allrecipes</span>
                </label>
                <label>
                  <input type="checkbox" checked={recipeSources.includes("pioneerwoman")} onChange={() => toggleRecipeSource("pioneerwoman")} />
                  <span>The Pioneer Woman</span>
                </label>
              </fieldset>
              <button className="recipe-search-button" type="button" onClick={checkRecipes} disabled={isCheckingRecipes || !mealIdeas.length || !recipeSources.length}>
                {isCheckingRecipes ? <LoaderCircle className="spin" size={18} /> : <Search size={18} />}
                <span>{isCheckingRecipes ? "Checking sites" : "Check recipe sites"}</span>
              </button>
              {recipeError && <p className="store-error" role="alert">{recipeError}</p>}
              {recipeNotice && <p className="recipe-notice" role="status">{recipeNotice}</p>}
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
                                    <span>Replace list</span>
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
          </div>
        ) : activeTab === "recipes" ? (
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
                        <p>
                          {[
                            RECIPE_SOURCE_NAMES[recipe.source] || recipe.source,
                            recipe.servings ? `Makes ${scaleServings(recipe.servings, recipeScales[recipe.id] || 1)}` : null,
                            `${recipe.ingredients.length} ingredients`,
                            `${recipe.instructions.length} steps`,
                          ].filter(Boolean).join(" · ")}
                        </p>
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
                      <div className="recipe-toolbar">
                        <a className="saved-source-link" href={recipe.url} target="_blank" rel="noreferrer">
                          View original recipe <ExternalLink size={13} />
                        </a>
                        <div className="scale-picker" role="group" aria-label={`Scale ${recipe.title}`}>
                          {SCALE_OPTIONS.map((factor) => (
                            <button
                              key={factor}
                              type="button"
                              className={(recipeScales[recipe.id] || 1) === factor ? "active" : ""}
                              aria-pressed={(recipeScales[recipe.id] || 1) === factor}
                              onClick={() => setRecipeScales((current) => ({ ...current, [recipe.id]: factor }))}
                            >
                              {factor}×
                            </button>
                          ))}
                        </div>
                      </div>
                      <p className="recipe-servings">
                        {recipe.servings
                          ? `Makes ${scaleServings(recipe.servings, recipeScales[recipe.id] || 1)}${(recipeScales[recipe.id] || 1) > 1 ? ` (${recipeScales[recipe.id]}× of ${recipe.servings})` : ""}`
                          : "Servings not listed by the source."}
                        {!recipe.servings && recipe.source !== "themealdb" && (
                          <button type="button" className="text-link-button" disabled={refreshingRecipeId === recipe.id} onClick={() => refreshSavedRecipe(recipe.id)}>
                            {refreshingRecipeId === recipe.id ? "Loading…" : "Load from source"}
                          </button>
                        )}
                      </p>
                      <div className="saved-recipe-content">
                        <section>
                          <h4>Ingredients</h4>
                          <ul>
                            {recipe.ingredients.map((ingredient, index) => (
                              <li key={`${ingredient.name}-${index}`}>
                                {ingredient.amount && <strong>{scaleAmount(ingredient.amount, recipeScales[recipe.id] || 1)} </strong>}{ingredient.name}
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
                          ) : (
                            <div className="instructions-missing">
                              <p className="source-no-match">Instructions weren't saved with this recipe.</p>
                              <button
                                type="button"
                                className="alternative-trigger"
                                disabled={refreshingRecipeId === recipe.id}
                                onClick={() => refreshSavedRecipe(recipe.id)}
                              >
                                {refreshingRecipeId === recipe.id ? <LoaderCircle className="spin" size={14} /> : <Search size={14} />}
                                <span>{refreshingRecipeId === recipe.id ? "Loading" : "Load instructions from source"}</span>
                              </button>
                            </div>
                          )}
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
        ) : activeTab === "plan" ? (
          <MealPlan
            savedRecipes={savedRecipes}
            isLoadingSavedRecipes={isLoadingSavedRecipes}
            selectedStore={selectedStore}
            accountConnected={accountConnected}
            isConnectingAccount={isConnectingAccount}
            onConnectAccount={connectAccount}
            onAccountRejected={() => setAccountConnected(false)}
            connectNotice={cartNotice}
            connectError={cartError}
          />
        ) : activeTab === "enhancements" ? (
          <section className="enhancements-view" id="enhancements-view" role="tabpanel" aria-labelledby="enhancements-tab">
            <div className="enhancements-heading">
              <div>
                <p className="panel-kicker">ON THE ROADMAP</p>
                <h2>Future enhancements</h2>
              </div>
              <span className="item-count">{String(FUTURE_ENHANCEMENTS.length).padStart(2, "0")}</span>
            </div>
            <ol className="enhancements-list">
              {FUTURE_ENHANCEMENTS.map((enhancement, index) => (
                <li className="enhancement-row" key={enhancement.title}>
                  <span className="ingredient-index">{String(index + 1).padStart(2, "0")}</span>
                  <div>
                    <h3>{enhancement.title}</h3>
                    <p>{enhancement.description}</p>
                  </div>
                  <span className="enhancement-status">PLANNED</span>
                </li>
              ))}
            </ol>
          </section>
        ) : (
          <section className="settings-view" id="settings-view" role="tabpanel" aria-labelledby="settings-tab">
            <div className="settings-heading">
              <div>
                <p className="panel-kicker">SECURE CONNECTION</p>
                <h2>Kroger API credentials</h2>
              </div>
              <span className={krogerConfigured ? "credential-status configured" : "credential-status"}>
                {krogerConfigured === null ? "CHECKING" : krogerConfigured ? "CONFIGURED" : "NOT SET"}
              </span>
            </div>
            <p className="settings-description">
              Credentials are encrypted in the local database. The encryption key is stored separately in this device’s operating-system credential vault. Secrets are never shown again after saving.
            </p>
            <form className="credential-form" onSubmit={saveKrogerSettings}>
              <label htmlFor="kroger-client-id">Client ID</label>
              <input
                id="kroger-client-id"
                autoComplete="off"
                value={krogerClientId}
                onChange={(event) => setKrogerClientId(event.target.value)}
                required
              />
              <label htmlFor="kroger-client-secret">Client secret</label>
              <input
                id="kroger-client-secret"
                type="password"
                autoComplete="new-password"
                value={krogerClientSecret}
                onChange={(event) => setKrogerClientSecret(event.target.value)}
                required
              />
              <div className="credential-actions">
                <button className="recipe-search-button" type="submit" disabled={isSavingKrogerSettings || !krogerClientId.trim() || !krogerClientSecret}>
                  {isSavingKrogerSettings ? <LoaderCircle className="spin" size={17} /> : null}
                  <span>{isSavingKrogerSettings ? "Saving securely" : "Save credentials"}</span>
                </button>
                <button className="clear-credentials-button" type="button" onClick={clearKrogerSettings} disabled={isSavingKrogerSettings || !krogerConfigured}>
                  Clear saved credentials
                </button>
              </div>
            </form>
            {krogerSettingsError && <p className="store-error" role="alert">{krogerSettingsError}</p>}
            {krogerSettingsNotice && <p className="recipe-notice" role="status">{krogerSettingsNotice}</p>}

            <div className="settings-heading account-heading">
              <div>
                <p className="panel-kicker">SHOPPER ACCOUNT</p>
                <h2>Dillons account</h2>
              </div>
              <span className={accountConnected ? "credential-status configured" : "credential-status"}>
                {accountConnected === null ? "CHECKING" : accountConnected ? "CONNECTED" : "NOT CONNECTED"}
              </span>
            </div>
            <p className="settings-description">
              Sign in on Kroger’s own page to let this app add items to your Dillons cart. Your password never passes through this app; the sign-in token is encrypted like the credentials above.
              {accountRedirectUri && (
                <> Your Kroger developer app must list this redirect URI: <code className="redirect-uri">{accountRedirectUri}</code></>
              )}
            </p>
            <div className="credential-actions">
              <button
                className="recipe-search-button"
                type="button"
                onClick={connectAccount}
                disabled={isConnectingAccount || !krogerConfigured}
              >
                {isConnectingAccount ? <LoaderCircle className="spin" size={17} /> : null}
                <span>{accountConnected ? "Reconnect Dillons account" : "Connect Dillons account"}</span>
              </button>
              <button className="clear-credentials-button" type="button" onClick={disconnectAccount} disabled={isConnectingAccount || !accountConnected}>
                Disconnect
              </button>
            </div>
            {!krogerConfigured && krogerConfigured !== null && (
              <p className="settings-description">Save the Kroger API credentials first.</p>
            )}
            {accountError && <p className="store-error" role="alert">{accountError}</p>}
            {accountNotice && <p className="recipe-notice" role="status">{accountNotice}</p>}
          </section>
        )}

        <footer className="page-footer"><span>SUPPER RUN</span><span>PLAN WELL. EAT WELL.</span></footer>
      </main>
    </div>
  );
}