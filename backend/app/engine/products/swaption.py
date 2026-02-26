from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from typing import Iterable, MutableMapping, Optional, Protocol, Sequence

import numpy as np
from scipy.special import ndtr  # type: ignore[import-untyped]

from app.engine.market.sabr import SabrVolType, sabr_implied_vol
from app.engine.market.yield_curve import YieldCurve
from app.engine.math.bizday import BusinessCalendar, add_business_days, adjust_business_day
from app.engine.math.daycount import year_fraction
from app.engine.math.rate_conversion import Compounding, forward_rate_from_dfs
from app.engine.products.models.schedule_models import (
    CashflowPeriod,
    ModelParamRow,
    SabrInterpolationSpec,
    TradeHeader,
    TradeSwaption,
)
from app.engine.products.pricing_model import (
    PricingModelConfig,
    resolve_pricing_model_config,
)
from app.engine.products.sabr_interpolation import SwaptionAtmSabrInterpolator
from app.engine.products.schedule_utils import (
    LegScheduleSpec,
    build_leg_schedule,
    parse_tenor,
)

_VOL_FLOOR = 1e-10


class SwaptionPricingModel(Enum):
    SHIFTED_BLACK = "SHIFTED_BLACK"
    BACHELIER = "BACHELIER"


class SwaptionVolInterpModel(Enum):
    SHIFTED_SABR = "SHIFTED_SABR"


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    as_of: date


@dataclass(frozen=True)
class VolSwaptionPoint:
    vol_id: str
    snapshot_id: str
    ccy: str
    ref_rate_id: str
    index_tenor: str
    expiry_tenor: Optional[str]
    expiry_date: Optional[date]
    swap_tenor: str
    x_years: float
    vol_daycount: str
    smile_type: str
    strike_rate: Optional[float]
    moneyness: Optional[float]
    quote_type: str
    quote_shift: float
    sabr_shift: float
    sigma: float
    source_symbol: Optional[str] = None
    surface_tag: Optional[str] = None


@dataclass(frozen=True)
class SwaptionPricingInput:
    discount_curve_id: str
    forward_curve_id: Optional[str]
    discount_daycount: str
    forward_daycount: str
    vol_quote_type: Optional[str] = None
    pricing_model: Optional[str] = None
    vol_interp_model: Optional[str] = None
    model_tag: Optional[str] = None
    surface_tag: Optional[str] = None
    as_of: Optional[date] = None


@dataclass(frozen=True)
class SwaptionPricingData:
    run_id: str
    trade: TradeHeader
    trade_swaption: TradeSwaption
    snapshot: MarketSnapshot
    discount_curve: YieldCurve
    forward_curve: YieldCurve
    vol_points: tuple[VolSwaptionPoint, ...]
    model_param_rows: tuple[ModelParamRow, ...]
    sabr_interpolation_spec: SabrInterpolationSpec
    sabr_interpolator: SwaptionAtmSabrInterpolator
    pricing: SwaptionPricingInput
    as_of: date
    fixed_calendar: BusinessCalendar
    float_calendar: BusinessCalendar
    cash_settle_calendar: Optional[BusinessCalendar]


@dataclass(frozen=True)
class SwaptionPVResult:
    pv: float
    forward_swap_rate: float
    strike_rate: float
    implied_vol: float
    annuity: float
    settlement_date: date


class SwaptionDataProvider(Protocol):
    def get_trade(self, trade_id: str) -> TradeHeader: ...

    def get_trade_swaption(self, trade_id: str) -> TradeSwaption: ...

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot: ...

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve: ...

    def get_vol_swaption(
        self,
        *,
        snapshot_id: str,
        ccy: str,
        ref_rate_id: str,
        index_tenor: str,
        quote_type: str,
        surface_tag: Optional[str] = None,
    ) -> Sequence[VolSwaptionPoint]: ...

    def get_model_params(
        self,
        *,
        snapshot_id: str,
        model_tag: str,
        scope: str,
        param_key: str,
    ) -> Sequence[ModelParamRow]: ...

    def get_business_calendar(self, cal_id: str) -> BusinessCalendar: ...

    def get_pricing_models(
        self,
        *,
        profile_id: str,
        product: str,
    ) -> Sequence[PricingModelConfig]: ...

    def get_sabr_interpolation_spec(
        self,
        *,
        product: str,
        model_tag: str,
    ) -> SabrInterpolationSpec: ...

