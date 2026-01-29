import math

from app.engine.math.interpolation import Curve


def test_log_linear_df_interpolation():
    x = [1.0, 2.0]
    df = [0.99, 0.97]
    curve = Curve.from_nodes(x, df_nodes=df, interp_method="LOG_LINEAR_DF")
    t = 1.5
    expected = math.exp(
        math.log(df[0]) + (math.log(df[1]) - math.log(df[0])) * 0.5
    )
    assert math.isclose(curve.df(t), expected)
    assert math.isclose(curve.value(t), expected)


def test_linear_zero_interpolation():
    x = [1.0, 2.0]
    zeros = [0.02, 0.04]
    curve = Curve.from_nodes(x, zero_nodes=zeros, interp_method="LINEAR_ZERO")
    assert math.isclose(curve.zero_rate(1.5), 0.03)
    assert math.isclose(curve.value(1.5), 0.03)


def test_flat_forward_extrapolation_right():
    x = [1.0, 2.0]
    df = [0.98, 0.95]
    curve = Curve.from_nodes(
        x, df_nodes=df, interp_method="LOG_LINEAR_DF", extrap_right="FLAT_FWD"
    )
    t = 3.0
    m = (math.log(df[1]) - math.log(df[0])) / (x[1] - x[0])
    expected = df[1] * math.exp(m * (t - x[1]))
    assert math.isclose(curve.df(t), expected)
