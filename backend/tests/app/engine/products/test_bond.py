from datetime import date
from typing import Sequence

import pytest

from app.engine.market.yield_curve import YieldCurve
from app.engine.math.bizday import BusinessCalendar
from app.engine.products.bond import (
    BondDataProvider,
    BondPricingData,
    BondPricingInput,
    BondPricingStateCache,
    load_bond_pricing_data,
    price_bond_from_data,
)
from app.engine.products.bond_schedule import build_bond_schedule_rows
from app.engine.products.models.schedule_models import (
    BondDef,
    BondScheduleRow,
    HistoricalFixing,
    MarketQuoteBond,
    TradeBond,
    TradeHeader,
)


class _StubProvider(BondDataProvider):
    def __init__(
        self,
        *,
        trade: TradeHeader,
        trade_bond: TradeBond,
        bond_def: BondDef,
        quote: MarketQuoteBond,
        schedule_rows: Sequence[BondScheduleRow],
        fixings: Sequence[HistoricalFixing],
        discount_curve: YieldCurve,
        forward_curve: YieldCurve,
    ) -> None:
        self._trade = trade
        self._trade_bond = trade_bond
        self._bond_def = bond_def
        self._quote = quote
        self._schedule_rows = tuple(schedule_rows)
        self._fixings = tuple(fixings)
        self._discount_curve = discount_curve
        self._forward_curve = forward_curve

    def get_trade(self, trade_id: str) -> TradeHeader:
        return self._trade

    def get_trade_bond(self, trade_id: str) -> TradeBond:
        return self._trade_bond

    def get_bond_def(self, security_id: str) -> BondDef:
        return self._bond_def

    def get_market_quote_bond(self, security_id: str, snapshot_id: str) -> MarketQuoteBond:
        return self._quote

    def get_bond_schedule(self, trade_id: str, base_security_id: str) -> Sequence[BondScheduleRow]:
        return self._schedule_rows

    def get_historical_fixings(
        self, index_id: str, start_date: date, end_date: date
    ) -> Sequence[HistoricalFixing]:
        return tuple(
            f
            for f in self._fixings
            if f.index_id == index_id and start_date <= f.fixing_date <= end_date
        )

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve:
        if curve_id == "USD_FWD":
            return self._forward_curve
        return self._discount_curve


def _flat_curve(rate: float = 0.0) -> YieldCurve:
    return YieldCurve.from_nodes(
        [0.25, 0.5, 1.0, 2.0],
        zero_nodes=[rate, rate, rate, rate],
        interp_method="LINEAR",
    )


def _base_trade(trade_id: str, notional: float = 1_000_000.0) -> TradeHeader:
    return TradeHeader(
        trade_id=trade_id,
        product="BOND",
        ccy="USD",
        notional=notional,
        buy_sell="BUY",
        trade_date=date(2026, 1, 2),
        effective_date=date(2026, 1, 1),
        maturity_date=date(2026, 7, 1),
    )


def _base_terms(
    *,
    trade_id: str,
    security_id: str,
    coupon_type: str,
    coupon_rate: float | None,
    coupon_daycount: str | None,
    coupon_freq: str | None,
    float_index_id: str | None,
    float_spread: float | None,
    redemption: float,
    issue_date: date,
    maturity_date: date,
) -> tuple[TradeHeader, BondDef, TradeBond]:
    trade = _base_trade(trade_id)
    bond_def = BondDef(
        security_id=security_id,
        issue_date=issue_date,
        maturity_date=maturity_date,
        coupon_type=coupon_type,
        coupon_rate=coupon_rate,
        float_index_id=float_index_id,
        float_spread=float_spread,
        coupon_daycount=coupon_daycount,
        coupon_freq=coupon_freq,
        coupon_bdc="MF" if coupon_type != "ZC" else None,
        coupon_cal_id="USNY" if coupon_type != "ZC" else None,
        redemption=redemption,
        ccy="USD",
    )
    trade_bond = TradeBond(
        trade_id=trade.trade_id,
        security_id=security_id,
        coupon_type=coupon_type,
        coupon_rate=None,
        coupon_daycount=None,
        coupon_freq=None,
        coupon_bdc=None,
        coupon_cal_id=None,
        float_index_id=None,
        float_spread=None,
        issuer="TEST",
        redemption=redemption,
        settlement_ccy="USD",
    )
    return trade, bond_def, trade_bond


