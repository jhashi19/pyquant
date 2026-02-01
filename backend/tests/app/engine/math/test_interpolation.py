import math
import numpy as np
import pytest
from app.engine.math.interpolation import CurveInterpolator

# Business scenarios for interpolation
@pytest.mark.parametrize("method", ["LOG_LINEAR", "LINEAR", "CUBIC_SPLINE", "MONOTONE_CONVEX_SPLINE"])
def test_curve_nodes_consistency(method):
    """
    Business Rule: The curve must return the exact input values at the nodes.
    """
    x_nodes = [1.0, 2.0, 3.0, 5.0]
    df_nodes = [0.95, 0.90, 0.85, 0.75]
    
    curve = CurveInterpolator.from_nodes(x_nodes, df_nodes=df_nodes, interp_method=method)
    
    # Check DFs at nodes
    calculated_dfs = curve.df(x_nodes)
    np.testing.assert_allclose(calculated_dfs, df_nodes, rtol=1e-10)

def test_curve_zero_rate_roundtrip():
    """
    Business Rule: Constructing a curve from zero rates should yield the same zero rates at nodes.
    """
    x_nodes = [0.5, 1.0, 2.0]
    zero_rates = [0.01, 0.015, 0.02]
    
    curve = CurveInterpolator.from_nodes(
        x_nodes, zero_nodes=zero_rates, interp_method="LINEAR", compounding="CONTINUOUS"
    )
    
    calculated_zeros = curve.zero_rate(x_nodes)
    np.testing.assert_allclose(calculated_zeros, zero_rates, rtol=1e-10)

def test_log_linear_df_behavior():
    """
    Business Rule: LOG_LINEAR_DF implies constant forward rate between nodes (exponential decay of DF).
    DF(t) = DF(t1) * exp(-r * (t - t1))
    """
    x = [1.0, 2.0]
    df = [0.9, 0.8] # r approx 10% and 11%
    curve = CurveInterpolator.from_nodes(x, df_nodes=df, interp_method="LOG_LINEAR")
    
    t_mid = 1.5
    # Expected: Log-linear interpolation of DF
    # ln(DF(t)) is linear between ln(DF(1)) and ln(DF(2))
    expected_log_df = 0.5 * (math.log(df[0]) + math.log(df[1]))
    expected_df = math.exp(expected_log_df)
    
    assert math.isclose(curve.df(t_mid), expected_df, rel_tol=1e-10)

def test_linear_zero_behavior():
    """
    Business Rule: LINEAR_ZERO implies the zero rate is linearly interpolated.
    """
    x = [1.0, 2.0]
    zeros = [0.03, 0.04]
    curve = CurveInterpolator.from_nodes(
        x, zero_nodes=zeros, interp_method="LINEAR", compounding="CONTINUOUS"
    )
    
    t_mid = 1.5
    expected_zero = 0.035
    assert math.isclose(curve.zero_rate(t_mid), expected_zero, rel_tol=1e-10)

def test_flat_forward_extrapolation_log_linear():
    """
    Business Rule: For Log-Linear DF interpolation, Flat Forward extrapolation means
    extending the exponential decay rate (slope of log DF) from the last segment.
    """
    x = [1.0, 2.0]
    df = [math.exp(-0.03), math.exp(-0.08)] # r1=3%, r2=4% (avg) -> fwd approx 5%
    
    curve = CurveInterpolator.from_nodes(
        x, df_nodes=df, interp_method="LOG_LINEAR", extrap_right="FLAT_FWD"
    )
    
    t_test = 3.0
    # Forward rate between 1 and 2:
    # f = - (ln(DF2) - ln(DF1)) / (2 - 1)
    fwd_rate = -(math.log(df[1]) - math.log(df[0]))
    
    # DF(3) = DF(2) * exp(-f * (3-2))
    expected_df = df[1] * math.exp(-fwd_rate * (3.0 - 2.0))
    
    assert math.isclose(curve.df(t_test), expected_df, rel_tol=1e-10)

def test_curve_vectorization():
    """
    Technical Rule: The curve should handle array inputs efficiently.
    """
    x = [1.0, 2.0]
    df = [0.95, 0.90]
    curve = CurveInterpolator.from_nodes(x, df_nodes=df)
    
    t_in = np.array([1.0, 1.5, 2.0])
    df_out = curve.df(t_in)
    
    assert len(df_out) == 3
    assert math.isclose(df_out[0], 0.95)
    assert math.isclose(df_out[2], 0.90)

def test_invalid_inputs():
    """
    Business Rule: Invalid curve definitions should be rejected.
    """
    with pytest.raises(ValueError, match="strictly increasing"):
        CurveInterpolator.from_nodes([2.0, 1.0], df_nodes=[0.9, 0.8])
        
    with pytest.raises(ValueError, match="positive"):
        CurveInterpolator.from_nodes([1.0], df_nodes=[-0.5])

    with pytest.raises(ValueError, match="exactly one"):
        CurveInterpolator.from_nodes([1.0], df_nodes=[0.9], zero_nodes=[0.1])


def test_value_returns_input_kind():
    x_nodes = [1.0, 2.0]
    df_nodes = [0.95, 0.9]
    curve_df = CurveInterpolator.from_nodes(x_nodes, df_nodes=df_nodes)
    assert math.isclose(curve_df.value(1.0), df_nodes[0])

    zero_nodes = [0.01, 0.02]
    curve_zero = CurveInterpolator.from_nodes(x_nodes, zero_nodes=zero_nodes, interp_method="LINEAR")
    assert math.isclose(curve_zero.value(2.0), zero_nodes[1])
