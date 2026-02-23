from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

import numpy as np

from app.engine.market.yield_curve import YieldCurve
from app.engine.math.daycount import year_fraction
from app.engine.products.models.schedule_models import TradeHeader


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    as_of: date


@dataclass(frozen=True)
class FxSpot:
    base_ccy: str
    quote_ccy: str
    pair: str
    spot: float
    bid: Optional[float] = None
    ask: Optional[float] = None


@dataclass(frozen=True)
class TradeFxFwd:
    trade_id: str
    base_ccy: str
    quote_ccy: str
    pair: str
    deliver_date: date
    forward_rate: float
    settle_bdc: str
    deliver_cal_id: str
    pay_rec_base: str
    notional_ccy: Optional[str] = None


@dataclass(frozen=True)
class FxForwardPricingInput:
    base_curve_id: str
    quote_curve_id: str
    base_curve_daycount: str
    quote_curve_daycount: str
    input_side: str = "MID"
    as_of: Optional[date] = None


@dataclass(frozen=True)
class FxForwardPricingData:
    run_id: str
    trade: TradeHeader
    trade_fxfwd: TradeFxFwd
    spot: FxSpot
    base_curve: YieldCurve
    quote_curve: YieldCurve
    pricing: FxForwardPricingInput
    as_of: date


@dataclass(frozen=True)
class FxForwardPVResult:
    pv_quote: float
    quote_ccy: str
    pv_base: float
    base_ccy: str
    pv_reporting: float
    reporting_ccy: str
    forward_mark: float
    forward_agreed: float
    spot: float


class FxForwardDataProvider(Protocol):
    def get_trade(self, trade_id: str) -> TradeHeader: ...

    def get_trade_fxfwd(self, trade_id: str) -> TradeFxFwd: ...

    def get_fx_spot(self, base_ccy: str, quote_ccy: str, snapshot_id: str) -> FxSpot: ...

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot: ...

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve: ...


def _resolve_spot(spot: FxSpot, side: str) -> float:
    key = side.upper()
    if key == "MID":
        return spot.spot
    if key == "BID":
        if spot.bid is None:
            raise ValueError("fx_spot.bid is required for BID pricing.")
        return spot.bid
    if key == "ASK":
        if spot.ask is None:
            raise ValueError("fx_spot.ask is required for ASK pricing.")
        return spot.ask
    raise ValueError(f"Unsupported input_side: {side!r}")


def _resolve_pay_rec_sign(pay_rec_base: str) -> float:
    match pay_rec_base.upper():
        case "REC":
            return 1.0
        case "PAY":
            return -1.0
        case _:
            raise ValueError("trade_fxfwd.pay_rec_base must be 'PAY' or 'REC'.")


def _resolve_base_notional(trade: TradeHeader, fwd: TradeFxFwd) -> float:
    base_ccy = fwd.base_ccy.upper()
    quote_ccy = fwd.quote_ccy.upper()
    notional_ccy = (fwd.notional_ccy or base_ccy).upper()
    notional = float(trade.notional)

    match notional_ccy:
        case _ if notional_ccy == base_ccy:
            return notional
        case _ if notional_ccy == quote_ccy:
            if fwd.forward_rate <= 0.0:
                raise ValueError("trade_fxfwd.forward_rate must be positive for quote-notional input.")
            return notional / float(fwd.forward_rate)
        case _:
            raise ValueError(
                "trade_fxfwd.notional_ccy must match either base_ccy or quote_ccy."
            )


def _resolve_reporting_pv_from_quote(
    pv_quote: float,
    *,
    trade_ccy: str,
    base_ccy: str,
    quote_ccy: str,
    spot: float,
) -> float:
    trade_ccy_u = trade_ccy.upper()
    base_ccy_u = base_ccy.upper()
    quote_ccy_u = quote_ccy.upper()
    match trade_ccy_u:
        case _ if trade_ccy_u == quote_ccy_u:
            return float(pv_quote)
        case _ if trade_ccy_u == base_ccy_u:
            if spot == 0.0:
                raise ValueError("spot must be non-zero to convert quote PV into base currency.")
            return float(pv_quote / spot)
        case _:
            raise ValueError(
                "trade.ccy must match base_ccy or quote_ccy for fx_forward reporting PV."
            )


