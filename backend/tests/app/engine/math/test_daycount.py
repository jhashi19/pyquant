from datetime import date
import pytest
from app.engine.math.daycount import year_fraction, DayCountConvention

@pytest.mark.parametrize("convention, start, end, expected", [
    ("ACT/360", date(2024, 1, 1), date(2024, 1, 31), 30.0 / 360.0),
    ("ACT/360", date(2024, 1, 1), date(2025, 1, 1), 366.0 / 360.0), # Leap year 2024
    ("ACT/365F", date(2024, 1, 1), date(2025, 1, 1), 366.0 / 365.0),
    ("30/360", date(2024, 1, 1), date(2024, 2, 1), 30.0 / 360.0), # Full month
])
def test_standard_conventions(convention, start, end, expected):
    """
    Business Rule: Verify standard day count calculations against expected fractions.
    """
    yf = year_fraction(start, end, convention)
    assert pytest.approx(yf, 1e-10) == expected

def test_30_360_us_end_of_month():
    """
    Business Rule: US 30/360 (Bond Basis) rules for end of month.
    - D1 is 31 -> 30
    - D2 is 31 and D1 is 30/31 -> 30
    """
    # Case 1: Start on 31st
    start = date(2024, 1, 31)
    end = date(2024, 2, 1) # 1 day later
    # D1=31->30, D2=1. Diff = 1 day.
    # Formula: 360*(Y2-Y1) + 30*(M2-M1) + (D2-D1)
    # 360*0 + 30*(2-1) + (1-30) = 30 - 29 = 1.
    assert year_fraction(start, end, "30/360") == 1.0 / 360.0

    # Case 2: End on 31st, Start on 30th
    start = date(2024, 4, 30)
    end = date(2024, 5, 31)
    # D1=30. D2=31. Since D1 is 30, D2 becomes 30.
    # 360*0 + 30*(5-4) + (30-30) = 30.
    assert year_fraction(start, end, "30/360") == 30.0 / 360.0

def test_30e_360_eurobond():
    """
    Business Rule: 30E/360 (Eurobond).
    - D1 is 31 -> 30
    - D2 is 31 -> 30
    """
    start = date(2024, 1, 31)
    end = date(2024, 2, 28) # Feb 28
    # D1=30, D2=28.
    # 30*(2-1) + (28-30) = 30 - 2 = 28.
    assert year_fraction(start, end, "30E/360") == 28.0 / 360.0

def test_act_act_isda_leap_year():
    """
    Business Rule: ACT/ACT ISDA splits calculation across leap and non-leap years.
    """
    # 2023 (365), 2024 (366)
    start = date(2023, 12, 31)
    end = date(2024, 1, 2)
    
    # 1 day in 2023 (Dec 31 to Jan 1) -> 1/365
    # 1 day in 2024 (Jan 1 to Jan 2) -> 1/366
    expected = (1.0 / 365.0) + (1.0 / 366.0)
    
    assert year_fraction(start, end, "ACT/ACT-ISDA") == pytest.approx(expected, 1e-10)

def test_same_date():
    assert year_fraction(date(2024, 1, 1), date(2024, 1, 1), "ACT/360") == 0.0

def test_negative_period():
    """
    Business Rule: End < Start should return negative year fraction.
    """
    start = date(2024, 1, 2)
    end = date(2024, 1, 1)
    assert year_fraction(start, end, "ACT/360") == -1.0 / 360.0
