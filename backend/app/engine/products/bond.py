from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Literal, Protocol, Sequence

import numpy as np
from scipy.optimize import brentq  # type: ignore[import-untyped]

from app.engine.market.yield_curve import YieldCurve
from app.engine.math.daycount import year_fraction
from app.engine.math.rate_conversion import Compounding, discount_factor, forward_rate_from_dfs
from app.engine.products.models.schedule_models import (
    BondDef,
    BondScheduleRow,
    HistoricalFixing,
    MarketQuoteBond,
    TradeBond,
    TradeHeader,
)


@dataclass(frozen=True)
class BondPricingInput:
    settle_date: date
    discount_curve_id: str
    curve_daycount: str
    forward_curve_id: Optional[str] = None
    forward_daycount: Optional[str] = None
    z_spread_daycount: str
    z_spread_compounding: Compounding | str = Compounding.CONTINUOUS
    z_spread_freq: int = 1
    float_compounding: Compounding | str = Compounding.SIMPLE
    input_side: str = "MID"
    price_ccy: Optional[str] = None


@dataclass(frozen=True)
class BondPricingState:
    run_id: str
    security_id: str
    discount_curve_id: str
    settle_date: date
    price_kind: Literal["DIRTY"]
    input_side: str
    price_value: float
    price_ccy: Optional[str]
    accrued_interest: float
    obs_clean_price: Optional[float]
    obs_dirty_price: Optional[float]
    z_spread: float
    z_spread_daycount: str
    z_spread_compounding: Compounding | str
    z_spread_compounding_freq: int


@dataclass(frozen=True)
class BondPVResult:
    pv_dirty: float
    pv_clean: float
    accrued_interest: float
    z_spread: float


@dataclass(frozen=True)
class BondPricingStateCache:
    _store: dict[tuple[str, str, str, date], BondPricingState]

    def get(
        self, run_id: str, security_id: str, curve_id: str, settle_date: date
    ) -> Optional[BondPricingState]:
        return self._store.get((run_id, security_id, curve_id, settle_date))

    def put(self, state: BondPricingState) -> None:
        key = (state.run_id, state.security_id, state.discount_curve_id, state.settle_date)
        self._store[key] = state


@dataclass(frozen=True)
class BondPricingData:
    run_id: str
    trade: TradeHeader
    bond_def: BondDef
    trade_bond: TradeBond
    quote: MarketQuoteBond
    schedule_rows: tuple[BondScheduleRow, ...]
    discount_curve: YieldCurve
    forward_curve: YieldCurve
    fixings: tuple[HistoricalFixing, ...]
    pricing: BondPricingInput


class BondDataProvider(Protocol):
    def get_trade(self, trade_id: str) -> TradeHeader: ...

    def get_trade_bond(self, trade_id: str) -> TradeBond: ...

    def get_bond_def(self, security_id: str) -> BondDef: ...

    def get_market_quote_bond(self, security_id: str, snapshot_id: str) -> MarketQuoteBond: ...

    def get_bond_schedule(self, trade_id: str, base_security_id: str) -> Sequence[BondScheduleRow]: ...

    def get_historical_fixings(
        self, index_id: str, start_date: date, end_date: date
    ) -> Sequence[HistoricalFixing]: ...

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve: ...


def _year_fractions_from_dates(
    base: date, dates: Iterable[date], daycount: str
) -> np.ndarray:
    return np.array([year_fraction(base, d, daycount) for d in dates], dtype=float)


def _apply_z_spread(
    df_curve: np.ndarray,
    t: np.ndarray,
    z: float,
    *,
    compounding: Compounding | str,
    freq: int,
) -> np.ndarray:
    z_df = discount_factor(z, t, compounding, freq=freq)
    return df_curve * z_df


