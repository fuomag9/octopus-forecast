"""The forecasting logic.

The Italian electricity bill is a sum of many components: the energy commodity
(materia prima), transport & meter management (trasporto e gestione contatore),
system charges (oneri di sistema), the retailer's fixed fee (commercializzazione),
excise duty (accise) and VAT (IVA). Some of these are *fixed* (paid every month
regardless of how much you use), the rest are *variable* (driven by kWh).

Rather than hard-coding the dozens of regulated rates (which ARERA changes every
quarter), we learn the user's *own* all-in cost structure directly from their
billing history with a simple ordinary-least-squares fit:

    monthly_cost ≈ fixed_monthly + unit_rate · kWh

`fixed_monthly` is everything you pay at zero consumption (standing charges,
power quota, fixed network/system fees). `unit_rate` is the blended cost of one
extra kWh including its share of network, system charges, excise and VAT. This
automatically captures every parameter that actually appears on the bill.

The month's cost is then projected from consumption measured so far this month
plus an estimate of the rest of the month.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import mean

from .api import BilledPeriod, DailyReading

# Sanity clamps so a pathological fit can't produce absurd sensor values.
MIN_UNIT_RATE = 0.03
MAX_UNIT_RATE = 1.50
MIN_FIXED = 0.0
MAX_FIXED = 200.0


@dataclass
class CostModel:
    """The learned (or configured) cost structure, all values VAT-inclusive."""

    fixed_monthly: float
    unit_rate: float
    method: str  # "regression" | "tariff_params" | "average_rate" | "manual"
    r2: float | None
    n_points: int
    points: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class MonthProjection:
    """Projection of the current calendar month's total consumption."""

    month: str  # "YYYY-MM"
    days_in_month: int
    days_with_data: int
    mtd_kwh: float
    data_through: date | None
    daily_avg_recent: float | None
    blended_daily_avg: float | None
    projected_kwh: float
    method: str  # "run_rate_blended" | "historical" | "none"


@dataclass
class ForecastResult:
    """Everything the sensors expose."""

    model: CostModel
    projection: MonthProjection
    forecast_cost: float
    forecast_net: float
    forecast_tax: float
    fixed_component: float
    variable_component: float
    mtd_cost: float
    remaining_cost: float
    confidence: str


# --- helpers -----------------------------------------------------------------


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _is_full_calendar_month(start: date, end: date) -> bool:
    """True if start..end covers a whole calendar month (day 1 → last day)."""
    if start.day != 1:
        return False
    last_day = calendar.monthrange(end.year, end.month)[1]
    # end may be recorded as the last day, or the 1st of the next month.
    return (end.month == start.month and end.day == last_day) or (
        end.day == 1 and (end.month - start.month) % 12 == 1
    )


def aggregate_monthly(periods: list[BilledPeriod]) -> dict[str, tuple[float, float]]:
    """Aggregate billed charges into clean full-calendar-month (kWh, cost) totals.

    Multiple charges for the same month (e.g. an adjustment/conguaglio) are
    summed. Partial or irregular periods are dropped so they don't skew the fit.
    """
    months: dict[str, list[BilledPeriod]] = {}
    for p in periods:
        start = _parse_date(p.start)
        end = _parse_date(p.end)
        if start is None or end is None:
            continue
        if not _is_full_calendar_month(start, end):
            continue
        key = f"{start.year:04d}-{start.month:02d}"
        months.setdefault(key, []).append(p)

    result: dict[str, tuple[float, float]] = {}
    for key, group in months.items():
        result[key] = (
            sum(p.kwh for p in group),
            sum(p.gross_cost for p in group),
        )
    return result


def fit_cost_model(
    periods: list[BilledPeriod],
    *,
    standing_charge_year: float | None,
    consumption_charge: float | None,
    vat_rate: float,
    fixed_override: float | None = None,
    rate_override: float | None = None,
) -> CostModel | None:
    """Learn (or configure) the fixed + variable cost structure."""

    if fixed_override is not None and rate_override is not None:
        return CostModel(
            fixed_monthly=fixed_override,
            unit_rate=rate_override,
            method="manual",
            r2=None,
            n_points=0,
        )

    monthly = aggregate_monthly(periods)
    points = [(kwh, cost) for kwh, cost in monthly.values()]
    vat_mult = 1.0 + max(vat_rate, 0.0) / 100.0

    # Preferred: ordinary least squares on the real billing history.
    if len(points) >= 3 and len({round(x) for x, _ in points}) >= 2:
        model = _ols(points)
        if model is not None:
            if fixed_override is not None:
                model.fixed_monthly = fixed_override
            if rate_override is not None:
                model.unit_rate = rate_override
            return model

    # Two data points: still fit a line, but flag lower confidence.
    if len(points) == 2 and points[0][0] != points[1][0]:
        model = _ols(points)
        if model is not None:
            model.method = "regression"
            return model

    # Not enough history: fall back to an all-in *average rate* if we have any
    # billed months, otherwise to the published tariff parameters.
    if points:
        avg_rate = sum(c for _, c in points) / max(sum(k for k, _ in points), 1e-9)
        return CostModel(
            fixed_monthly=_clamp(
                (standing_charge_year or 0) / 12 * vat_mult, MIN_FIXED, MAX_FIXED
            ),
            unit_rate=_clamp(avg_rate, MIN_UNIT_RATE, MAX_UNIT_RATE),
            method="average_rate",
            r2=None,
            n_points=len(points),
            points=points,
        )

    if consumption_charge is not None:
        # Energy commodity + a nominal uplift for the regulated per-kWh
        # components we can't see yet (network + system + excise), then VAT.
        # 1.6x is a conservative Italian domestic blended uplift; it is only
        # used until real bills arrive and the regression takes over.
        rate = consumption_charge * 1.6 * vat_mult
        return CostModel(
            fixed_monthly=_clamp(
                (standing_charge_year or 72) / 12 * vat_mult, MIN_FIXED, MAX_FIXED
            ),
            unit_rate=_clamp(rate, MIN_UNIT_RATE, MAX_UNIT_RATE),
            method="tariff_params",
            r2=None,
            n_points=0,
        )

    return None


