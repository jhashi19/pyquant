from datetime import date

from app.engine.math.bizday import BusinessCalendar
from app.engine.products.schedule_utils import (
    build_cashflow_periods,
    build_unadjusted_schedule_dates,
)


def data_factory_dates(
    *,
    start: date,
    end: date,
    tenor: str,
    stub_type: str = "BACK",
) -> list[date]:
    return build_unadjusted_schedule_dates(start, end, tenor, stub_type=stub_type)


def data_factory_periods(
    *,
    dates: list[date],
    calendar: BusinessCalendar,
    payment_calendar: BusinessCalendar | None = None,
    bdc: str = "FOLLOWING",
    pay_lag: int = 0,
    accrual_bdc: str | None = None,
):
    return build_cashflow_periods(
        dates,
        calendar=calendar,
        payment_calendar=payment_calendar,
        bdc=bdc,
        pay_lag=pay_lag,
        accrual_bdc=accrual_bdc,
    )


def test_monthly_schedule_keeps_end_of_month_anchor() -> None:
    dates = data_factory_dates(
        start=date(2025, 1, 31),
        end=date(2025, 4, 30),
        tenor="1M",
        stub_type="BACK",
    )
    assert dates == [
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 31),
        date(2025, 4, 30),
    ]


def test_payment_lag_is_applied_from_adjusted_accrual_end() -> None:
    periods = data_factory_periods(
        dates=[date(2026, 1, 15), date(2026, 2, 15)],
        calendar=BusinessCalendar(),
        bdc="MOD_FOLLOWING",
        pay_lag=1,
        accrual_bdc="MOD_FOLLOWING",
    )

    period = periods[0]
    assert period.accrual_end == date(2026, 2, 16)
    assert period.payment_date == date(2026, 2, 17)


def test_payment_calendar_can_differ_from_accrual_calendar() -> None:
    accrual_calendar = BusinessCalendar()
    payment_calendar = BusinessCalendar.from_holidays([date(2026, 7, 1)])

    periods = data_factory_periods(
        dates=[date(2026, 6, 1), date(2026, 7, 1)],
        calendar=accrual_calendar,
        payment_calendar=payment_calendar,
        bdc="FOLLOWING",
        pay_lag=0,
        accrual_bdc="NONE",
    )

    period = periods[0]
    assert period.accrual_end == date(2026, 7, 1)
    assert period.payment_date == date(2026, 7, 2)


def test_long_back_stub_merges_last_short_period() -> None:
    back = data_factory_dates(
        start=date(2026, 1, 1),
        end=date(2026, 10, 20),
        tenor="3M",
        stub_type="BACK",
    )
    long_back = data_factory_dates(
        start=date(2026, 1, 1),
        end=date(2026, 10, 20),
        tenor="3M",
        stub_type="LONG_BACK",
    )

    assert len(long_back) == len(back) - 1
    assert long_back[-1] == date(2026, 10, 20)
    assert long_back[-2] == date(2026, 7, 1)
