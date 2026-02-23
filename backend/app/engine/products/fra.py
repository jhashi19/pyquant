from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

import numpy as np

from app.engine.market.yield_curve import YieldCurve
from app.engine.math.bizday import (
    BusinessCalendar,
    add_business_days,
    adjust_business_day,
)
from app.engine.math.daycount import year_fraction
from app.engine.math.rate_conversion import Compounding, forward_rate_from_dfs
from app.engine.products.models.schedule_models import RefRateRule, TradeHeader


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    as_of: date


@dataclass(frozen=True)
class HistoricalFixing:
    index_id: str
    fixing_date: date
    rate: float


@dataclass(frozen=True)
class TradeFRA:
    trade_id: str
    ccy: str
    notional: float
    pay_rec: str  # PAY/REC on fixed leg
    fra_rate_agreed: float
    ref_rate_id: str
    accrual_start_date: date
    accrual_end_date: date
    daycount: str
    pay_bdc: str
    pay_cal_id: str
    fixing_lag_bd: int
    fixing_bdc: str
    fixing_cal_id: str
    settlement_type: str  # CASH


@dataclass(frozen=True)
class FRAPricingInput:
    discount_curve_id: str
    forward_curve_id: Optional[str]
    discount_curve_daycount: str
    projection_curve_daycount: Optional[str] = None
    projection_compounding: Compounding | str = Compounding.SIMPLE
    include_settled: bool = False
    as_of: Optional[date] = None


@dataclass(frozen=True)
class FRAPricingData:
    run_id: str
    trade: TradeHeader
    fra: TradeFRA
    ref_rate_rule: RefRateRule
    discount_curve: YieldCurve
    forward_curve: YieldCurve
    fixing_calendar: BusinessCalendar
    payment_calendar: BusinessCalendar
    historical_fixing: Optional[HistoricalFixing]
    pricing: FRAPricingInput
    as_of: date


@dataclass(frozen=True)
class FRAPVResult:
    pv: float
    settlement_amount: float
    settlement_date: date
    fixing_date: date
    fixing_rate: float
    agreed_rate: float
    accrual_factor: float
    df_to_settlement: float


class FRADataProvider(Protocol):
    def get_trade(self, trade_id: str) -> TradeHeader: ...

    def get_trade_fra(self, trade_id: str) -> TradeFRA: ...

    def get_ref_rate_rule(self, index_id: str) -> RefRateRule: ...

    def get_historical_fixing(self, index_id: str, fixing_date: date) -> Optional[HistoricalFixing]: ...

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot: ...

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve: ...

    def get_business_calendar(self, cal_id: str) -> BusinessCalendar: ...


def _fixing_date(fra: TradeFRA, fixing_calendar: BusinessCalendar) -> date:
    lagged = add_business_days(fra.accrual_start_date, -fra.fixing_lag_bd, fixing_calendar)
    return adjust_business_day(lagged, fra.fixing_bdc, fixing_calendar)


def _settlement_date(fra: TradeFRA, payment_calendar: BusinessCalendar) -> date:
    return adjust_business_day(fra.accrual_start_date, fra.pay_bdc, payment_calendar)


def _project_forward_rate(
    fra: TradeFRA,
    *,
    as_of: date,
    forward_curve: YieldCurve,
    projection_curve_daycount: str,
    compounding: Compounding | str,
) -> float:
    t_start = year_fraction(as_of, fra.accrual_start_date, projection_curve_daycount)
    t_end = year_fraction(as_of, fra.accrual_end_date, projection_curve_daycount)
    df_start = float(np.asarray(forward_curve.df(t_start)))
    df_end = float(np.asarray(forward_curve.df(t_end)))
    accrual_factor = year_fraction(fra.accrual_start_date, fra.accrual_end_date, fra.daycount)
    return float(forward_rate_from_dfs(df_start, df_end, accrual_factor, compounding))


def _resolve_floating_rate(
    fra: TradeFRA,
    *,
    fixing_date: date,
    as_of: date,
    historical_fixing: Optional[HistoricalFixing],
    forward_curve: YieldCurve,
    projection_curve_daycount: str,
    compounding: Compounding | str,
) -> float:
    if fixing_date <= as_of:
        if historical_fixing is None:
            raise ValueError("historical_fixing is required because fixing date is in the past.")
        return float(historical_fixing.rate)

    return _project_forward_rate(
        fra,
        as_of=as_of,
        forward_curve=forward_curve,
        projection_curve_daycount=projection_curve_daycount,
        compounding=compounding,
    )


