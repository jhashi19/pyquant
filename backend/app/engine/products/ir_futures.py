from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol

import numpy as np

from app.engine.market.yield_curve import YieldCurve
from app.engine.math.daycount import year_fraction
from app.engine.math.rate_conversion import Compounding, forward_rate_from_dfs
from app.engine.products.models.schedule_models import RefRateRule


@dataclass(frozen=True)
class IrFuturesDef:
    fut_code: str
    display_name: str
    exchange_code: str
    ccy: str
    underlying_ref_rate_id: Optional[str]
    contract_notional: float
    tick_size: float
    tick_value: float
    quote_conv: str  # PRICE / RATE


@dataclass(frozen=True)
class MarketQuoteIrFutures:
    fut_code: str
    contract_month: str
    price_mid: float
    price_bid: Optional[float] = None
    price_ask: Optional[float] = None


@dataclass(frozen=True)
class TradeIrFutures:
    trade_id: str
    fut_code: str
    contract_month: str
    last_trading_date: Optional[date]
    position_lots: int
    price_agreed: float
    margin_style: str
    ref_rate_id: Optional[str]
    accrual_start_date: date
    accrual_end_date: date
    accrual_daycount: Optional[str]
    convexity_model: str
    convexity_adj_rate: float
    hw_mean_reversion: Optional[float]
    hw_vol: Optional[float]
    cal_id_override: Optional[str] = None


@dataclass(frozen=True)
class IrFuturesPricingInput:
    input_side: str = "MID"
    forward_curve_id: Optional[str] = None
    forward_curve_daycount: Optional[str] = None
    forward_rate_compounding: Compounding | str = Compounding.SIMPLE
    delta_shift_bp: float = 1.0
    delta_scheme: str = "CENTRAL"
    compute_theoretical: bool = True


@dataclass(frozen=True)
class IrFuturesPricingData:
    run_id: str
    trade: TradeIrFutures
    fut_def: IrFuturesDef
    quote: MarketQuoteIrFutures
    pricing: IrFuturesPricingInput
    as_of: Optional[date] = None
    forward_curve: Optional[YieldCurve] = None
    ref_rate_rule: Optional[RefRateRule] = None


@dataclass(frozen=True)
class IrFuturesPVResult:
    pv: float
    price_mark: float
    price_agreed: float
    rate_mark: float
    rate_agreed: float
    pv_theoretical: Optional[float] = None
    price_theoretical: Optional[float] = None
    rate_theoretical: Optional[float] = None
    forward_rate: Optional[float] = None
    convexity_adjustment_rate: float = 0.0
    delta_pv_parallel_per_bp: Optional[float] = None


class IrFuturesDataProvider(Protocol):
    def get_trade_ir_futures(self, trade_id: str) -> TradeIrFutures: ...

    def get_ir_futures_def(self, fut_code: str) -> IrFuturesDef: ...

    def get_market_quote_ir_futures(
        self, fut_code: str, contract_month: str, snapshot_id: str
    ) -> MarketQuoteIrFutures: ...

    def get_market_snapshot(self, snapshot_id: str) -> "MarketSnapshot": ...

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve: ...

    def get_ref_rate_rule(self, index_id: str) -> RefRateRule: ...


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    as_of: date


def _resolve_mark_price(quote: MarketQuoteIrFutures, side: str) -> float:
    key = side.upper()
    if key == "MID":
        return quote.price_mid
    if key == "BID":
        if quote.price_bid is None:
            raise ValueError("price_bid is required for BID pricing.")
        return quote.price_bid
    if key == "ASK":
        if quote.price_ask is None:
            raise ValueError("price_ask is required for ASK pricing.")
        return quote.price_ask
    raise ValueError(f"Unsupported input_side: {side!r}")


def _quoted_rate_from_price(price: float, quote_conv: str) -> float:
    conv = quote_conv.upper()
    if conv == "PRICE":
        return 100.0 - price
    if conv == "RATE":
        return price
    raise ValueError(f"Unsupported quote_conv: {quote_conv!r}")


def _price_from_decimal_rate(rate_decimal: float, quote_conv: str) -> float:
    conv = quote_conv.upper()
    if conv == "PRICE":
        return 100.0 - 100.0 * rate_decimal
    if conv == "RATE":
        return rate_decimal
    raise ValueError(f"Unsupported quote_conv: {quote_conv!r}")