def price_fx_forward_from_data(data: FxForwardPricingData) -> FxForwardPVResult:
    trade = data.trade
    fwd = data.trade_fxfwd
    if fwd.pair != fwd.base_ccy + fwd.quote_ccy:
        raise ValueError("trade_fxfwd.pair must equal base_ccy||quote_ccy.")
    if data.spot.pair != fwd.pair:
        raise ValueError("fx_spot.pair must match trade_fxfwd.pair.")

    spot = _resolve_spot(data.spot, data.pricing.input_side)
    sign = _resolve_pay_rec_sign(fwd.pay_rec_base)
    base_notional = _resolve_base_notional(trade, fwd)

    as_of = data.as_of
    if fwd.deliver_date <= as_of:
        pv_quote = 0.0
        pv_base = 0.0
        pv_reporting = _resolve_reporting_pv_from_quote(
            pv_quote,
            trade_ccy=trade.ccy,
            base_ccy=fwd.base_ccy,
            quote_ccy=fwd.quote_ccy,
            spot=spot,
        )
        return FxForwardPVResult(
            pv_quote=pv_quote,
            quote_ccy=fwd.quote_ccy,
            pv_base=pv_base,
            base_ccy=fwd.base_ccy,
            pv_reporting=pv_reporting,
            reporting_ccy=trade.ccy,
            forward_mark=fwd.forward_rate,
            forward_agreed=fwd.forward_rate,
            spot=spot,
        )

    t_base = year_fraction(as_of, fwd.deliver_date, data.pricing.base_curve_daycount)
    t_quote = year_fraction(as_of, fwd.deliver_date, data.pricing.quote_curve_daycount)
    df_base = float(np.asarray(data.base_curve.df(t_base)))
    df_quote = float(np.asarray(data.quote_curve.df(t_quote)))

    forward_mark = spot * df_base / df_quote
    forward_agreed = fwd.forward_rate
    pv_quote = sign * base_notional * (forward_mark - forward_agreed) * df_quote
    if spot == 0.0:
        raise ValueError("spot must be non-zero for FX forward base-currency PV conversion.")
    pv_base = pv_quote / spot
    pv_reporting = _resolve_reporting_pv_from_quote(
        pv_quote,
        trade_ccy=trade.ccy,
        base_ccy=fwd.base_ccy,
        quote_ccy=fwd.quote_ccy,
        spot=spot,
    )

    return FxForwardPVResult(
        pv_quote=pv_quote,
        quote_ccy=fwd.quote_ccy,
        pv_base=float(pv_base),
        base_ccy=fwd.base_ccy,
        pv_reporting=pv_reporting,
        reporting_ccy=trade.ccy,
        forward_mark=float(forward_mark),
        forward_agreed=float(forward_agreed),
        spot=float(spot),
    )


def load_fx_forward_pricing_data(
    provider: FxForwardDataProvider,
    *,
    run_id: str,
    trade_id: str,
    snapshot_id: str,
    pricing: FxForwardPricingInput,
) -> FxForwardPricingData:
    trade = provider.get_trade(trade_id)
    trade_fxfwd = provider.get_trade_fxfwd(trade_id)
    spot = provider.get_fx_spot(trade_fxfwd.base_ccy, trade_fxfwd.quote_ccy, snapshot_id)
    snapshot = provider.get_market_snapshot(snapshot_id)
    as_of = pricing.as_of or snapshot.as_of
    base_curve = provider.get_yield_curve(pricing.base_curve_id, snapshot_id)
    quote_curve = provider.get_yield_curve(pricing.quote_curve_id, snapshot_id)
    return FxForwardPricingData(
        run_id=run_id,
        trade=trade,
        trade_fxfwd=trade_fxfwd,
        spot=spot,
        base_curve=base_curve,
        quote_curve=quote_curve,
        pricing=pricing,
        as_of=as_of,
    )