def price_fra_from_data(data: FRAPricingData) -> FRAPVResult:
    fra = data.fra
    if fra.settlement_type != "CASH":
        raise ValueError(f"Unsupported FRA settlement_type: {fra.settlement_type!r}")

    fixing_date = _fixing_date(fra, data.fixing_calendar)
    settlement_date = _settlement_date(fra, data.payment_calendar)
    as_of = data.as_of

    if settlement_date <= as_of:
        return FRAPVResult(
            pv=0.0,
            settlement_amount=0.0,
            settlement_date=settlement_date,
            fixing_date=fixing_date,
            fixing_rate=fra.fra_rate_agreed,
            agreed_rate=fra.fra_rate_agreed,
            accrual_factor=year_fraction(fra.accrual_start_date, fra.accrual_end_date, fra.daycount),
            df_to_settlement=1.0,
        )

    projection_curve_daycount = (
        data.pricing.projection_curve_daycount or data.ref_rate_rule.daycount
    )
    floating_rate = _resolve_floating_rate(
        fra,
        fixing_date=fixing_date,
        as_of=as_of,
        historical_fixing=data.historical_fixing,
        forward_curve=data.forward_curve,
        projection_curve_daycount=projection_curve_daycount,
        compounding=data.pricing.projection_compounding,
    )
    agreed_rate = fra.fra_rate_agreed
    accrual_factor = year_fraction(fra.accrual_start_date, fra.accrual_end_date, fra.daycount)

    # Cash-settled FRA market convention: settle at accrual start with discounting by realized/proj floating rate.
    # Note: If supporting 'Settle at Maturity', the formula would be simply: notional * accrual * diff
    diff = floating_rate - agreed_rate
    sign = 1.0 if fra.pay_rec.upper() == "PAY" else -1.0
    settlement_amount = sign * fra.notional * accrual_factor * diff / (
        1.0 + floating_rate * accrual_factor
    )

    t_settle = year_fraction(as_of, settlement_date, data.pricing.discount_curve_daycount)
    df_to_settlement = float(np.asarray(data.discount_curve.df(t_settle)))
    pv = settlement_amount * df_to_settlement

    return FRAPVResult(
        pv=float(pv),
        settlement_amount=float(settlement_amount),
        settlement_date=settlement_date,
        fixing_date=fixing_date,
        fixing_rate=float(floating_rate),
        agreed_rate=float(agreed_rate),
        accrual_factor=float(accrual_factor),
        df_to_settlement=float(df_to_settlement),
    )


def load_fra_pricing_data(
    provider: FRADataProvider,
    *,
    run_id: str,
    trade_id: str,
    snapshot_id: str,
    pricing: FRAPricingInput,
) -> FRAPricingData:
    trade = provider.get_trade(trade_id)
    fra = provider.get_trade_fra(trade_id)
    ref_rate_rule = provider.get_ref_rate_rule(fra.ref_rate_id)
    snapshot = provider.get_market_snapshot(snapshot_id)
    as_of = pricing.as_of or snapshot.as_of

    discount_curve = provider.get_yield_curve(pricing.discount_curve_id, snapshot_id)
    forward_curve_id = pricing.forward_curve_id or pricing.discount_curve_id
    forward_curve = provider.get_yield_curve(forward_curve_id, snapshot_id)

    fixing_calendar = provider.get_business_calendar(fra.fixing_cal_id)
    payment_calendar = provider.get_business_calendar(fra.pay_cal_id)

    fixing_date = _fixing_date(fra, fixing_calendar)
    historical_fixing = provider.get_historical_fixing(fra.ref_rate_id, fixing_date)

    return FRAPricingData(
        run_id=run_id,
        trade=trade,
        fra=fra,
        ref_rate_rule=ref_rate_rule,
        discount_curve=discount_curve,
        forward_curve=forward_curve,
        fixing_calendar=fixing_calendar,
        payment_calendar=payment_calendar,
        historical_fixing=historical_fixing,
        pricing=pricing,
        as_of=as_of,
    )