def data_factory(
    *,
    trade: TradeHeader,
    bond_def: BondDef,
    trade_bond: TradeBond,
    quote: MarketQuoteBond,
    fixings: Sequence[HistoricalFixing] = (),
    discount_curve: YieldCurve | None = None,
    forward_curve: YieldCurve | None = None,
    pricing: BondPricingInput | None = None,
) -> BondPricingData:
    discount_curve_obj = discount_curve or _flat_curve(0.0)
    forward_curve_obj = forward_curve or _flat_curve(0.0)
    rows = build_bond_schedule_rows(
        trade,
        trade_bond,
        bond_def,
        quote,
        fixings,
        calendars={"USNY": BusinessCalendar()},
    )
    provider = _StubProvider(
        trade=trade,
        trade_bond=trade_bond,
        bond_def=bond_def,
        quote=quote,
        schedule_rows=rows,
        fixings=fixings,
        discount_curve=discount_curve_obj,
        forward_curve=forward_curve_obj,
    )
    pricing_input = pricing or BondPricingInput(
        settle_date=bond_def.issue_date,
        discount_curve_id="USD_OIS",
        curve_daycount="ACT/365F",
        z_spread_daycount="ACT/365F",
    )
    return load_bond_pricing_data(
        provider,
        run_id="RUN_1",
        trade_id=trade.trade_id,
        snapshot_id="SNAP_1",
        pricing=pricing_input,
    )


def _dirty_per_100_from_schedule(rows: Sequence[BondScheduleRow]) -> float:
    return float(
        sum(
            (
                row.fixed_amount_per_base
                if row.fixed_amount_per_base is not None
                else row.amount_per_base
            )
            or 0.0
            for row in rows
            if row.payment_type in {"INTEREST", "PRINCIPAL"}
        )
    )


def test_fixed_bond_dirty_price_is_reproduced_with_calibrated_z_spread() -> None:
    trade, bond_def, trade_bond = _base_terms(
        trade_id="BOND_T1",
        security_id="BOND_FIX",
        coupon_type="FIX",
        coupon_rate=0.04,
        coupon_daycount="ACT/360",
        coupon_freq="6M",
        float_index_id=None,
        float_spread=None,
        redemption=100.0,
        issue_date=date(2026, 1, 1),
        maturity_date=date(2026, 7, 1),
    )
    provisional_quote = MarketQuoteBond(security_id="BOND_FIX", clean_price_mid=100.0)
    rows = build_bond_schedule_rows(
        trade,
        trade_bond,
        bond_def,
        provisional_quote,
        (),
        calendars={"USNY": BusinessCalendar()},
    )
    dirty_per_100 = _dirty_per_100_from_schedule(rows)

    data = data_factory(
        trade=trade,
        bond_def=bond_def,
        trade_bond=trade_bond,
        quote=MarketQuoteBond(security_id="BOND_FIX", dirty_price_mid=dirty_per_100),
    )

    result = price_bond_from_data(data)
    assert result.z_spread == pytest.approx(0.0, abs=1e-10)
    assert result.accrued_interest == pytest.approx(0.0, abs=1e-12)
    assert result.pv_dirty == pytest.approx(dirty_per_100 * trade.notional / 100.0, rel=1e-10)
    assert result.pv_clean == pytest.approx(result.pv_dirty, rel=1e-10)


