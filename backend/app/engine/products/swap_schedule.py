from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

import numpy as np

from app.engine.math.bizday import (
    BusinessCalendar,
    add_business_days,
    adjust_business_day,
)
from app.engine.math.daycount import year_fraction
from app.engine.products.models.schedule_models import (
    HistoricalFixing,
    LegScheduleSpec,
    RefRateRule,
    SwapScheduleRow,
    TradeHeader,
    TradeIRS,
    TradeIRSAmortizingStep,
)
from app.engine.products.schedule_utils import build_leg_schedule


def build_fixing_map(fixings: Iterable[HistoricalFixing]) -> dict[tuple[str, date], float]:
    return {(f.index_id, f.fixing_date): f.rate for f in fixings}


def _resolve_calendar(
    calendars: dict[str, BusinessCalendar], cal_id: Optional[str]
) -> BusinessCalendar:
    if cal_id is None:
        raise ValueError("calendar id is required but missing.")
    if cal_id not in calendars:
        raise ValueError(f"calendar id not found: {cal_id!r}")
    return calendars[cal_id]


def _float_rate_calc_type(rule: RefRateRule) -> str:
    if rule.rate_type == "ON":
        if rule.accrual_conv == "COMPOUND_IN_ARREARS":
            return "OIS_COMPOUNDED"
        if rule.accrual_conv == "AVERAGE":
            return "OIS_AVERAGED"
        return "IBOR_SINGLE"
    return "IBOR_SINGLE"


def _opposite_pay_rec(pay_rec: str) -> str:
    tag = pay_rec.upper()
    if tag == "PAY":
        return "REC"
    if tag == "REC":
        return "PAY"
    raise ValueError(f"Unsupported pay_rec: {pay_rec!r}")


def _build_notional_resolver(
    trade_notional: float,
    amortizing_steps: Optional[Iterable[TradeIRSAmortizingStep]],
):
    steps = tuple(amortizing_steps or ())
    if not steps:
        return lambda payment_date: trade_notional

    sorted_steps = sorted(steps, key=lambda s: (s.change_date, s.step_no))
    change_ord = np.array([s.change_date.toordinal() for s in sorted_steps], dtype=np.int64)
    ratios = np.array([float(s.notional_ratio) for s in sorted_steps], dtype=float)

    def _resolve(payment_date: date) -> float:
        idx = int(np.searchsorted(change_ord, payment_date.toordinal(), side="right") - 1)
        if idx < 0:
            return trade_notional
        return trade_notional * ratios[idx]

    return _resolve


def _resolve_observation_window(
    accrual_start: date,
    accrual_end: date,
    *,
    rule: RefRateRule,
    fixing_calendar: BusinessCalendar,
) -> tuple[date, date]:
    if rule.rate_type != "ON" or rule.lookback_days == 0:
        return accrual_start, accrual_end
    obs_start = add_business_days(accrual_start, -rule.lookback_days, fixing_calendar)
    obs_end = add_business_days(accrual_end, -rule.lookback_days, fixing_calendar)
    obs_start = adjust_business_day(obs_start, rule.fixing_bdc, fixing_calendar)
    obs_end = adjust_business_day(obs_end, rule.fixing_bdc, fixing_calendar)
    return obs_start, obs_end


