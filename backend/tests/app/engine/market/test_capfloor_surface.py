from datetime import date

import numpy as np

from app.engine.market.capfloor_surface import (
    CapFloorTermVolQuote,
    MarketQuoteCapFloor,
    calibrate_shifted_sabr_params_by_expiry,
    build_vol_capfloor_rows_from_market_quotes,
    build_vol_capfloor_rows_from_stripped,
    strip_capfloor_term_quotes,
)
from app.engine.market.sabr import SabrParams, SabrVolType, sabr_implied_vol


def test_strip_capfloor_term_quotes_basic():
    quotes = [
        CapFloorTermVolQuote(x_years=0.5, sigma=0.20, strike_rate=0.02, expiry_date=date(2026, 7, 1)),
        CapFloorTermVolQuote(x_years=1.0, sigma=0.22, strike_rate=0.02, expiry_date=date(2027, 1, 1)),
        CapFloorTermVolQuote(x_years=1.5, sigma=0.24, strike_rate=0.02, expiry_date=date(2027, 7, 1)),
    ]
    forwards = [0.018, 0.019, 0.02]
    dfs = [0.995, 0.99, 0.985]
    accr = [0.5, 0.5, 0.5]
    stripped = strip_capfloor_term_quotes(
        quotes,
        forwards,
        dfs,
        accr,
        quote_type="SLN_VOL",
        quote_shift=0.02,
    )
    assert stripped.shape == (3,)
    assert np.all(np.isfinite(stripped))
    assert np.all(stripped > 0.0)


def test_build_vol_capfloor_rows_from_stripped():
    quotes = [
        CapFloorTermVolQuote(x_years=0.5, sigma=0.20, strike_rate=0.02, expiry_tenor="6M"),
        CapFloorTermVolQuote(x_years=1.0, sigma=0.22, strike_rate=0.02, expiry_tenor="1Y"),
    ]
    rows = build_vol_capfloor_rows_from_stripped(
        snapshot_id="SNAP_1",
        ccy="USD",
        ref_rate_id="USD-SOFR-3M",
        index_tenor="3M",
        quotes=quotes,
        stripped_optionlet_vols=[0.21, 0.23],
        quote_type="LN_VOL",
        sabr_shift=0.02,
    )
    assert len(rows) == 2
    assert rows[0].snapshot_id == "SNAP_1"
    assert rows[0].smile_type == "STRIKE"
    assert rows[0].quote_type == "LN_VOL"


def test_build_vol_capfloor_rows_from_market_quotes_optionlet_and_term():
    optionlet_quote = MarketQuoteCapFloor(
        snapshot_id="SNAP_1",
        ccy="USD",
        ref_rate_id="USD-SOFR-3M",
        index_tenor="3M",
        cp_flag="C",
        quote_kind="OPTIONLET_VOL",
        expiry_tenor="6M",
        expiry_date=None,
        x_years=0.5,
        vol_daycount="ACT/365F",
        smile_type="ATM",
        strike_rate=None,
        moneyness=None,
        quote_type="LN_VOL",
        sigma_mid=0.24,
    )
    term_quotes = [
        MarketQuoteCapFloor(
            snapshot_id="SNAP_1",
            ccy="USD",
            ref_rate_id="USD-SOFR-3M",
            index_tenor="3M",
            cp_flag="C",
            quote_kind="TERM_VOL",
            expiry_tenor="1Y",
            expiry_date=None,
            x_years=1.0,
            vol_daycount="ACT/365F",
            smile_type="STRIKE",
            strike_rate=0.02,
            moneyness=None,
            quote_type="LN_VOL",
            sigma_mid=0.25,
            annuity_factor=0.49,
            forward_rate=0.021,
        ),
        MarketQuoteCapFloor(
            snapshot_id="SNAP_1",
            ccy="USD",
            ref_rate_id="USD-SOFR-3M",
            index_tenor="3M",
            cp_flag="C",
            quote_kind="TERM_VOL",
            expiry_tenor="2Y",
            expiry_date=None,
            x_years=2.0,
            vol_daycount="ACT/365F",
            smile_type="STRIKE",
            strike_rate=0.02,
            moneyness=None,
            quote_type="LN_VOL",
            sigma_mid=0.27,
            annuity_factor=0.95,
            forward_rate=0.022,
        ),
    ]

    rows = build_vol_capfloor_rows_from_market_quotes(
        [optionlet_quote, *term_quotes],
        sabr_shift=0.02,
    )
    assert len(rows) == 3
    assert rows[0].snapshot_id == "SNAP_1"
    assert all(r.quote_type == "LN_VOL" for r in rows)