def _pv_from_cashflows(
    curve: YieldCurve,
    cashflow_dates: Iterable[date],
    cashflow_amounts: np.ndarray,
    *,
    as_of: date,
    curve_daycount: str,
    z_spread: float,
    z_spread_daycount: str,
    z_spread_compounding: Compounding | str,
    z_spread_freq: int,
) -> float:
    t_curve = _year_fractions_from_dates(as_of, cashflow_dates, curve_daycount)
    t_z = _year_fractions_from_dates(as_of, cashflow_dates, z_spread_daycount)
    df_curve = np.asarray(curve.df(t_curve), dtype=float)
    df_adj = _apply_z_spread(
        df_curve, t_z, z_spread, compounding=z_spread_compounding, freq=z_spread_freq
    )
    return float(np.sum(cashflow_amounts * df_adj))


def _bracket_root(func, x0: float, step: float, max_steps: int) -> tuple[float, float]:
    a = x0 - step
    b = x0 + step
    fa = func(a)
    fb = func(b)
    for _ in range(max_steps):
        if fa == 0.0:
            return a, a
        if fb == 0.0:
            return b, b
        if fa * fb < 0:
            return a, b
        step *= 2.0
        a = x0 - step
        b = x0 + step
        fa = func(a)
        fb = func(b)
    raise ValueError("Failed to bracket root for z-spread.")


def calibrate_z_spread(
    target_dirty_price: float,
    *,
    curve: YieldCurve,
    cashflow_dates: Iterable[date],
    cashflow_amounts: np.ndarray,
    as_of: date,
    curve_daycount: str,
    z_spread_daycount: str,
    z_spread_compounding: Compounding | str,
    z_spread_freq: int,
) -> float:
    def _objective(z: float) -> float:
        pv = _pv_from_cashflows(
            curve,
            cashflow_dates,
            cashflow_amounts,
            as_of=as_of,
            curve_daycount=curve_daycount,
            z_spread=z,
            z_spread_daycount=z_spread_daycount,
            z_spread_compounding=z_spread_compounding,
            z_spread_freq=z_spread_freq,
        )
        return pv - target_dirty_price

    a, b = _bracket_root(_objective, 0.0, 0.0025, 20)
    if a == b:
        return float(a)
    return float(brentq(_objective, a, b, maxiter=100, xtol=1e-12))


def _resolve_bond_terms(bond_def: BondDef, trade_bond: TradeBond) -> BondDef:
    if trade_bond.security_id and trade_bond.security_id != bond_def.security_id:
        raise ValueError("trade_bond.security_id does not match bond_def.security_id.")

    coupon_type = trade_bond.coupon_type or bond_def.coupon_type
    coupon_rate = (
        trade_bond.coupon_rate if trade_bond.coupon_rate is not None else bond_def.coupon_rate
    )
    coupon_daycount = (
        trade_bond.coupon_daycount
        if trade_bond.coupon_daycount is not None
        else bond_def.coupon_daycount
    )
    coupon_freq = (
        trade_bond.coupon_freq if trade_bond.coupon_freq is not None else bond_def.coupon_freq
    )
    coupon_bdc = (
        trade_bond.coupon_bdc if trade_bond.coupon_bdc is not None else bond_def.coupon_bdc
    )
    coupon_cal_id = (
        trade_bond.coupon_cal_id
        if trade_bond.coupon_cal_id is not None
        else bond_def.coupon_cal_id
    )
    float_index_id = (
        trade_bond.float_index_id
        if trade_bond.float_index_id is not None
        else bond_def.float_index_id
    )
    float_spread = (
        trade_bond.float_spread
        if trade_bond.float_spread is not None
        else bond_def.float_spread
    )
    redemption = trade_bond.redemption if trade_bond.redemption is not None else bond_def.redemption

    return BondDef(
        security_id=bond_def.security_id,
        issue_date=bond_def.issue_date,
        maturity_date=bond_def.maturity_date,
        coupon_type=coupon_type,
        coupon_rate=coupon_rate,
        float_index_id=float_index_id,
        float_spread=float_spread,
        coupon_daycount=coupon_daycount,
        coupon_freq=coupon_freq,
        coupon_bdc=coupon_bdc,
        coupon_cal_id=coupon_cal_id,
        first_coupon_date=bond_def.first_coupon_date,
        last_coupon_date=bond_def.last_coupon_date,
        redemption=redemption,
        settlement_days=bond_def.settlement_days,
        settlement_bdc=bond_def.settlement_bdc,
        settlement_cal_id=bond_def.settlement_cal_id,
        ccy=bond_def.ccy,
    )


