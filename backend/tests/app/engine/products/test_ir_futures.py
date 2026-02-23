from datetime import date
from typing import Optional

import numpy as np

from app.engine.market.yield_curve import YieldCurve
from app.engine.products.ir_futures import (
    IrFuturesDataProvider,
    IrFuturesDef,
    IrFuturesPricingData,
    IrFuturesPricingInput,
    MarketQuoteIrFutures,
    MarketSnapshot,
    TradeIrFutures,
    load_ir_futures_pricing_data,
    price_ir_futures_from_data,
)
from app.engine.products.models.schedule_models import RefRateRule


class _StubProvider(IrFuturesDataProvider):
    def __init__(
        self,
        *,
        convexity_model: str = "NONE",
        convexity_adj_rate: float = 0.0,
        hw_mean_reversion: Optional[float] = None,
        hw_vol: Optional[float] = None,
    ) -> None:
        self._snapshot = MarketSnapshot(snapshot_id="SNAP_1", as_of=date(2026, 1, 2))
        self._trade = TradeIrFutures(
            trade_id="IRFUT_1",
            fut_code="CME_SOFR3M",
            contract_month="202606",
            last_trading_date=None,
            position_lots=10,
            price_agreed=95.0,
            margin_style="EXCHANGE",
            ref_rate_id="USD-SOFR-3M",
            accrual_start_date=date(2026, 6, 17),
            accrual_end_date=date(2026, 9, 17),
            accrual_daycount="ACT/360",
            convexity_model=convexity_model,
            convexity_adj_rate=convexity_adj_rate,
            hw_mean_reversion=hw_mean_reversion,
            hw_vol=hw_vol,
            cal_id_override=None,
        )
        self._fut_def = IrFuturesDef(
            fut_code="CME_SOFR3M",
            display_name="CME SOFR 3M",
            exchange_code="CME",
            ccy="USD",
            underlying_ref_rate_id="USD-SOFR-3M",
            contract_notional=1_000_000.0,
            tick_size=0.0025,
            tick_value=6.25,
            quote_conv="PRICE",
        )
        self._quote = MarketQuoteIrFutures(
            fut_code="CME_SOFR3M",
            contract_month="202606",
            price_mid=95.25,
            price_bid=95.24,
            price_ask=95.26,
        )
        self._curve = YieldCurve.from_nodes(
            [0.25, 0.5, 1.0, 2.0],
            zero_nodes=[0.04, 0.04, 0.04, 0.04],
            interp_method="LINEAR",
        )
        self._ref_rate_rule = RefRateRule(
            index_id="USD-SOFR-3M",
            rate_type="TERM",
            accrual_conv="SIMPLE",
            daycount="ACT/360",
            fixing_cal_id="USNY",
            fixing_bdc="MF",
            lookback_days=0,
        )

    def get_trade_ir_futures(self, trade_id: str) -> TradeIrFutures:
        return self._trade

    def get_ir_futures_def(self, fut_code: str) -> IrFuturesDef:
        return self._fut_def

    def get_market_quote_ir_futures(
        self, fut_code: str, contract_month: str, snapshot_id: str
    ) -> MarketQuoteIrFutures:
        return self._quote

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        return self._snapshot

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve:
        return self._curve

    def get_ref_rate_rule(self, index_id: str) -> RefRateRule:
        return self._ref_rate_rule


def data_factory(
    *,
    convexity_model: str = "NONE",
    convexity_adj_rate: float = 0.0,
    hw_mean_reversion: Optional[float] = None,
    hw_vol: Optional[float] = None,
    pricing: Optional[IrFuturesPricingInput] = None,
) -> IrFuturesPricingData:
    provider = _StubProvider(
        convexity_model=convexity_model,
        convexity_adj_rate=convexity_adj_rate,
        hw_mean_reversion=hw_mean_reversion,
        hw_vol=hw_vol,
    )
    pricing_input = pricing or IrFuturesPricingInput(
        input_side="MID",
        forward_curve_id="USD-SOFR-3M",
        compute_theoretical=True,
    )
    return load_ir_futures_pricing_data(
        provider,
        run_id="RUN_1",
        trade_id="IRFUT_1",
        snapshot_id="SNAP_1",
        pricing=pricing_input,
    )


def test_ir_futures_market_and_theoretical_price_with_delta() -> None:
    data = data_factory(
        pricing=IrFuturesPricingInput(
            input_side="MID",
            forward_curve_id="USD-SOFR-3M",
            forward_curve_daycount="ACT/365F",
            compute_theoretical=True,
            delta_shift_bp=1.0,
            delta_scheme="CENTRAL",
        )
    )
    result = price_ir_futures_from_data(data)
    assert np.isfinite(result.pv)
    assert result.price_theoretical is not None
    assert result.forward_rate is not None
    assert result.delta_pv_parallel_per_bp is not None
    assert result.delta_pv_parallel_per_bp < 0.0


def test_ir_futures_hw1f_convexity_positive() -> None:
    data = data_factory(
        convexity_model="HW1F",
        convexity_adj_rate=0.0,
        hw_mean_reversion=0.05,
        hw_vol=0.01,
    )
    result = price_ir_futures_from_data(data)
    assert result.convexity_adjustment_rate > 0.0
    assert result.rate_theoretical is not None


def test_ir_futures_skip_theoretical() -> None:
    data = data_factory(
        pricing=IrFuturesPricingInput(
            input_side="MID",
            compute_theoretical=False,
        )
    )
    result = price_ir_futures_from_data(data)
    assert result.price_theoretical is None
    assert result.delta_pv_parallel_per_bp is None
