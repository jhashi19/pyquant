from datetime import date

from app.engine.math.bizday import BusinessCalendar, add_business_days, adjust_business_day


def test_following_and_modified_following():
    cal = BusinessCalendar.from_holidays([])
    d = date(2024, 3, 31)  # Sunday
    assert adjust_business_day(d, "FOLLOWING", cal) == date(2024, 4, 1)
    assert adjust_business_day(d, "MOD_FOLLOWING", cal) == date(2024, 3, 29)


def test_nearest_with_tiebreaker():
    cal = BusinessCalendar.from_holidays(
        [date(2024, 2, 13), date(2024, 2, 14), date(2024, 2, 15)]
    )
    d = date(2024, 2, 14)
    assert adjust_business_day(d, "NEAREST", cal, nearest_tiebreaker="PREV") == date(
        2024, 2, 12
    )


def test_add_business_days():
    cal = BusinessCalendar.from_holidays([date(2024, 1, 1)])
    assert add_business_days(date(2024, 1, 5), 1, cal) == date(2024, 1, 8)