def _normalize_pricing_model(value: str) -> SwaptionPricingModel:
    key = value.strip().upper()
    match key:
        case "SHIFTED_BLACK":
            return SwaptionPricingModel.SHIFTED_BLACK
        case "BACHELIER":
            return SwaptionPricingModel.BACHELIER
        case _:
            raise ValueError(f"Unsupported swaption pricing_model: {value!r}")


def _normalize_vol_interp_model(value: str) -> SwaptionVolInterpModel:
    key = value.strip().upper()
    match key:
        case "SHIFTED_SABR":
            return SwaptionVolInterpModel.SHIFTED_SABR
        case _:
            raise ValueError(f"Unsupported swaption vol_interp_model: {value!r}")


def _resolve_effective_pricing_input(
    provider: SwaptionDataProvider,
    *,
    trade: TradeHeader,
    pricing: SwaptionPricingInput,
) -> SwaptionPricingInput:
    model_cfg: Optional[PricingModelConfig] = None
    if trade.pricing_profile_id is not None:
        rows = provider.get_pricing_models(
            profile_id=trade.pricing_profile_id,
            product=trade.product,
        )
        model_cfg = resolve_pricing_model_config(rows, ccy=trade.ccy)
        if model_cfg is None:
            raise ValueError(
                "pricing_model is not defined for trade pricing_profile_id/product/ccy."
            )

    resolved_pricing_model = (
        pricing.pricing_model or (model_cfg.pricing_model if model_cfg is not None else None)
    )
    if resolved_pricing_model is None:
        raise ValueError("pricing_model must be provided via pricing_model table or pricing input.")

    resolved_vol_interp_model = (
        pricing.vol_interp_model
        or (model_cfg.vol_interp_model if model_cfg is not None else None)
    )
    if resolved_vol_interp_model is None:
        raise ValueError("vol_interp_model must be provided via pricing_model table or pricing input.")

    resolved_model_tag = (
        pricing.model_tag
        or (model_cfg.model_tag if model_cfg is not None else None)
    )
    if resolved_model_tag is None:
        raise ValueError("model_tag must be provided via pricing_model table or pricing input.")

    resolved_vol_quote_type = (
        pricing.vol_quote_type
        or (model_cfg.vol_quote_type if model_cfg is not None else None)
    )
    if resolved_vol_quote_type is None:
        raise ValueError("vol_quote_type must be provided via pricing_model table or pricing input.")
    resolved_surface_tag = (
        pricing.surface_tag or (model_cfg.surface_tag if model_cfg is not None else None)
    )
    return replace(
        pricing,
        pricing_model=resolved_pricing_model,
        vol_interp_model=resolved_vol_interp_model,
        model_tag=resolved_model_tag,
        vol_quote_type=resolved_vol_quote_type,
        surface_tag=resolved_surface_tag,
    )


def _sabr_vol_type_for_model(model: SwaptionPricingModel) -> SabrVolType:
    match model:
        case SwaptionPricingModel.SHIFTED_BLACK:
            return SabrVolType.LOGNORMAL
        case SwaptionPricingModel.BACHELIER:
            return SabrVolType.NORMAL
        case _:
            raise ValueError(f"Unsupported swaption pricing model: {model!r}")


def _is_call(cp_flag: str) -> bool:
    key = cp_flag.strip().upper()
    if key == "C":
        return True
    if key == "P":
        return False
    raise ValueError("trade_swaption.cp_flag must be 'C' or 'P'.")


def _position_sign(buy_sell: str) -> float:
    key = buy_sell.strip().upper()
    if key in {"BUY", "B", "LONG"}:
        return 1.0
    if key in {"SELL", "S", "SHORT"}:
        return -1.0
    raise ValueError("trade.buy_sell must be BUY/SELL style value.")