def _ols(points: list[tuple[float, float]]) -> CostModel | None:
    n = len(points)
    if n < 2:
        return None
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        return None
    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n

    # Coefficient of determination.
    y_mean = sy / n
    ss_tot = sum((y - y_mean) ** 2 for _, y in points)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in points)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-9 else 1.0

    a = _clamp(a, MIN_FIXED, MAX_FIXED)
    b = _clamp(b, MIN_UNIT_RATE, MAX_UNIT_RATE)
    return CostModel(
        fixed_monthly=round(a, 2),
        unit_rate=round(b, 5),
        method="regression",
        r2=round(r2, 4),
        n_points=n,
        points=points,
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def project_month(
    daily_readings: list[DailyReading],
    monthly_history: dict[str, tuple[float, float]],
    now: datetime,
) -> MonthProjection:
    """Project the current calendar month's total kWh."""

    year, month = now.year, now.month
    key = f"{year:04d}-{month:02d}"
    days_in_month = calendar.monthrange(year, month)[1]

    this_month = [
        r for r in daily_readings
        if r.start.year == year and r.start.month == month
    ]
    mtd_kwh = round(sum(r.kwh for r in this_month), 3)
    days_with_data = len(this_month)
    data_through = max((r.end.date() for r in this_month), default=None)

    # Historical whole-month kWh totals, preferring the same calendar month
    # (seasonality) but falling back to the overall average.
    all_month_kwh = [kwh for kwh, _ in monthly_history.values()]
    same_month_kwh = [
        kwh for k, (kwh, _) in monthly_history.items() if k.endswith(f"-{month:02d}")
    ]
    hist_month_total = (
        mean(same_month_kwh) if same_month_kwh
        else mean(all_month_kwh) if all_month_kwh
        else None
    )

    if days_with_data == 0:
        # No live data yet this month: fall back purely to history.
        projected = round(hist_month_total, 2) if hist_month_total else 0.0
        return MonthProjection(
            month=key,
            days_in_month=days_in_month,
            days_with_data=0,
            mtd_kwh=0.0,
            data_through=None,
            daily_avg_recent=None,
            blended_daily_avg=None,
            projected_kwh=projected,
            method="historical" if hist_month_total else "none",
        )

    daily_avg_recent = mtd_kwh / days_with_data
    hist_daily = (
        hist_month_total / days_in_month if hist_month_total else daily_avg_recent
    )

    # Weight the real run-rate by how much of the month we've actually measured.
    # Early in the month the few noisy days matter less than the seasonal norm;
    # late in the month the measured run-rate dominates.
    w = days_with_data / days_in_month
    blended_daily = w * daily_avg_recent + (1 - w) * hist_daily
    remaining_days = max(days_in_month - days_with_data, 0)
    projected = round(mtd_kwh + blended_daily * remaining_days, 2)

    return MonthProjection(
        month=key,
        days_in_month=days_in_month,
        days_with_data=days_with_data,
        mtd_kwh=mtd_kwh,
        data_through=data_through,
        daily_avg_recent=round(daily_avg_recent, 3),
        blended_daily_avg=round(blended_daily, 3),
        projected_kwh=projected,
        method="run_rate_blended",
    )


def build_forecast(
    model: CostModel,
    projection: MonthProjection,
    vat_rate: float,
) -> ForecastResult:
    """Combine the cost model and the consumption projection into a bill forecast."""

    fixed = model.fixed_monthly
    variable = round(model.unit_rate * projection.projected_kwh, 2)
    forecast_cost = round(fixed + variable, 2)

    vat_mult = 1.0 + max(vat_rate, 0.0) / 100.0
    forecast_net = round(forecast_cost / vat_mult, 2)
    forecast_tax = round(forecast_cost - forecast_net, 2)

    # Cost accrued so far: the fixed part accrues pro-rata across the month, the
    # variable part follows the kWh actually measured.
    frac = (
        projection.days_with_data / projection.days_in_month
        if projection.days_in_month
        else 0.0
    )
    mtd_cost = round(fixed * frac + model.unit_rate * projection.mtd_kwh, 2)
    remaining_cost = round(forecast_cost - mtd_cost, 2)

    confidence = _confidence(model, projection)

    return ForecastResult(
        model=model,
        projection=projection,
        forecast_cost=forecast_cost,
        forecast_net=forecast_net,
        forecast_tax=forecast_tax,
        fixed_component=round(fixed, 2),
        variable_component=variable,
        mtd_cost=mtd_cost,
        remaining_cost=remaining_cost,
        confidence=confidence,
    )


def _confidence(model: CostModel, projection: MonthProjection) -> str:
    score = 0
    if model.method == "regression" and model.n_points >= 4:
        score += 2
    elif model.method in ("regression", "manual"):
        score += 1
    if model.r2 is not None and model.r2 >= 0.9:
        score += 2
    elif model.r2 is not None and model.r2 >= 0.7:
        score += 1
    if projection.method == "run_rate_blended":
        if projection.days_with_data >= projection.days_in_month * 0.5:
            score += 2
        else:
            score += 1
    if score >= 5:
        return "high"
    if score >= 3:
        return "medium"
    return "low"