def _validate_trade_period(trade: TradeIrFutures) -> None:
    if trade.accrual_end_date <= trade.accrual_start_date:
        raise ValueError("trade_ir_futures accrual_end_date must be after accrual_start_date.")


def _resolve_ref_rate_id(trade: TradeIrFutures, fut_def: IrFuturesDef) -> Optional[str]:
    return trade.ref_rate_id if trade.ref_rate_id is not None else fut_def.underlying_ref_rate_id


def _resolve_accrual_daycount(
    trade: TradeIrFutures,
    ref_rate_rule: Optional[RefRateRule],
) -> str:
    if trade.accrual_daycount is not None:
        return trade.accrual_daycount
    if ref_rate_rule is not None:
        return ref_rate_rule.daycount
    raise ValueError(
        "accrual_daycount is required when neither trade_ir_futures.accrual_daycount nor ref_rate_rule.daycount is available."
    )


def _resolve_curve_daycount(
    pricing: IrFuturesPricingInput,
    ref_rate_rule: Optional[RefRateRule],
) -> str:
    if pricing.forward_curve_daycount is not None:
        return pricing.forward_curve_daycount
    if ref_rate_rule is not None:
        return ref_rate_rule.daycount
    raise ValueError(
        "forward_curve_daycount is required when neither pricing.forward_curve_daycount nor ref_rate_rule.daycount is available."
    )


def _curve_window(
    trade: TradeIrFutures,
    *,
    as_of: date,
    curve_daycount: str,
    accrual_daycount: str,
) -> tuple[float, float, float]:
    t_start = float(year_fraction(as_of, trade.accrual_start_date, curve_daycount))
    t_end = float(year_fraction(as_of, trade.accrual_end_date, curve_daycount))
    accrual = float(
        year_fraction(trade.accrual_start_date, trade.accrual_end_date, accrual_daycount)
    )
    if accrual <= 0.0:
        raise ValueError("trade_ir_futures accrual year fraction must be positive.")
    return t_start, t_end, accrual


def _forward_rate_from_curve(
    curve: YieldCurve,
    *,
    t_start: float,
    t_end: float,
    accrual: float,
    compounding: Compounding | str,
) -> float:
    df_start = float(np.asarray(curve.df(t_start)))
    df_end = float(np.asarray(curve.df(t_end)))
    return float(forward_rate_from_dfs(df_start, df_end, accrual, compounding))


def _forward_rate_with_parallel_zero_shift(
    curve: YieldCurve,
    *,
    t_start: float,
    t_end: float,
    accrual: float,
    compounding: Compounding | str,
    shift_rate: float,
) -> float:
    df_start = float(np.asarray(curve.df(t_start)))
    df_end = float(np.asarray(curve.df(t_end)))
    df_start_shift = df_start * float(np.exp(-shift_rate * t_start))
    df_end_shift = df_end * float(np.exp(-shift_rate * t_end))
    return float(
        forward_rate_from_dfs(df_start_shift, df_end_shift, accrual, compounding)
    )


def _convexity_adjustment_rate(
    trade: TradeIrFutures,
    *,
    as_of: date,
    curve_daycount: str,
) -> float:
    model = trade.convexity_model.strip().upper()
    additive = float(trade.convexity_adj_rate)
    if model == "NONE":
        return additive
    if model == "ADDITIVE":
        return additive
    if model != "HW1F":
        raise ValueError(f"Unsupported convexity_model: {trade.convexity_model!r}")
    if trade.hw_mean_reversion is None or trade.hw_vol is None:
        raise ValueError("HW1F convexity_model requires hw_mean_reversion and hw_vol.")
    a = float(trade.hw_mean_reversion)
    sigma = float(trade.hw_vol)
    if a < 0.0 or sigma < 0.0:
        raise ValueError("hw_mean_reversion and hw_vol must be non-negative.")

    t1 = float(max(year_fraction(as_of, trade.accrual_start_date, curve_daycount), 0.0))
    t2 = float(max(year_fraction(as_of, trade.accrual_end_date, curve_daycount), 0.0))
    delta = max(t2 - t1, 0.0)
    if sigma <= 0.0 or delta <= 0.0:
        return additive

    # Hull-White 1F approximation for futures-vs-forward convexity adjustment (rate units).
    if a <= 1e-8:
        hw_bias = sigma * sigma * t1 * delta * delta
    else:
        numer1 = 1.0 - np.exp(-a * delta)
        numer2 = 1.0 - np.exp(-2.0 * a * t1)
        hw_bias = (sigma * sigma / (2.0 * a * a * a)) * (numer1 * numer1) * numer2
    return float(additive + hw_bias)