def _resolve_dirty_price(quote: MarketQuoteBond, side: str) -> float:
    key = side.upper()
    if key == "MID":
        if quote.dirty_price_mid is None:
            raise ValueError("dirty_price_mid is required for MID pricing.")
        return quote.dirty_price_mid
    if key == "BID":
        if quote.dirty_price_bid is None:
            raise ValueError("dirty_price_bid is required for BID pricing.")
        return quote.dirty_price_bid
    if key == "ASK":
        if quote.dirty_price_ask is None:
            raise ValueError("dirty_price_ask is required for ASK pricing.")
        return quote.dirty_price_ask
    raise ValueError(f"Unsupported input_side: {side!r}")


def _aggregate_cashflows_from_bond_schedule(
    rows: Iterable[BondScheduleRow],
    *,
    notional: float,
) -> tuple[list[date], np.ndarray]:
    totals: dict[date, float] = {}
    for row in rows:
        if row.payment_type not in {"INTEREST", "PRINCIPAL"}:
            continue
        amount_per_base = row.fixed_amount_per_base
        if amount_per_base is None:
            amount_per_base = row.amount_per_base
        if amount_per_base is None:
            if row.payment_type == "INTEREST":
                if row.rate is None or row.accrual_factor is None:
                    raise ValueError("bond_schedule missing rate/accrual_factor for interest.")
                amount_per_base = row.base_notional * row.notional_factor * row.rate * row.accrual_factor
            else:
                amount_per_base = row.base_notional * row.principal_factor
        scaled = amount_per_base * (notional / row.base_notional)
        totals[row.payment_date] = totals.get(row.payment_date, 0.0) + float(scaled)
    dates = sorted(totals.keys())
    amounts = np.array([totals[d] for d in dates], dtype=float)
    return dates, amounts


def _resolve_accrual_factor(row: BondScheduleRow, *, fallback_daycount: str) -> float:
    if row.accrual_factor is not None:
        return float(row.accrual_factor)
    if row.start_date is None or row.end_date is None:
        raise ValueError("bond_schedule missing start_date/end_date for accrual.")
    daycount = row.daycount if row.daycount is not None else fallback_daycount
    return float(year_fraction(row.start_date, row.end_date, daycount))


def _resolve_float_rate_from_fixings(
    row: BondScheduleRow,
    fixing_map: dict[tuple[str, date], float],
    *,
    as_of: date,
) -> Optional[float]:
    if row.fixing_date is None or row.index_id is None:
        return None
    if row.fixing_date > as_of:
        return None
    base = fixing_map.get((row.index_id, row.fixing_date))
    if base is None:
        return None
    spread = float(row.spread) if row.spread is not None else 0.0
    gearing = float(row.gearing) if row.gearing is not None else 1.0
    return base * gearing + spread


def _resolve_float_rate_from_curve(
    row: BondScheduleRow,
    *,
    forward_curve: YieldCurve,
    as_of: date,
    forward_daycount: str,
    compounding: Compounding | str,
    fallback_daycount: str,
) -> float:
    if row.start_date is None or row.end_date is None:
        raise ValueError("bond_schedule missing start_date/end_date for float rate.")
    t_start = year_fraction(as_of, row.start_date, forward_daycount)
    t_end = year_fraction(as_of, row.end_date, forward_daycount)
    df_start = float(np.asarray(forward_curve.df(t_start)))
    df_end = float(np.asarray(forward_curve.df(t_end)))
    accrual = _resolve_accrual_factor(row, fallback_daycount=fallback_daycount)
    base = float(forward_rate_from_dfs(df_start, df_end, accrual, compounding))
    spread = float(row.spread) if row.spread is not None else 0.0
    gearing = float(row.gearing) if row.gearing is not None else 1.0
    return base * gearing + spread


