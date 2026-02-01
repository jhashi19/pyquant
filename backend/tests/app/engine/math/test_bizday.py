from datetime import date
import pytest
from app.engine.math.bizday import (
    BusinessCalendar, 
    adjust_business_day, 
    add_business_days, 
    BusinessDayRule
)

@pytest.fixture
def jp_calendar():
    # Simple mock calendar: Weekends + specific holidays
    holidays = [
        date(2024, 1, 1), # New Year
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 5, 3), # Golden Week
        date(2024, 5, 4), # Saturday
        date(2024, 5, 5), # Sunday
        date(2024, 5, 6), # Observed
    ]
    return BusinessCalendar.from_holidays(holidays)

def test_is_business_day(jp_calendar):
    # Weekday
    assert jp_calendar.is_business_day(date(2024, 1, 4)) is True
    # Holiday
    assert jp_calendar.is_business_day(date(2024, 1, 1)) is False
    # Weekend (Saturday)
    assert jp_calendar.is_business_day(date(2024, 1, 6)) is False

@pytest.mark.parametrize("rule, input_date, expected_date", [
    ("FOLLOWING", date(2024, 1, 1), date(2024, 1, 4)), # 1,2,3 are holidays
    ("PRECEDING", date(2024, 1, 1), date(2023, 12, 29)), # Dec 29 (Fri)
    ("MOD_FOLLOWING", date(2024, 1, 1), date(2024, 1, 4)), # Same month, forward
])
def test_adjust_rules(jp_calendar, rule, input_date, expected_date):
    """
    Business Rule: Verify date rolling logic.
    """
    adj = adjust_business_day(input_date, rule, jp_calendar)
    assert adj == expected_date

def test_modified_following_month_boundary():
    """
    Business Rule: Modified Following should stay in the same month.
    """
    # Create a calendar where month-end is holiday
    # 2024-03-31 is Sunday. 2024-03-30 is Saturday. 2024-03-29 is Friday (Biz).
    cal = BusinessCalendar.from_holidays([])
    
    target = date(2024, 3, 31) # Sunday
    
    # Following would go to Apr 1
    assert adjust_business_day(target, "FOLLOWING", cal) == date(2024, 4, 1)
    
    # Modified Following should go back to Mar 29
    assert adjust_business_day(target, "MOD_FOLLOWING", cal) == date(2024, 3, 29)

def test_add_business_days(jp_calendar):
    """
    Business Rule: Adding business days should skip holidays and weekends.
    """
    start = date(2024, 1, 4) # Thursday
    # +1 -> Fri Jan 5
    assert add_business_days(start, 1, jp_calendar) == date(2024, 1, 5)
    
    # +2 -> Fri Jan 5 -> (Sat, Sun, Mon 8 is Holiday) -> Tue Jan 9
    # Note: Jan 8 is not a holiday in this mock calendar, so it lands on Jan 8 (Mon)
    assert add_business_days(start, 2, jp_calendar) == date(2024, 1, 8)

def test_add_business_days_negative(jp_calendar):
    start = date(2024, 1, 4) # Thursday
    # -1 -> Jan 3 (Hol) -> Jan 2 (Hol) -> Jan 1 (Hol) -> Dec 31 (Sun) -> Dec 30 (Sat) -> Dec 29 (Fri)
    assert add_business_days(start, -1, jp_calendar) == date(2023, 12, 29)