def _pv_from_prices(
    position_lots: int,
    mark_price: float,
    agreed_price: float,
    tick_size: float,
    tick_value: float,
) -> float:
    return (
        float(position_lots)
        * (mark_price - agreed_price)
        / tick_size
        * tick_value
    )


def _delta_pv_parallel_per_bp(
    trade: TradeIrFutures,
    fut_def: IrFuturesDef,
    *,
    forward_curve: YieldCurve,
    t_start: float,
    t_end: float,
    accrual: float,
    base_convexity_adj: float,
    compounding: Compounding | str,
    price_agreed: float,
    delta_shift_bp: float,
    delta_scheme: str,
) -> float:
    shift_bp = float(delta_shift_bp)
    if shift_bp <= 0.0:
        raise ValueError("delta_shift_bp must be positive.")
    shift_rate = shift_bp * 1e-4

    f_up = _forward_rate_with_parallel_zero_shift(
        forward_curve,
        t_start=t_start,
        t_end=t_end,
        accrual=accrual,
        compounding=compounding,
        shift_rate=shift_rate,
    )
    rate_up = f_up + base_convexity_adj
    price_up = _price_from_decimal_rate(rate_up, fut_def.quote_conv)
    pv_up = _pv_from_prices(
        trade.position_lots, price_up, price_agreed, fut_def.tick_size, fut_def.tick_value
    )

    scheme = delta_scheme.strip().upper()
    if scheme == "FORWARD":
        f_0 = _forward_rate_from_curve(
            forward_curve,
            t_start=t_start,
            t_end=t_end,
            accrual=accrual,
            compounding=compounding,
        )
        price_0 = _price_from_decimal_rate(f_0 + base_convexity_adj, fut_def.quote_conv)
        pv_0 = _pv_from_prices(
            trade.position_lots, price_0, price_agreed, fut_def.tick_size, fut_def.tick_value
        )
        return (pv_up - pv_0) / shift_bp

    if scheme != "CENTRAL":
        raise ValueError("delta_scheme must be 'CENTRAL' or 'FORWARD'.")

    f_dn = _forward_rate_with_parallel_zero_shift(
        forward_curve,
        t_start=t_start,
        t_end=t_end,
        accrual=accrual,
        compounding=compounding,
        shift_rate=-shift_rate,
    )
    rate_dn = f_dn + base_convexity_adj
    price_dn = _price_from_decimal_rate(rate_dn, fut_def.quote_conv)
    pv_dn = _pv_from_prices(
        trade.position_lots, price_dn, price_agreed, fut_def.tick_size, fut_def.tick_value
    )
    return (pv_up - pv_dn) / (2.0 * shift_bp)


