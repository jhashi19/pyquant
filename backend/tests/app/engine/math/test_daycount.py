from datetime import date

from app.engine.math.daycount import year_fraction


def test_act_360():
    assert year_fraction(date(2024, 1, 1), date(2024, 1, 31), "ACT/360") == 30 / 360


def test_act_365f():
    assert year_fraction(date(2023, 1, 1), date(2023, 7, 1), "ACT/365F") == 181 / 365


def test_thirty_e_360():
    frac = year_fraction(date(2024, 1, 31), date(2024, 2, 29), "30E/360")
    assert frac == (360 * 0 + 30 * 1 + (30 - 30)) / 360


def test_act_act_isda():
    frac = year_fraction(date(2023, 12, 31), date(2024, 1, 2), "ACT/ACT-ISDA")
    assert frac == (1 / 365) + (1 / 366)
