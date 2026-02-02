import math

from app.engine.market.yield_curve import YieldCurve


def test_yield_curve_delegates_interpolator():
    x = [1.0, 2.0]
    df = [0.95, 0.9]
    curve = YieldCurve.from_nodes(x, df_nodes=df, interp_method="LOG_LINEAR")
    assert math.isclose(curve.df(1.0), df[0])
    assert math.isclose(curve.value(2.0), df[1])


def test_yield_curve_value_with_zero_input():
    x = [1.0, 2.0]
    zeros = [0.01, 0.02]
    curve = YieldCurve.from_nodes(x, zero_nodes=zeros, interp_method="LINEAR")
    assert math.isclose(curve.value(2.0), zeros[1])
