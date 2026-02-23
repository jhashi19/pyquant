from datetime import date
from typing import Optional

import pytest

from app.engine.market.yield_curve import YieldCurve
from app.engine.math.bizday import BusinessCalendar
from app.engine.products.fra import (
    FRADataProvider,
    FRAPricingData,
    FRAPricingInput,
    HistoricalFixing,
    MarketSnapshot,
    TradeFRA,
    load_fra_pricing_data,
    price_fra_from_data,
)
from app.engine.products.models.schedule_models import RefRateRule, TradeHeader


class _StubProvider(FRADataProvider):
    def __init__(
        self,
        *,
        fixing_lag_bd: int,
        historical_fixing_rate: Optional[float],
        forward_zero_rate: float,
    ) -> None:
        self._trade = TradeHeader(
            trade_id="FRA_1",
            product="FRA",
            ccy="USD",
            notional=1_000_000.0,
            buy_sell="BUY",
            trade_date=date(2026, 1, 2),
            effective_date=date(2026, 1, 2),
            maturity_date=date(2026, 7, 2),
        )
        self._fra = TradeFRA(
            trade_id="FRA_1",
            ccy="USD",
            notional=1_000_000.0,
            pay_rec="PAY",
            fra_rate_agreed=0.03,
            ref_rate_id="USD-SOFR-3M",
            accrual_start_date=date(2026, 4, 2),
            accrual_end_date=date(2026, 7, 2),
            daycount="ACT/360",
            pay_bdc="MF",
            pay_cal_id="USNY",
            fixing_lag_bd=fixing_lag_bd,
            fixing_bdc="MF",
            fixing_cal_id="USNY",
            settlement_type="CASH",
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
        self._historical_fixing_rate = historical_fixing_rate

    def get_trade(self, trade_id: str) -> TradeHeader:
        return self._trade

    def get_trade_fra(self, trade_id: str) -> TradeFRA:
        return self._fra

    def get_ref_rate_rule(self, index_id: str) -> RefRateRule:
        return self._rule

    def get_historical_fixing(self, index_id: str, fixing_date: date) -> Optional[HistoricalFixing]:
        if self._historical_fixing_rate is None:
            return None
        return HistoricalFixing(
            index_id=index_id,
            fixing_date=fixing_date,
            rate=self._historical_fixing_rate,
        )

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot:
        return self._snapshot

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve:
        if curve_id == "USD_FWD":
            return self._forward_curve
        return self._discount_curve

    def get_business_calendar(self, cal_id: str) -> BusinessCalendar:
        return BusinessCalendar()


def data_factory(
    *,
    fixing_lag_bd: int = 0,
    historical_fixing_rate: Optional[float] = None,
    forward_zero_rate: float = 0.04,
    as_of: Optional[date] = None,
    pricing: Optional[FRAPricingInput] = None,
) -> FRAPricingData:
    provider = _StubProvider(
        fixing_lag_bd=fixing_lag_bd,
        historical_fixing_rate=historical_fixing_rate,
        forward_zero_rate=forward_zero_rate,
    )
    pricing_input = pricing or FRAPricingInput(
        discount_curve_id="USD_OIS",
        forward_curve_id="USD_FWD",
        discount_curve_daycount="ACT/365F",
        projection_curve_daycount="ACT/365F",
        as_of=as_of,
    )
    return load_fra_pricing_data(
        provider,
        run_id="RUN_1",
        trade_id="FRA_1",
        snapshot_id="SNAP_1",
        pricing=pricing_input,
    )


def test_fra_projects_forward_rate_for_future_fixing() -> None:
    data = data_factory(
        fixing_lag_bd=0,
        forward_zero_rate=0.04,
        as_of=date(2026, 1, 2),
    )

    result = price_fra_from_data(data)
    assert result.fixing_rate > result.agreed_rate
    assert result.settlement_amount > 0.0
    assert result.pv > 0.0


def test_fra_uses_historical_fixing_when_fixing_date_is_past() -> None:
    data = data_factory(
        fixing_lag_bd=2,
        historical_fixing_rate=0.025,
        forward_zero_rate=0.08,
        as_of=date(2026, 4, 1),
    )

    result = price_fra_from_data(data)
    assert result.fixing_date <= date(2026, 4, 1)
    assert result.fixing_rate == pytest.approx(0.025, abs=1e-12)


def test_fra_raises_if_historical_fixing_missing_for_past_fixing_date() -> None:
    data = data_factory(
        fixing_lag_bd=2,
        historical_fixing_rate=None,
        forward_zero_rate=0.04,
        as_of=date(2026, 4, 1),
    )

    with pytest.raises(ValueError, match="historical_fixing is required"):
        price_fra_from_data(data)


def test_fra_returns_zero_after_settlement_date() -> None:
    data = data_factory(
        fixing_lag_bd=0,
        forward_zero_rate=0.04,
        as_of=date(2026, 4, 3),
    )

    result = price_fra_from_data(data)
    assert result.pv == 0.0
    assert result.settlement_amount == 0.0
