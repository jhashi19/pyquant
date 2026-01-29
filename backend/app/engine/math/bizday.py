from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Iterable, Optional

from .date_utils import DateLike, to_date


DEFAULT_WEEKEND = frozenset({5, 6})  # Saturday=5, Sunday=6


class BusinessDayRule(Enum):
    NONE = "NONE"
    FOLLOWING = "FOLLOWING"
    PRECEDING = "PRECEDING"
    MOD_FOLLOWING = "MOD_FOLLOWING"
    MOD_PRECEDING = "MOD_PRECEDING"
    NEAREST = "NEAREST"


@dataclass(frozen=True)
class BusinessCalendar:
    holidays: frozenset[date] = field(default_factory=frozenset)
    weekend: frozenset[int] = field(default_factory=lambda: DEFAULT_WEEKEND)

    @classmethod
    def from_holidays(
        cls, holidays: Iterable[DateLike], weekend: Optional[Iterable[int]] = None
    ) -> "BusinessCalendar":
        holiday_set = frozenset(to_date(h) for h in holidays)
        if weekend is None:
            weekend_set = DEFAULT_WEEKEND
        else:
            weekend_set = frozenset(int(d) for d in weekend)
        return cls(holidays=holiday_set, weekend=weekend_set)

    def is_business_day(self, value: DateLike) -> bool:
        d = to_date(value)
        if d.weekday() in self.weekend:
            return False
        return d not in self.holidays

    def adjust(
        self,
        value: DateLike,
        rule_tag: BusinessDayRule | str,
        *,
        nearest_tiebreaker: str = "NEXT",
    ) -> date:
        return adjust_business_day(
            value, rule_tag, self, nearest_tiebreaker=nearest_tiebreaker
        )

    def add_business_days(self, value: DateLike, days: int) -> date:
        return add_business_days(value, days, self)


def _next_business_day(d: date, calendar: BusinessCalendar) -> date:
    current = d
    while not calendar.is_business_day(current):
        current += timedelta(days=1)
    return current


def _prev_business_day(d: date, calendar: BusinessCalendar) -> date:
    current = d
    while not calendar.is_business_day(current):
        current -= timedelta(days=1)
    return current


def adjust_business_day(
    value: DateLike,
    rule_tag: BusinessDayRule | str,
    calendar: BusinessCalendar,
    *,
    nearest_tiebreaker: str = "NEXT",
) -> date:
    d = to_date(value)
    tag = _normalize_rule(rule_tag)
    if tag == BusinessDayRule.NONE:
        return d
    if calendar.is_business_day(d):
        return d

    match tag:
        case BusinessDayRule.FOLLOWING:
            return _next_business_day(d, calendar)
        case BusinessDayRule.PRECEDING:
            return _prev_business_day(d, calendar)
        case BusinessDayRule.MOD_FOLLOWING:
            next_bd = _next_business_day(d, calendar)
            if next_bd.month != d.month:
                return _prev_business_day(d, calendar)
            return next_bd
        case BusinessDayRule.MOD_PRECEDING:
            prev_bd = _prev_business_day(d, calendar)
            if prev_bd.month != d.month:
                return _next_business_day(d, calendar)
            return prev_bd
        case BusinessDayRule.NEAREST:
            prev_bd = _prev_business_day(d, calendar)
            next_bd = _next_business_day(d, calendar)
            dist_prev = (d - prev_bd).days
            dist_next = (next_bd - d).days
            if dist_prev < dist_next:
                return prev_bd
            if dist_next < dist_prev:
                return next_bd
            tie = nearest_tiebreaker.upper()
            if tie == "PREV":
                return prev_bd
            return next_bd
        case _:
            raise ValueError(f"Unsupported business day rule: {rule_tag!r}")


def _normalize_rule(rule: BusinessDayRule | str) -> BusinessDayRule:
    if isinstance(rule, BusinessDayRule):
        return rule
    key = str(rule).strip().upper()
    match key:
        case "NONE":
            return BusinessDayRule.NONE
        case "FOLLOWING" | "F":
            return BusinessDayRule.FOLLOWING
        case "PRECEDING" | "P":
            return BusinessDayRule.PRECEDING
        case "MOD_FOLLOWING" | "MF":
            return BusinessDayRule.MOD_FOLLOWING
        case "MOD_PRECEDING" | "MP":
            return BusinessDayRule.MOD_PRECEDING
        case "NEAREST" | "N":
            return BusinessDayRule.NEAREST
        case _:
            raise ValueError(f"Unsupported business day rule: {rule!r}")


def add_business_days(value: DateLike, days: int, calendar: BusinessCalendar) -> date:
    d = to_date(value)
    if days == 0:
        return d

    step = 1 if days > 0 else -1
    remaining = abs(days)
    current = d
    while remaining:
        current += timedelta(days=step)
        if calendar.is_business_day(current):
            remaining -= 1
    return current