def test_fixed_bond_uses_cached_calibrated_z_spread() -> None:
    trade, bond_def, trade_bond = _base_terms(
        trade_id="BOND_T2",
        security_id="BOND_FIX_CACHE",
        coupon_type="FIX",
        coupon_rate=0.04,
        coupon_daycount="ACT/360",
        coupon_freq="6M",
        float_index_id=None,
        float_spread=None,
        redemption=100.0,
        issue_date=date(2026, 1, 1),
        maturity_date=date(2026, 7, 1),
    )
    provisional_quote = MarketQuoteBond(security_id="BOND_FIX_CACHE", clean_price_mid=100.0)
    rows = build_bond_schedule_rows(
        trade,
        trade_bond,
        bond_def,
        provisional_quote,
        (),
        calendars={"USNY": BusinessCalendar()},
    )
    dirty_per_100 = _dirty_per_100_from_schedule(rows)

    data_1 = data_factory(
        trade=trade,
        bond_def=bond_def,
        trade_bond=trade_bond,
        quote=MarketQuoteBond(
            security_id="BOND_FIX_CACHE",
            dirty_price_mid=dirty_per_100,
        ),
    )
    data_2 = data_factory(
        trade=trade,
        bond_def=bond_def,
        trade_bond=trade_bond,
        quote=MarketQuoteBond(
            security_id="BOND_FIX_CACHE",
            dirty_price_mid=dirty_per_100 + 20.0,
        ),
    )

    cache = BondPricingStateCache(_store={})
    result_1 = price_bond_from_data(data_1, cache=cache)
    result_2 = price_bond_from_data(data_2, cache=cache)

    assert result_1.z_spread == result_2.z_spread
    assert result_1.pv_dirty == pytest.approx(result_2.pv_dirty, rel=1e-12)


def test_float_bond_requires_fixing_for_past_coupon_period() -> None:
    trade, bond_def, trade_bond = _base_terms(
        trade_id="BOND_T3",
        security_id="BOND_FLT",
        coupon_type="FLOAT",
        coupon_rate=None,
        coupon_daycount="ACT/360",
        coupon_freq="6M",
        float_index_id="USD-SOFR-6M",
        float_spread=0.001,
        redemption=100.0,
        issue_date=date(2025, 7, 1),
        maturity_date=date(2026, 7, 1),
    )
    trade = TradeHeader(
        trade_id=trade.trade_id,
        product=trade.product,
        ccy=trade.ccy,
        notional=trade.notional,
        buy_sell=trade.buy_sell,
        trade_date=trade.trade_date,
        effective_date=date(2025, 7, 1),
        maturity_date=date(2026, 7, 1),
    )

    data = data_factory(
        trade=trade,
        bond_def=bond_def,
        trade_bond=trade_bond,
        quote=MarketQuoteBond(security_id="BOND_FLT", dirty_price_mid=100.0),
        fixings=(),
        discount_curve=_flat_curve(0.02),
        forward_curve=_flat_curve(0.03),
        pricing=BondPricingInput(
            settle_date=date(2025, 10, 1),
            discount_curve_id="USD_OIS",
            curve_daycount="ACT/365F",
            forward_curve_id="USD_FWD",
            forward_daycount="ACT/365F",
            z_spread_daycount="ACT/365F",
        ),
    )

    with pytest.raises(ValueError, match="historical_fixing is required"):
        price_bond_from_data(data)


def test_zero_coupon_bond_has_no_accrued_interest() -> None:
    trade, bond_def, trade_bond = _base_terms(
        trade_id="BOND_T4",
        security_id="BOND_ZC",
        coupon_type="ZC",
        coupon_rate=None,
        coupon_daycount=None,
        coupon_freq=None,
        float_index_id=None,
        float_spread=None,
        redemption=100.0,
        issue_date=date(2026, 1, 1),
        maturity_date=date(2026, 7, 1),
    )
    data = data_factory(
        trade=trade,
        bond_def=bond_def,
        trade_bond=trade_bond,
        quote=MarketQuoteBond(security_id="BOND_ZC", dirty_price_mid=100.0),
        discount_curve=_flat_curve(0.0),
    )

    result = price_bond_from_data(data)
    assert result.accrued_interest == pytest.approx(0.0, abs=1e-12)
    assert result.pv_clean == pytest.approx(result.pv_dirty, rel=1e-10)
