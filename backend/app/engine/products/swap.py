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


def price_swap_from_data(data: SwapPricingData) -> SwapPVResult:
    fixing_map = {(f.index_id, f.fixing_date): f.rate for f in data.fixings}
    rows = data.schedule_rows
    if not rows:
        raise ValueError("swap_schedule rows are required for swap PV.")

    as_of = data.as_of
    discount_daycount = data.pricing.discount_daycount
    forward_daycount = data.pricing.forward_daycount
    compounding = data.pricing.float_compounding

    active_rows = [
        row
        for row in rows
        if (data.pricing.include_settled or row.is_settled != 1) and row.payment_date > as_of
    ]
    if not active_rows:
        return SwapPVResult(pv=0.0, pv_fixed=0.0, pv_float=0.0)

    pay_dates = [row.payment_date for row in active_rows]
    leg_arr = np.asarray([row.leg_id for row in active_rows], dtype="U8")
    pay_rec_arr = np.asarray([row.pay_rec.upper() for row in active_rows], dtype="U4")
    payment_type_arr = np.asarray([row.payment_type for row in active_rows], dtype="U10")
    rate_calc_type_arr = np.asarray(
        [row.rate_calc_type if row.rate_calc_type is not None else "" for row in active_rows],
        dtype="U20",
    )
    sign_arr = np.where(pay_rec_arr == "REC", 1.0, -1.0)

    notional_arr = np.asarray(
        [np.nan if row.notional is None else float(row.notional) for row in active_rows], dtype=float
    )
    needs_notional_mask = payment_type_arr != "FEE"
    if np.any(np.isnan(notional_arr[needs_notional_mask])):
        raise ValueError(
            "swap_schedule.notional is required for INTEREST/PRINCIPAL rows to support amortization."
        )

    fixed_amount_arr = np.asarray(
        [np.nan if row.fixed_amount is None else float(row.fixed_amount) for row in active_rows],
        dtype=float,
    )
    amount_arr = np.asarray(
        [np.nan if row.amount is None else float(row.amount) for row in active_rows],
        dtype=float,
    )
    amount_arr = np.where(np.isnan(fixed_amount_arr), amount_arr, fixed_amount_arr)

    needs_calc = np.isnan(amount_arr)
    fee_mask = needs_calc & (payment_type_arr == "FEE")
    if np.any(fee_mask):
        raise ValueError("swap_schedule missing amount for FEE cashflow.")

    principal_mask = needs_calc & (payment_type_arr == "PRINCIPAL")
    if np.any(principal_mask):
        principal_factor_arr = np.asarray(
            [
                np.nan if row.principal_factor is None else float(row.principal_factor)
                for row in active_rows
            ],
            dtype=float,
        )
        if np.any(np.isnan(principal_factor_arr[principal_mask])):
            raise ValueError("swap_schedule missing principal_factor for principal cashflow.")
        amount_arr[principal_mask] = notional_arr[principal_mask] * principal_factor_arr[principal_mask]

    interest_mask = needs_calc & (payment_type_arr == "INTEREST")
    if np.any(interest_mask):
        accrual_arr = np.asarray(
            [
                float(row.accrual_factor)
                if row.accrual_factor is not None
                else (
                    float(year_fraction(row.start_date, row.end_date, row.daycount))
                    if (row.start_date is not None and row.end_date is not None and row.daycount is not None)
                    else np.nan
                )
                for row in active_rows
            ],
            dtype=float,
        )
        if np.any(np.isnan(accrual_arr[interest_mask])):
            raise ValueError("swap_schedule missing start/end/daycount for accrual.")

        rate_arr = np.asarray(
            [np.nan if row.rate is None else float(row.rate) for row in active_rows], dtype=float
        )
        unresolved_rate_mask = interest_mask & np.isnan(rate_arr)
        fixed_unresolved = unresolved_rate_mask & (rate_calc_type_arr == "FIXED")
        if np.any(fixed_unresolved):
            raise ValueError("swap_schedule missing fixed rate for FIXED leg.")

        float_mask = unresolved_rate_mask & (rate_calc_type_arr != "FIXED")
        if np.any(float_mask):
            row_idx = np.where(float_mask)[0]
            fix_rate_arr = np.full(row_idx.size, np.nan, dtype=float)
            for i, idx in enumerate(row_idx):
                row = active_rows[int(idx)]
                if row.fixing_date is None or row.index_id is None or row.fixing_date > as_of:
                    continue
                key = (row.index_id, row.fixing_date)
                if key in fixing_map:
                    fix_rate_arr[i] = float(fixing_map[key])

            use_curve = np.isnan(fix_rate_arr)
            if np.any(use_curve):
                curve_idx = row_idx[use_curve]
                obs_start_dates: list[date] = []
                obs_end_dates: list[date] = []
                for idx in curve_idx:
                    row = active_rows[int(idx)]
                    obs_start = row.obs_start_date if row.obs_start_date is not None else row.start_date
                    obs_end = row.obs_end_date if row.obs_end_date is not None else row.end_date
                    if obs_start is None or obs_end is None:
                        raise ValueError("swap_schedule missing observation/accrual dates for float rate.")
                    obs_start_dates.append(obs_start)
                    obs_end_dates.append(obs_end)
                t_start = _year_fractions_from_dates(as_of, obs_start_dates, forward_daycount)
                t_end = _year_fractions_from_dates(as_of, obs_end_dates, forward_daycount)
                df_start = np.asarray(data.forward_curve.df(t_start), dtype=float)
                df_end = np.asarray(data.forward_curve.df(t_end), dtype=float)
                fwd_accrual = np.array(
                    [
                        year_fraction(obs_start_dates[i], obs_end_dates[i], forward_daycount)
                        for i in range(len(obs_start_dates))
                    ],
                    dtype=float,
                )
                fwd_rates = np.asarray(
                    forward_rate_from_dfs(df_start, df_end, fwd_accrual, compounding), dtype=float
                )
                fix_rate_arr[use_curve] = fwd_rates

            spread_arr = np.asarray(
                [0.0 if active_rows[int(idx)].spread is None else float(active_rows[int(idx)].spread) for idx in row_idx],
                dtype=float,
            )
            gearing_arr = np.asarray(
                [1.0 if active_rows[int(idx)].gearing is None else float(active_rows[int(idx)].gearing) for idx in row_idx],
                dtype=float,
            )
            rate_arr[row_idx] = fix_rate_arr * gearing_arr + spread_arr

        amount_arr[interest_mask] = (
            notional_arr[interest_mask] * rate_arr[interest_mask] * accrual_arr[interest_mask]
        )

    if np.any(np.isnan(amount_arr)):
        raise ValueError("swap_schedule contains unresolved cashflow amount.")

    t_pay = _year_fractions_from_dates(as_of, pay_dates, discount_daycount)
    df_arr = np.asarray(data.discount_curve.df(t_pay), dtype=float)
    pv_components = amount_arr * sign_arr * df_arr
    pv_fixed = float(np.sum(pv_components[leg_arr == "FIXED"]))
    pv_float = float(np.sum(pv_components[leg_arr == "FLOAT"]))
    total = float(np.sum(pv_components))
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
