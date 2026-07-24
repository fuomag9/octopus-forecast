# Octopus Forecast (Italy)

Forecasts your **monthly electricity bill** for Octopus Energy Italy from your
real consumption and billing data via the Kraken API.

It **learns your own fixed + per‑kWh cost structure** from your billing history
(`cost ≈ fixed_monthly + unit_rate × kWh`), so it captures every Italian bill
component — energy, transport, oneri di sistema, accise and IVA — without any
rate tables to maintain. It then projects the current month from the consumption
already metered plus your historical run‑rate.

**Sensors:** monthly bill forecast, monthly energy forecast, cost/energy month‑to‑date,
remaining cost, fixed monthly cost, effective unit rate, account balance, last bill.

**Setup:** add the integration and paste your personal API key from
*octopusenergy.it → area personale → Impostazioni*.

Unofficial community integration; not affiliated with Octopus Energy.
