from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Union


DateLike = Union[date, datetime, str]


def to_date(value: DateLike) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid date string: {value!r}") from exc
    raise TypeError(f"Unsupported date type: {type(value)!r}")


def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_in_year(year: int) -> int:
    return 366 if is_leap_year(year) else 365


def last_day_of_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]


def is_end_of_month(value: DateLike) -> bool:
    d = to_date(value)
    return d.day == last_day_of_month(d.year, d.month)


def add_months(value: DateLike, months: int, *, eom: bool | None = None) -> date:
    d = to_date(value)
    if months == 0:
        return d
    total = (d.month - 1) + months
    year = d.year + total // 12
    month = total % 12 + 1
    last = last_day_of_month(year, month)
    if eom is None:
        eom = is_end_of_month(d)
    day = last if eom else min(d.day, last)
    return date(year, month, day)