def _resolve_float_rate(
    row: BondScheduleRow,
    *,
    forward_curve: YieldCurve,
    as_of: date,
    forward_daycount: str,
    compounding: Compounding | str,
    fixing_map: dict[tuple[str, date], float],
    fallback_daycount: str,
) -> float:
    if row.fixing_date is not None and row.fixing_date <= as_of and row.index_id is not None:
        if (row.index_id, row.fixing_date) not in fixing_map:
            raise ValueError(
                "historical_fixing is required for past coupon period but missing."
            )
    fixed = _resolve_float_rate_from_fixings(row, fixing_map, as_of=as_of)
    if fixed is not None:
        return fixed
    return _resolve_float_rate_from_curve(
        row,
        forward_curve=forward_curve,
        as_of=as_of,
        forward_daycount=forward_daycount,
        compounding=compounding,
        fallback_daycount=fallback_daycount,
    )


def _aggregate_float_cashflows_from_bond_schedule(
    rows: Iterable[BondScheduleRow],
    *,
    notional: float,
    as_of: date,
    coupon_daycount: str,
    forward_curve: YieldCurve,
    forward_daycount: str,
    compounding: Compounding | str,
    fixing_map: dict[tuple[str, date], float],
) -> tuple[list[date], np.ndarray]:
    totals: dict[date, float] = {}
    for row in rows:
        if row.payment_type not in {"INTEREST", "PRINCIPAL"}:
            continue
        if row.payment_type == "PRINCIPAL":
            amount_per_base = row.base_notional * row.principal_factor
        else:
            accrual = _resolve_accrual_factor(row, fallback_daycount=coupon_daycount)
            rate = _resolve_float_rate(
                row,
                forward_curve=forward_curve,
                as_of=as_of,
                forward_daycount=forward_daycount,
                compounding=compounding,
                fixing_map=fixing_map,
                fallback_daycount=coupon_daycount,
            )
            amount_per_base = row.base_notional * row.notional_factor * rate * accrual
        scaled = amount_per_base * (notional / row.base_notional)
        totals[row.payment_date] = totals.get(row.payment_date, 0.0) + float(scaled)
    dates = sorted(totals.keys())
    amounts = np.array([totals[d] for d in dates], dtype=float)
    return dates, amounts


def _accrued_interest_from_bond_schedule(
    settle_date: date,
    rows: Iterable[BondScheduleRow],
    *,
    coupon_daycount: str,
    coupon_rate: float,
    notional: float,
) -> float:
    for row in rows:
        if row.payment_type != "INTEREST":
            continue
        if row.start_date is None or row.end_date is None:
            raise ValueError("start_date/end_date required for interest rows.")
        if row.start_date <= settle_date < row.end_date:
            dc = row.daycount if row.daycount is not None else coupon_daycount
            rate = row.rate if row.rate is not None else coupon_rate
            accrual = year_fraction(row.start_date, settle_date, dc)
            return notional * rate * accrual
    return 0.0


