from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Protocol, Sequence

import numpy as np

from app.engine.market.yield_curve import YieldCurve
from app.engine.math.daycount import year_fraction
from app.engine.math.rate_conversion import Compounding, forward_rate_from_dfs
from app.engine.products.models.schedule_models import (
    HistoricalFixing,
    SwapScheduleRow,
    TradeHeader,
    TradeIRS,
)


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    as_of: date


@dataclass(frozen=True)
class SwapPricingInput:
    discount_curve_id: str
    forward_curve_id: Optional[str]
    discount_daycount: str
    forward_daycount: str
    float_compounding: Compounding | str = Compounding.SIMPLE
    include_settled: bool = False
    as_of: Optional[date] = None


@dataclass(frozen=True)
class SwapPricingData:
    run_id: str
    trade: TradeHeader
    trade_irs: TradeIRS
    schedule_rows: tuple[SwapScheduleRow, ...]
    discount_curve: YieldCurve
    forward_curve: YieldCurve
    pricing: SwapPricingInput
    as_of: date
    fixings: tuple[HistoricalFixing, ...]


@dataclass(frozen=True)
class SwapPVResult:
    pv: float
    pv_fixed: float
    pv_float: float


class SwapDataProvider(Protocol):
    def get_trade(self, trade_id: str) -> TradeHeader: ...

    def get_trade_irs(self, trade_id: str) -> TradeIRS: ...

    def get_swap_schedule(self, trade_id: str) -> Sequence[SwapScheduleRow]: ...

    def get_historical_fixings(
        self, index_id: str, start_date: date, end_date: date
    ) -> Sequence[HistoricalFixing]: ...

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot: ...

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve: ...


def _year_fractions_from_dates(
    base: date, dates: Iterable[date], daycount: str
) -> np.ndarray:
    return np.array([year_fraction(base, d, daycount) for d in dates], dtype=float)


def _resolve_accrual_factor(row: SwapScheduleRow) -> float:
    if row.accrual_factor is not None:
        return float(row.accrual_factor)
    if row.start_date is None or row.end_date is None or row.daycount is None:
        raise ValueError("swap_schedule missing start/end/daycount for accrual.")
    return float(year_fraction(row.start_date, row.end_date, row.daycount))


def _resolve_notional(row: SwapScheduleRow, trade: TradeHeader) -> float:
    return float(row.notional) if row.notional is not None else float(trade.notional)


def _apply_spread_gearing(base_rate: float, row: SwapScheduleRow) -> float:
    spread = float(row.spread) if row.spread is not None else 0.0
    gearing = float(row.gearing) if row.gearing is not None else 1.0
    return base_rate * gearing + spread


def _resolve_base_rate_from_fixing(
    row: SwapScheduleRow,
    fixing_map: dict[tuple[str, date], float],
    *,
    as_of: date,
) -> Optional[float]:
    if row.fixing_date is None or row.index_id is None:
        return None
    if row.fixing_date > as_of:
        return None
    return fixing_map.get((row.index_id, row.fixing_date))


def _resolve_base_rate_from_curve(
    row: SwapScheduleRow,
    *,
    forward_curve: YieldCurve,
    as_of: date,
    forward_daycount: str,
    compounding: Compounding | str,
) -> float:
    if row.start_date is None or row.end_date is None:
        raise ValueError("swap_schedule missing start/end date for float rate.")
    t_start = year_fraction(as_of, row.start_date, forward_daycount)
    t_end = year_fraction(as_of, row.end_date, forward_daycount)
    df_start = float(np.asarray(forward_curve.df(t_start)))
    df_end = float(np.asarray(forward_curve.df(t_end)))
    accrual = _resolve_accrual_factor(row)
    return float(forward_rate_from_dfs(df_start, df_end, accrual, compounding))


def _resolve_rate(
    row: SwapScheduleRow,
    *,
    forward_curve: YieldCurve,
    as_of: date,
    forward_daycount: str,
    compounding: Compounding | str,
    fixing_map: dict[tuple[str, date], float],
) -> float:
    if row.rate is not None:
        return float(row.rate)
    if row.rate_calc_type == "FIXED":
        raise ValueError("swap_schedule missing fixed rate for FIXED leg.")
    base = _resolve_base_rate_from_fixing(row, fixing_map, as_of=as_of)
    if base is None:
        base = _resolve_base_rate_from_curve(
            row,
            forward_curve=forward_curve,
            as_of=as_of,
            forward_daycount=forward_daycount,
            compounding=compounding,
        )
    return _apply_spread_gearing(base, row)


