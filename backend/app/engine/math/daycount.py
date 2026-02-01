from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Callable

from .date_utils import DateLike, days_in_year, to_date


class DayCountConvention(str, Enum):
    ACT_360 = "ACT_360"
    ACT_365F = "ACT_365F"
    ACT_ACT_ISDA = "ACT_ACT_ISDA"
    THIRTY_E_360 = "THIRTY_E_360"
    THIRTY_360_US = "THIRTY_360_US"


_ALIASES = {
    "ACT/360": DayCountConvention.ACT_360,
    "ACT_360": DayCountConvention.ACT_360,
    "ACT/365F": DayCountConvention.ACT_365F,
    "ACT_365F": DayCountConvention.ACT_365F,
    "ACT/365": DayCountConvention.ACT_365F,
    "ACT/ACT-ISDA": DayCountConvention.ACT_ACT_ISDA,
    "ACT_ACT_ISDA": DayCountConvention.ACT_ACT_ISDA,
    "30E/360": DayCountConvention.THIRTY_E_360,
    "30E_360": DayCountConvention.THIRTY_E_360,
    "THIRTY_E_360": DayCountConvention.THIRTY_E_360,
    "30/360": DayCountConvention.THIRTY_360_US,
    "30_360_US": DayCountConvention.THIRTY_360_US,
    "THIRTY_360_US": DayCountConvention.THIRTY_360_US,
}


def _normalize(convention: str) -> DayCountConvention:
    key = convention.strip().upper().replace("-", "_")
    if key in _ALIASES:
        return _ALIASES[key]
    raise ValueError(f"Unsupported daycount convention: {convention!r}")


def year_fraction(start: DateLike, end: DateLike, convention: str) -> float:
    start_date = to_date(start)
    end_date = to_date(end)
    if start_date == end_date:
        return 0.0
    sign = 1.0
    if end_date < start_date:
        start_date, end_date = end_date, start_date
        sign = -1.0

    tag = _normalize(convention)
    func: Callable[[date, date], float]
    match tag:
        case DayCountConvention.ACT_360:
            func = _act_360
        case DayCountConvention.ACT_365F:
            func = _act_365f
        case DayCountConvention.THIRTY_E_360:
            func = _thirty_e_360
        case DayCountConvention.ACT_ACT_ISDA:
            func = _act_act_isda
        case DayCountConvention.THIRTY_360_US:
            func = _thirty_360_us
        case _:
            raise ValueError(f"Unsupported daycount convention: {convention!r}")
    return sign * func(start_date, end_date)


def _act_360(start: date, end: date) -> float:
    return (end - start).days / 360.0


def _act_365f(start: date, end: date) -> float:
    return (end - start).days / 365.0


def _thirty_e_360(start: date, end: date) -> float:
    d1 = 30 if start.day == 31 else start.day
    d2 = 30 if end.day == 31 else end.day
    return (
        360 * (end.year - start.year)
        + 30 * (end.month - start.month)
        + (d2 - d1)
    ) / 360.0


def _thirty_360_us(start: date, end: date) -> float:
    d1 = start.day
    d2 = end.day
    if d1 == 31:
        d1 = 30
    if d2 == 31 and d1 == 30:
        d2 = 30
    return (
        360 * (end.year - start.year)
        + 30 * (end.month - start.month)
        + (d2 - d1)
    ) / 360.0


def _act_act_isda(start: date, end: date) -> float:
    total = 0.0
    current = start
    while current < end:
        year_end = date(current.year + 1, 1, 1)
        segment_end = min(year_end, end)
        days = (segment_end - current).days
        total += days / days_in_year(current.year)
        current = segment_end
    return total
