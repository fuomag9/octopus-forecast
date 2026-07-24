# Octopus Forecast (Italy) — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Forecasts your **monthly electricity bill** for [Octopus Energy Italy](https://octopusenergy.it)
from your real consumption and billing data — pulled live from Octopus's Kraken API.

Instead of hard‑coding Italy's many regulated tariff components (which ARERA
changes every quarter), this integration **learns your own all‑in cost structure
from your billing history** and combines it with the consumption already measured
this month to project where the bill will land.

## What it forecasts, and how

An Italian electricity bill is a sum of components that fall into two groups:

| Type | Examples | Behaviour |
|------|----------|-----------|
| **Fixed** (non‑consumption) | commercializzazione / standing charge, quota potenza, fixed transport & system fees | billed every month even at zero usage |
| **Variable** (consumption) | materia prima (energy), variable transport, oneri di sistema, accise, the VAT on all of it | scales with kWh |

The integration fits a simple model to your actual monthly bills:

```
monthly_cost  ≈  fixed_monthly  +  unit_rate × kWh
```

* `fixed_monthly` = everything you pay at zero consumption (VAT included)
* `unit_rate` = the blended all‑in cost of one extra kWh (energy + network + system charges + excise + VAT)

Because the fit is done on *your* bills, it automatically captures every charge
that actually appears on them — no rate tables to maintain. As new bills arrive
the model keeps improving. With fewer than three billed months it falls back to
an average‑rate estimate, and finally to the published tariff parameters, so you
get a sensible number from day one.

The current month's total is projected as:

```
projected_kWh = kWh_so_far + remaining_days × blended_daily_average
```

where the daily average blends the month's actual run‑rate with your historical
monthly norm, weighted by how much of the month has already been metered (early
in the month it leans on history, later it leans on the real run‑rate). Octopus
delivers metered data with roughly a two‑day lag, which the projection accounts for.

## Sensors

| Sensor | Description |
|--------|-------------|
| **Monthly bill forecast** | Projected total € for the current calendar month (VAT incl.). Rich attributes: fixed vs variable split, VAT, month‑to‑date, remaining, projected kWh, model R², confidence, tariff. |
| **Monthly energy forecast** | Projected total kWh for the month |
| **Cost this month to date** | € accrued so far (fixed pro‑rata + variable on metered kWh) |
| **Energy this month to date** | kWh metered so far this month |
| **Remaining cost this month** | Forecast total − cost to date |
| **Fixed monthly cost** | The non‑consumption part of your bill (€/month) |
| **Effective unit rate** | Your true blended €/kWh, all‑in |
| **Account balance** | Current Octopus account balance |
| **Last bill** | Most recent statement total (+ period/VAT attributes) |
| **Average monthly bill** | Baseline average of your billed months; attributes expose the full learned kWh→€ history |

All sensors are grouped under one device per account.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/fuomag9/octopus-forecast`, category **Integration**.
2. Search for **Octopus Forecast** and install it.
3. Restart Home Assistant.

### Manual

Copy `custom_components/octopus_forecast` into your Home Assistant `config/custom_components/` folder and restart.

## Configuration

**Settings → Devices & Services → Add Integration → Octopus Forecast**, then paste your **personal API key**.

### Getting your API key

Octopus Energy Italy does **not** show the API key anywhere in its website — you
have to generate one from your account via their API. Do it once:

1. Log in at [octopusenergy.it](https://octopusenergy.it/area-personale).
2. Open your browser's developer console (**F12 → Console**).
3. Paste this and press Enter:

   ```js
   fetch('/api/graphql/kraken', {
     method: 'POST',
     headers: { 'Content-Type': 'application/json' },
     credentials: 'include',
     body: JSON.stringify({ query: 'mutation { regenerateSecretKey { key } }' })
   })
     .then(r => r.json())
     .then(d => console.log('YOUR OCTOPUS API KEY:', d.data.regenerateSecretKey.key));
   ```

4. Copy the printed key and paste it into the integration.

> ⚠️ This **generates** the key. If you run it again it *regenerates* it and
> invalidates the previous one, so keep the key somewhere safe and only rerun it
> if you need to rotate it.

If the key exposes more than one account you'll pick which to forecast; otherwise
it's selected automatically. The key is stored by Home Assistant and only ever
sent to Octopus's own API over HTTPS.

### Options

**Configure** on the integration lets you adjust:

* **VAT rate** — defaults to 10% (Italian domestic band). Only used for the net/VAT split and the cold‑start fallback; the learned model already includes VAT.
* **Fixed monthly cost override / Unit rate override** — pin the model by hand. Leave both empty to let it learn from your bills.

## Notes & limitations

* **Electricity only.** Gas is read but not forecast (the reference account has no active gas supply).
* Your active tariff (e.g. *Octopus Fissa 12M*) is shown in the forecast attributes, including when it expires. When a fixed tariff rolls over to a variable one (e.g. *Octopus Flex*), keep an eye on the forecast — the learned rate adapts as the new bills come in.
* This is an unofficial, community‑built integration and is not affiliated with or endorsed by Octopus Energy.

## License

MIT — see [LICENSE](LICENSE).
