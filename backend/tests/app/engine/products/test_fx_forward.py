from datetime import date
import math

import pytest

from app.engine.market.yield_curve import YieldCurve
from app.engine.products.fx_forward import (
    FxForwardDataProvider,
    FxForwardPVResult,
    FxForwardPricingData,
    FxForwardPricingInput,
    FxSpot,
    MarketSnapshot,
    TradeFxFwd,
    load_fx_forward_pricing_data,
    price_fx_forward_from_data,
)
from app.engine.products.models.schedule_models import TradeHeader


class _StubProvider(FxForwardDataProvider):
    def __init__(
        self,
        *,
        trade_ccy: str,
        trade_notional: float,
        forward_rate: float,
        spot: float,
        notional_ccy: str | None,
    ) -> None:
        as_of = date(2026, 1, 2)
        self._snapshot = MarketSnapshot(snapshot_id="SNAP_1", as_of=as_of)
        self._trade = TradeHeader(
            trade_id="FXFWD_1",
            product="FXFWD",
            ccy=trade_ccy,
            notional=trade_notional,
            buy_sell="buy",
            trade_date=as_of,
            effective_date=as_of,
            maturity_date=date(2026, 7, 2),
        )
        self._trade_fxfwd = TradeFxFwd(
            trade_id="FXFWD_1",
            base_ccy="USD",
            quote_ccy="JPY",
            pair="USDJPY",
            deliver_date=date(2026, 7, 2),
            forward_rate=forward_rate,
            settle_bdc="MF",
            deliver_cal_id="USNY",
            pay_rec_base="REC",
            notional_ccy=notional_ccy,
        )
        self._spot = FxSpot(
            base_ccy="USD",
            quote_ccy="JPY",
            pair="USDJPY",
            spot=spot,
        )
        self._curve = YieldCurve.from_nodes(
            [0.25, 1.0],
            zero_nodes=[0.0, 0.0],
            interp_method="LINEAR",
        )

    def get_trade(self, trade_id: str) -> TradeHeader:
        return self._trade

    def get_trade_fxfwd(self, trade_id: str) -> TradeFxFwd:
        return self._trade_fxfwd

    def get_fx_spot(self, base_ccy: str, quote_ccy: str, snapshot_id: str) -> FxSpot:
        return self._spot

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        return self._snapshot

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve:
        return self._curve


def data_factory(
    *,
    trade_ccy: str,
    trade_notional: float = 1_000_000.0,
    forward_rate: float = 108.0,
    spot: float = 110.0,
    notional_ccy: str | None = None,
    pricing: FxForwardPricingInput | None = None,
) -> FxForwardPricingData:
    provider = _StubProvider(
        trade_ccy=trade_ccy,
        trade_notional=trade_notional,
        forward_rate=forward_rate,
        spot=spot,
        notional_ccy=notional_ccy,
    )
    pricing_input = pricing or FxForwardPricingInput(
        base_curve_id="USD_OIS",
        quote_curve_id="JPY_OIS",
        base_curve_daycount="ACT/365F",
        quote_curve_daycount="ACT/365F",
    )
    return load_fx_forward_pricing_data(
        provider,
        run_id="RUN_1",
        trade_id="FXFWD_1",
        snapshot_id="SNAP_1",
        pricing=pricing_input,
    )


def test_fx_forward_pv_explicit_quote_and_base_currency() -> None:
    data = data_factory(trade_ccy="JPY")
    result = price_fx_forward_from_data(data)

    assert isinstance(result, FxForwardPVResult)
    assert result.quote_ccy == "JPY"
    assert result.base_ccy == "USD"
    assert result.reporting_ccy == "JPY"
    assert math.isclose(result.pv_quote, 2_000_000.0, rel_tol=1e-12)
    assert math.isclose(result.pv_base, result.pv_quote / 110.0, rel_tol=1e-12)
    assert math.isclose(result.pv_reporting, result.pv_quote, rel_tol=1e-12)


def test_fx_forward_reporting_pv_in_base_currency() -> None:
    data = data_factory(trade_ccy="USD")
    result = price_fx_forward_from_data(data)

    assert result.reporting_ccy == "USD"
    assert math.isclose(result.pv_reporting, result.pv_base, rel_tol=1e-12)


def test_fx_forward_quote_notional_is_supported() -> None:
    data = data_factory(
        trade_ccy="JPY",
        trade_notional=220_000_000.0,
        forward_rate=110.0,
        spot=112.0,
        notional_ccy="JPY",
    )
    result = price_fx_forward_from_data(data)

    assert math.isclose(result.pv_quote, 4_000_000.0, rel_tol=1e-12)
    assert math.isclose(result.pv_base, result.pv_quote / 112.0, rel_tol=1e-12)


def test_fx_forward_third_currency_reporting_is_rejected() -> None:
    data = data_factory(trade_ccy="EUR")
    with pytest.raises(ValueError, match="trade.ccy must match base_ccy or quote_ccy"):
        price_fx_forward_from_data(data)
