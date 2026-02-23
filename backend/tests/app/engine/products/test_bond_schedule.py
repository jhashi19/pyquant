from datetime import date

from app.engine.math.bizday import BusinessCalendar
from app.engine.products.bond_schedule import build_bond_schedule_rows
from app.engine.products.models.schedule_models import (
    BondDef,
    BondScheduleRow,
    HistoricalFixing,
    MarketQuoteBond,
    TradeBond,
    TradeHeader,
)


def _base_trade() -> TradeHeader:
    return TradeHeader(
        trade_id="BOND_T1",
        product="BOND",
        ccy="USD",
        notional=1_000_000.0,
        buy_sell="BUY",
        trade_date=date(2026, 1, 2),
        effective_date=date(2026, 1, 1),
        maturity_date=date(2027, 1, 1),
    )


def _fixed_bond_def() -> BondDef:
    return BondDef(
        security_id="BOND_001",
        issue_date=date(2026, 1, 1),
        maturity_date=date(2027, 1, 1),
        coupon_type="FIX",
        coupon_rate=0.04,
        float_index_id=None,
        float_spread=None,
        coupon_daycount="ACT/360",
        coupon_freq="6M",
        coupon_bdc="MF",
        coupon_cal_id="USNY",
        redemption=100.0,
        ccy="USD",
    )


def _base_trade_bond() -> TradeBond:
    return TradeBond(
        trade_id="BOND_T1",
        security_id="BOND_001",
        coupon_type="FIX",
        coupon_rate=None,
        coupon_daycount=None,
        coupon_freq=None,
        coupon_bdc=None,
        coupon_cal_id=None,
        float_index_id=None,
        float_spread=None,
        issuer="TEST",
        redemption=100.0,
        settlement_ccy="USD",
    )


def data_factory(
    *,
    trade: TradeHeader | None = None,
    trade_bond: TradeBond | None = None,
    bond_def: BondDef | None = None,
    quote: MarketQuoteBond | None = None,
    fixings: tuple[HistoricalFixing, ...] = (),
) -> list[BondScheduleRow]:
    trade_obj = trade or _base_trade()
    trade_bond_obj = trade_bond or _base_trade_bond()
    bond_def_obj = bond_def or _fixed_bond_def()
    quote_obj = quote or MarketQuoteBond(
        security_id=bond_def_obj.security_id,
        clean_price_mid=100.0,
    )
    return build_bond_schedule_rows(
        trade_obj,
        trade_bond_obj,
        bond_def_obj,
        quote_obj,
        fixings,
        calendars={"USNY": BusinessCalendar()},
    )


def test_fixed_bond_schedule_contains_coupon_and_principal() -> None:
    rows = data_factory()

    interest_rows = [r for r in rows if r.payment_type == "INTEREST"]
    principal_rows = [r for r in rows if r.payment_type == "PRINCIPAL"]

    assert len(interest_rows) == 2
    assert len(principal_rows) == 1
    assert principal_rows[0].amount_per_base == 100.0
    assert all(r.security_id == "BOND_001" for r in rows)


def test_bond_schedule_uses_trade_override_identity() -> None:
    trade_bond = TradeBond(
        trade_id="BOND_T1",
        security_id="BOND_001",
        coupon_type="FIX",
        coupon_rate=0.05,
        coupon_daycount=None,
        coupon_freq=None,
        coupon_bdc=None,
        coupon_cal_id=None,
        float_index_id=None,
        float_spread=None,
        issuer="TEST",
        redemption=100.0,
        settlement_ccy="USD",
    )

    rows = data_factory(
        trade_bond=trade_bond,
        quote=MarketQuoteBond(security_id="BOND_001", clean_price_mid=100.0),
    )

    first_interest = next(r for r in rows if r.payment_type == "INTEREST")
    assert first_interest.security_id == "BOND_001#BOND_T1"
    assert first_interest.rate == 0.05


def test_float_bond_schedule_sets_fixed_coupon_when_fixing_exists() -> None:
    bond_def = BondDef(
        security_id="BOND_FLT",
        issue_date=date(2026, 1, 1),
        maturity_date=date(2026, 7, 1),
        coupon_type="FLOAT",
        coupon_rate=None,
        float_index_id="USD-SOFR-6M",
        float_spread=0.001,
        coupon_daycount="ACT/360",
        coupon_freq="6M",
        coupon_bdc="MF",
        coupon_cal_id="USNY",
        redemption=101.5,
        ccy="USD",
    )
    trade_bond = TradeBond(
        trade_id="BOND_T1",
        security_id="BOND_FLT",
        coupon_type="FLOAT",
        coupon_rate=None,
        coupon_daycount=None,
        coupon_freq=None,
        coupon_bdc=None,
        coupon_cal_id=None,
        float_index_id=None,
        float_spread=None,
        issuer="TEST",
        redemption=101.5,
        settlement_ccy="USD",
    )

    fixings = (
        HistoricalFixing(
            index_id="USD-SOFR-6M",
            fixing_date=date(2026, 1, 1),
            rate=0.03,
        ),
    )

    rows = data_factory(
        trade_bond=trade_bond,
        bond_def=bond_def,
        quote=MarketQuoteBond(security_id="BOND_FLT", clean_price_mid=100.0),
        fixings=fixings,
    )

    interest_row = next(r for r in rows if r.payment_type == "INTEREST")
    principal_row = next(r for r in rows if r.payment_type == "PRINCIPAL")

    assert interest_row.rate is not None
    assert interest_row.rate == 0.031
    assert interest_row.fixed_amount_per_base is not None
    assert principal_row.amount_per_base == 101.5
