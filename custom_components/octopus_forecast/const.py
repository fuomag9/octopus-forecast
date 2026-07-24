"""Constants for the Octopus Forecast integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "octopus_forecast"

# --- Config / options keys ---------------------------------------------------
CONF_API_KEY = "api_key"
CONF_ACCOUNT_NUMBER = "account_number"
CONF_VAT_RATE = "vat_rate"
CONF_FIXED_OVERRIDE = "fixed_monthly_override"
CONF_RATE_OVERRIDE = "unit_rate_override"
CONF_HISTORY_MONTHS = "history_months"

# --- Defaults ----------------------------------------------------------------
# Italian domestic electricity VAT is 10% (up to the standard consumption band).
DEFAULT_VAT_RATE = 10.0
DEFAULT_HISTORY_MONTHS = 18
# The distributor delivers metered data with a lag; readings are typically
# available up to ~2 days ago. Used when deciding which days are "complete".
READING_LAG_DAYS = 2

# The integration polls Kraken. Consumption/billing data changes at most daily,
# so a few hours between refreshes is plenty and keeps well under rate limits.
UPDATE_INTERVAL = timedelta(hours=3)

# --- Kraken (Octopus Energy Italy) API ---------------------------------------
API_BASE = "https://api.oeit-kraken.energy/v1/graphql/"
ELECTRICITY_MARKET = "ITA_ELECTRICITY"
GAS_MARKET = "ITA_GAS"

# Refresh the JWT a little before it actually expires.
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)

# --- Attribution -------------------------------------------------------------
ATTRIBUTION = "Data provided by Octopus Energy Italy (Kraken)"
MANUFACTURER = "Octopus Energy"
