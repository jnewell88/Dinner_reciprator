// Shared cart helpers for the Shopping and Meal Plan tabs.

// Packages to buy: a hand-edited quantity, else the cost estimate's package count.
export function itemQuantity(item) {
  return item.quantity ?? item.product?.quantity ?? 1;
}

// A hand-edited quantity is priced per package; otherwise use the quantity-aware estimate.
export function itemCost(item) {
  const product = item.product;
  if (!product) return null;
  if (item.quantity != null && Number.isFinite(Number(product.price))) return Number(product.price) * item.quantity;
  return product.estimated_cost ?? product.price;
}

export function clampQuantity(value) {
  return Math.min(99, Math.max(1, Math.round(Number(value)) || 1));
}

// Cart lines for every kept item with a UPC.
export function cartLines(items) {
  return items
    .filter((item) => item.product?.upc && !item.removed)
    .map((item) => ({ upc: item.product.upc, quantity: itemQuantity(item) }));
}

export class CartError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function addLinesToCart(lines) {
  const response = await fetch("/api/cart/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: lines, modality: "PICKUP" }),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "Items could not be added to the cart.";
    throw new CartError(detail, response.status);
  }
  return data;
}

export function cartAddedMessage({ added, packages }) {
  return `Added ${added} item${added === 1 ? "" : "s"} (${packages} package${packages === 1 ? "" : "s"}) to your Dillons cart.`;
}

export const DILLONS_CART_URL = "https://www.dillons.com/cart";