def _accrued_interest_from_float_schedule(
    settle_date: date,
    rows: Iterable[BondScheduleRow],
    *,
    coupon_daycount: str,
    notional: float,
    forward_curve: YieldCurve,
    forward_daycount: str,
    compounding: Compounding | str,
    fixing_map: dict[tuple[str, date], float],
) -> float:
    for row in rows:
        if row.payment_type != "INTEREST":
            continue
        if row.start_date is None or row.end_date is None:
            raise ValueError("start_date/end_date required for interest rows.")
        if row.start_date <= settle_date < row.end_date:
            daycount = row.daycount if row.daycount is not None else coupon_daycount
            rate = _resolve_float_rate(
                row,
                forward_curve=forward_curve,
                as_of=settle_date,
                forward_daycount=forward_daycount,
                compounding=compounding,
                fixing_map=fixing_map,
                fallback_daycount=daycount,
            )
            accrual = year_fraction(row.start_date, settle_date, daycount)
            return notional * rate * accrual
    return 0.0


def fixed_bond_pv(
    run_id: str,
    trade: TradeHeader,
    bond_def: BondDef,
    trade_bond: TradeBond,
    quote: MarketQuoteBond,
    bond_schedule_rows: Iterable[BondScheduleRow],
    *,
    curve: YieldCurve,
    pricing: BondPricingInput,
    cache: Optional[BondPricingStateCache] = None,
) -> BondPVResult:
    bond = _resolve_bond_terms(bond_def, trade_bond)
    if bond.coupon_type != "FIX":

        raise ValueError("bond coupon_type must be FIX for fixed bond PV.")
    if bond.coupon_rate is None or bond.coupon_daycount is None:
        raise ValueError("fixed bond requires coupon_rate and coupon_daycount.")

    cashflow_dates, cashflow_amounts = _aggregate_cashflows_from_bond_schedule(
        bond_schedule_rows, notional=trade.notional
    )
    valid_dates: list[date] = []
    valid_amounts: list[float] = []
    for d, amt in zip(cashflow_dates, cashflow_amounts):
        if d > pricing.settle_date:
            valid_dates.append(d)
            valid_amounts.append(float(amt))
    cashflow_dates = valid_dates
    cashflow_amounts = np.array(valid_amounts, dtype=float)

    accrued = _accrued_interest_from_bond_schedule(
        pricing.settle_date,
        bond_schedule_rows,
        coupon_daycount=bond.coupon_daycount,
        coupon_rate=bond.coupon_rate,
        notional=trade.notional,
    )

    cached_state = cache.get(run_id, bond.security_id, pricing.discount_curve_id, pricing.settle_date) if cache else None
    if cached_state is not None:
        z_spread = cached_state.z_spread
    else:
        dirty = _resolve_dirty_price(quote, pricing.input_side)
        obs_dirty = dirty
        obs_clean = dirty - accrued
        target_dirty = dirty * trade.notional / 100.0
        z_spread = calibrate_z_spread(
            target_dirty,
            curve=curve,
            cashflow_dates=cashflow_dates,
            cashflow_amounts=cashflow_amounts,
            as_of=pricing.settle_date,
            curve_daycount=pricing.curve_daycount,
            z_spread_daycount=pricing.z_spread_daycount,
            z_spread_compounding=pricing.z_spread_compounding,
            z_spread_freq=pricing.z_spread_freq,
        )

        if cache is not None:
            cache.put(
                BondPricingState(
                    run_id=run_id,
                    security_id=bond.security_id,
                    discount_curve_id=pricing.discount_curve_id,
                    settle_date=pricing.settle_date,
                    price_kind="DIRTY",
                    input_side=pricing.input_side,
                    price_value=dirty,
                    price_ccy=quote.quote_ccy,
                    accrued_interest=accrued * 100.0 / trade.notional,
                    obs_clean_price=obs_clean,
                    obs_dirty_price=obs_dirty,
                    z_spread=z_spread,
                    z_spread_daycount=pricing.z_spread_daycount,
                    z_spread_compounding=pricing.z_spread_compounding,
                    z_spread_compounding_freq=pricing.z_spread_freq,
                )
            )

    pv_dirty = _pv_from_cashflows(
        curve,
        cashflow_dates,
        cashflow_amounts,
        as_of=pricing.settle_date,
        curve_daycount=pricing.curve_daycount,
        z_spread=z_spread,
        z_spread_daycount=pricing.z_spread_daycount,
        z_spread_compounding=pricing.z_spread_compounding,
        z_spread_freq=pricing.z_spread_freq,
    )

    pv_clean = pv_dirty - accrued
    return BondPVResult(
        pv_dirty=pv_dirty,
        pv_clean=pv_clean,
        accrued_interest=accrued,
        z_spread=z_spread,
    )