def test_build_vol_capfloor_rows_from_market_quotes_sln_requires_quote_shift():
    bad = MarketQuoteCapFloor(
        snapshot_id="SNAP_1",
        ccy="USD",
        ref_rate_id="USD-SOFR-3M",
        index_tenor="3M",
        cp_flag="C",
        quote_kind="OPTIONLET_VOL",
        expiry_tenor="1Y",
        expiry_date=None,
        x_years=1.0,
        vol_daycount="ACT/365F",
        smile_type="ATM",
        strike_rate=None,
        moneyness=None,
        quote_type="SLN_VOL",
        sigma_mid=0.25,
    )
    try:
        build_vol_capfloor_rows_from_market_quotes([bad], sabr_shift=0.0)
        assert False, "Expected ValueError for missing quote_shift."
    except ValueError as exc:
        assert "quote_shift" in str(exc)


def test_strip_capfloor_term_quotes_normal_quote_type():
    quotes = [
        CapFloorTermVolQuote(x_years=1.0, sigma=0.01, strike_rate=0.02, expiry_date=date(2027, 1, 1)),
        CapFloorTermVolQuote(x_years=2.0, sigma=0.011, strike_rate=0.02, expiry_date=date(2028, 1, 1)),
    ]
    forwards = [0.021, 0.022]
    dfs = [0.99, 0.97]
    accr = [0.5, 0.5]
    out = strip_capfloor_term_quotes(
        quotes,
        forwards,
        dfs,
        accr,
        quote_type="N_VOL",
    )
    assert out.shape == (2,)
    assert np.all(np.isfinite(out))


def test_calibrate_shifted_sabr_params_by_expiry():
    params = SabrParams(alpha=0.025, beta=0.5, rho=-0.2, nu=0.45, shift=0.02)
    expiries = [1.0, 2.0]
    forwards = {1.0: 0.022, 2.0: 0.024}
    strikes = [0.015, 0.02, 0.025]
    rows = []
    for x in expiries:
        fwd = forwards[x]
        vols = sabr_implied_vol(strikes, fwd, x, params, vol_type=SabrVolType.LOGNORMAL)
        for i, k in enumerate(strikes):
            rows.append(
                build_vol_capfloor_rows_from_stripped(
                    snapshot_id="SNAP_1",
                    ccy="USD",
                    ref_rate_id="USD-SOFR-3M",
                    index_tenor="3M",
                    quotes=[
                        CapFloorTermVolQuote(
                            x_years=x,
                            sigma=float(vols[i]),
                            strike_rate=float(k),
                            expiry_tenor="1Y" if x == 1.0 else "2Y",
                        )
                    ],
                    stripped_optionlet_vols=[float(vols[i])],
                    quote_type="LN_VOL",
                    sabr_shift=params.shift,
                )[0]
            )

    out = calibrate_shifted_sabr_params_by_expiry(
        rows,
        forwards_by_expiry=forwards,
        model_tag="SABR_SHIFTED",
        scope="INDEX",
        param_key="USD-SOFR-3M",
    )
    assert len(out) == 10
    assert {r.param_name for r in out} == {"alpha", "beta", "rho", "nu", "shift"}
    assert np.all(np.isfinite(np.array([r.param_val for r in out], dtype=float)))
