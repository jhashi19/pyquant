from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Iterable, Optional

from ..math.bizday import (
    BusinessCalendar,
    BusinessDayRule,
    add_business_days,
    adjust_business_day,
)
from ..math.date_utils import DateLike, add_months, is_end_of_month, to_date


class StubType(Enum):
    NONE = "NONE"
    FRONT = "FRONT"
    BACK = "BACK"
    BOTH = "BOTH"


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
    bdc: BusinessDayRule | str = BusinessDayRule.MOD_FOLLOWING
    stub_type: StubType | str = StubType.BACK
    first_date: Optional[DateLike] = None
    last_date: Optional[DateLike] = None
    eom: Optional[bool] = None
    pay_lag: int = 0
    accrual_bdc: Optional[BusinessDayRule | str] = None
    accrual_calendar: Optional[BusinessCalendar] = None
    fixing_lag: Optional[int] = None
    fixing_calendar: Optional[BusinessCalendar] = None


@dataclass(frozen=True)
class SwapSchedule:
    fixed_leg: tuple[CashflowPeriod, ...]
    float_leg: tuple[CashflowPeriod, ...]


def parse_tenor(value: str | Tenor) -> Tenor:
    if isinstance(value, Tenor):
        if value.is_zero():
            raise ValueError("Tenor must be non-zero.")
        return value

    text = str(value).strip().upper()
    if not text:
        raise ValueError("Tenor must be non-empty.")

    unit = text[-1]
    if unit not in {"D", "W", "M", "Y"}:
        raise ValueError(f"Unsupported tenor unit: {value!r}")

    try:
        amount = int(text[:-1])
    except ValueError as exc:
        raise ValueError(f"Invalid tenor: {value!r}") from exc

    if amount <= 0:
        raise ValueError("Tenor amount must be positive.")

    if unit == "D":
        return Tenor(days=amount)
    if unit == "W":
        return Tenor(days=amount * 7)
    if unit == "M":
        return Tenor(months=amount)
    return Tenor(months=amount * 12)


def add_tenor(value: DateLike, tenor: Tenor, *, eom: Optional[bool] = None) -> date:
    d = to_date(value)
    if tenor.is_zero():
        return d
    out = d
    if tenor.months:
        out = add_months(out, tenor.months, eom=eom)
    if tenor.days:
        out = out + timedelta(days=tenor.days)
    return out


def normalize_stub_type(value: StubType | str) -> StubType:
    if isinstance(value, StubType):
        return value
    key = str(value).strip().upper()
    match key:
        case "NONE":
            return StubType.NONE
        case "FRONT":
            return StubType.FRONT
        case "BACK":
            return StubType.BACK
        case "BOTH":
            return StubType.BOTH
        case _:
            raise ValueError(f"Unsupported stub type: {value!r}")


def _infer_eom(start: date, tenor: Tenor, eom: Optional[bool]) -> bool:
    if eom is not None:
        return eom
    if tenor.is_month_based():
        return is_end_of_month(start)
    return False


def _append_unique(target: list[date], value: date) -> None:
    if not target or target[-1] != value:
        target.append(value)


def _extend_unique(target: list[date], values: Iterable[date]) -> None:
    for d in values:
        _append_unique(target, d)


def _regular_dates_forward(
    start: date, end: date, tenor: Tenor, *, eom: bool
) -> tuple[list[date], bool]:
    dates = [start]
    current = start
    while True:
        nxt = add_tenor(current, tenor, eom=eom)
        if nxt <= current:
            raise ValueError("Tenor does not advance schedule.")
        if nxt >= end:
            stub = nxt != end
            break
        dates.append(nxt)
        current = nxt
    dates.append(end)
    return dates, stub


def _regular_dates_backward(
    start: date, end: date, tenor: Tenor, *, eom: bool
) -> tuple[list[date], bool]:
    dates = [end]
    current = end
    step = tenor.negated()
    while True:
        prev = add_tenor(current, step, eom=eom)
        if prev >= current:
            raise ValueError("Tenor does not advance schedule.")
        if prev <= start:
            stub = prev != start
            break
        dates.append(prev)
        current = prev
    dates.append(start)
    dates.reverse()
    return dates, stub