def float_bond_pv(
    run_id: str,
    trade: TradeHeader,
    bond_def: BondDef,
    trade_bond: TradeBond,
    quote: MarketQuoteBond,
    bond_schedule_rows: Iterable[BondScheduleRow],
    *,
    discount_curve: YieldCurve,
    forward_curve: YieldCurve,
    pricing: BondPricingInput,
    fixings: Iterable[HistoricalFixing],
    cache: Optional[BondPricingStateCache] = None,
) -> BondPVResult:
    bond = _resolve_bond_terms(bond_def, trade_bond)
    if bond.coupon_type != "FLOAT":
        raise ValueError("bond coupon_type must be FLOAT for floating bond PV.")
    if bond.coupon_daycount is None:
        raise ValueError("floating bond requires coupon_daycount.")
    if bond.float_index_id is None:
        raise ValueError("floating bond requires float_index_id.")

    forward_daycount = pricing.forward_daycount or pricing.curve_daycount
    if forward_daycount is None:
        raise ValueError("forward_daycount is required for floating bond pricing.")

    fixing_map = {(f.index_id, f.fixing_date): f.rate for f in fixings}

    cashflow_dates, cashflow_amounts = _aggregate_float_cashflows_from_bond_schedule(
        bond_schedule_rows,
        notional=trade.notional,
        as_of=pricing.settle_date,
        coupon_daycount=bond.coupon_daycount,
        forward_curve=forward_curve,
        forward_daycount=forward_daycount,
        compounding=pricing.float_compounding,
        fixing_map=fixing_map,
    )
    valid_dates: list[date] = []
    valid_amounts: list[float] = []
    for d, amt in zip(cashflow_dates, cashflow_amounts):
        if d > pricing.settle_date:
            valid_dates.append(d)
            valid_amounts.append(float(amt))
    cashflow_dates = valid_dates
    cashflow_amounts = np.array(valid_amounts, dtype=float)

    accrued = _accrued_interest_from_float_schedule(
        pricing.settle_date,
        bond_schedule_rows,
        coupon_daycount=bond.coupon_daycount,
        notional=trade.notional,
        forward_curve=forward_curve,
        forward_daycount=forward_daycount,
        compounding=pricing.float_compounding,
        fixing_map=fixing_map,
    )

    cached_state = (
        cache.get(run_id, bond.security_id, pricing.discount_curve_id, pricing.settle_date)
        if cache
        else None
    )
    if cached_state is not None:
        z_spread = cached_state.z_spread
    else:
        dirty = _resolve_dirty_price(quote, pricing.input_side)
        obs_dirty = dirty
        obs_clean = dirty - accrued
        target_dirty = dirty * trade.notional / 100.0
        z_spread = calibrate_z_spread(
            target_dirty,
            curve=discount_curve,
            cashflow_dates=cashflow_dates,
            cashflow_amounts=cashflow_amounts,
            as_of=pricing.settle_date,
            curve_daycount=pricing.curve_daycount,
            z_spread_daycount=pricing.z_spread_daycount,
            z_spread_compounding=pricing.z_spread_compounding,
            z_spread_freq=pricing.z_spread_freq,
        )

        if cache is not None:
            cache.put(
                BondPricingState(
                    run_id=run_id,
                    security_id=bond.security_id,
                    discount_curve_id=pricing.discount_curve_id,
                    settle_date=pricing.settle_date,
                    price_kind="DIRTY",
                    input_side=pricing.input_side,
                    price_value=dirty,
                    price_ccy=quote.quote_ccy,
                    accrued_interest=accrued * 100.0 / trade.notional,
                    obs_clean_price=obs_clean,
                    obs_dirty_price=obs_dirty,
                    z_spread=z_spread,
                    z_spread_daycount=pricing.z_spread_daycount,
                    z_spread_compounding=pricing.z_spread_compounding,
                    z_spread_compounding_freq=pricing.z_spread_freq,
                )
            )

    pv_dirty = _pv_from_cashflows(
        discount_curve,
        cashflow_dates,
        cashflow_amounts,
        as_of=pricing.settle_date,
        curve_daycount=pricing.curve_daycount,
        z_spread=z_spread,
        z_spread_daycount=pricing.z_spread_daycount,
        z_spread_compounding=pricing.z_spread_compounding,
        z_spread_freq=pricing.z_spread_freq,
    )

    pv_clean = pv_dirty - accrued
    return BondPVResult(
        pv_dirty=pv_dirty,
        pv_clean=pv_clean,
        accrued_interest=accrued,
        z_spread=z_spread,
    )


