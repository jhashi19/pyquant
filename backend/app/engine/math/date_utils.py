from __future__ import annotations

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