def _tenor_to_years(tenor: str) -> float:
    t = parse_tenor(tenor)
    return float(t.months / 12.0 + t.days / 365.0)


def _year_fractions_from_dates(base: date, dates: Iterable[date], daycount: str) -> np.ndarray:
    return np.array([year_fraction(base, d, daycount) for d in dates], dtype=float)


def _shifted_black_option_value(
    forward_rate: float,
    strike_rate: float,
    sigma: float,
    expiry: float,
    *,
    shift: float,
    is_call: bool,
) -> float:
    f = float(forward_rate + shift)
    k = float(strike_rate + shift)
    if f <= 0.0 or k <= 0.0:
        raise ValueError("Shifted Black requires forward+shift and strike+shift > 0.")

    intrinsic = max(f - k, 0.0) if is_call else max(k - f, 0.0)
    if sigma <= 0.0 or expiry <= 0.0:
        return float(intrinsic)

    std = sigma * np.sqrt(expiry)
    d1 = (np.log(f / k) + 0.5 * std * std) / std
    d2 = d1 - std
    call = f * ndtr(d1) - k * ndtr(d2)
    if is_call:
        return float(call)
    return float(call - (f - k))


def _normal_option_value(
    forward_rate: float,
    strike_rate: float,
    sigma: float,
    expiry: float,
    *,
    is_call: bool,
) -> float:
    intrinsic = max(forward_rate - strike_rate, 0.0) if is_call else max(strike_rate - forward_rate, 0.0)
    if sigma <= 0.0 or expiry <= 0.0:
        return float(intrinsic)
    std = sigma * np.sqrt(expiry)
    x = (forward_rate - strike_rate) / std
    call = (forward_rate - strike_rate) * ndtr(x) + std * np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)
    if is_call:
        return float(call)
    return float(call - (forward_rate - strike_rate))


def _resolve_swap_start_date(
    trade_swaption: TradeSwaption,
    *,
    float_calendar: BusinessCalendar,
) -> date:
    if trade_swaption.swap_start_date is not None:
        return adjust_business_day(
            trade_swaption.swap_start_date,
            trade_swaption.swap_float_bdc,
            float_calendar,
        )

    anchor = add_business_days(
        trade_swaption.expiry_date,
        trade_swaption.swap_spot_lag_bd,
        float_calendar,
    )
    return adjust_business_day(anchor, trade_swaption.swap_float_bdc, float_calendar)


def _build_leg_periods(
    trade_swaption: TradeSwaption,
    *,
    swap_start_date: date,
    fixed_calendar: BusinessCalendar,
    float_calendar: BusinessCalendar,
) -> tuple[tuple[CashflowPeriod, ...], tuple[CashflowPeriod, ...]]:
    fixed_spec = LegScheduleSpec(
        freq=trade_swaption.swap_fixed_freq,
        calendar=fixed_calendar,
        payment_calendar=fixed_calendar,
        bdc=trade_swaption.swap_fixed_bdc,
    )
    float_spec = LegScheduleSpec(
        freq=trade_swaption.swap_float_freq,
        calendar=float_calendar,
        payment_calendar=float_calendar,
        bdc=trade_swaption.swap_float_bdc,
    )
    fixed_leg = build_leg_schedule(swap_start_date, trade_swaption.swap_maturity, fixed_spec)
    float_leg = build_leg_schedule(swap_start_date, trade_swaption.swap_maturity, float_spec)
    return fixed_leg, float_leg


