import math

import numpy as np
import pytest

from app.engine.market.sabr import (
    SabrCalibrationPreset,
    SabrParams,
    sabr_implied_vol,
    calibrate_sabr,
)


def test_sabr_lognormal_implied_vol_positive():
    forward = 0.025
    expiry = 5.0
    strikes = np.array([0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04], dtype=float)
    params = SabrParams(alpha=0.03, beta=0.5, rho=-0.2, nu=0.6)

    vols = sabr_implied_vol(strikes, forward, expiry, params, vol_type="LOGNORMAL")
    assert np.all(vols > 0.0)
    assert vols.shape == strikes.shape


def test_sabr_calibration_with_predefined_beta():
    forward = 0.025
    expiry = 5.0
    strikes = np.array([0.01, 0.015, 0.02, 0.025, 0.03, 0.035, 0.04], dtype=float)
    true_params = SabrParams(alpha=0.03, beta=0.5, rho=-0.2, nu=0.6)
    market_vols = sabr_implied_vol(strikes, forward, expiry, true_params, vol_type="LOGNORMAL")

    result = calibrate_sabr(
        strikes,
        market_vols,
        forward,
        expiry,
        vol_type="LOGNORMAL",
        preset=SabrCalibrationPreset.BETA_50,
    )

    assert result.success
    assert result.rmse < 1e-8
    assert math.isclose(result.params.beta, 0.5, rel_tol=0.0, abs_tol=1e-12)
    assert math.isclose(result.params.alpha, true_params.alpha, rel_tol=0.0, abs_tol=1e-5)
    assert math.isclose(result.params.rho, true_params.rho, rel_tol=0.0, abs_tol=1e-5)
    assert math.isclose(result.params.nu, true_params.nu, rel_tol=0.0, abs_tol=1e-5)


def test_sabr_calibration_with_custom_fixed_parameters():
    forward = 0.025
    expiry = 3.0
    strikes = np.array([0.015, 0.02, 0.025, 0.03, 0.035], dtype=float)
    true_params = SabrParams(alpha=0.022, beta=0.35, rho=-0.15, nu=0.45)
    market_vols = sabr_implied_vol(strikes, forward, expiry, true_params, vol_type="LOGNORMAL")

    result = calibrate_sabr(
        strikes,
        market_vols,
        forward,
        expiry,
        vol_type="LOGNORMAL",
        preset="FULL",
        fixed_params={"beta": true_params.beta, "rho": true_params.rho},
    )

    assert result.success
    assert result.rmse < 1e-8
    assert math.isclose(result.params.beta, true_params.beta, rel_tol=0.0, abs_tol=1e-12)
    assert math.isclose(result.params.rho, true_params.rho, rel_tol=0.0, abs_tol=1e-12)
    assert math.isclose(result.params.alpha, true_params.alpha, rel_tol=0.0, abs_tol=1e-5)
    assert math.isclose(result.params.nu, true_params.nu, rel_tol=0.0, abs_tol=1e-5)


def test_sabr_calibration_lm_requires_observation_count():
    forward = 0.025
    expiry = 2.0
    strikes = np.array([0.02, 0.025, 0.03], dtype=float)
    market_vols = np.array([0.25, 0.2, 0.21], dtype=float)

    with pytest.raises(ValueError):
        calibrate_sabr(
            strikes,
            market_vols,
            forward,
            expiry,
            preset="FULL",  # 4 free params but only 3 observations
        )


def test_shifted_sabr_supports_negative_forward_and_strikes():
    forward = -0.0025
    expiry = 2.0
    strikes = np.array([-0.01, -0.005, 0.0, 0.005], dtype=float)
    params = SabrParams(alpha=0.02, beta=0.5, rho=-0.1, nu=0.4, shift=0.03)

    vols = sabr_implied_vol(strikes, forward, expiry, params, vol_type="LOGNORMAL")
    assert np.all(vols > 0.0)


def test_shifted_sabr_calibration_with_negative_rates():
    forward = -0.001
    expiry = 4.0
    strikes = np.array([-0.01, -0.005, -0.001, 0.002, 0.006], dtype=float)
    true_params = SabrParams(alpha=0.018, beta=0.5, rho=-0.25, nu=0.55, shift=0.03)
    market_vols = sabr_implied_vol(strikes, forward, expiry, true_params, vol_type="LOGNORMAL")

    result = calibrate_sabr(
        strikes,
        market_vols,
        forward,
        expiry,
        vol_type="LOGNORMAL",
        preset=SabrCalibrationPreset.BETA_50,
        shift=true_params.shift,
    )

    assert result.success
    assert result.rmse < 1e-8
    assert math.isclose(result.shift, true_params.shift, rel_tol=0.0, abs_tol=1e-12)
    assert math.isclose(result.params.alpha, true_params.alpha, rel_tol=0.0, abs_tol=1e-5)
    assert math.isclose(result.params.rho, true_params.rho, rel_tol=0.0, abs_tol=1e-5)
    assert math.isclose(result.params.nu, true_params.nu, rel_tol=0.0, abs_tol=1e-5)
