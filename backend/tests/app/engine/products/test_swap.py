from datetime import date
from typing import Sequence

import pytest

from app.engine.market.yield_curve import YieldCurve
from app.engine.products.models.schedule_models import (
    HistoricalFixing,
    SwapScheduleRow,
    TradeHeader,
    TradeIRS,
)
from app.engine.products.swap import (
    SwapDataProvider,
    SwapPricingData,
    SwapPricingInput,
    MarketSnapshot,
    load_swap_pricing_data,
    price_swap_from_data,
)


class _StubProvider(SwapDataProvider):
    def __init__(
        self,
        *,
        schedule_rows: Sequence[SwapScheduleRow],
        fixings: Sequence[HistoricalFixing],
        forward_zero_rate: float,
    ) -> None:
        self._trade = TradeHeader(
            trade_id="SWAP_1",
            product="IRS",
            ccy="USD",
            notional=100_000_000.0,
            buy_sell="BUY",
            trade_date=date(2026, 1, 2),
            effective_date=date(2026, 1, 2),
            maturity_date=date(2026, 7, 2),
        )
        self._trade_irs = TradeIRS(
            trade_id="SWAP_1",
            pay_rec="PAY",
            fixed_rate=0.02,
            fixed_daycount="ACT/360",
            fixed_freq="6M",
            fixed_bdc="MF",
            fixed_cal_id="USNY",
            float_index_id="USD-SOFR-6M",
            float_spread=0.0,
            float_daycount="ACT/360",
            float_freq="6M",
            float_bdc="MF",
            float_cal_id="USNY",
            stub_type="BACK",
            settle_ccy="USD",
        )
        self._rows = tuple(schedule_rows)
        self._fixings = tuple(fixings)
        self._snapshot = MarketSnapshot(snapshot_id="SNAP_1", as_of=date(2026, 1, 2))
        self._discount_curve = YieldCurve.from_nodes(
            [0.25, 0.5, 1.0, 2.0],
            zero_nodes=[0.0, 0.0, 0.0, 0.0],
            interp_method="LINEAR",
        )
        self._forward_curve = YieldCurve.from_nodes(
            [0.25, 0.5, 1.0, 2.0],
            zero_nodes=[forward_zero_rate] * 4,
            interp_method="LINEAR",
        )

    def get_trade(self, trade_id: str) -> TradeHeader:
        return self._trade

    def get_trade_irs(self, trade_id: str) -> TradeIRS:
        return self._trade_irs

    def get_swap_schedule(self, trade_id: str) -> Sequence[SwapScheduleRow]:
        return self._rows

    def get_historical_fixings(
        self, index_id: str, start_date: date, end_date: date
    ) -> Sequence[HistoricalFixing]:
        return tuple(
            f
            for f in self._fixings
            if f.index_id == index_id and start_date <= f.fixing_date <= end_date
        )

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        return self._snapshot

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve:
        if curve_id == "USD_SOFR":
            return self._forward_curve
        return self._discount_curve


def data_factory(
    *,
    schedule_rows: Sequence[SwapScheduleRow],
    fixings: Sequence[HistoricalFixing] = (),
    forward_zero_rate: float = 0.03,
    pricing: SwapPricingInput | None = None,
) -> SwapPricingData:
    provider = _StubProvider(
        schedule_rows=schedule_rows,
        fixings=fixings,
        forward_zero_rate=forward_zero_rate,
    )
    pricing_input = pricing or SwapPricingInput(
        discount_curve_id="USD_OIS",
        forward_curve_id="USD_SOFR",
        discount_daycount="ACT/365F",
        forward_daycount="ACT/365F",
    )
    return load_swap_pricing_data(
        provider,
        run_id="RUN_1",
        trade_id="SWAP_1",
        snapshot_id="SNAP_1",
        pricing=pricing_input,
    )


