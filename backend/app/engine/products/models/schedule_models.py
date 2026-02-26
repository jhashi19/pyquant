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
    pricing_profile_id: Optional[str] = None


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
    clean_price_mid: Optional[float] = None
    clean_price_bid: Optional[float] = None
    clean_price_ask: Optional[float] = None
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
    clean_price_agreed: Optional[float] = None


@dataclass(frozen=True)
class TradeCapFloor:
    trade_id: str
    ccy: str
    cp_flag: str
    index_id: str
    index_tenor: str
    strike_rate: float
    start_date: date
    end_date: date
    pay_rec: str
    pay_freq: str
    pay_bdc: BusinessDayRule | str
    pay_cal_id: str


@dataclass(frozen=True)
class TradeSwaption:
    trade_id: str
    ccy: str
    option_style: str
    cp_flag: str
    expiry_date: date
    exercise_open: Optional[date]
    exercise_close: Optional[date]
    settlement: str
    cash_settle_method: Optional[str]
    cash_settle_lag_bd: Optional[int]
    cash_settle_bdc: Optional[BusinessDayRule | str]
    cash_settle_cal_id: Optional[str]
    swap_pay_rec: str
    swap_fixed_rate: Optional[float]
    swap_fixed_dc: str
    swap_fixed_freq: str
    swap_fixed_bdc: BusinessDayRule | str
    swap_fixed_cal: str
    swap_index_id: str
    swap_index_tenor: str
    swap_spread: float
    swap_float_dc: str
    swap_float_freq: str
    swap_float_bdc: BusinessDayRule | str
    swap_float_cal: str
    swap_start_date: Optional[date]
    swap_spot_lag_bd: int
    swap_maturity: date


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
class TradeIRSAmortizingStep:
    trade_id: str
    step_no: int
    change_date: date
    notional_ratio: float


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
class ModelParamRow:
    snapshot_id: str
    model_tag: str
    scope: str
    param_key: str
    expiry_tenor: Optional[str]
    expiry_date: Optional[date]
    x_years: Optional[float]
    swap_tenor: Optional[str]
    strike_rate: Optional[float]
    moneyness: Optional[float]
    param_name: str
    param_val: float
    param_unit: Optional[str] = None
    source_symbol: Optional[str] = None
    note: Optional[str] = None


@dataclass(frozen=True)
class SabrInterpolationSpec:
    product: str
    model_tag: str
    beta_strategy: str  # FIXED / INTERPOLATE_LOGIT
    beta_fixed_value: Optional[float] = None
    nu_interp_transform: str = "LOG"
    rho_interp_transform: str = "ATANH"
    alpha_interp_mode: str = "TOTAL_VARIANCE_LINEAR"
    alpha_solver: str = "NEWTON_WITH_BISECTION_FALLBACK"
    alpha_cache_enabled: bool = True
    newton_tol: float = 1e-12
    newton_max_iter: int = 20
    boundary_warn_tol: float = 1e-8


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
    base_notional: float
    notional_factor: float
    principal_factor: float
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    daycount: Optional[str] = None
    accrual_factor: Optional[float] = None
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


@dataclass(frozen=True)
class CapFloorScheduleRow:
    trade_id: str
    cashflow_no: int
    payment_date: date
    ccy: str
    cp_flag: str
    pay_rec: str
    start_date: date
    end_date: date
    daycount: str
    accrual_factor: float
    notional: float
    strike_rate: float
    index_id: str
    rate_calc_type: str
    fixing_date: Optional[date] = None
    obs_start_date: Optional[date] = None
    obs_end_date: Optional[date] = None
    observed_rate: Optional[float] = None
    forward_rate: Optional[float] = None
    payoff_rate: Optional[float] = None
    amount: Optional[float] = None
    fixed_amount: Optional[float] = None
    is_fixed: int = 0
    is_settled: int = 0
    settled_amount: Optional[float] = None
    settled_date: Optional[date] = None
    settlement_ref: Optional[str] = None