def build_swap_schedule_rows(
    trade: TradeHeader,
    irs: TradeIRS,
    ref_rate: RefRateRule,
    *,
    calendars: dict[str, BusinessCalendar],
    amortizing_steps: Optional[Iterable[TradeIRSAmortizingStep]] = None,
    fixings: Optional[Iterable[HistoricalFixing]] = None,
) -> list[SwapScheduleRow]:
    if trade.effective_date is None or trade.maturity_date is None:
        raise ValueError("IRS trade requires effective_date and maturity_date.")

    fixing_map = build_fixing_map(fixings or [])
    fixed_cal = _resolve_calendar(calendars, irs.fixed_cal_id)
    float_cal = _resolve_calendar(calendars, irs.float_cal_id)
    fix_cal = _resolve_calendar(calendars, ref_rate.fixing_cal_id)

    fixed_spec = LegScheduleSpec(
        freq=irs.fixed_freq,
        calendar=fixed_cal,
        payment_calendar=fixed_cal,
        bdc=irs.fixed_bdc,
        stub_type=irs.stub_type or "BACK",
        pay_lag=0,
        accrual_bdc=irs.fixed_bdc,
        accrual_calendar=fixed_cal,
    )
    float_spec = LegScheduleSpec(
        freq=irs.float_freq,
        calendar=float_cal,
        payment_calendar=float_cal,
        bdc=irs.float_bdc,
        stub_type=irs.stub_type or "BACK",
        pay_lag=0,
        accrual_bdc=irs.float_bdc,
        accrual_calendar=float_cal,
        fixing_lag=ref_rate.lookback_days,
        fixing_calendar=fix_cal,
        fixing_bdc=ref_rate.fixing_bdc,
    )

    fixed_periods = build_leg_schedule(trade.effective_date, trade.maturity_date, fixed_spec)
    float_periods = build_leg_schedule(trade.effective_date, trade.maturity_date, float_spec)

    rows: list[SwapScheduleRow] = []
    ccy = irs.settle_ccy if irs.settle_ccy is not None else trade.ccy
    fixed_pay_rec = irs.pay_rec
    float_pay_rec = _opposite_pay_rec(irs.pay_rec)
    resolve_notional = _build_notional_resolver(trade.notional, amortizing_steps)

    cashflow_no = 1
    for period in fixed_periods:
        notional = resolve_notional(period.payment_date)
        accrual = year_fraction(period.accrual_start, period.accrual_end, irs.fixed_daycount)
        amount = notional * irs.fixed_rate * accrual
        rows.append(
            SwapScheduleRow(
                trade_id=trade.trade_id,
                leg_id="FIXED",
                cashflow_no=cashflow_no,
                payment_date=period.payment_date,
                payment_type="INTEREST",
                pay_rec=fixed_pay_rec,
                ccy=ccy,
                start_date=period.accrual_start,
                end_date=period.accrual_end,
                daycount=irs.fixed_daycount,
                accrual_factor=accrual,
                notional=notional,
                principal_factor=0.0,
                rate_calc_type="FIXED",
                rate=irs.fixed_rate,
                amount=amount,
                fixed_amount=amount,
            )
        )
        cashflow_no += 1

    cashflow_no = 1
    for period in float_periods:
        notional = resolve_notional(period.payment_date)
        obs_start, obs_end = _resolve_observation_window(
            period.accrual_start,
            period.accrual_end,
            rule=ref_rate,
            fixing_calendar=fix_cal,
        )
        accrual = year_fraction(period.accrual_start, period.accrual_end, irs.float_daycount)
        rate = None
        amount = None
        fixed_amount = None
        if period.fixing_date is not None:
            key = (irs.float_index_id, period.fixing_date)
            if key in fixing_map:
                rate = fixing_map[key] + irs.float_spread
                amount = notional * rate * accrual
                fixed_amount = amount
        rows.append(
            SwapScheduleRow(
                trade_id=trade.trade_id,
                leg_id="FLOAT",
                cashflow_no=cashflow_no,
                payment_date=period.payment_date,
                payment_type="INTEREST",
                pay_rec=float_pay_rec,
                ccy=ccy,
                start_date=period.accrual_start,
                end_date=period.accrual_end,
                daycount=irs.float_daycount,
                accrual_factor=accrual,
                notional=notional,
                principal_factor=0.0,
                index_id=irs.float_index_id,
                spread=irs.float_spread,
                gearing=1.0,
                rate_calc_type=_float_rate_calc_type(ref_rate),
                fixing_date=period.fixing_date,
                obs_start_date=obs_start,
                obs_end_date=obs_end,
                rate=rate,
                amount=amount,
                fixed_amount=fixed_amount,
            )
        )
        cashflow_no += 1

    return rows
