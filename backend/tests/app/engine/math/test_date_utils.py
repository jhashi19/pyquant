from datetime import date, datetime

import pytest

from app.engine.math.date_utils import add_months, days_in_year, is_end_of_month, to_date


def test_add_months_keeps_end_of_month_when_anchor_is_month_end() -> None:
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_add_months_without_eom_flag_caps_day_to_month_end() -> None:
    assert add_months(date(2025, 1, 30), 1, eom=False) == date(2025, 2, 28)
    assert add_months(date(2025, 1, 30), 2, eom=False) == date(2025, 3, 30)


def test_to_date_accepts_date_datetime_and_iso_string() -> None:
    assert to_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert to_date(datetime(2026, 1, 2, 15, 30)) == date(2026, 1, 2)
    assert to_date("2026-01-02") == date(2026, 1, 2)


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2024, 366),
        (2025, 365),
    ],
)
def test_days_in_year(year: int, expected: int) -> None:
    assert days_in_year(year) == expected


def test_invalid_date_string_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid date string"):
        to_date("2026/01/02")


def test_is_end_of_month_detection() -> None:
    assert is_end_of_month(date(2025, 2, 28)) is True
    assert is_end_of_month(date(2025, 2, 27)) is False
