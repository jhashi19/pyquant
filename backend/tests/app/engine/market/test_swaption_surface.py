from datetime import date

import numpy as np

from app.engine.market.sabr import SabrParams, SabrVolType, sabr_implied_vol
from app.engine.market.swaption_cube import (
    MarketQuoteSwaption,
    VolSwaptionRowPayload,
    build_vol_swaption_rows_from_market_quotes,
    calibrate_shifted_sabr_params_for_swaption_cube,
    normalize_swaption_quote_to_sln,
)


def _black_shifted_option_value(
    forward: float,
    strike: float,
    sigma: float,
    expiry: float,
    *,
    shift: float,
) -> float:
    from scipy.special import ndtr  # type: ignore[import-untyped]

    f = forward + shift
    k = strike + shift
    std = sigma * np.sqrt(expiry)
    d1 = (np.log(f / k) + 0.5 * std * std) / std
    d2 = d1 - std
    return float(f * ndtr(d1) - k * ndtr(d2))


def _normal_option_value(forward: float, strike: float, sigma: float, expiry: float) -> float:
    from scipy.special import ndtr  # type: ignore[import-untyped]

    std = sigma * np.sqrt(expiry)
    x = (forward - strike) / std
    return float((forward - strike) * ndtr(x) + std * np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi))


def test_normalize_swaption_quote_lognormal_to_shifted_black_preserves_value() -> None:
    row = MarketQuoteSwaption(
        snapshot_id="SNAP_1",
        ccy="USD",
        ref_rate_id="USD-SOFR-3M",
        index_tenor="3M",
        cp_flag="C",
        expiry_tenor="2Y",
        expiry_date=date(2028, 1, 2),
        swap_tenor="5Y",
        x_years=2.0,
        vol_daycount="ACT/365F",
        smile_type="STRIKE",
        strike_rate=0.025,
        moneyness=None,
        forward_rate=0.022,
        quote_type="LN_VOL",
        sigma_mid=0.31,
    )

    out = normalize_swaption_quote_to_sln(row, normalized_quote_shift=0.02, sabr_shift=0.02)
    assert out.quote_type == "SLN_VOL"
    assert out.quote_shift == 0.02

    target = _black_shifted_option_value(0.022, 0.025, 0.31, 2.0, shift=0.0)
    rebuilt = _black_shifted_option_value(0.022, 0.025, out.sigma, 2.0, shift=0.02)
    assert abs(target - rebuilt) < 1e-10


def test_normalize_swaption_quote_normal_to_shifted_black_preserves_value() -> None:
    row = MarketQuoteSwaption(
        snapshot_id="SNAP_1",
        ccy="USD",
        ref_rate_id="USD-SOFR-3M",
        index_tenor="3M",
        cp_flag="C",
        expiry_tenor="1Y",
        expiry_date=date(2027, 1, 2),
        swap_tenor="5Y",
        x_years=1.0,
        vol_daycount="ACT/365F",
        smile_type="STRIKE",
        strike_rate=0.02,
        moneyness=None,
        forward_rate=0.021,
        quote_type="N_VOL",
        sigma_mid=0.008,
    )

    out = normalize_swaption_quote_to_sln(row, normalized_quote_shift=0.01, sabr_shift=0.01)
    target = _normal_option_value(0.021, 0.02, 0.008, 1.0)
    rebuilt = _black_shifted_option_value(0.021, 0.02, out.sigma, 1.0, shift=0.01)
    assert abs(target - rebuilt) < 1e-10


def test_build_rows_and_calibrate_shifted_sabr_for_swaption_cube_node() -> None:
    params = SabrParams(alpha=0.02, beta=0.5, rho=-0.2, nu=0.45, shift=0.02)
    strikes = np.array([0.01, 0.015, 0.02, 0.025, 0.03], dtype=float)
    forward = 0.02
    expiry = 2.0
    vols = sabr_implied_vol(strikes, forward, expiry, params, vol_type=SabrVolType.LOGNORMAL)

    raw_quotes = [
        MarketQuoteSwaption(
            snapshot_id="SNAP_1",
            ccy="USD",
            ref_rate_id="USD-SOFR-3M",
            index_tenor="3M",
            cp_flag="C",
            expiry_tenor="2Y",
            expiry_date=None,
            swap_tenor="5Y",
            x_years=expiry,
            vol_daycount="ACT/365F",
            smile_type="STRIKE",
            strike_rate=float(k),
            moneyness=None,
            forward_rate=forward,
            quote_type="SLN_VOL",
            quote_shift=params.shift,
            sigma_mid=float(v),
        )
        for k, v in zip(strikes, vols)
    ]
    vol_rows = build_vol_swaption_rows_from_market_quotes(
        raw_quotes,
        normalized_quote_shift=params.shift,
        sabr_shift=params.shift,
    )
    assert len(vol_rows) == len(strikes)

    out = calibrate_shifted_sabr_params_for_swaption_cube(
        vol_rows,
        forwards_by_node={(expiry, "5Y"): forward},
        model_tag="SABR_SHIFTED",
        scope="INDEX",
        param_key="USD-SOFR-3M",
    )

    assert len(out) == 5
    out_map = {row.param_name: row.param_val for row in out}
    assert abs(out_map["alpha"] - params.alpha) < 1e-5
    assert abs(out_map["beta"] - params.beta) < 1e-12
    assert abs(out_map["rho"] - params.rho) < 1e-5
    assert abs(out_map["nu"] - params.nu) < 1e-5
    assert abs(out_map["shift"] - params.shift) < 1e-12
