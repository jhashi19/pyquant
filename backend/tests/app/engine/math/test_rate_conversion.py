import math
import numpy as np
import pytest
from app.engine.math.rate_conversion import (
    discount_factor,
    zero_rate_from_df,
    convert_rate,
    forward_rate_from_dfs,
    Compounding
)

@pytest.mark.parametrize("compounding", ["CONTINUOUS", "SIMPLE", "DISCRETE"])
def test_rate_df_roundtrip(compounding):
    """
    Business Rule: Converting Rate -> DF -> Rate should recover the original rate.
    """
    r_in = 0.05
    t = 2.5
    freq = 2 if compounding == "DISCRETE" else 1
    
    df = discount_factor(r_in, t, compounding, freq=freq)
    r_out = zero_rate_from_df(df, t, compounding, freq=freq)
    
    assert math.isclose(r_out, r_in, rel_tol=1e-10)

def test_simple_interest_mechanics():
    """
    Business Rule: Simple interest DF = 1 / (1 + r*t)
    """
    r = 0.10
    t = 0.5
    expected_df = 1 / (1 + 0.10 * 0.5) # 1 / 1.05
    
    assert math.isclose(discount_factor(r, t, "SIMPLE"), expected_df, rel_tol=1e-10)

def test_continuous_compounding_mechanics():
    """
    Business Rule: Continuous DF = exp(-r*t)
    """
    r = 0.10
    t = 2.0
    expected_df = math.exp(-0.10 * 2.0)
    
    assert math.isclose(discount_factor(r, t, "CONTINUOUS"), expected_df, rel_tol=1e-10)

def test_discrete_compounding_mechanics():
    """
    Business Rule: Discrete DF = 1 / (1 + r/freq)^(freq*t)
    """
    r = 0.10
    t = 2.0
    freq = 2 # Semiannual
    # (1 + 0.05)^4
    expected_df = 1 / ((1 + 0.05) ** 4)
    
    assert math.isclose(discount_factor(r, t, "DISCRETE", freq=freq), expected_df, rel_tol=1e-10)

def test_convert_rate_equivalence():
    """
    Business Rule: Converting a rate from one compounding to another should result in the same Discount Factor.
    """
    r_simple = 0.05
    t = 1.0
    
    # Convert Simple -> Continuous
    r_cont = convert_rate(r_simple, t, "SIMPLE", "CONTINUOUS")
    
    df_simple = discount_factor(r_simple, t, "SIMPLE")
    df_cont = discount_factor(r_cont, t, "CONTINUOUS")
    
    assert math.isclose(df_simple, df_cont, rel_tol=1e-10)

def test_forward_rate_calculation():
    """
    Business Rule: Forward rate is the rate implied by two DFs.
    DF(T2) / DF(T1) = DF_forward(T1, T2)
    """
    t1 = 1.0
    t2 = 2.0
    df1 = 0.95
    df2 = 0.90
    
    # Implied forward DF for period T2-T1
    implied_fwd_df = df2 / df1
    dt = t2 - t1
    
    # Calculate forward rate (Simple)
    fwd_rate = forward_rate_from_dfs(df1, df2, dt, "SIMPLE")
    
    # Verify: 1 / (1 + f * dt) should match implied_fwd_df
    recalc_fwd_df = discount_factor(fwd_rate, dt, "SIMPLE")
    
    assert math.isclose(recalc_fwd_df, implied_fwd_df, rel_tol=1e-10)

def test_zero_time_handling():
    """
    Business Rule: At t=0, DF is 1.0. Zero rate calculation should handle division by zero gracefully (usually 0.0 or limit).
    """
    df = 1.0
    t = 0.0
    # Should not raise error
    r = zero_rate_from_df(df, t, "CONTINUOUS")
    assert r == 0.0

def test_vectorization():
    """
    Technical Rule: Functions should handle numpy arrays.
    """
    r = np.array([0.01, 0.02])
    t = np.array([1.0, 2.0])
    
    dfs = discount_factor(r, t, "CONTINUOUS")
    assert isinstance(dfs, np.ndarray)
    assert len(dfs) == 2
    assert math.isclose(dfs[0], math.exp(-0.01))
