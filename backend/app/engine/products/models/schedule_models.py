from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional, Literal

from app.engine.math.bizday import BusinessCalendar, BusinessDayRule


class StubType(Enum):
    NONE = "NONE"
    FRONT = "FRONT"
    BACK = "BACK"
    LONG_FRONT = "LONG_FRONT"
    LONG_BACK = "LONG_BACK"
    BOTH = "BOTH"


class TenorUnit(Enum):
    DAY = "D"
    WEEK = "W"
    MONTH = "M"
    YEAR = "Y"


@dataclass(frozen=True)
class Tenor:
    months: int = 0
    days: int = 0

    def is_zero(self) -> bool:
        return self.months == 0 and self.days == 0

    def negated(self) -> "Tenor":
        return Tenor(-self.months, -self.days)

    def is_month_based(self) -> bool:
        return self.months != 0


@dataclass(frozen=True)
class CashflowPeriod:
    unadjusted_start: date
    unadjusted_end: date
    accrual_start: date
    accrual_end: date
    payment_date: date
    fixing_date: Optional[date] = None


@dataclass(frozen=True)
class LegScheduleSpec:
    freq: str | Tenor
    calendar: BusinessCalendar = field(default_factory=BusinessCalendar)
    payment_calendar: Optional[BusinessCalendar] = None
    bdc: BusinessDayRule | str = BusinessDayRule.MOD_FOLLOWING
    stub_type: StubType | str = StubType.BACK
    first_date: Optional[date | str] = None
    last_date: Optional[date | str] = None
    eom: Optional[bool] = None
    pay_lag: int = 0
    accrual_bdc: Optional[BusinessDayRule | str] = None
    accrual_calendar: Optional[BusinessCalendar] = None
    fixing_lag: Optional[int] = None
    fixing_calendar: Optional[BusinessCalendar] = None
    fixing_bdc: Optional[BusinessDayRule | str] = None


@dataclass(frozen=True)
class SwapSchedule:
    fixed_leg: tuple[CashflowPeriod, ...]
    float_leg: tuple[CashflowPeriod, ...]


@dataclass(frozen=True)
class TradeHeader:
    trade_id: str
    product: str
    ccy: str
    notional: float
    buy_sell: str
    trade_date: date
    effective_date: Optional[date]
    maturity_date: Optional[date]


@dataclass(frozen=True)
class BondDef:
    security_id: str
    issue_date: date
    maturity_date: date
    coupon_type: str  # FIX/FLOAT/ZC
    coupon_rate: Optional[float]
    float_index_id: Optional[str]
    float_spread: Optional[float]
    coupon_daycount: Optional[str]
    coupon_freq: Optional[str]
    coupon_bdc: Optional[BusinessDayRule | str]
    coupon_cal_id: Optional[str]
    first_coupon_date: Optional[date] = None
    last_coupon_date: Optional[date] = None
    redemption: float = 100.0
    settlement_days: Optional[int] = None
    settlement_bdc: Optional[BusinessDayRule | str] = None
    settlement_cal_id: Optional[str] = None
    ccy: Optional[str] = None


@dataclass(frozen=True)
class MarketQuoteBond:
    security_id: str
    dirty_price_mid: Optional[float] = None
    dirty_price_bid: Optional[float] = None
    dirty_price_ask: Optional[float] = None
    quote_ccy: Optional[str] = None


@dataclass(frozen=True)
class TradeBond:
    trade_id: str
    security_id: Optional[str]
    coupon_type: str
    coupon_rate: Optional[float]
    coupon_daycount: Optional[str]
    coupon_freq: Optional[str]
    coupon_bdc: Optional[BusinessDayRule | str]
    coupon_cal_id: Optional[str]
    float_index_id: Optional[str]
    float_spread: Optional[float]
    issuer: Optional[str]
    redemption: float
    settlement_ccy: str


@dataclass(frozen=True)
class TradeIRS:
    trade_id: str
    pay_rec: str  # PAY/REC, refers to fixed leg direction
    fixed_rate: float
    fixed_daycount: str
    fixed_freq: str
    fixed_bdc: BusinessDayRule | str
    fixed_cal_id: str
    float_index_id: str
    float_spread: float
    float_daycount: str
    float_freq: str
    float_bdc: BusinessDayRule | str
    float_cal_id: str
    stub_type: Optional[str]
    settle_ccy: Optional[str]



@dataclass(frozen=True)
class RefRateRule:
    index_id: str
    rate_type: str
    accrual_conv: str
    daycount: str
    fixing_cal_id: str
    fixing_bdc: BusinessDayRule | str
    lookback_days: int


@dataclass(frozen=True)
class HistoricalFixing:
    index_id: str
    fixing_date: date
    rate: float


@dataclass(frozen=True)
class SwapScheduleRow:
    trade_id: str
    leg_id: str
    cashflow_no: int
    payment_date: date
    payment_type: str
    pay_rec: str
    ccy: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    daycount: Optional[str] = None
    accrual_factor: Optional[float] = None
    notional: Optional[float] = None
    principal_factor: Optional[float] = None
    index_id: Optional[str] = None
    spread: Optional[float] = None
    gearing: Optional[float] = None
    rate_calc_type: Optional[str] = None
    fixing_date: Optional[date] = None
    obs_start_date: Optional[date] = None
    obs_end_date: Optional[date] = None
    rate: Optional[float] = None
    amount: Optional[float] = None
    fixed_amount: Optional[float] = None
    settled_amount: Optional[float] = None
    is_settled: int = 0
    settled_date: Optional[date] = None
    settlement_ref: Optional[str] = None


@dataclass(frozen=True)
class BondScheduleRow:
    security_id: str
    base_security_id: str
    trade_id: Optional[str]
    cashflow_no: int
    payment_date: date
    payment_type: str
    ccy: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    daycount: Optional[str] = None
    accrual_factor: Optional[float] = None
    base_notional: float
    notional_factor: float
    principal_factor: float
    rate_calc_type: Optional[str] = None
    index_id: Optional[str] = None
    spread: Optional[float] = None
    gearing: Optional[float] = None
    fixing_date: Optional[date] = None
    obs_start_date: Optional[date] = None
    obs_end_date: Optional[date] = None
    rate: Optional[float] = None
    amount_per_base: Optional[float] = None
    fixed_amount_per_base: Optional[float] = None
    is_stub: int = 0
