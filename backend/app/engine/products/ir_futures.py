from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol


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
    cal_id_override: Optional[str] = None


@dataclass(frozen=True)
class IrFuturesPricingInput:
    input_side: str = "MID"


@dataclass(frozen=True)
class IrFuturesPricingData:
    run_id: str
    trade: TradeIrFutures
    fut_def: IrFuturesDef
    quote: MarketQuoteIrFutures
    pricing: IrFuturesPricingInput


@dataclass(frozen=True)
class IrFuturesPVResult:
    pv: float
    price_mark: float
    price_agreed: float
    rate_mark: float
    rate_agreed: float


class IrFuturesDataProvider(Protocol):
    def get_trade_ir_futures(self, trade_id: str) -> TradeIrFutures: ...

    def get_ir_futures_def(self, fut_code: str) -> IrFuturesDef: ...

    def get_market_quote_ir_futures(
        self, fut_code: str, contract_month: str, snapshot_id: str
    ) -> MarketQuoteIrFutures: ...


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


def _implied_rate_from_price(price: float, quote_conv: str) -> float:
    conv = quote_conv.upper()
    if conv == "PRICE":
        return 100.0 - price
    if conv == "RATE":
        return price
    raise ValueError(f"Unsupported quote_conv: {quote_conv!r}")


def price_ir_futures_from_data(data: IrFuturesPricingData) -> IrFuturesPVResult:
    if data.fut_def.tick_size <= 0.0:
        raise ValueError("tick_size must be positive.")
    if data.fut_def.tick_value <= 0.0:
        raise ValueError("tick_value must be positive.")

    price_mark = _resolve_mark_price(data.quote, data.pricing.input_side)
    price_agreed = data.trade.price_agreed
    price_diff = price_mark - price_agreed

    pv = data.trade.position_lots * price_diff / data.fut_def.tick_size * data.fut_def.tick_value

    rate_mark = _implied_rate_from_price(price_mark, data.fut_def.quote_conv)
    rate_agreed = _implied_rate_from_price(price_agreed, data.fut_def.quote_conv)

    return IrFuturesPVResult(
        pv=float(pv),
        price_mark=float(price_mark),
        price_agreed=float(price_agreed),
        rate_mark=float(rate_mark),
        rate_agreed=float(rate_agreed),
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
    return IrFuturesPricingData(
        run_id=run_id,
        trade=trade,
        fut_def=fut_def,
        quote=quote,
        pricing=pricing,
    )