def _forward_swap_rate_and_annuity(
    *,
    as_of: date,
    trade_swaption: TradeSwaption,
    fixed_leg,
    float_leg,
    discount_curve: YieldCurve,
    forward_curve: YieldCurve,
    discount_daycount: str,
    forward_daycount: str,
) -> tuple[float, float]:
    fixed_pay_dates = [p.payment_date for p in fixed_leg]
    fixed_accrual = np.array(
        [year_fraction(p.accrual_start, p.accrual_end, trade_swaption.swap_fixed_dc) for p in fixed_leg],
        dtype=float,
    )
    fixed_t = _year_fractions_from_dates(as_of, fixed_pay_dates, discount_daycount)
    fixed_df = np.asarray(discount_curve.df(fixed_t), dtype=float)
    annuity = float(np.sum(fixed_accrual * fixed_df))
    if annuity <= 0.0:
        raise ValueError("Underlying swap annuity must be positive.")

    float_start_dates = [p.accrual_start for p in float_leg]
    float_end_dates = [p.accrual_end for p in float_leg]
    float_pay_dates = [p.payment_date for p in float_leg]

    float_accrual = np.array(
        [year_fraction(s, e, trade_swaption.swap_float_dc) for s, e in zip(float_start_dates, float_end_dates)],
        dtype=float,
    )
    t_start = _year_fractions_from_dates(as_of, float_start_dates, forward_daycount)
    t_end = _year_fractions_from_dates(as_of, float_end_dates, forward_daycount)
    df_start = np.asarray(forward_curve.df(t_start), dtype=float)
    df_end = np.asarray(forward_curve.df(t_end), dtype=float)
    forwards = np.asarray(
        forward_rate_from_dfs(df_start, df_end, float_accrual, Compounding.SIMPLE),
        dtype=float,
    )

    float_t_pay = _year_fractions_from_dates(as_of, float_pay_dates, discount_daycount)
    float_df_pay = np.asarray(discount_curve.df(float_t_pay), dtype=float)
    spread = float(trade_swaption.swap_spread)
    float_leg_pv = float(np.sum((forwards + spread) * float_accrual * float_df_pay))
    forward_swap_rate = float(float_leg_pv / annuity)
    return forward_swap_rate, annuity


def _resolve_settlement_date(
    trade_swaption: TradeSwaption,
    *,
    fixed_calendar: BusinessCalendar,
    cash_settle_calendar: Optional[BusinessCalendar],
) -> date:
    settlement = trade_swaption.settlement.strip().upper()
    if settlement == "PHYS":
        return trade_swaption.expiry_date

    if settlement != "CASH":
        raise ValueError(f"Unsupported settlement: {trade_swaption.settlement!r}")

    lag = 0 if trade_swaption.cash_settle_lag_bd is None else int(trade_swaption.cash_settle_lag_bd)
    cal = cash_settle_calendar if cash_settle_calendar is not None else fixed_calendar
    bdc = trade_swaption.cash_settle_bdc if trade_swaption.cash_settle_bdc is not None else trade_swaption.swap_fixed_bdc
    anchor = add_business_days(trade_swaption.expiry_date, lag, cal)
    return adjust_business_day(anchor, bdc, cal)


def _build_vol_shift_surface(vol_points: Sequence[VolSwaptionPoint]) -> dict[tuple[float, float], float]:
    out: dict[tuple[float, float], float] = {}
    for row in vol_points:
        key = (_round_years(row.x_years), _round_years(_tenor_to_years(row.swap_tenor)))
        prev = out.get(key)
        if prev is None:
            out[key] = float(row.sabr_shift)
            continue
        if abs(prev - float(row.sabr_shift)) > 1e-12:
            raise ValueError(
                "vol_swaption.sabr_shift must be unique per (expiry, swap_tenor) node."
            )
    return out


def _build_swaption_sabr_interpolator(
    *,
    model_param_rows: Sequence[ModelParamRow],
    vol_points: Sequence[VolSwaptionPoint],
    as_of: date,
    pricing: SwaptionPricingInput,
    sabr_interpolation_spec: SabrInterpolationSpec,
    forward_rate_index_key: str,
) -> SwaptionAtmSabrInterpolator:
    if pricing.pricing_model is None:
        raise ValueError("pricing_model must be resolved before building SABR interpolator.")
    if pricing.vol_interp_model is None:
        raise ValueError("vol_interp_model must be resolved before building SABR interpolator.")
    model = _normalize_pricing_model(pricing.pricing_model)
    vol_interp_model = _normalize_vol_interp_model(pricing.vol_interp_model)
    match vol_interp_model:
        case SwaptionVolInterpModel.SHIFTED_SABR:
            pass
        case _:
            raise ValueError(f"Unsupported swaption vol_interp_model: {pricing.vol_interp_model!r}")
    sabr_vol_type = _sabr_vol_type_for_model(model)
    vol_shift_surface = _build_vol_shift_surface(vol_points)
    return SwaptionAtmSabrInterpolator(
        model_param_rows,
        as_of=as_of,
        daycount=pricing.forward_daycount,
        vol_type=sabr_vol_type,
        interpolation_spec=sabr_interpolation_spec,
        vol_points=vol_points,
        vol_shift_surface=vol_shift_surface,
        forward_rate_index_key=forward_rate_index_key,
    )


