"""Config and options flow for Octopus Forecast."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import OctopusApiError, OctopusAuthError, OctopusEnergyItaly
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_API_KEY,
    CONF_FIXED_OVERRIDE,
    CONF_RATE_OVERRIDE,
    CONF_VAT_RATE,
    DEFAULT_VAT_RATE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class OctopusForecastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._accounts: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the API key and validate it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            session = async_get_clientsession(self.hass)
            client = OctopusEnergyItaly(session, api_key)
            try:
                accounts = await client.async_validate()
            except OctopusAuthError:
                errors["base"] = "invalid_auth"
            except OctopusApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating API key")
                errors["base"] = "unknown"
            else:
                self._api_key = api_key
                self._accounts = accounts
                if len(accounts) == 1:
                    return await self._create(accounts[0]["number"])
                return await self.async_step_account()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
            description_placeholders={
                "url": "https://octopusenergy.it/area-personale"
            },
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an account when the API key exposes more than one."""
        if user_input is not None:
            return await self._create(user_input[CONF_ACCOUNT_NUMBER])

        numbers = [a["number"] for a in self._accounts]
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT_NUMBER): SelectSelector(
                        SelectSelectorConfig(
                            options=numbers, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def _create(self, account_number: str) -> ConfigFlowResult:
        await self.async_set_unique_id(account_number)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Octopus {account_number}",
            data={
                CONF_API_KEY: self._api_key,
                CONF_ACCOUNT_NUMBER: account_number,
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the API key stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            session = async_get_clientsession(self.hass)
            client = OctopusEnergyItaly(session, api_key)
            try:
                await client.async_validate()
            except OctopusAuthError:
                errors["base"] = "invalid_auth"
            except OctopusApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates={CONF_API_KEY: api_key}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OctopusForecastOptionsFlow:
        return OctopusForecastOptionsFlow()


class OctopusForecastOptionsFlow(OptionsFlow):
    """Tune VAT and the fixed/variable cost overrides."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # Empty override fields mean "let the model decide".
            cleaned = {
                k: v for k, v in user_input.items() if v not in (None, "")
            }
            return self.async_create_entry(title="", data=cleaned)

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_VAT_RATE,
                        default=opts.get(CONF_VAT_RATE, DEFAULT_VAT_RATE),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=30, step=0.5, mode=NumberSelectorMode.BOX,
                            unit_of_measurement="%",
                        )
                    ),
                    vol.Optional(
                        CONF_FIXED_OVERRIDE,
                        description={
                            "suggested_value": opts.get(CONF_FIXED_OVERRIDE)
                        },
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=200, step=0.01, mode=NumberSelectorMode.BOX,
                            unit_of_measurement="€",
                        )
                    ),
                    vol.Optional(
                        CONF_RATE_OVERRIDE,
                        description={
                            "suggested_value": opts.get(CONF_RATE_OVERRIDE)
                        },
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0, max=2, step=0.0001, mode=NumberSelectorMode.BOX,
                            unit_of_measurement="€/kWh",
                        )
                    ),
                }
            ),
        )
