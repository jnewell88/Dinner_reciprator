// Mirrors scale_amount() / scale_servings() in api.py for the saved-recipe view.
const UNICODE_FRACTIONS = { "¼": 0.25, "½": 0.5, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875 };
const QUANTITY_PATTERN = /^\s*(\d+\s+\d+\s*\/\s*\d+|\d+\s*[¼½¾⅓⅔⅛⅜⅝⅞]|\d+\s*\/\s*\d+|\d+(?:\.\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞])/;

function quantityValue(text) {
  const clean = text.replace(/\s+/g, " ").trim();
  const mixedUnicode = clean.match(/^(\d+)\s*([¼½¾⅓⅔⅛⅜⅝⅞])$/);
  if (mixedUnicode) return Number(mixedUnicode[1]) + UNICODE_FRACTIONS[mixedUnicode[2]];
  if (clean in UNICODE_FRACTIONS) return UNICODE_FRACTIONS[clean];
  if (clean.includes("/")) {
    const [left, denominator] = clean.split("/").map((part) => part.trim());
    const pieces = left.split(" ");
    const whole = pieces.length > 1 ? Number(pieces[0]) : 0;
    return whole + Number(pieces[pieces.length - 1]) / Number(denominator);
  }
  return Number(clean);
}

function formatQuantity(value) {
  const whole = Math.floor(value + 1e-9);
  const remainder = value - whole;
  if (remainder < 1e-6) return String(whole);
  for (const denominator of [2, 3, 4, 8, 16]) {
    const numerator = Math.round(remainder * denominator);
    if (numerator > 0 && numerator < denominator && Math.abs(numerator / denominator - remainder) < 1e-3) {
      return whole ? `${whole} ${numerator}/${denominator}` : `${numerator}/${denominator}`;
    }
  }
  return String(Math.round(value * 100) / 100);
}

export function scaleAmount(amount, factor) {
  if (factor === 1 || !amount) return amount;
  return amount.split("+").map((part) => {
    const trimmed = part.trim();
    const match = trimmed.match(QUANTITY_PATTERN);
    if (!match) return trimmed;
    return `${formatQuantity(quantityValue(match[1]) * factor)}${trimmed.slice(match[0].length)}`;
  }).join(" + ");
}

export function scaleServings(servings, factor) {
  if (factor === 1 || !servings) return servings;
  return servings.replace(/\d+/g, (number) => String(Number(number) * factor));
}

export const SCALE_OPTIONS = [1, 2, 4];