def zero_coupon_bond_pv(
    run_id: str,
    trade: TradeHeader,
    bond_def: BondDef,
    trade_bond: TradeBond,
    quote: MarketQuoteBond,
    bond_schedule_rows: Iterable[BondScheduleRow],
    *,
    discount_curve: YieldCurve,
    pricing: BondPricingInput,
    cache: Optional[BondPricingStateCache] = None,
) -> BondPVResult:
    bond = _resolve_bond_terms(bond_def, trade_bond)
    if bond.coupon_type != "ZC":
        raise ValueError("bond coupon_type must be ZC for zero coupon PV.")

    cashflow_dates, cashflow_amounts = _aggregate_cashflows_from_bond_schedule(
        bond_schedule_rows, notional=trade.notional
    )
    valid_dates: list[date] = []
    valid_amounts: list[float] = []
    for d, amt in zip(cashflow_dates, cashflow_amounts):
        if d > pricing.settle_date:
            valid_dates.append(d)
            valid_amounts.append(float(amt))
    cashflow_dates = valid_dates
    cashflow_amounts = np.array(valid_amounts, dtype=float)

    accrued = 0.0
    cached_state = (
        cache.get(run_id, bond.security_id, pricing.discount_curve_id, pricing.settle_date)
        if cache
        else None
    )
    if cached_state is not None:
        z_spread = cached_state.z_spread
    else:
        dirty = _resolve_dirty_price(quote, pricing.input_side)
        obs_dirty = dirty
        obs_clean = dirty
        target_dirty = dirty * trade.notional / 100.0
        z_spread = calibrate_z_spread(
            target_dirty,
            curve=discount_curve,
            cashflow_dates=cashflow_dates,
            cashflow_amounts=cashflow_amounts,
            as_of=pricing.settle_date,
            curve_daycount=pricing.curve_daycount,
            z_spread_daycount=pricing.z_spread_daycount,
            z_spread_compounding=pricing.z_spread_compounding,
            z_spread_freq=pricing.z_spread_freq,
        )

        if cache is not None:
            cache.put(
                BondPricingState(
                    run_id=run_id,
                    security_id=bond.security_id,
                    discount_curve_id=pricing.discount_curve_id,
                    settle_date=pricing.settle_date,
                    price_kind="DIRTY",
                    input_side=pricing.input_side,
                    price_value=dirty,
                    price_ccy=quote.quote_ccy,
                    accrued_interest=0.0,
                    obs_clean_price=obs_clean,
                    obs_dirty_price=obs_dirty,
                    z_spread=z_spread,
                    z_spread_daycount=pricing.z_spread_daycount,
                    z_spread_compounding=pricing.z_spread_compounding,
                    z_spread_compounding_freq=pricing.z_spread_freq,
                )
            )

    pv_dirty = _pv_from_cashflows(
        discount_curve,
        cashflow_dates,
        cashflow_amounts,
        as_of=pricing.settle_date,
        curve_daycount=pricing.curve_daycount,
        z_spread=z_spread,
        z_spread_daycount=pricing.z_spread_daycount,
        z_spread_compounding=pricing.z_spread_compounding,
        z_spread_freq=pricing.z_spread_freq,
    )

    pv_clean = pv_dirty
    return BondPVResult(
        pv_dirty=pv_dirty,
        pv_clean=pv_clean,
        accrued_interest=accrued,
        z_spread=z_spread,
    )


