"""Thin async client for the Octopus Energy Italy (Kraken) GraphQL API.

Only the handful of queries the forecast needs are implemented. Everything the
integration reads is read-only; no mutations other than authentication are used.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import aiohttp

from .const import API_BASE, ELECTRICITY_MARKET, TOKEN_REFRESH_MARGIN

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class OctopusApiError(Exception):
    """A non-recoverable error talking to Kraken."""


class OctopusAuthError(OctopusApiError):
    """Authentication failed (bad/expired API key)."""


# --- GraphQL documents -------------------------------------------------------

_AUTH_MUTATION = """
mutation ($apiKey: String!) {
  obtainKrakenToken(input: {APIKey: $apiKey}) {
    token
    refreshToken
    refreshExpiresIn
  }
}
"""

_ACCOUNTS_QUERY = """
query {
  viewer {
    accounts {
      number
      brand
    }
  }
}
"""

_OVERVIEW_QUERY = """
query ($acc: String!) {
  account(accountNumber: $acc) {
    number
    balance
    properties { id address }
    marketSupplyAgreements(first: 30) {
      edges {
        node {
          id
          isActive
          validFrom
          validTo
          supplyPoint { id marketName externalIdentifier }
          product { code displayName fullName term params }
        }
      }
    }
  }
}
"""

# Historical billed consumption paired with its charge amount. `amount` is the
# gross value in cents (VAT included). Charges that carry a `consumption` block
# are the per-period electricity/gas charges.
_LEDGER_QUERY = """
query ($acc: String!, $first: Int!) {
  account(accountNumber: $acc) {
    ledgers {
      ledgerType
      number
      transactions(first: $first) {
        edges {
          node {
            __typename
            postedDate
            title
            amount
            isReversed
            ... on Charge {
              consumption { quantity startDate endDate }
            }
          }
        }
      }
    }
  }
}
"""

# Daily import (consumption) measurements for one supply point. NB: the
# marketSupplyPointId filter must be the POD external identifier, and
# utilityFilters is a list.
_MEASUREMENTS_QUERY = """
query (
  $pid: ID!
  $first: Int!
  $start: DateTime!
  $end: DateTime!
  $uf: [UtilityFiltersInput]
) {
  property(id: $pid) {
    measurements(first: $first, startAt: $start, endAt: $end, utilityFilters: $uf) {
      edges {
        node {
          value
          unit
          ... on IntervalMeasurementType { startAt endAt }
        }
      }
    }
  }
}
"""

_LAST_STATEMENT_QUERY = """
query ($acc: String!) {
  account(accountNumber: $acc) {
    bills(first: 1) {
      edges {
        node {
          id
          fromDate
          toDate
          issuedDate
          ... on PeriodBasedDocumentType {
            totalCharges { grossTotal netTotal taxTotal }
          }
        }
      }
    }
  }
}
"""


@dataclass
class BilledPeriod:
    """A billed consumption charge: kWh over a period with its gross cost."""

    start: str
    end: str
    kwh: float
    gross_cost: float  # euros, VAT included


@dataclass
class DailyReading:
    """A single day's metered consumption."""

    start: datetime
    end: datetime
    kwh: float


@dataclass
class AccountOverview:
    """The bits of the account the forecast needs."""

    account_number: str
    balance: float  # euros; positive = in credit for the customer
    property_id: str | None
    address: str | None
    pod: str | None
    market_name: str | None
    product_code: str | None
    product_name: str | None
    tariff_valid_from: str | None
    tariff_valid_to: str | None
    standing_charge_year: float | None
    consumption_charge: float | None
    raw_agreements: list[dict] = field(default_factory=list)


