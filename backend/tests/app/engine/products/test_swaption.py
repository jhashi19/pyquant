from datetime import date
from typing import Sequence

import numpy as np
import pytest

from app.engine.market.yield_curve import YieldCurve
from app.engine.math.bizday import BusinessCalendar
from app.engine.math.daycount import year_fraction
from app.engine.products.models.schedule_models import (
    ModelParamRow,
    SabrInterpolationSpec,
    TradeHeader,
    TradeSwaption,
)
from app.engine.products.pricing_model import PricingModelConfig
from app.engine.products.sabr_interpolation import SwaptionAtmSabrInterpolator
from app.engine.products.swaption import (
    MarketSnapshot,
    SwaptionDataProvider,
    SwaptionPricingData,
    SwaptionPricingInput,
    VolSwaptionPoint,
    load_swaption_pricing_data,
    price_swaption_from_data,
)


def _shifted_black_call(
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
    if sigma <= 0.0 or expiry <= 0.0:
        return float(max(f - k, 0.0))
    std = sigma * np.sqrt(expiry)
    d1 = (np.log(f / k) + 0.5 * std * std) / std
    d2 = d1 - std
    return float(f * ndtr(d1) - k * ndtr(d2))


class _StubProvider(SwaptionDataProvider):
    def __init__(
        self,
        *,
        option_style: str = "EUROPEAN",
        settlement: str = "PHYS",
        cash_settle_method: str | None = None,
        buy_sell: str = "BUY",
    ) -> None:
        self._trade = TradeHeader(
            trade_id="SWPT_1",
            product="SWAPTION",
            ccy="USD",
            notional=10_000_000.0,
            buy_sell=buy_sell,
            trade_date=date(2026, 1, 2),
            effective_date=date(2026, 1, 4),
            maturity_date=date(2032, 1, 4),
            pricing_profile_id="STD",
        )
        self._trade_swaption = TradeSwaption(
            trade_id="SWPT_1",
            ccy="USD",
            option_style=option_style,
            cp_flag="C",
            expiry_date=date(2027, 1, 4),
            exercise_open=None,
            exercise_close=None,
            settlement=settlement,
            cash_settle_method=cash_settle_method,
            cash_settle_lag_bd=2 if settlement == "CASH" else None,
            cash_settle_bdc="MF" if settlement == "CASH" else None,
            cash_settle_cal_id="USNY" if settlement == "CASH" else None,
            swap_pay_rec="PAY",
            swap_fixed_rate=0.026,
            swap_fixed_dc="ACT/365F",
            swap_fixed_freq="6M",
            swap_fixed_bdc="MF",
            swap_fixed_cal="USNY",
            swap_index_id="USD-SOFR-3M",
            swap_index_tenor="3M",
            swap_spread=0.0,
            swap_float_dc="ACT/365F",
            swap_float_freq="3M",
            swap_float_bdc="MF",
            swap_float_cal="USNY",
            swap_start_date=None,
            swap_spot_lag_bd=2,
            swap_maturity=date(2032, 1, 4),
        )
        self._snapshot = MarketSnapshot(snapshot_id="SNAP_1", as_of=date(2026, 1, 2))
        self._curve = YieldCurve.from_nodes(
            [0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
            zero_nodes=[0.03, 0.03, 0.03, 0.03, 0.03, 0.03],
            interp_method="LINEAR",
        )
        self._vol_points = (
            VolSwaptionPoint(
                vol_id="V1",
                snapshot_id="SNAP_1",
                ccy="USD",
                ref_rate_id="USD-SOFR-3M",
                index_tenor="3M",
                expiry_tenor="1Y",
                expiry_date=None,
                swap_tenor="5Y",
                x_years=1.0,
                vol_daycount="ACT/365F",
                smile_type="ATM",
                strike_rate=None,
                moneyness=None,
                quote_type="SLN_VOL",
                quote_shift=0.02,
                sabr_shift=0.02,
                sigma=0.25,
            ),
        )

    def get_trade(self, trade_id: str) -> TradeHeader:
        return self._trade

    def get_trade_swaption(self, trade_id: str) -> TradeSwaption:
        return self._trade_swaption

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        return self._snapshot

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve:
        return self._curve

    def get_vol_swaption(
        self,
        *,
        snapshot_id: str,
        ccy: str,
        ref_rate_id: str,
        index_tenor: str,
        quote_type: str,
        surface_tag: str | None = None,
    ) -> Sequence[VolSwaptionPoint]:
        return self._vol_points

    def get_model_params(
        self,
        *,
        snapshot_id: str,
        model_tag: str,
        scope: str,
        param_key: str,
    ) -> Sequence[ModelParamRow]:
        if scope != "GLOBAL":
            return ()
        return (
            ModelParamRow(
                snapshot_id=snapshot_id,
                model_tag=model_tag,
                scope="GLOBAL",
                param_key="GLOBAL",
                expiry_tenor=None,
                expiry_date=None,
                x_years=None,
                swap_tenor=None,
                strike_rate=None,
                moneyness=None,
                param_name="alpha",
                param_val=0.02,
            ),
            ModelParamRow(
                snapshot_id=snapshot_id,
                model_tag=model_tag,
                scope="GLOBAL",
                param_key="GLOBAL",
                expiry_tenor=None,
                expiry_date=None,
                x_years=None,
                swap_tenor=None,
                strike_rate=None,
                moneyness=None,
                param_name="beta",
                param_val=0.5,
            ),
            ModelParamRow(
                snapshot_id=snapshot_id,
                model_tag=model_tag,
                scope="GLOBAL",
                param_key="GLOBAL",
                expiry_tenor=None,
                expiry_date=None,
                x_years=None,
                swap_tenor=None,
                strike_rate=None,
                moneyness=None,
                param_name="rho",
                param_val=-0.1,
            ),
            ModelParamRow(
                snapshot_id=snapshot_id,
                model_tag=model_tag,
                scope="GLOBAL",
                param_key="GLOBAL",
                expiry_tenor=None,
                expiry_date=None,
                x_years=None,
                swap_tenor=None,
                strike_rate=None,
                moneyness=None,
                param_name="nu",
                param_val=0.4,
            ),
            ModelParamRow(
                snapshot_id=snapshot_id,
                model_tag=model_tag,
                scope="GLOBAL",
                param_key="GLOBAL",
                expiry_tenor=None,
                expiry_date=None,
                x_years=None,
                swap_tenor=None,
                strike_rate=None,
                moneyness=None,
                param_name="shift",
                param_val=0.02,
            ),
        )

    def get_business_calendar(self, cal_id: str) -> BusinessCalendar:
        return BusinessCalendar()

    def get_pricing_models(
        self,
        *,
        profile_id: str,
        product: str,
    ) -> Sequence[PricingModelConfig]:
        return (
            PricingModelConfig(
                profile_id="STD",
                product="SWAPTION",
                scope="GLOBAL",
                scope_key="GLOBAL",
                pricing_model="SHIFTED_BLACK",
                vol_interp_model="SHIFTED_SABR",
                model_tag="SABR_SHIFTED",
                vol_quote_type="SLN_VOL",
                surface_tag=None,
            ),
        )

    def get_sabr_interpolation_spec(
        self,
        *,
        product: str,
        model_tag: str,
    ) -> SabrInterpolationSpec:
        return SabrInterpolationSpec(
            product="SWAPTION",
            model_tag=model_tag,
            beta_strategy="FIXED",
            beta_fixed_value=0.5,
        )


def data_factory(
    *,
    option_style: str = "EUROPEAN",
    settlement: str = "PHYS",
    cash_settle_method: str | None = None,
    buy_sell: str = "BUY",
    pricing: SwaptionPricingInput | None = None,
) -> SwaptionPricingData:
    provider = _StubProvider(
        option_style=option_style,
        settlement=settlement,
        cash_settle_method=cash_settle_method,
        buy_sell=buy_sell,
    )
    pricing_input = pricing or SwaptionPricingInput(
        discount_curve_id="USD_DISC",
        forward_curve_id="USD_FWD",
        discount_daycount="ACT/365F",
        forward_daycount="ACT/365F",
    )
    return load_swaption_pricing_data(
        provider,
        run_id="RUN_1",
        trade_id="SWPT_1",
        snapshot_id="SNAP_1",
        pricing=pricing_input,
    )


def test_swaption_price_european_physical_matches_shifted_black_formula() -> None:
    data = data_factory(settlement="PHYS")
    result = price_swaption_from_data(data)

    expiry = year_fraction(data.as_of, data.trade_swaption.expiry_date, data.pricing.forward_daycount)
    option_value = _shifted_black_call(
        result.forward_swap_rate,
        result.strike_rate,
        result.implied_vol,
        expiry,
        shift=0.02,
    )
    expected = data.trade.notional * result.annuity * option_value
    assert abs(result.pv - expected) < 1e-6


def test_swaption_price_cash_settlement_branches_run() -> None:
    data_par = data_factory(settlement="CASH", cash_settle_method="PAR_YIELD_ANN")
    out_par = price_swaption_from_data(data_par)
    assert np.isfinite(out_par.pv)
    assert out_par.settlement_date > data_par.trade_swaption.expiry_date

    data_disc = data_factory(settlement="CASH", cash_settle_method="DISCOUNTED_SWAP_PV")
    out_disc = price_swaption_from_data(data_disc)
    assert np.isfinite(out_disc.pv)
    assert abs(out_par.annuity - out_disc.annuity) > 1e-12


def test_swaption_sell_position_changes_sign() -> None:
    buy = price_swaption_from_data(data_factory(buy_sell="BUY"))
    sell = price_swaption_from_data(data_factory(buy_sell="SELL"))
    assert buy.pv > 0.0
    assert sell.pv < 0.0


def test_swaption_non_european_entrypoint_is_reserved() -> None:
    data = data_factory(option_style="BERMUDAN")
    with pytest.raises(NotImplementedError):
        price_swaption_from_data(data)


def test_swaption_price_bachelier_branch_runs() -> None:
    data = data_factory(
        pricing=SwaptionPricingInput(
            discount_curve_id="USD_DISC",
            forward_curve_id="USD_FWD",
            discount_daycount="ACT/365F",
            forward_daycount="ACT/365F",
            pricing_model="BACHELIER",
        )
    )
    out = price_swaption_from_data(data)
    assert np.isfinite(out.pv)
    assert np.isfinite(out.implied_vol)


def test_swaption_load_reuses_sabr_interpolator_from_cache() -> None:
    provider = _StubProvider()
    pricing_input = SwaptionPricingInput(
        discount_curve_id="USD_DISC",
        forward_curve_id="USD_FWD",
        discount_daycount="ACT/365F",
        forward_daycount="ACT/365F",
    )
    cache: dict[tuple[str, ...], SwaptionAtmSabrInterpolator] = {}
    data1 = load_swaption_pricing_data(
        provider,
        run_id="RUN_1",
        trade_id="SWPT_1",
        snapshot_id="SNAP_1",
        pricing=pricing_input,
        sabr_interpolator_cache=cache,
    )
    data2 = load_swaption_pricing_data(
        provider,
        run_id="RUN_2",
        trade_id="SWPT_1",
        snapshot_id="SNAP_1",
        pricing=pricing_input,
        sabr_interpolator_cache=cache,
    )
    assert len(cache) == 1
    assert data1.sabr_interpolator is data2.sabr_interpolator