def _resolve_cashflow_amount(
    row: SwapScheduleRow,
    *,
    trade: TradeHeader,
    forward_curve: YieldCurve,
    as_of: date,
    forward_daycount: str,
    compounding: Compounding | str,
    fixing_map: dict[tuple[str, date], float],
) -> float:
    if row.fixed_amount is not None:
        return float(row.fixed_amount)
    if row.amount is not None:
        return float(row.amount)
    if row.payment_type == "FEE":
        raise ValueError("swap_schedule missing amount for FEE cashflow.")
    if row.payment_type == "PRINCIPAL":
        notional = _resolve_notional(row, trade)
        if row.principal_factor is None:
            raise ValueError("swap_schedule missing principal_factor for principal cashflow.")
        return notional * float(row.principal_factor)
    accrual = _resolve_accrual_factor(row)
    rate = _resolve_rate(
        row,
        forward_curve=forward_curve,
        as_of=as_of,
        forward_daycount=forward_daycount,
        compounding=compounding,
        fixing_map=fixing_map,
    )
    notional = _resolve_notional(row, trade)
    return notional * rate * accrual


def price_swap_from_data(data: SwapPricingData) -> SwapPVResult:
    fixing_map = {(f.index_id, f.fixing_date): f.rate for f in data.fixings}
    rows = data.schedule_rows
    if not rows:
        raise ValueError("swap_schedule rows are required for swap PV.")

    as_of = data.as_of
    discount_daycount = data.pricing.discount_daycount
    forward_daycount = data.pricing.forward_daycount
    compounding = data.pricing.float_compounding

    pv_fixed = 0.0
    pv_float = 0.0
    pv_other = 0.0

    for row in rows:
        if not data.pricing.include_settled and row.is_settled == 1:
            continue
        if row.payment_date <= as_of:
            continue
        amount = _resolve_cashflow_amount(
            row,
            trade=data.trade,
            forward_curve=data.forward_curve,
            as_of=as_of,
            forward_daycount=forward_daycount,
            compounding=compounding,
            fixing_map=fixing_map,
        )
        t_pay = year_fraction(as_of, row.payment_date, discount_daycount)
        df = float(np.asarray(data.discount_curve.df(t_pay)))
        sign = 1.0 if row.pay_rec.upper() == "REC" else -1.0
        pv = sign * amount * df
        if row.leg_id == "FIXED":
            pv_fixed += pv
        elif row.leg_id == "FLOAT":
            pv_float += pv
        else:
            pv_other += pv

    total = pv_fixed + pv_float + pv_other
    return SwapPVResult(pv=total, pv_fixed=pv_fixed, pv_float=pv_float)


def load_swap_pricing_data(
    provider: SwapDataProvider,
    *,
    run_id: str,
    trade_id: str,
    snapshot_id: str,
    pricing: SwapPricingInput,
) -> SwapPricingData:
    trade = provider.get_trade(trade_id)
    trade_irs = provider.get_trade_irs(trade_id)
    schedule_rows = tuple(provider.get_swap_schedule(trade_id))
    if not schedule_rows:
        raise ValueError("swap_schedule rows are missing for pricing.")

    snapshot = provider.get_market_snapshot(snapshot_id)
    as_of = pricing.as_of or snapshot.as_of

    discount_curve = provider.get_yield_curve(pricing.discount_curve_id, snapshot_id)
    fwd_curve_id = pricing.forward_curve_id or pricing.discount_curve_id
    forward_curve = provider.get_yield_curve(fwd_curve_id, snapshot_id)

    fixing_dates = [row.fixing_date for row in schedule_rows if row.fixing_date is not None]
    fixings: Sequence[HistoricalFixing] = ()
    if fixing_dates:
        start_date = min(fixing_dates)
        end_date = max(fixing_dates)
        fixings = provider.get_historical_fixings(trade_irs.float_index_id, start_date, end_date)

    return SwapPricingData(
        run_id=run_id,
        trade=trade,
        trade_irs=trade_irs,
        schedule_rows=schedule_rows,
        discount_curve=discount_curve,
        forward_curve=forward_curve,
        pricing=pricing,
        as_of=as_of,
        fixings=tuple(fixings),
    )
