from datetime import date
from typing import Sequence

import numpy as np
import pytest

from app.engine.market.yield_curve import YieldCurve
from app.engine.math.bizday import BusinessCalendar
from app.engine.products.capfloor import (
    CapFloorDataProvider,
    CapFloorPricingData,
    CapFloorPricingInput,
    MarketSnapshot,
    VolCapFloorPoint,
    load_capfloor_pricing_data,
    price_capfloor_from_data,
)
from app.engine.products.models.schedule_models import (
    CapFloorScheduleRow,
    HistoricalFixing,
    ModelParamRow,
    RefRateRule,
    TradeCapFloor,
    TradeHeader,
)
from app.engine.products.pricing_model import PricingModelConfig


class _StubProvider(CapFloorDataProvider):
    def __init__(self, *, missing_fixing: bool = False) -> None:
        self._trade = TradeHeader(
            trade_id="CAP_1",
            product="CAPFLOOR",
            ccy="USD",
            notional=10_000_000.0,
            buy_sell="buy",
            trade_date=date(2026, 1, 1),
            effective_date=date(2026, 1, 1),
            maturity_date=date(2027, 1, 1),
            pricing_profile_id="STD",
        )
        self._cap = TradeCapFloor(
            trade_id="CAP_1",
            ccy="USD",
            cp_flag="C",
            index_id="USD-SOFR-3M",
            index_tenor="3M",
            strike_rate=0.02,
            start_date=date(2026, 1, 1),
            end_date=date(2027, 1, 1),
            pay_rec="REC",
            pay_freq="3M",
            pay_bdc="MF",
            pay_cal_id="USNY",
        )
        self._rule = RefRateRule(
            index_id="USD-SOFR-3M",
            rate_type="TERM",
            accrual_conv="SIMPLE",
            daycount="ACT/360",
            fixing_cal_id="USNY",
            fixing_bdc="MF",
            lookback_days=0,
        )
        self._snapshot = MarketSnapshot(snapshot_id="SNAP_1", as_of=date(2026, 2, 1))
        self._curve = YieldCurve.from_nodes(
            [0.25, 0.5, 1.0, 2.0],
            zero_nodes=[0.03, 0.03, 0.03, 0.03],
            interp_method="LINEAR",
        )
        self._vol = (
            VolCapFloorPoint(
                vol_id="V1",
                snapshot_id="SNAP_1",
                ccy="USD",
                ref_rate_id="USD-SOFR-3M",
                index_tenor="3M",
                expiry_tenor="6M",
                expiry_date=None,
                x_years=0.5,
                vol_daycount="ACT/365F",
                smile_type="ATM",
                strike_rate=None,
                quote_type="LN_VOL",
                sigma=0.25,
                sabr_shift=0.02,
            ),
            VolCapFloorPoint(
                vol_id="V2",
                snapshot_id="SNAP_1",
                ccy="USD",
                ref_rate_id="USD-SOFR-3M",
                index_tenor="3M",
                expiry_tenor="1Y",
                expiry_date=None,
                x_years=1.0,
                vol_daycount="ACT/365F",
                smile_type="ATM",
                strike_rate=None,
                quote_type="LN_VOL",
                sigma=0.26,
                sabr_shift=0.02,
            ),
        )
        self._schedule = (
            CapFloorScheduleRow(
                trade_id="CAP_1",
                cashflow_no=1,
                payment_date=date(2026, 6, 1),
                ccy="USD",
                cp_flag="C",
                pay_rec="REC",
                start_date=date(2026, 3, 1),
                end_date=date(2026, 6, 1),
                daycount="ACT/360",
                accrual_factor=0.25,
                notional=10_000_000.0,
                strike_rate=0.02,
                index_id="USD-SOFR-3M",
                rate_calc_type="IBOR_SINGLE",
                fixing_date=date(2026, 3, 1),
                obs_start_date=date(2026, 3, 1),
                obs_end_date=date(2026, 6, 1),
            ),
            CapFloorScheduleRow(
                trade_id="CAP_1",
                cashflow_no=2,
                payment_date=date(2026, 9, 1),
                ccy="USD",
                cp_flag="C",
                pay_rec="REC",
                start_date=date(2026, 6, 1),
                end_date=date(2026, 9, 1),
                daycount="ACT/360",
                accrual_factor=0.25,
                notional=10_000_000.0,
                strike_rate=0.02,
                index_id="USD-SOFR-3M",
                rate_calc_type="IBOR_SINGLE",
                fixing_date=date(2026, 6, 1),
                obs_start_date=date(2026, 6, 1),
                obs_end_date=date(2026, 9, 1),
            ),
        )
        self._missing_fixing = missing_fixing

    def get_trade(self, trade_id: str) -> TradeHeader:
        return self._trade

    def get_trade_capfloor(self, trade_id: str) -> TradeCapFloor:
        return self._cap

    def get_ref_rate_rule(self, index_id: str) -> RefRateRule:
        return self._rule

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        return self._snapshot

    def get_business_calendar(self, cal_id: str) -> BusinessCalendar:
        return BusinessCalendar()

    def get_schedule_capfloor(self, trade_id: str) -> Sequence[CapFloorScheduleRow]:
        return self._schedule

    def get_historical_fixings(
        self, index_id: str, start_date: date, end_date: date
    ) -> Sequence[HistoricalFixing]:
        if self._missing_fixing:
            return ()
        return (HistoricalFixing(index_id=index_id, fixing_date=start_date, rate=0.021),)

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve:
        return self._curve

    def get_vol_capfloor(
        self,
        *,
        snapshot_id: str,
        ccy: str,
        index_tenor: str,
        quote_type: str,
        ref_rate_id: str | None = None,
        surface_tag: str | None = None,
    ) -> Sequence[VolCapFloorPoint]:
        return self._vol

    def get_model_params(
        self,
        *,
        snapshot_id: str,
        model_tag: str,
        scope: str,
        param_key: str,
    ) -> Sequence[ModelParamRow]:
        if scope == "INDEX" and param_key == "USD-SOFR-3M":
            return (
                ModelParamRow(
                    snapshot_id=snapshot_id,
                    model_tag=model_tag,
                    scope=scope,
                    param_key=param_key,
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
                    scope=scope,
                    param_key=param_key,
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
                    scope=scope,
                    param_key=param_key,
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
                    scope=scope,
                    param_key=param_key,
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
                    scope=scope,
                    param_key=param_key,
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
        return ()

    def get_pricing_models(
        self,
        *,
        profile_id: str,
        product: str,
    ) -> Sequence[PricingModelConfig]:
        return (
            PricingModelConfig(
                profile_id="STD",
                product="CAPFLOOR",
                scope="GLOBAL",
                scope_key="GLOBAL",
                pricing_model="SHIFTED_BLACK",
                vol_interp_model="SHIFTED_SABR",
                model_tag="SABR_SHIFTED",
                vol_quote_type="LN_VOL",
                surface_tag=None,
            ),
        )


def data_factory(
    *,
    missing_fixing: bool = False,
    pricing: CapFloorPricingInput | None = None,
) -> CapFloorPricingData:
    provider = _StubProvider(missing_fixing=missing_fixing)
    pricing_input = pricing or CapFloorPricingInput(
        discount_curve_id="USD_DISC",
        forward_curve_id="USD_FWD",
        discount_daycount="ACT/365F",
        forward_daycount="ACT/365F",
    )
    return load_capfloor_pricing_data(
        provider,
        run_id="RUN_1",
        trade_id="CAP_1",
        snapshot_id="SNAP_1",
        pricing=pricing_input,
    )


def test_capfloor_price_from_data_runs():
    data = data_factory()
    result = price_capfloor_from_data(data)
    assert result.optionlet_count > 0
    assert result.pv >= 0.0


def test_capfloor_past_fixing_missing_raises():
    data = data_factory(
        missing_fixing=True,
        pricing=CapFloorPricingInput(
            discount_curve_id="USD_DISC",
            forward_curve_id="USD_FWD",
            discount_daycount="ACT/365F",
            forward_daycount="ACT/365F",
            as_of=date(2026, 5, 1),
        ),
    )
    with pytest.raises(ValueError, match="historical_fixing is required"):
        price_capfloor_from_data(data)


def test_capfloor_price_bachelier_model_runs():
    data = data_factory(
        pricing=CapFloorPricingInput(
            discount_curve_id="USD_DISC",
            forward_curve_id="USD_FWD",
            discount_daycount="ACT/365F",
            forward_daycount="ACT/365F",
            pricing_model="BACHELIER",
            vol_quote_type="LN_VOL",
        ),
    )
    result = price_capfloor_from_data(data)
    assert result.optionlet_count > 0
    assert np.isfinite(result.pv)