def load_bond_pricing_data(
    provider: BondDataProvider,
    *,
    run_id: str,
    trade_id: str,
    snapshot_id: str,
    pricing: BondPricingInput,
) -> BondPricingData:
    trade = provider.get_trade(trade_id)
    trade_bond = provider.get_trade_bond(trade_id)
    if trade_bond.security_id is None:
        raise ValueError("trade_bond.security_id is required to load bond_def.")
    bond_def = provider.get_bond_def(trade_bond.security_id)
    quote = provider.get_market_quote_bond(bond_def.security_id, snapshot_id)
    schedule_rows = tuple(provider.get_bond_schedule(trade_id, bond_def.security_id))
    if not schedule_rows:
        raise ValueError("bond_schedule rows are missing for pricing.")
    discount_curve = provider.get_yield_curve(pricing.discount_curve_id, snapshot_id)
    forward_curve_id = pricing.forward_curve_id or pricing.discount_curve_id
    forward_curve = discount_curve
    if forward_curve_id != pricing.discount_curve_id:
        forward_curve = provider.get_yield_curve(forward_curve_id, snapshot_id)

    bond = _resolve_bond_terms(bond_def, trade_bond)
    fixings: Sequence[HistoricalFixing] = ()
    if bond.coupon_type == "FLOAT":
        if bond.float_index_id is None:
            raise ValueError("floating bond requires float_index_id.")
        fixing_dates = [
            row.fixing_date for row in schedule_rows if row.fixing_date is not None
        ]
        if fixing_dates:
            start_date = min(fixing_dates)
            end_date = max(fixing_dates)
            fixings = provider.get_historical_fixings(
                bond.float_index_id, start_date, end_date
            )
    return BondPricingData(
        run_id=run_id,
        trade=trade,
        bond_def=bond_def,
        trade_bond=trade_bond,
        quote=quote,
        schedule_rows=schedule_rows,
        discount_curve=discount_curve,
        forward_curve=forward_curve,
        fixings=tuple(fixings),
        pricing=pricing,
    )


def price_bond_from_data(
    data: BondPricingData,
    *,
    cache: Optional[BondPricingStateCache] = None,
) -> BondPVResult:
    bond = _resolve_bond_terms(data.bond_def, data.trade_bond)
    if bond.coupon_type == "FIX":
        return fixed_bond_pv(
            data.run_id,
            data.trade,
            data.bond_def,
            data.trade_bond,
            data.quote,
            data.schedule_rows,
            curve=data.discount_curve,
            pricing=data.pricing,
            cache=cache,
        )
    if bond.coupon_type == "FLOAT":
        return float_bond_pv(
            data.run_id,
            data.trade,
            data.bond_def,
            data.trade_bond,
            data.quote,
            data.schedule_rows,
            discount_curve=data.discount_curve,
            forward_curve=data.forward_curve,
            pricing=data.pricing,
            fixings=data.fixings,
            cache=cache,
        )
    if bond.coupon_type == "ZC":
        return zero_coupon_bond_pv(
            data.run_id,
            data.trade,
            data.bond_def,
            data.trade_bond,
            data.quote,
            data.schedule_rows,
            discount_curve=data.discount_curve,
            pricing=data.pricing,
            cache=cache,
        )
    raise ValueError(f"Unsupported bond coupon_type: {bond.coupon_type!r}")