def price_ir_futures_from_data(data: IrFuturesPricingData) -> IrFuturesPVResult:
    if data.fut_def.tick_size <= 0.0:
        raise ValueError("tick_size must be positive.")
    if data.fut_def.tick_value <= 0.0:
        raise ValueError("tick_value must be positive.")
    _validate_trade_period(data.trade)

    price_mark = _resolve_mark_price(data.quote, data.pricing.input_side)
    price_agreed = data.trade.price_agreed
    pv_market = _pv_from_prices(
        data.trade.position_lots,
        price_mark,
        price_agreed,
        data.fut_def.tick_size,
        data.fut_def.tick_value,
    )

    rate_mark = _quoted_rate_from_price(price_mark, data.fut_def.quote_conv)
    rate_agreed = _quoted_rate_from_price(price_agreed, data.fut_def.quote_conv)

    pv_theoretical: Optional[float] = None
    price_theoretical: Optional[float] = None
    rate_theoretical: Optional[float] = None
    forward_rate: Optional[float] = None
    convexity_adj = 0.0
    delta_pv_per_bp: Optional[float] = None

    if data.pricing.compute_theoretical:
        if data.forward_curve is None or data.as_of is None:
            raise ValueError("forward_curve and as_of are required for theoretical pricing.")

        accrual_daycount = _resolve_accrual_daycount(data.trade, data.ref_rate_rule)
        curve_daycount = _resolve_curve_daycount(data.pricing, data.ref_rate_rule)
        t_start, t_end, accrual = _curve_window(
            data.trade,
            as_of=data.as_of,
            curve_daycount=curve_daycount,
            accrual_daycount=accrual_daycount,
        )
        forward_rate = _forward_rate_from_curve(
            data.forward_curve,
            t_start=t_start,
            t_end=t_end,
            accrual=accrual,
            compounding=data.pricing.forward_rate_compounding,
        )
        convexity_adj = _convexity_adjustment_rate(
            data.trade,
            as_of=data.as_of,
            curve_daycount=curve_daycount,
        )
        theo_rate_decimal = forward_rate + convexity_adj
        price_theoretical = _price_from_decimal_rate(theo_rate_decimal, data.fut_def.quote_conv)
        rate_theoretical = _quoted_rate_from_price(
            price_theoretical, data.fut_def.quote_conv
        )
        pv_theoretical = _pv_from_prices(
            data.trade.position_lots,
            price_theoretical,
            price_agreed,
            data.fut_def.tick_size,
            data.fut_def.tick_value,
        )
        delta_pv_per_bp = _delta_pv_parallel_per_bp(
            data.trade,
            data.fut_def,
            forward_curve=data.forward_curve,
            t_start=t_start,
            t_end=t_end,
            accrual=accrual,
            base_convexity_adj=convexity_adj,
            compounding=data.pricing.forward_rate_compounding,
            price_agreed=price_agreed,
            delta_shift_bp=data.pricing.delta_shift_bp,
            delta_scheme=data.pricing.delta_scheme,
        )

    return IrFuturesPVResult(
        pv=float(pv_market),
        price_mark=float(price_mark),
        price_agreed=float(price_agreed),
        rate_mark=float(rate_mark),
        rate_agreed=float(rate_agreed),
        pv_theoretical=(None if pv_theoretical is None else float(pv_theoretical)),
        price_theoretical=(
            None if price_theoretical is None else float(price_theoretical)
        ),
        rate_theoretical=(None if rate_theoretical is None else float(rate_theoretical)),
        forward_rate=(None if forward_rate is None else float(forward_rate)),
        convexity_adjustment_rate=float(convexity_adj),
        delta_pv_parallel_per_bp=(
            None if delta_pv_per_bp is None else float(delta_pv_per_bp)
        ),
    )


def load_ir_futures_pricing_data(
    provider: IrFuturesDataProvider,
    *,
    run_id: str,
    trade_id: str,
    snapshot_id: str,
    pricing: IrFuturesPricingInput,
) -> IrFuturesPricingData:
    trade = provider.get_trade_ir_futures(trade_id)
    fut_def = provider.get_ir_futures_def(trade.fut_code)
    quote = provider.get_market_quote_ir_futures(
        trade.fut_code, trade.contract_month, snapshot_id
    )
    snapshot = provider.get_market_snapshot(snapshot_id)
    as_of = snapshot.as_of

    ref_rate_rule: Optional[RefRateRule] = None
    ref_rate_id = _resolve_ref_rate_id(trade, fut_def)
    if ref_rate_id is not None:
        ref_rate_rule = provider.get_ref_rate_rule(ref_rate_id)

    forward_curve: Optional[YieldCurve] = None
    if pricing.compute_theoretical:
        curve_id = pricing.forward_curve_id
        if curve_id is None:
            raise ValueError("forward_curve_id is required when compute_theoretical=True.")
        forward_curve = provider.get_yield_curve(curve_id, snapshot_id)

    return IrFuturesPricingData(
        run_id=run_id,
        trade=trade,
        fut_def=fut_def,
        quote=quote,
        pricing=pricing,
        as_of=as_of,
        forward_curve=forward_curve,
        ref_rate_rule=ref_rate_rule,
    )
