"""Sensors for Octopus Forecast."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import OctopusForecastConfigEntry
from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import ForecastData, OctopusForecastCoordinator

CURRENCY = "EUR"
RATE_UNIT = "EUR/kWh"


@dataclass(frozen=True, kw_only=True)
class OctopusForecastSensorDescription(SensorEntityDescription):
    """Description with a value getter and optional attribute getter."""

    value_fn: Callable[[ForecastData], float | str | None]
    attrs_fn: Callable[[ForecastData], dict | None] | None = None


def _forecast_attrs(data: ForecastData) -> dict:
    f = data.forecast
    m = data.model
    p = data.projection
    o = data.overview
    return {
        "net_excl_vat": f.forecast_net,
        "vat": f.forecast_tax,
        "fixed_component": f.fixed_component,
        "variable_component": f.variable_component,
        "month_to_date_cost": f.mtd_cost,
        "remaining_cost": f.remaining_cost,
        "projected_kwh": p.projected_kwh,
        "month_to_date_kwh": p.mtd_kwh,
        "days_with_data": p.days_with_data,
        "days_in_month": p.days_in_month,
        "data_through": p.data_through.isoformat() if p.data_through else None,
        "fixed_monthly_rate": m.fixed_monthly,
        "unit_rate": m.unit_rate,
        "model_method": m.method,
        "model_r2": m.r2,
        "model_data_points": m.n_points,
        "projection_method": p.method,
        "confidence": f.confidence,
        "tariff": o.product_name,
        "tariff_valid_to": o.tariff_valid_to,
        "vat_rate_percent": data.vat_rate,
    }


def _history_attrs(data: ForecastData) -> dict:
    # Expose the learned (kWh -> €) history so the model is transparent.
    costs = [c for _, c in data.monthly_history.values()]
    kwhs = [k for k, _ in data.monthly_history.values()]
    return {
        "billed_months": len(costs),
        "average_kwh": round(sum(kwhs) / len(kwhs), 1) if kwhs else None,
        "min_cost": round(min(costs), 2) if costs else None,
        "max_cost": round(max(costs), 2) if costs else None,
        "history": {
            k: {"kwh": round(v[0], 1), "cost": round(v[1], 2)}
            for k, v in sorted(data.monthly_history.items())
        },
    }


SENSORS: tuple[OctopusForecastSensorDescription, ...] = (
    OctopusForecastSensorDescription(
        key="month_cost_forecast",
        translation_key="month_cost_forecast",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:cash-clock",
        value_fn=lambda d: d.forecast.forecast_cost,
        attrs_fn=_forecast_attrs,
    ),
    OctopusForecastSensorDescription(
        key="month_energy_forecast",
        translation_key="month_energy_forecast",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:lightning-bolt-outline",
        value_fn=lambda d: d.projection.projected_kwh,
        attrs_fn=lambda d: {
            "month_to_date_kwh": d.projection.mtd_kwh,
            "daily_average_kwh": d.projection.blended_daily_avg,
            "days_with_data": d.projection.days_with_data,
            "days_in_month": d.projection.days_in_month,
            "method": d.projection.method,
        },
    ),
    OctopusForecastSensorDescription(
        key="month_to_date_cost",
        translation_key="month_to_date_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:cash",
        value_fn=lambda d: d.forecast.mtd_cost,
    ),
    OctopusForecastSensorDescription(
        key="month_to_date_energy",
        translation_key="month_to_date_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:lightning-bolt",
        value_fn=lambda d: d.projection.mtd_kwh,
    ),
    OctopusForecastSensorDescription(
        key="remaining_cost",
        translation_key="remaining_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:cash-minus",
        value_fn=lambda d: d.forecast.remaining_cost,
    ),
    OctopusForecastSensorDescription(
        key="fixed_monthly_cost",
        translation_key="fixed_monthly_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:file-document-outline",
        value_fn=lambda d: d.model.fixed_monthly,
        attrs_fn=lambda d: {
            "description": "Amount billed each month regardless of consumption "
            "(standing charge, power quota, fixed network/system fees), VAT included.",
            "source": d.model.method,
        },
    ),
    OctopusForecastSensorDescription(
        key="unit_rate",
        translation_key="unit_rate",
        native_unit_of_measurement=RATE_UNIT,
        suggested_display_precision=4,
        icon="mdi:currency-eur",
        value_fn=lambda d: d.model.unit_rate,
        attrs_fn=lambda d: {
            "description": "Blended all-in cost of one extra kWh (energy, network, "
            "system charges, excise and VAT).",
            "source": d.model.method,
        },
    ),
    OctopusForecastSensorDescription(
        key="account_balance",
        translation_key="account_balance",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:wallet-outline",
        value_fn=lambda d: d.overview.balance,
    ),
    OctopusForecastSensorDescription(
        key="last_bill",
        translation_key="last_bill",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:receipt-text-outline",
        value_fn=lambda d: d.last_statement["gross"] if d.last_statement else None,
        attrs_fn=lambda d: (
            {
                "period_from": d.last_statement["from"],
                "period_to": d.last_statement["to"],
                "issued": d.last_statement["issued"],
                "net_excl_vat": d.last_statement["net"],
                "vat": d.last_statement["tax"],
            }
            if d.last_statement
            else None
        ),
    ),
    OctopusForecastSensorDescription(
        key="average_monthly_bill",
        translation_key="average_monthly_bill",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:chart-box-outline",
        value_fn=lambda d: (
            round(
                sum(c for _, c in d.monthly_history.values())
                / len(d.monthly_history),
                2,
            )
            if d.monthly_history
            else None
        ),
        attrs_fn=_history_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OctopusForecastConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        OctopusForecastSensor(coordinator, description) for description in SENSORS
    )


class OctopusForecastSensor(
    CoordinatorEntity[OctopusForecastCoordinator], SensorEntity
):
    """A single forecast sensor."""

    entity_description: OctopusForecastSensorDescription
    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: OctopusForecastCoordinator,
        description: OctopusForecastSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        account = coordinator.account_number
        self._attr_unique_id = f"{account}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account)},
            name=f"Octopus Forecast {account}",
            manufacturer=MANUFACTURER,
            model="Electricity bill forecast",
            configuration_url="https://octopusenergy.it/area-personale",
        )

    @property
    def native_value(self) -> float | str | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
