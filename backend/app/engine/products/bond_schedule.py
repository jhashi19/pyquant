from __future__ import annotations

from typing import Iterable

from app.engine.math.bizday import BusinessCalendar
from app.engine.math.daycount import year_fraction
from app.engine.products.models.schedule_models import (
    BondDef,
    BondScheduleRow,
    HistoricalFixing,
    LegScheduleSpec,
    MarketQuoteBond,
    TradeBond,
    TradeHeader,
    StubType,
)
from app.engine.products.schedule_utils import build_bond_schedule


def _resolve_calendar(
    calendars: dict[str, BusinessCalendar], cal_id: str | None
) -> BusinessCalendar:
    if cal_id is None:
        raise ValueError("calendar id is required but missing.")
    if cal_id not in calendars:
        raise ValueError(f"calendar id not found: {cal_id!r}")
    return calendars[cal_id]


def _schedule_security_id(
    bond_def: BondDef,
    trade: TradeHeader,
    trade_bond: TradeBond,
) -> str:
    overrides = (
        trade_bond.coupon_rate is not None and trade_bond.coupon_rate != bond_def.coupon_rate
    ) or (
        trade_bond.coupon_daycount is not None
        and trade_bond.coupon_daycount != bond_def.coupon_daycount
    ) or (
        trade_bond.coupon_freq is not None and trade_bond.coupon_freq != bond_def.coupon_freq
    ) or (
        trade_bond.coupon_bdc is not None and trade_bond.coupon_bdc != bond_def.coupon_bdc
    ) or (
        trade_bond.coupon_cal_id is not None and trade_bond.coupon_cal_id != bond_def.coupon_cal_id
    ) or (
        trade_bond.float_index_id is not None
        and trade_bond.float_index_id != bond_def.float_index_id
    ) or (
        trade_bond.float_spread is not None
        and trade_bond.float_spread != bond_def.float_spread
    ) or (
        trade_bond.redemption is not None and trade_bond.redemption != bond_def.redemption
    )
    if overrides:
        return f"{bond_def.security_id}#{trade.trade_id}"
    return bond_def.security_id


