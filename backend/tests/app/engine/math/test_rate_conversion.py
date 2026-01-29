import math

from app.engine.math.rate_conversion import (
    convert_rate,
    discount_factor,
    forward_rate_from_dfs,
    zero_rate_from_df,
)


def test_continuous_df_roundtrip():
    r = 0.05
    t = 2.0
    df = discount_factor(r, t, "CONT")
    assert math.isclose(zero_rate_from_df(df, t, "CONT"), r)


def test_simple_df_roundtrip():
    r = 0.1
    t = 0.5
    df = discount_factor(r, t, "SIMPLE")
    assert math.isclose(zero_rate_from_df(df, t, "SIMPLE"), r)


def test_convert_rate_between_conventions():
    r = 0.03
    t = 1.5
    simple = convert_rate(r, t, "CONT", "SIMPLE")
    df = discount_factor(r, t, "CONT")
    assert math.isclose(simple, zero_rate_from_df(df, t, "SIMPLE"))


def test_forward_rate_from_dfs():
    t = 0.5
    f = 0.02
    df0 = 1.0
    df1 = discount_factor(f, t, "SIMPLE")
    assert math.isclose(forward_rate_from_dfs(df0, df1, t, "SIMPLE"), f)


def test_discrete_compounding_roundtrip():
    r = 0.05
    t = 1.0
    df = discount_factor(r, t, "DISCRETE", freq=2)
    assert math.isclose(zero_rate_from_df(df, t, "DISCRETE", freq=2), r)