def _swaption_sabr_cache_key(
    *,
    snapshot_id: str,
    as_of: date,
    ccy: str,
    ref_rate_id: str,
    index_tenor: str,
    pricing: SwaptionPricingInput,
) -> tuple[str, ...]:
    if pricing.model_tag is None:
        raise ValueError("model_tag must be resolved before building SABR cache key.")
    if pricing.pricing_model is None:
        raise ValueError("pricing_model must be resolved before building SABR cache key.")
    if pricing.vol_interp_model is None:
        raise ValueError("vol_interp_model must be resolved before building SABR cache key.")
    if pricing.vol_quote_type is None:
        raise ValueError("vol_quote_type must be resolved before building SABR cache key.")
    return (
        snapshot_id,
        as_of.isoformat(),
        ccy,
        ref_rate_id,
        index_tenor,
        pricing.model_tag,
        pricing.pricing_model,
        pricing.vol_interp_model,
        pricing.vol_quote_type,
        "" if pricing.surface_tag is None else pricing.surface_tag,
        pricing.forward_daycount,
    )


def _round_years(x: float) -> float:
    return round(float(x), 12)


def _irr_settlement_annuity(
    *,
    fixed_leg: Sequence[CashflowPeriod],
    fixed_daycount: str,
    swap_rate: float,
) -> float:
    accruals = np.array(
        [year_fraction(p.accrual_start, p.accrual_end, fixed_daycount) for p in fixed_leg],
        dtype=float,
    )
    if accruals.size == 0:
        raise ValueError("Fixed leg schedule is empty for IRR annuity.")
    annuity = 0.0
    df = 1.0
    for alpha in accruals:
        den = 1.0 + swap_rate * float(alpha)
        if den <= 0.0:
            raise ValueError(
                "PAR_YIELD_ANN IRR annuity is not defined because 1 + swap_rate * accrual <= 0."
            )
        df /= den
        annuity += float(alpha) * df
    return float(annuity)