def test_swap_pv_uses_fixed_and_floating_legs_consistently() -> None:
    rows = (
        SwapScheduleRow(
            trade_id="SWAP_1",
            leg_id="FIXED",
            cashflow_no=1,
            payment_date=date(2026, 7, 2),
            payment_type="INTEREST",
            pay_rec="PAY",
            ccy="USD",
            start_date=date(2026, 1, 2),
            end_date=date(2026, 7, 2),
            daycount="ACT/360",
            accrual_factor=0.5,
            notional=100_000_000.0,
            principal_factor=0.0,
            rate_calc_type="FIXED",
            rate=0.02,
        ),
        SwapScheduleRow(
            trade_id="SWAP_1",
            leg_id="FLOAT",
            cashflow_no=1,
            payment_date=date(2026, 7, 2),
            payment_type="INTEREST",
            pay_rec="REC",
            ccy="USD",
            start_date=date(2026, 1, 2),
            end_date=date(2026, 7, 2),
            daycount="ACT/360",
            accrual_factor=0.5,
            notional=100_000_000.0,
            principal_factor=0.0,
            index_id="USD-SOFR-6M",
            spread=0.0,
            gearing=1.0,
            rate_calc_type="IBOR_SINGLE",
            fixing_date=date(2025, 12, 31),
            obs_start_date=date(2026, 1, 2),
            obs_end_date=date(2026, 7, 2),
        ),
    )
    data = data_factory(
        schedule_rows=rows,
        fixings=(
            HistoricalFixing(
                index_id="USD-SOFR-6M",
                fixing_date=date(2025, 12, 31),
                rate=0.03,
            ),
        ),
    )

    result = price_swap_from_data(data)
    assert result.pv_fixed == pytest.approx(-1_000_000.0, rel=1e-12)
    assert result.pv_float == pytest.approx(1_500_000.0, rel=1e-12)
    assert result.pv == pytest.approx(500_000.0, rel=1e-12)


def test_swap_returns_zero_when_all_cashflows_are_past() -> None:
    rows = (
        SwapScheduleRow(
            trade_id="SWAP_1",
            leg_id="FIXED",
            cashflow_no=1,
            payment_date=date(2025, 1, 2),
            payment_type="INTEREST",
            pay_rec="PAY",
            ccy="USD",
            notional=1_000_000.0,
            principal_factor=0.0,
            rate_calc_type="FIXED",
            rate=0.01,
            accrual_factor=0.5,
            amount=5_000.0,
            is_settled=1,
        ),
    )
    data = data_factory(
        schedule_rows=rows,
        pricing=SwapPricingInput(
            discount_curve_id="USD_OIS",
            forward_curve_id=None,
            discount_daycount="ACT/365F",
            forward_daycount="ACT/365F",
            include_settled=False,
        ),
    )

    result = price_swap_from_data(data)
    assert result.pv == 0.0
    assert result.pv_fixed == 0.0
    assert result.pv_float == 0.0


def test_swap_projects_float_rate_from_curve_if_fixing_not_available() -> None:
    rows = (
        SwapScheduleRow(
            trade_id="SWAP_1",
            leg_id="FLOAT",
            cashflow_no=1,
            payment_date=date(2026, 7, 1),
            payment_type="INTEREST",
            pay_rec="REC",
            ccy="USD",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 7, 1),
            daycount="ACT/360",
            accrual_factor=0.25,
            notional=10_000_000.0,
            principal_factor=0.0,
            index_id="USD-SOFR-3M",
            spread=0.0,
            gearing=1.0,
            rate_calc_type="IBOR_SINGLE",
            fixing_date=date(2026, 4, 1),
            obs_start_date=date(2026, 4, 1),
            obs_end_date=date(2026, 7, 1),
        ),
    )
    data = data_factory(schedule_rows=rows, forward_zero_rate=0.03)

    result = price_swap_from_data(data)
    assert result.pv_fixed == 0.0
    assert result.pv_float > 0.0
    assert result.pv > 0.0