def build_bond_schedule_rows(
    trade: TradeHeader,
    trade_bond: TradeBond,
    bond_def: BondDef,
    quote: MarketQuoteBond,
    fixings: Iterable[HistoricalFixing],
    *,
    calendars: dict[str, BusinessCalendar],
    base_notional: float = 100.0,
) -> list[BondScheduleRow]:
    if bond_def.ccy is None:
        raise ValueError("bond_def.ccy is required.")

    if trade_bond.security_id and trade_bond.security_id != bond_def.security_id:
        raise ValueError("trade_bond.security_id does not match bond_def.security_id.")
    if quote.security_id and quote.security_id != bond_def.security_id:
        raise ValueError("market_quote_bond.security_id does not match bond_def.security_id.")

    coupon_type = trade_bond.coupon_type or bond_def.coupon_type
    coupon_rate = (
        trade_bond.coupon_rate if trade_bond.coupon_rate is not None else bond_def.coupon_rate
    )
    coupon_daycount = (
        trade_bond.coupon_daycount
        if trade_bond.coupon_daycount is not None
        else bond_def.coupon_daycount
    )
    coupon_freq = (
        trade_bond.coupon_freq if trade_bond.coupon_freq is not None else bond_def.coupon_freq
    )
    coupon_bdc = (
        trade_bond.coupon_bdc if trade_bond.coupon_bdc is not None else bond_def.coupon_bdc
    )
    coupon_cal_id = (
        trade_bond.coupon_cal_id
        if trade_bond.coupon_cal_id is not None
        else bond_def.coupon_cal_id
    )
    float_index_id = (
        trade_bond.float_index_id
        if trade_bond.float_index_id is not None
        else bond_def.float_index_id
    )
    float_spread = (
        trade_bond.float_spread
        if trade_bond.float_spread is not None
        else bond_def.float_spread
    )
    redemption = trade_bond.redemption if trade_bond.redemption is not None else bond_def.redemption

    schedule_security_id = _schedule_security_id(bond_def, trade, trade_bond)
    base_security_id = bond_def.security_id

    if coupon_type not in {"FIX", "FLOAT", "ZC"}:
        raise ValueError(f"Unsupported coupon_type: {coupon_type!r}")

    if coupon_type in {"FIX", "FLOAT"}:
        if coupon_daycount is None or coupon_freq is None:
            raise ValueError("bond_def requires coupon_daycount and coupon_freq.")
        if coupon_bdc is None or coupon_cal_id is None:
            raise ValueError("bond_def requires coupon_bdc and coupon_cal_id.")
        coupon_cal = _resolve_calendar(calendars, coupon_cal_id)
        spec = LegScheduleSpec(
            freq=coupon_freq,
            calendar=coupon_cal,
            payment_calendar=coupon_cal,
            bdc=coupon_bdc,
            stub_type=StubType.BACK,
            first_date=bond_def.first_coupon_date,
            last_date=bond_def.last_coupon_date,
            pay_lag=0,
            accrual_bdc=coupon_bdc,
            accrual_calendar=coupon_cal,
        )
        schedule = build_bond_schedule(
            bond_def.issue_date, bond_def.maturity_date, coupon_leg=spec
        )
    else:
        schedule = tuple()

    fixing_map = {(f.index_id, f.fixing_date): f.rate for f in fixings}

    rows: list[BondScheduleRow] = []
    cashflow_no = 1

    if coupon_type in {"FIX", "FLOAT"}:
        if coupon_daycount is None:
            raise ValueError("coupon_daycount is required for coupon periods.")
        for period in schedule:
            accrual = year_fraction(
                period.accrual_start, period.accrual_end, coupon_daycount
            )
            rate = None
            amount_per_base = None
            fixed_amount_per_base = None
            rate_calc_type = "FIXED" if coupon_type == "FIX" else "IBOR_SINGLE"

            if coupon_type == "FIX":
                if coupon_rate is None:
                    raise ValueError("Fixed bond requires coupon_rate.")
                rate = coupon_rate
                amount_per_base = base_notional * rate * accrual
                fixed_amount_per_base = amount_per_base
            else:
                fixing_date = period.accrual_start
                if float_index_id is not None:
                    key = (float_index_id, fixing_date)
                    if key in fixing_map:
                        rate = fixing_map[key] + (float_spread or 0.0)
                        amount_per_base = base_notional * rate * accrual
                        fixed_amount_per_base = amount_per_base

            rows.append(
                BondScheduleRow(
                    security_id=schedule_security_id,
                    base_security_id=base_security_id,
                    trade_id=trade.trade_id,
                    cashflow_no=cashflow_no,
                    payment_date=period.payment_date,
                    start_date=period.accrual_start,
                    end_date=period.accrual_end,
                    payment_type="INTEREST",
                    ccy=bond_def.ccy,
                    daycount=coupon_daycount,
                    accrual_factor=accrual,
                    base_notional=base_notional,
                    notional_factor=1.0,
                    principal_factor=0.0,
                    rate_calc_type=rate_calc_type,
                    index_id=float_index_id,
                    spread=float_spread,
                    gearing=1.0,
                    fixing_date=period.accrual_start,
                    obs_start_date=period.accrual_start,
                    obs_end_date=period.accrual_end,
                    rate=rate,
                    amount_per_base=amount_per_base,
                    fixed_amount_per_base=fixed_amount_per_base,
                    is_stub=0,
                )
            )
            cashflow_no += 1

    principal_factor = redemption / 100.0
    rows.append(
        BondScheduleRow(
            security_id=schedule_security_id,
            base_security_id=base_security_id,
            trade_id=trade.trade_id,
            cashflow_no=cashflow_no,
            payment_date=bond_def.maturity_date,
            payment_type="PRINCIPAL",
            ccy=bond_def.ccy,
            base_notional=base_notional,
            notional_factor=1.0,
            principal_factor=principal_factor,
            rate_calc_type="FIXED",
            amount_per_base=base_notional * principal_factor,
            fixed_amount_per_base=base_notional * principal_factor,
            is_stub=0,
        )
    )

    return rows