def _price_swaption_european(data: SwaptionPricingData) -> SwaptionPVResult:
    trade_swaption = data.trade_swaption
    as_of = data.as_of

    swap_start = _resolve_swap_start_date(trade_swaption, float_calendar=data.float_calendar)
    if trade_swaption.swap_maturity <= swap_start:
        raise ValueError("trade_swaption.swap_maturity must be after swap start date.")

    fixed_leg, float_leg = _build_leg_periods(
        trade_swaption,
        swap_start_date=swap_start,
        fixed_calendar=data.fixed_calendar,
        float_calendar=data.float_calendar,
    )
    forward_swap_rate, annuity = _forward_swap_rate_and_annuity(
        as_of=as_of,
        trade_swaption=trade_swaption,
        fixed_leg=fixed_leg,
        float_leg=float_leg,
        discount_curve=data.discount_curve,
        forward_curve=data.forward_curve,
        discount_daycount=data.pricing.discount_daycount,
        forward_daycount=data.pricing.forward_daycount,
    )

    strike = (
        forward_swap_rate
        if trade_swaption.swap_fixed_rate is None
        else float(trade_swaption.swap_fixed_rate)
    )
    expiry = float(max(year_fraction(as_of, trade_swaption.expiry_date, data.pricing.forward_daycount), 0.0))
    swap_years = float(
        year_fraction(swap_start, trade_swaption.swap_maturity, data.pricing.forward_daycount)
    )

    if data.pricing.pricing_model is None:
        raise ValueError("pricing_model must be resolved before pricing.")
    if data.pricing.vol_interp_model is None:
        raise ValueError("vol_interp_model must be resolved before pricing.")
    model = _normalize_pricing_model(data.pricing.pricing_model)
    sabr_vol_type = _sabr_vol_type_for_model(model)
    sabr_params = data.sabr_interpolator.resolve(
        expiry=expiry,
        swap_years=swap_years,
        forward=forward_swap_rate,
    )

    if expiry <= 0.0:
        sigma = 0.0
    else:
        sigma = float(
            np.asarray(
                sabr_implied_vol(
                    [strike],
                    forward_swap_rate,
                    expiry,
                    sabr_params,
                    vol_type=sabr_vol_type,
                ),
                dtype=float,
            )[0]
        )

    is_call = _is_call(trade_swaption.cp_flag)

    match model:
        case SwaptionPricingModel.SHIFTED_BLACK:
            option_value = _shifted_black_option_value(
                forward_swap_rate,
                strike,
                sigma,
                expiry,
                shift=sabr_params.shift,
                is_call=is_call,
            )
        case SwaptionPricingModel.BACHELIER:
            option_value = _normal_option_value(
                forward_swap_rate,
                strike,
                sigma,
                expiry,
                is_call=is_call,
            )
        case _:
            raise ValueError(f"Unsupported swaption pricing_model: {data.pricing.pricing_model!r}")

    settlement_date = _resolve_settlement_date(
        trade_swaption,
        fixed_calendar=data.fixed_calendar,
        cash_settle_calendar=data.cash_settle_calendar,
    )
    annuity_multiplier = annuity

    if trade_swaption.settlement.strip().upper() == "CASH":
        method = (
            "DISCOUNTED_SWAP_PV"
            if trade_swaption.cash_settle_method is None
            else trade_swaption.cash_settle_method.strip().upper()
        )
        t_settle = float(year_fraction(as_of, settlement_date, data.pricing.discount_daycount))
        df_settle = float(np.asarray(data.discount_curve.df(t_settle), dtype=float))
        if method == "PAR_YIELD_ANN":
            annuity_settle = _irr_settlement_annuity(
                fixed_leg=fixed_leg,
                fixed_daycount=trade_swaption.swap_fixed_dc,
                swap_rate=forward_swap_rate,
            )
            annuity_multiplier = float(df_settle * annuity_settle)
        elif method == "DISCOUNTED_SWAP_PV":
            annuity_multiplier = annuity
        else:
            raise ValueError(f"Unsupported cash_settle_method: {trade_swaption.cash_settle_method!r}")

    sign = _position_sign(data.trade.buy_sell)
    pv = float(sign * data.trade.notional * annuity_multiplier * option_value)

    return SwaptionPVResult(
        pv=pv,
        forward_swap_rate=float(forward_swap_rate),
        strike_rate=float(strike),
        implied_vol=float(sigma),
        annuity=float(annuity_multiplier),
        settlement_date=settlement_date,
    )


def price_swaption_from_data(data: SwaptionPricingData) -> SwaptionPVResult:
    style = data.trade_swaption.option_style.strip().upper()
    if style == "EUROPEAN":
        return _price_swaption_european(data)
    if style in {"AMERICAN", "BERMUDAN"}:
        raise NotImplementedError(
            f"{style} swaption pricing is not implemented yet. Use this dispatch point for future tree/MC engines."
        )
    raise ValueError(f"Unsupported option_style: {data.trade_swaption.option_style!r}")