def _it_decimal(value: str | None) -> float | None:
    """Parse an Italian-formatted decimal (comma separator) into a float."""
    if value is None:
        return None
    text = str(value)
    # Italian format uses "." for thousands and "," for the decimal separator.
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class OctopusEnergyItaly:
    """Minimal Kraken client with automatic token handling."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._api_key = api_key
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    # -- auth -----------------------------------------------------------------

    async def _authenticate(self) -> None:
        data = await self._raw_request(
            _AUTH_MUTATION, {"apiKey": self._api_key}, authed=False
        )
        payload = (data.get("obtainKrakenToken") or {})
        token = payload.get("token")
        if not token:
            raise OctopusAuthError("No token returned from obtainKrakenToken")
        self._token = token
        expires_in = payload.get("refreshExpiresIn") or 3600
        self._token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
        _LOGGER.debug("Obtained Kraken token, valid ~%ss", expires_in)

    async def _ensure_token(self) -> None:
        if self._token is None or self._token_expiry is None:
            await self._authenticate()
            return
        if datetime.now(UTC) >= self._token_expiry - TOKEN_REFRESH_MARGIN:
            await self._authenticate()

    # -- transport ------------------------------------------------------------

    async def _raw_request(
        self, query: str, variables: dict, *, authed: bool = True
    ) -> dict:
        headers = {"Content-Type": "application/json"}
        if authed:
            await self._ensure_token()
            headers["Authorization"] = self._token or ""

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                resp = await self._session.post(
                    API_BASE,
                    json={"query": query, "variables": variables},
                    headers=headers,
                )
                body = await resp.json()
        except aiohttp.ClientError as err:
            raise OctopusApiError(f"Network error contacting Kraken: {err}") from err
        except (json.JSONDecodeError, ValueError) as err:
            raise OctopusApiError(f"Invalid JSON from Kraken: {err}") from err

        errors = body.get("errors")
        if errors:
            messages = "; ".join(e.get("message", "?") for e in errors)
            codes = {
                (e.get("extensions") or {}).get("errorCode") for e in errors
            }
            if any(c in {"KT-CT-1139", "KT-CT-1135"} for c in codes) or (
                "authentic" in messages.lower() or "signature" in messages.lower()
            ):
                raise OctopusAuthError(messages)
            # Some fields (e.g. a single supply point) can be individually
            # unauthorized while the rest of the payload is fine.
            data = body.get("data")
            if data is None:
                raise OctopusApiError(messages)
            _LOGGER.debug("Partial GraphQL errors (continuing): %s", messages)
            return data

        data = body.get("data")
        if data is None:
            raise OctopusApiError("Empty GraphQL response")
        return data

    async def async_query(self, query: str, variables: dict) -> dict:
        """Run an authenticated query, retrying once after a token refresh."""
        try:
            return await self._raw_request(query, variables)
        except OctopusAuthError:
            self._token = None
            return await self._raw_request(query, variables)

    # -- high level -----------------------------------------------------------

    async def async_validate(self) -> list[dict]:
        """Confirm the API key works and return the visible accounts."""
        data = await self.async_query(_ACCOUNTS_QUERY, {})
        accounts = ((data.get("viewer") or {}).get("accounts")) or []
        if not accounts:
            raise OctopusApiError("API key is valid but exposes no accounts")
        return accounts

    async def async_get_overview(self, account_number: str) -> AccountOverview:
        data = await self.async_query(_OVERVIEW_QUERY, {"acc": account_number})
        account = data.get("account") or {}

        properties = account.get("properties") or []
        prop = properties[0] if properties else {}

        agreements = [
            e["node"]
            for e in ((account.get("marketSupplyAgreements") or {}).get("edges") or [])
            if e.get("node")
        ]
        elec = [
            a
            for a in agreements
            if (a.get("supplyPoint") or {}).get("marketName") == ELECTRICITY_MARKET
        ]
        active = next((a for a in elec if a.get("isActive")), None)
        # Fall back to the most recent electricity agreement if none is flagged
        # active (e.g. between agreements).
        if active is None and elec:
            active = max(elec, key=lambda a: a.get("validFrom") or "")

        product = (active or {}).get("product") or {}
        params = {}
        raw_params = product.get("params")
        if raw_params:
            try:
                # params is a JSON string that may itself be double-encoded.
                params = json.loads(raw_params)
                if isinstance(params, str):
                    params = json.loads(params)
            except (json.JSONDecodeError, TypeError):
                params = {}

        sp = (active or {}).get("supplyPoint") or {}
        balance_cents = account.get("balance")

        return AccountOverview(
            account_number=account.get("number") or account_number,
            balance=round((balance_cents or 0) / 100.0, 2),
            property_id=prop.get("id"),
            address=prop.get("address"),
            pod=sp.get("externalIdentifier"),
            market_name=sp.get("marketName"),
            product_code=product.get("code"),
            product_name=product.get("displayName") or product.get("fullName"),
            tariff_valid_from=(active or {}).get("validFrom"),
            tariff_valid_to=(active or {}).get("validTo"),
            standing_charge_year=_it_decimal(params.get("annual_standing_charge")),
            consumption_charge=_it_decimal(params.get("consumption_charge")),
            raw_agreements=agreements,
        )

    async def async_get_billed_history(
        self, account_number: str, market: str = ELECTRICITY_MARKET, first: int = 60
    ) -> list[BilledPeriod]:
        data = await self.async_query(
            _LEDGER_QUERY, {"acc": account_number, "first": first}
        )
        ledgers = (data.get("account") or {}).get("ledgers") or []
        wanted = "ELECTRICITY" if "ELECTRICITY" in market else market
        periods: list[BilledPeriod] = []
        for ledger in ledgers:
            ltype = ledger.get("ledgerType") or ""
            if wanted not in ltype:
                continue
            for edge in (ledger.get("transactions") or {}).get("edges") or []:
                node = edge.get("node") or {}
                if node.get("__typename") != "Charge" or node.get("isReversed"):
                    continue
                cons = node.get("consumption")
                if not cons or cons.get("quantity") in (None, "0", "0.0000"):
                    continue
                try:
                    kwh = float(cons["quantity"])
                except (TypeError, ValueError):
                    continue
                if kwh <= 0:
                    continue
                periods.append(
                    BilledPeriod(
                        start=cons.get("startDate"),
                        end=cons.get("endDate"),
                        kwh=kwh,
                        gross_cost=round((node.get("amount") or 0) / 100.0, 2),
                    )
                )
        periods.sort(key=lambda p: p.start or "")
        return periods

    async def async_get_daily_consumption(
        self,
        property_id: str,
        pod: str,
        start: datetime,
        end: datetime,
    ) -> list[DailyReading]:
        variables = {
            "pid": property_id,
            "first": 400,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "uf": [
                {
                    "electricityFilters": {
                        "readingFrequencyType": "DAY_INTERVAL",
                        "readingDirection": "CONSUMPTION",
                        "marketSupplyPointId": pod,
                    }
                }
            ],
        }
        data = await self.async_query(_MEASUREMENTS_QUERY, variables)
        prop = data.get("property") or {}
        edges = (prop.get("measurements") or {}).get("edges") or []
        readings: list[DailyReading] = []
        for edge in edges:
            node = edge.get("node") or {}
            try:
                kwh = float(node.get("value"))
            except (TypeError, ValueError):
                continue
            s = node.get("startAt")
            e = node.get("endAt")
            if not s or not e:
                continue
            readings.append(
                DailyReading(
                    start=datetime.fromisoformat(s),
                    end=datetime.fromisoformat(e),
                    kwh=kwh,
                )
            )
        readings.sort(key=lambda r: r.start)
        return readings

    async def async_get_last_statement(self, account_number: str) -> dict | None:
        data = await self.async_query(_LAST_STATEMENT_QUERY, {"acc": account_number})
        edges = (
            ((data.get("account") or {}).get("bills") or {}).get("edges") or []
        )
        if not edges:
            return None
        node = edges[0].get("node") or {}
        totals = node.get("totalCharges") or {}
        return {
            "from": node.get("fromDate"),
            "to": node.get("toDate"),
            "issued": node.get("issuedDate"),
            "gross": round((totals.get("grossTotal") or 0) / 100.0, 2),
            "net": round((totals.get("netTotal") or 0) / 100.0, 2),
            "tax": round((totals.get("taxTotal") or 0) / 100.0, 2),
        }
