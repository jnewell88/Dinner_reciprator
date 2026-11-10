KROGER_BASE_URL = "https://api.kroger.com/v1"

# Kroger shopper-account linking (OAuth authorization code). Register this exact redirect URI
# on the app at developer.kroger.com.
KROGER_REDIRECT_URI = "http://localhost:8000/api/kroger/callback"
KROGER_ACCOUNT_SCOPE = "cart.basic:write"