def build_unadjusted_schedule_dates(
    start: DateLike,
    end: DateLike,
    tenor: str | Tenor,
    *,
    stub_type: StubType | str = StubType.BACK,
    first_date: Optional[DateLike] = None,
    last_date: Optional[DateLike] = None,
    eom: Optional[bool] = None,
) -> list[date]:
    start_date = to_date(start)
    end_date = to_date(end)
    if end_date <= start_date:
        raise ValueError("end must be after start.")

    tenor_obj = parse_tenor(tenor)
    eom_flag = _infer_eom(start_date, tenor_obj, eom)

    first = to_date(first_date) if first_date is not None else None
    last = to_date(last_date) if last_date is not None else None

    if first is not None or last is not None:
        mid_start = first if first is not None else start_date
        mid_end = last if last is not None else end_date
        if mid_start < start_date or mid_end > end_date:
            raise ValueError("Explicit stub dates must be within start/end.")
        if mid_end <= mid_start:
            raise ValueError("Explicit stub dates must be increasing.")

        dates: list[date] = []
        _append_unique(dates, start_date)
        mid_dates, _ = _regular_dates_forward(mid_start, mid_end, tenor_obj, eom=eom_flag)
        _extend_unique(dates, mid_dates)
        _append_unique(dates, end_date)
        return dates

    tag = normalize_stub_type(stub_type)
    if tag == StubType.BOTH:
        raise ValueError("stub_type BOTH requires first_date and last_date.")

    if tag == StubType.FRONT:
        dates, stub = _regular_dates_backward(start_date, end_date, tenor_obj, eom=eom_flag)
    else:
        dates, stub = _regular_dates_forward(start_date, end_date, tenor_obj, eom=eom_flag)

    if tag == StubType.NONE and stub:
        raise ValueError("Schedule does not fit tenor with stub_type=NONE.")
    return dates


def build_cashflow_periods(
    dates: Iterable[date],
    *,
    calendar: BusinessCalendar,
    bdc: BusinessDayRule | str,
    pay_lag: int = 0,
    accrual_bdc: Optional[BusinessDayRule | str] = None,
    accrual_calendar: Optional[BusinessCalendar] = None,
    fixing_lag: Optional[int] = None,
    fixing_calendar: Optional[BusinessCalendar] = None,
) -> list[CashflowPeriod]:
    date_list = list(dates)
    if len(date_list) < 2:
        raise ValueError("At least two schedule dates are required.")

    acc_bdc = accrual_bdc if accrual_bdc is not None else bdc
    acc_cal = accrual_calendar if accrual_calendar is not None else calendar
    pay_cal = calendar
    fix_cal = fixing_calendar if fixing_calendar is not None else calendar

    periods: list[CashflowPeriod] = []
    for i in range(len(date_list) - 1):
        unadj_start = date_list[i]
        unadj_end = date_list[i + 1]

        if _is_none_bdc(acc_bdc):
            accrual_start = unadj_start
            accrual_end = unadj_end
        else:
            accrual_start = adjust_business_day(unadj_start, acc_bdc, acc_cal)
            accrual_end = adjust_business_day(unadj_end, acc_bdc, acc_cal)

        if pay_lag:
            pay_anchor = add_business_days(unadj_end, pay_lag, pay_cal)
        else:
            pay_anchor = unadj_end

        if _is_none_bdc(bdc):
            payment_date = pay_anchor
        else:
            payment_date = adjust_business_day(pay_anchor, bdc, pay_cal)

        fixing_date = None
        if fixing_lag is not None:
            fixing_date = add_business_days(accrual_start, -fixing_lag, fix_cal)

        periods.append(
            CashflowPeriod(
                unadjusted_start=unadj_start,
                unadjusted_end=unadj_end,
                accrual_start=accrual_start,
                accrual_end=accrual_end,
                payment_date=payment_date,
                fixing_date=fixing_date,
            )
        )

    return periods


def _is_none_bdc(rule: BusinessDayRule | str) -> bool:
    if isinstance(rule, BusinessDayRule):
        return rule == BusinessDayRule.NONE
    return str(rule).strip().upper() == "NONE"


def build_leg_schedule(
    start: DateLike,
    end: DateLike,
    spec: LegScheduleSpec,
) -> tuple[CashflowPeriod, ...]:
    calendar = spec.calendar if spec.calendar is not None else BusinessCalendar()
    unadjusted = build_unadjusted_schedule_dates(
        start,
        end,
        spec.freq,
        stub_type=spec.stub_type,
        first_date=spec.first_date,
        last_date=spec.last_date,
        eom=spec.eom,
    )
    periods = build_cashflow_periods(
        unadjusted,
        calendar=calendar,
        bdc=spec.bdc,
        pay_lag=spec.pay_lag,
        accrual_bdc=spec.accrual_bdc,
        accrual_calendar=spec.accrual_calendar,
        fixing_lag=spec.fixing_lag,
        fixing_calendar=spec.fixing_calendar,
    )
    return tuple(periods)


def build_swap_schedule(
    effective_date: DateLike,
    maturity_date: DateLike,
    *,
    fixed_leg: LegScheduleSpec,
    float_leg: LegScheduleSpec,
) -> SwapSchedule:
    fixed_periods = build_leg_schedule(effective_date, maturity_date, fixed_leg)
    float_periods = build_leg_schedule(effective_date, maturity_date, float_leg)
    return SwapSchedule(fixed_leg=fixed_periods, float_leg=float_periods)


def build_bond_schedule(
    issue_date: DateLike,
    maturity_date: DateLike,
    *,
    coupon_leg: LegScheduleSpec,
) -> tuple[CashflowPeriod, ...]:
    return build_leg_schedule(issue_date, maturity_date, coupon_leg)