def load_swaption_pricing_data(
    provider: SwaptionDataProvider,
    *,
    run_id: str,
    trade_id: str,
    snapshot_id: str,
    pricing: SwaptionPricingInput,
    sabr_interpolator_cache: Optional[
        MutableMapping[tuple[str, ...], SwaptionAtmSabrInterpolator]
    ] = None,
) -> SwaptionPricingData:
    trade = provider.get_trade(trade_id)
    pricing_eff = _resolve_effective_pricing_input(provider, trade=trade, pricing=pricing)
    trade_swaption = provider.get_trade_swaption(trade_id)
    snapshot = provider.get_market_snapshot(snapshot_id)
    as_of = pricing_eff.as_of if pricing_eff.as_of is not None else snapshot.as_of

    discount_curve = provider.get_yield_curve(pricing_eff.discount_curve_id, snapshot_id)
    forward_curve_id = pricing_eff.forward_curve_id or pricing_eff.discount_curve_id
    forward_curve = provider.get_yield_curve(forward_curve_id, snapshot_id)

    if pricing_eff.vol_quote_type is None:
        raise ValueError("vol_quote_type must be resolved before loading swaption data.")
    vol_points = provider.get_vol_swaption(
        snapshot_id=snapshot_id,
        ccy=trade_swaption.ccy,
        ref_rate_id=trade_swaption.swap_index_id,
        index_tenor=trade_swaption.swap_index_tenor,
        quote_type=pricing_eff.vol_quote_type,
        surface_tag=pricing_eff.surface_tag,
    )
    if not vol_points:
        raise ValueError("vol_swaption rows are missing for swaption pricing.")

    if pricing_eff.model_tag is None:
        raise ValueError("model_tag must be resolved before loading swaption data.")
    sabr_interpolation_spec = provider.get_sabr_interpolation_spec(
        product=trade.product,
        model_tag=pricing_eff.model_tag,
    )
    global_rows = provider.get_model_params(
        snapshot_id=snapshot_id,
        model_tag=pricing_eff.model_tag,
        scope="GLOBAL",
        param_key="GLOBAL",
    )
    ccy_rows = provider.get_model_params(
        snapshot_id=snapshot_id,
        model_tag=pricing_eff.model_tag,
        scope="CCY",
        param_key=trade_swaption.ccy,
    )
    index_rows = provider.get_model_params(
        snapshot_id=snapshot_id,
        model_tag=pricing_eff.model_tag,
        scope="INDEX",
        param_key=trade_swaption.swap_index_id,
    )
    model_param_rows = tuple(global_rows) + tuple(ccy_rows) + tuple(index_rows)

    cache_key = _swaption_sabr_cache_key(
        snapshot_id=snapshot_id,
        as_of=as_of,
        ccy=trade_swaption.ccy,
        ref_rate_id=trade_swaption.swap_index_id,
        index_tenor=trade_swaption.swap_index_tenor,
        pricing=pricing_eff,
    )
    sabr_interpolator = (
        None
        if sabr_interpolator_cache is None
        else sabr_interpolator_cache.get(cache_key)
    )
    if sabr_interpolator is None:
        sabr_interpolator = _build_swaption_sabr_interpolator(
            model_param_rows=model_param_rows,
            vol_points=vol_points,
            as_of=as_of,
            pricing=pricing_eff,
            sabr_interpolation_spec=sabr_interpolation_spec,
            forward_rate_index_key=trade_swaption.swap_index_id,
        )
        if sabr_interpolator_cache is not None:
            sabr_interpolator_cache[cache_key] = sabr_interpolator

    fixed_calendar = provider.get_business_calendar(trade_swaption.swap_fixed_cal)
    float_calendar = provider.get_business_calendar(trade_swaption.swap_float_cal)
    cash_settle_calendar: Optional[BusinessCalendar] = None
    if trade_swaption.cash_settle_cal_id is not None:
        cash_settle_calendar = provider.get_business_calendar(trade_swaption.cash_settle_cal_id)

    return SwaptionPricingData(
        run_id=run_id,
        trade=trade,
        trade_swaption=trade_swaption,
        snapshot=snapshot,
        discount_curve=discount_curve,
        forward_curve=forward_curve,
        vol_points=tuple(vol_points),
        model_param_rows=model_param_rows,
        sabr_interpolation_spec=sabr_interpolation_spec,
        sabr_interpolator=sabr_interpolator,
        pricing=pricing_eff,
        as_of=as_of,
        fixed_calendar=fixed_calendar,
        float_calendar=float_calendar,
        cash_settle_calendar=cash_settle_calendar,
    )
