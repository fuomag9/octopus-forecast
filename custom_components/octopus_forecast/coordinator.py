"""DataUpdateCoordinator for Octopus Forecast."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AccountOverview,
    OctopusApiError,
    OctopusAuthError,
    OctopusEnergyItaly,
)
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_API_KEY,
    CONF_FIXED_OVERRIDE,
    CONF_RATE_OVERRIDE,
    CONF_VAT_RATE,
    DEFAULT_VAT_RATE,
    DOMAIN,
    UPDATE_INTERVAL,
)
from .forecast import (
    CostModel,
    ForecastResult,
    MonthProjection,
    aggregate_monthly,
    build_forecast,
    fit_cost_model,
    project_month,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ForecastData:
    """Bundle returned by the coordinator for the sensors to consume."""

    overview: AccountOverview
    model: CostModel
    projection: MonthProjection
    forecast: ForecastResult
    last_statement: dict | None
    monthly_history: dict[str, tuple[float, float]]
    vat_rate: float


class OctopusForecastCoordinator(DataUpdateCoordinator[ForecastData]):
    """Fetches Octopus data and computes the monthly bill forecast."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        self.account_number: str = entry.data[CONF_ACCOUNT_NUMBER]
        session = async_get_clientsession(hass)
        self.api = OctopusEnergyItaly(session, entry.data[CONF_API_KEY])

    def _opt(self, key: str, default=None):
        return self.entry.options.get(key, self.entry.data.get(key, default))

    async def _async_update_data(self) -> ForecastData:
        vat_rate = float(self._opt(CONF_VAT_RATE, DEFAULT_VAT_RATE))
        fixed_override = self._opt(CONF_FIXED_OVERRIDE)
        rate_override = self._opt(CONF_RATE_OVERRIDE)

        try:
            overview = await self.api.async_get_overview(self.account_number)
            periods = await self.api.async_get_billed_history(self.account_number)
            last_statement = await self.api.async_get_last_statement(
                self.account_number
            )

            now = dt_util.now()
            # Fetch from the start of the previous month so we always cover the
            # current month even across the month boundary + metering lag.
            first_prev = (now.replace(day=1) - timedelta(days=1)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            daily = []
            if overview.property_id and overview.pod:
                daily = await self.api.async_get_daily_consumption(
                    overview.property_id, overview.pod, first_prev, now
                )
        except OctopusAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OctopusApiError as err:
            raise UpdateFailed(str(err)) from err

        monthly_history = aggregate_monthly(periods)

        model = fit_cost_model(
            periods,
            standing_charge_year=overview.standing_charge_year,
            consumption_charge=overview.consumption_charge,
            vat_rate=vat_rate,
            fixed_override=(
                float(fixed_override) if fixed_override not in (None, "") else None
            ),
            rate_override=(
                float(rate_override) if rate_override not in (None, "") else None
            ),
        )

        if model is None:
            raise UpdateFailed(
                "Not enough billing history or tariff data to build a forecast yet"
            )

        projection = project_month(daily, monthly_history, now)
        forecast = build_forecast(model, projection, vat_rate)

        _LOGGER.debug(
            "Forecast %s: %.2f€ (%.0f kWh) fixed=%.2f rate=%.4f method=%s conf=%s",
            projection.month,
            forecast.forecast_cost,
            projection.projected_kwh,
            model.fixed_monthly,
            model.unit_rate,
            model.method,
            forecast.confidence,
        )

        return ForecastData(
            overview=overview,
            model=model,
            projection=projection,
            forecast=forecast,
            last_statement=last_statement,
            monthly_history=monthly_history,
            vat_rate=vat_rate,
        )
