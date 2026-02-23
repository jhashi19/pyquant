from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from typing import Iterable, Optional, Protocol, Sequence

import numpy as np
from scipy.special import ndtr  # type: ignore[import-untyped]

from app.engine.market.sabr import (
    SabrParams,
    SabrVolType,
    sabr_implied_vol,
)
from app.engine.market.yield_curve import YieldCurve
from app.engine.math.bizday import (
    BusinessCalendar,
    add_business_days,
    adjust_business_day,
)
from app.engine.math.daycount import year_fraction
from app.engine.math.rate_conversion import Compounding, forward_rate_from_dfs
from app.engine.products.models.schedule_models import (
    CapFloorScheduleRow,
    HistoricalFixing,
    LegScheduleSpec,
    ModelParamRow,
    RefRateRule,
    TradeCapFloor,
    TradeHeader,
)
from app.engine.products.pricing_model import (
    PricingModelConfig,
    resolve_pricing_model_config,
)
from app.engine.products.schedule_utils import add_tenor, build_leg_schedule, parse_tenor

_VOL_FLOOR = 1e-10
_STRIKE_TOL = 1e-12
_MONEYNESS_TOL = 1e-12


class CapFloorPricingModel(Enum):
    SHIFTED_BLACK = "SHIFTED_BLACK"
    BACHELIER = "BACHELIER"


class CapFloorVolInterpModel(Enum):
    SHIFTED_SABR = "SHIFTED_SABR"


@dataclass(frozen=True)
class MarketSnapshot:
    snapshot_id: str
    as_of: date


@dataclass(frozen=True)
class VolCapFloorPoint:
    vol_id: str
    snapshot_id: str
    ccy: str
    ref_rate_id: Optional[str]
    index_tenor: str
    expiry_tenor: Optional[str]
    expiry_date: Optional[date]
    x_years: float
    vol_daycount: str
    smile_type: str
    strike_rate: Optional[float]
    quote_type: str
    sigma: float
    sabr_shift: float
    quote_shift: Optional[float] = None
    source_symbol: Optional[str] = None
    surface_tag: Optional[str] = None


@dataclass(frozen=True)
class CapFloorPricingInput:
    discount_curve_id: str
    forward_curve_id: Optional[str]
    discount_daycount: str
    forward_daycount: str
    vol_quote_type: Optional[str] = None
    pricing_model: Optional[str] = None
    vol_interp_model: Optional[str] = None
    model_tag: Optional[str] = None
    surface_tag: Optional[str] = None
    include_settled: bool = False
    as_of: Optional[date] = None


@dataclass(frozen=True)
class CapFloorPricingData:
    run_id: str
    trade: TradeHeader
    trade_capfloor: TradeCapFloor
    ref_rate_rule: RefRateRule
    snapshot: MarketSnapshot
    discount_curve: YieldCurve
    forward_curve: YieldCurve
    vol_points: tuple[VolCapFloorPoint, ...]
    model_param_rows: tuple[ModelParamRow, ...]
    schedule_rows: tuple[CapFloorScheduleRow, ...]
    fixings: tuple[HistoricalFixing, ...]
    pricing: CapFloorPricingInput
    as_of: date


@dataclass(frozen=True)
class CapFloorPVResult:
    pv: float
    pv_future: float
    pv_fixed: float
    optionlet_count: int


class CapFloorDataProvider(Protocol):
    def get_trade(self, trade_id: str) -> TradeHeader: ...

    def get_trade_capfloor(self, trade_id: str) -> TradeCapFloor: ...

    def get_ref_rate_rule(self, index_id: str) -> RefRateRule: ...

    def get_market_snapshot(self, snapshot_id: str) -> MarketSnapshot: ...

    def get_business_calendar(self, cal_id: str) -> BusinessCalendar: ...

    def get_schedule_capfloor(self, trade_id: str) -> Sequence[CapFloorScheduleRow]: ...

    def get_historical_fixings(
        self, index_id: str, start_date: date, end_date: date
    ) -> Sequence[HistoricalFixing]: ...

    def get_yield_curve(self, curve_id: str, snapshot_id: str) -> YieldCurve: ...

    def get_vol_capfloor(
        self,
        *,
        snapshot_id: str,
        ccy: str,
        index_tenor: str,
        quote_type: str,
        ref_rate_id: Optional[str] = None,
        surface_tag: Optional[str] = None,
    ) -> Sequence[VolCapFloorPoint]: ...

    def get_model_params(
        self,
        *,
        snapshot_id: str,
        model_tag: str,
        scope: str,
        param_key: str,
    ) -> Sequence[ModelParamRow]: ...

    def get_pricing_models(
        self,
        *,
        profile_id: str,
        product: str,
    ) -> Sequence[PricingModelConfig]: ...


@dataclass(frozen=True)
class _ParamCandidate:
    value: float
    scope_rank: int
    expiry_years: Optional[float]
    strike_rate: Optional[float]
    moneyness: Optional[float]


def _scope_rank(scope: str) -> int:
    key = scope.upper()
    if key == "GLOBAL":
        return 0
    if key == "CCY":
        return 1
    if key == "INDEX":
        return 2
    return -1


def _normalize_pricing_model(pricing_model: str) -> CapFloorPricingModel:
    key = pricing_model.strip().upper()
    match key:
        case "SHIFTED_BLACK":
            return CapFloorPricingModel.SHIFTED_BLACK
        case "BACHELIER":
            return CapFloorPricingModel.BACHELIER
        case _:
            raise ValueError(f"Unsupported capfloor pricing_model: {pricing_model!r}")


def _normalize_vol_interp_model(vol_interp_model: str) -> CapFloorVolInterpModel:
    key = vol_interp_model.strip().upper()
    match key:
        case "SHIFTED_SABR":
            return CapFloorVolInterpModel.SHIFTED_SABR
        case _:
            raise ValueError(f"Unsupported capfloor vol_interp_model: {vol_interp_model!r}")


def _resolve_effective_pricing_input(
    provider: CapFloorDataProvider,
    *,
    trade: TradeHeader,
    pricing: CapFloorPricingInput,
) -> CapFloorPricingInput:
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


def _normal_optionlet_value(
    forward_rate: float,
    strike_rate: float,
    sigma: float,
    expiry: float,
    *,
    is_call: bool,
) -> float:
    if sigma <= 0.0 or expiry <= 0.0:
        intrinsic = max(forward_rate - strike_rate, 0.0) if is_call else max(strike_rate - forward_rate, 0.0)
        return float(intrinsic)
    std = sigma * np.sqrt(expiry)
    x = (forward_rate - strike_rate) / std
    call = (forward_rate - strike_rate) * ndtr(x) + std * np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)
    if is_call:
        return float(call)
    return float(call - (forward_rate - strike_rate))




def _float_rate_calc_type(rule: RefRateRule) -> str:
    if rule.rate_type == "ON":
        if rule.accrual_conv == "COMPOUND_IN_ARREARS":
            return "OIS_COMPOUNDED"
        if rule.accrual_conv == "AVERAGE":
            return "OIS_AVERAGED"
        return "IBOR_SINGLE"
    return "IBOR_SINGLE"


def _build_obs_window(
    accrual_start: date,
    accrual_end: date,
    *,
    lookback_days: int,
    fixing_calendar: BusinessCalendar,
    fixing_bdc: str,
) -> tuple[date, date]:
    obs_start = add_business_days(accrual_start, -lookback_days, fixing_calendar)
    obs_end = add_business_days(accrual_end, -lookback_days, fixing_calendar)
    obs_start = adjust_business_day(obs_start, fixing_bdc, fixing_calendar)
    obs_end = adjust_business_day(obs_end, fixing_bdc, fixing_calendar)
    return obs_start, obs_end


def _fixing_map(fixings: Iterable[HistoricalFixing]) -> dict[tuple[str, date], float]:
    return {(f.index_id, f.fixing_date): float(f.rate) for f in fixings}


def build_capfloor_schedule_rows(
    trade: TradeHeader,
    trade_capfloor: TradeCapFloor,
    ref_rate_rule: RefRateRule,
    *,
    payment_calendar: BusinessCalendar,
    fixing_calendar: BusinessCalendar,
    as_of: Optional[date] = None,
    fixings: Iterable[HistoricalFixing] = (),
) -> list[CapFloorScheduleRow]:
    leg_spec = LegScheduleSpec(
        freq=trade_capfloor.pay_freq,
        calendar=payment_calendar,
        payment_calendar=payment_calendar,
        bdc=trade_capfloor.pay_bdc,
        fixing_lag=ref_rate_rule.lookback_days,
        fixing_calendar=fixing_calendar,
        fixing_bdc=ref_rate_rule.fixing_bdc,
    )
    periods = build_leg_schedule(trade_capfloor.start_date, trade_capfloor.end_date, leg_spec)
    rate_calc_type = _float_rate_calc_type(ref_rate_rule)
    fixing_map = _fixing_map(fixings)

    rows: list[CapFloorScheduleRow] = []
    is_call = trade_capfloor.cp_flag.upper() == "C"
    cashflow_no = 1
    for p in periods:
        obs_start, obs_end = _build_obs_window(
            p.accrual_start,
            p.accrual_end,
            lookback_days=ref_rate_rule.lookback_days,
            fixing_calendar=fixing_calendar,
            fixing_bdc=str(ref_rate_rule.fixing_bdc),
        )
        accrual = float(year_fraction(p.accrual_start, p.accrual_end, ref_rate_rule.daycount))
        fixing_date = obs_start

        observed_rate: Optional[float] = None
        payoff_rate: Optional[float] = None
        fixed_amount: Optional[float] = None
        amount: Optional[float] = None
        is_fixed = 0
        if as_of is not None and fixing_date <= as_of:
            key = (trade_capfloor.index_id, fixing_date)
            if key in fixing_map:
                observed_rate = fixing_map[key]
                payoff_rate = max(observed_rate - trade_capfloor.strike_rate, 0.0) if is_call else max(
                    trade_capfloor.strike_rate - observed_rate, 0.0
                )
                fixed_amount = float(trade.notional) * accrual * payoff_rate
                amount = fixed_amount
                is_fixed = 1

        rows.append(
            CapFloorScheduleRow(
                trade_id=trade.trade_id,
                cashflow_no=cashflow_no,
                payment_date=p.payment_date,
                ccy=trade_capfloor.ccy,
                cp_flag=trade_capfloor.cp_flag,
                pay_rec=trade_capfloor.pay_rec,
                start_date=p.accrual_start,
                end_date=p.accrual_end,
                daycount=ref_rate_rule.daycount,
                accrual_factor=accrual,
                notional=float(trade.notional),
                strike_rate=float(trade_capfloor.strike_rate),
                index_id=trade_capfloor.index_id,
                rate_calc_type=rate_calc_type,
                fixing_date=fixing_date,
                obs_start_date=obs_start,
                obs_end_date=obs_end,
                observed_rate=observed_rate,
                payoff_rate=payoff_rate,
                amount=amount,
                fixed_amount=fixed_amount,
                is_fixed=is_fixed,
                is_settled=0,
            )
        )
        cashflow_no += 1
    return rows


def _effective_vol_time(row: VolCapFloorPoint, as_of: date) -> float:
    x = float(row.x_years)
    if x > 0.0:
        return x
    if row.expiry_date is not None:
        return float(max(year_fraction(as_of, row.expiry_date, row.vol_daycount), 0.0))
    raise ValueError("vol_capfloor row requires x_years > 0 or expiry_date.")


def _group_vol_points_by_expiry(
    points: Iterable[VolCapFloorPoint], *, as_of: date
) -> dict[float, list[VolCapFloorPoint]]:
    grouped: dict[float, list[VolCapFloorPoint]] = {}
    for row in points:
        t = _effective_vol_time(row, as_of)
        grouped.setdefault(t, []).append(row)
    if not grouped:
        raise ValueError("vol_capfloor rows are required for capfloor pricing.")
    return grouped


def _resolve_surface_fallback_shift(points: Iterable[VolCapFloorPoint]) -> Optional[float]:
    shift_values = {float(p.sabr_shift) for p in points}
    if not shift_values:
        return None
    if len(shift_values) == 1:
        return next(iter(shift_values))
    raise ValueError("vol_capfloor.sabr_shift must be consistent across the selected surface.")


def _select_model_shift(
    param_resolver: "_ModelParamResolver",
    *,
    expiry: float,
    forward: float,
) -> float:
    shift_val = param_resolver.select_value("shift", expiry=expiry, strike=forward, forward=forward)
    return 0.0 if shift_val is None else float(shift_val)


def _node_anchor_date(
    row: VolCapFloorPoint,
    *,
    as_of: date,
) -> Optional[date]:
    if row.expiry_date is not None:
        return row.expiry_date
    if row.expiry_tenor is not None:
        return add_tenor(as_of, parse_tenor(row.expiry_tenor))
    return None


def _resolve_node_forward_rate(
    rows: Sequence[VolCapFloorPoint],
    *,
    node_expiry: float,
    as_of: date,
    forward_curve: YieldCurve,
    forward_daycount: str,
    index_daycount: str,
    index_tenor: str,
) -> float:
    if not rows:
        raise ValueError("vol_capfloor rows are required for node forward calculation.")
    anchor = _node_anchor_date(rows[0], as_of=as_of)
    tenor = parse_tenor(index_tenor)

    if anchor is not None:
        start_date = anchor
        end_date = add_tenor(start_date, tenor)
        t_start = float(max(year_fraction(as_of, start_date, forward_daycount), 0.0))
        t_end = float(max(year_fraction(as_of, end_date, forward_daycount), 0.0))
        accrual = float(max(year_fraction(start_date, end_date, index_daycount), _VOL_FLOOR))
    else:
        tenor_end = add_tenor(as_of, tenor)
        delta_fwd = float(max(year_fraction(as_of, tenor_end, forward_daycount), _VOL_FLOOR))
        delta_idx = float(max(year_fraction(as_of, tenor_end, index_daycount), _VOL_FLOOR))
        t_start = float(max(node_expiry, 0.0))
        t_end = t_start + delta_fwd
        accrual = delta_idx

    df_start = float(np.asarray(forward_curve.df(t_start)))
    df_end = float(np.asarray(forward_curve.df(t_end)))
    return float(forward_rate_from_dfs(df_start, df_end, accrual, Compounding.SIMPLE))


def _node_vol_from_shifted_sabr(
    *,
    node_expiry: float,
    node_forward: float,
    strike_rate: float,
    param_resolver: "_ModelParamResolver",
    pricing_model: CapFloorPricingModel,
) -> float:
    params = param_resolver.resolve(
        expiry=float(node_expiry),
        strike=float(strike_rate),
        forward=float(node_forward),
    )
    vol_type = SabrVolType.LOGNORMAL if pricing_model == CapFloorPricingModel.SHIFTED_BLACK else SabrVolType.NORMAL
    return float(
        np.asarray(
            sabr_implied_vol(
                np.asarray([strike_rate], dtype=float),
                float(node_forward),
                float(max(node_expiry, _VOL_FLOOR)),
                params,
                vol_type=vol_type,
            ),
            dtype=float,
        )[0]
    )


def _build_sabr_node_grid(
    grouped_rows: dict[float, list[VolCapFloorPoint]],
    *,
    as_of: date,
    forward_curve: YieldCurve,
    forward_daycount: str,
    index_daycount: str,
    index_tenor: str,
) -> tuple[np.ndarray, np.ndarray]:
    node_t = np.asarray(sorted(grouped_rows.keys()), dtype=float)
    node_forward = np.empty_like(node_t, dtype=float)
    for i, t in enumerate(node_t):
        node_rows = grouped_rows[float(t)]
        node_forward[i] = _resolve_node_forward_rate(
            node_rows,
            node_expiry=float(t),
            as_of=as_of,
            forward_curve=forward_curve,
            forward_daycount=forward_daycount,
            index_daycount=index_daycount,
            index_tenor=index_tenor,
        )
    return node_t, node_forward


def _build_node_vol_curve_shifted_sabr(
    node_t: np.ndarray,
    node_forward: np.ndarray,
    *,
    strike_rate: float,
    param_resolver: "_ModelParamResolver",
    pricing_model: CapFloorPricingModel,
) -> np.ndarray:
    node_v = np.empty_like(node_t, dtype=float)
    for i in range(node_t.size):
        t = float(node_t[i])
        node_v[i] = _node_vol_from_shifted_sabr(
            node_expiry=t,
            node_forward=float(node_forward[i]),
            strike_rate=strike_rate,
            param_resolver=param_resolver,
            pricing_model=pricing_model,
        )
    return node_v


def _interp_node_vol_curve(
    node_t: np.ndarray,
    node_v: np.ndarray,
    *,
    expiry: float,
) -> float:
    if expiry <= node_t[0]:
        return float(node_v[0])
    if expiry >= node_t[-1]:
        return float(node_v[-1])
    return float(np.interp(expiry, node_t, node_v))


def _shifted_black_optionlet_value(
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

    if sigma <= 0.0 or expiry <= 0.0:
        intrinsic = max(f - k, 0.0) if is_call else max(k - f, 0.0)
        return float(intrinsic)

    std = sigma * np.sqrt(expiry)
    d1 = (np.log(f / k) + 0.5 * std * std) / std
    d2 = d1 - std
    if is_call:
        return float(f * ndtr(d1) - k * ndtr(d2))
    return float(k * ndtr(-d2) - f * ndtr(-d1))


def _resolve_forward_rate_from_schedule(
    row: CapFloorScheduleRow,
    *,
    as_of: date,
    forward_curve: YieldCurve,
    forward_daycount: str,
    index_daycount: str,
) -> float:
    obs_start = row.obs_start_date if row.obs_start_date is not None else row.start_date
    obs_end = row.obs_end_date if row.obs_end_date is not None else row.end_date
    t_start = year_fraction(as_of, obs_start, forward_daycount)
    t_end = year_fraction(as_of, obs_end, forward_daycount)
    df_start = float(np.asarray(forward_curve.df(t_start)))
    df_end = float(np.asarray(forward_curve.df(t_end)))
    obs_accrual = year_fraction(obs_start, obs_end, index_daycount)
    return float(forward_rate_from_dfs(df_start, df_end, obs_accrual, Compounding.SIMPLE))


def _resolve_option_expiry(
    row: CapFloorScheduleRow,
    *,
    as_of: date,
    forward_daycount: str,
) -> float:
    anchor = row.fixing_date
    if anchor is None:
        anchor = row.obs_start_date if row.obs_start_date is not None else row.start_date
    return float(max(year_fraction(as_of, anchor, forward_daycount), 0.0))


def _is_past_observation(row: CapFloorScheduleRow, *, as_of: date) -> bool:
    if row.fixing_date is not None:
        return row.fixing_date <= as_of
    if row.obs_end_date is not None:
        return row.obs_end_date <= as_of
    return row.start_date <= as_of


def _resolve_observed_rate(
    row: CapFloorScheduleRow,
    *,
    as_of: date,
    fixing_map: dict[tuple[str, date], float],
) -> float:
    if row.observed_rate is not None:
        return float(row.observed_rate)
    if row.fixing_date is not None and row.fixing_date <= as_of:
        key = (row.index_id, row.fixing_date)
        if key in fixing_map:
            return float(fixing_map[key])
        raise ValueError("historical_fixing is required for past caplet period but missing.")
    raise ValueError(
        "schedule_capfloor past row requires observed_rate or fixing_date-based historical_fixing."
    )


def _sign_pay_rec(pay_rec: str) -> float:
    key = pay_rec.upper()
    if key == "REC":
        return 1.0
    if key == "PAY":
        return -1.0
    raise ValueError("trade_capfloor.pay_rec must be 'PAY' or 'REC'.")


def _is_call(cp_flag: str) -> bool:
    key = cp_flag.upper()
    if key == "C":
        return True
    if key == "P":
        return False
    raise ValueError("trade_capfloor.cp_flag must be 'C' or 'P'.")


def _expiry_years_from_row(
    row: ModelParamRow,
    *,
    as_of: date,
    forward_daycount: str,
) -> Optional[float]:
    if row.x_years is not None:
        return float(row.x_years)
    if row.expiry_date is not None:
        return float(year_fraction(as_of, row.expiry_date, forward_daycount))
    if row.expiry_tenor is not None:
        maturity = add_tenor(as_of, parse_tenor(row.expiry_tenor))
        return float(year_fraction(as_of, maturity, forward_daycount))
    return None


def _build_param_candidates(
    rows: Iterable[ModelParamRow],
    *,
    as_of: date,
    forward_daycount: str,
) -> dict[str, tuple[_ParamCandidate, ...]]:
    grouped: dict[str, list[_ParamCandidate]] = {}
    for row in rows:
        name = row.param_name.lower()
        if name not in {"alpha", "beta", "rho", "nu", "shift"}:
            continue
        candidate = _ParamCandidate(
            value=float(row.param_val),
            scope_rank=_scope_rank(row.scope),
            expiry_years=_expiry_years_from_row(
                row,
                as_of=as_of,
                forward_daycount=forward_daycount,
            ),
            strike_rate=(None if row.strike_rate is None else float(row.strike_rate)),
            moneyness=(None if row.moneyness is None else float(row.moneyness)),
        )
        grouped.setdefault(name, []).append(candidate)
    return {k: tuple(v) for k, v in grouped.items()}


def _build_base_params(candidates: dict[str, tuple[_ParamCandidate, ...]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, rows in candidates.items():
        best_scope = -1
        best_value: Optional[float] = None
        for row in rows:
            if row.expiry_years is not None or row.strike_rate is not None or row.moneyness is not None:
                continue
            if row.scope_rank >= best_scope:
                best_scope = row.scope_rank
                best_value = row.value
        if best_value is not None:
            out[name] = best_value
    return out


class _ModelParamResolver:
    def __init__(
        self,
        rows: Iterable[ModelParamRow],
        *,
        as_of: date,
        forward_daycount: str,
        fallback_shift: Optional[float],
    ) -> None:
        self._candidates = _build_param_candidates(
            rows,
            as_of=as_of,
            forward_daycount=forward_daycount,
        )
        self._base = _build_base_params(self._candidates)
        self._fallback_shift = fallback_shift

    def _select(
        self,
        name: str,
        *,
        expiry: float,
        strike: float,
        forward: float,
    ) -> Optional[float]:
        rows = self._candidates.get(name)
        if not rows:
            return self._base.get(name)

        best_score: Optional[tuple[int, int, float, float]] = None
        best_value: Optional[float] = None
        for row in rows:
            if row.strike_rate is not None and abs(row.strike_rate - strike) > _STRIKE_TOL:
                continue
            if row.moneyness is not None and abs(row.moneyness - (strike - forward)) > _MONEYNESS_TOL:
                continue

            expiry_distance = (
                abs(expiry - row.expiry_years) if row.expiry_years is not None else float("inf")
            )
            strike_distance = (
                abs(strike - row.strike_rate)
                if row.strike_rate is not None
                else (abs((strike - forward) - row.moneyness) if row.moneyness is not None else float("inf"))
            )
            axis_specificity = int(row.expiry_years is not None) + int(
                row.strike_rate is not None or row.moneyness is not None
            )
            score = (row.scope_rank, axis_specificity, -expiry_distance, -strike_distance)
            if best_score is None or score > best_score:
                best_score = score
                best_value = row.value

        if best_value is not None:
            return best_value
        return self._base.get(name)

    def select_value(
        self,
        name: str,
        *,
        expiry: float,
        strike: float,
        forward: float,
    ) -> Optional[float]:
        return self._select(name, expiry=expiry, strike=strike, forward=forward)

    def resolve(
        self,
        *,
        expiry: float,
        strike: float,
        forward: float,
    ) -> SabrParams:
        alpha = self._select("alpha", expiry=expiry, strike=strike, forward=forward)
        beta = self._select("beta", expiry=expiry, strike=strike, forward=forward)
        rho = self._select("rho", expiry=expiry, strike=strike, forward=forward)
        nu = self._select("nu", expiry=expiry, strike=strike, forward=forward)
        shift = self._select("shift", expiry=expiry, strike=strike, forward=forward)

        if alpha is None or beta is None or rho is None or nu is None:
            raise ValueError("model_param is missing SABR parameters: alpha/beta/rho/nu.")
        if shift is None:
            shift = 0.0 if self._fallback_shift is None else float(self._fallback_shift)

        return SabrParams(
            alpha=float(alpha),
            beta=float(beta),
            rho=float(rho),
            nu=float(nu),
            shift=float(shift),
        )


def price_capfloor_from_data(data: CapFloorPricingData) -> CapFloorPVResult:
    if not data.schedule_rows:
        return CapFloorPVResult(pv=0.0, pv_future=0.0, pv_fixed=0.0, optionlet_count=0)

    as_of = data.as_of
    if data.pricing.pricing_model is None:
        raise ValueError("pricing_model must be resolved before pricing.")
    if data.pricing.vol_interp_model is None:
        raise ValueError("vol_interp_model must be resolved before pricing.")
    pricing_model = _normalize_pricing_model(data.pricing.pricing_model)
    vol_interp_model = _normalize_vol_interp_model(data.pricing.vol_interp_model)
    grouped = _group_vol_points_by_expiry(data.vol_points, as_of=as_of)
    fallback_shift = _resolve_surface_fallback_shift(data.vol_points)
    param_resolver = _ModelParamResolver(
        data.model_param_rows,
        as_of=as_of,
        forward_daycount=data.pricing.forward_daycount,
        fallback_shift=fallback_shift,
    )

    fixing_map = _fixing_map(data.fixings)
    sign = _sign_pay_rec(data.trade_capfloor.pay_rec)
    is_call = _is_call(data.trade_capfloor.cp_flag)

    active_rows = [
        row
        for row in data.schedule_rows
        if (data.pricing.include_settled or row.is_settled != 1) and row.payment_date > as_of
    ]
    if not active_rows:
        return CapFloorPVResult(pv=0.0, pv_future=0.0, pv_fixed=0.0, optionlet_count=0)

    pay_times = np.array(
        [year_fraction(as_of, row.payment_date, data.pricing.discount_daycount) for row in active_rows],
        dtype=float,
    )
    df_pay = np.asarray(data.discount_curve.df(pay_times), dtype=float)
    node_t, node_forward = _build_sabr_node_grid(
        grouped,
        as_of=as_of,
        forward_curve=data.forward_curve,
        forward_daycount=data.pricing.forward_daycount,
        index_daycount=data.ref_rate_rule.daycount,
        index_tenor=data.trade_capfloor.index_tenor,
    )
    node_vol_cache: dict[float, np.ndarray] = {}

    pv_future = 0.0
    pv_fixed = 0.0
    optionlet_count = 0

    for i, row in enumerate(active_rows):
        optionlet_count += 1
        df = float(df_pay[i])
        strike = float(row.strike_rate)

        if row.fixed_amount is not None and row.is_fixed == 1:
            pv_fixed += sign * float(row.fixed_amount) * df
            continue

        if _is_past_observation(row, as_of=as_of):
            observed_rate = _resolve_observed_rate(row, as_of=as_of, fixing_map=fixing_map)
            payoff_rate = (
                max(observed_rate - strike, 0.0)
                if is_call
                else max(strike - observed_rate, 0.0)
            )
            amount = row.amount
            if amount is None:
                amount = float(row.notional) * float(row.accrual_factor) * payoff_rate
            pv_fixed += sign * float(amount) * df
            continue

        forward = (
            float(row.forward_rate)
            if row.forward_rate is not None
            else _resolve_forward_rate_from_schedule(
                row,
                as_of=as_of,
                forward_curve=data.forward_curve,
                forward_daycount=data.pricing.forward_daycount,
                index_daycount=data.ref_rate_rule.daycount,
            )
        )
        expiry = _resolve_option_expiry(
            row,
            as_of=as_of,
            forward_daycount=data.pricing.forward_daycount,
        )
        model_shift = _select_model_shift(param_resolver, expiry=expiry, forward=forward)
        strike_key = float(strike)
        node_v = node_vol_cache.get(strike_key)
        if node_v is None:
            match vol_interp_model:
                case CapFloorVolInterpModel.SHIFTED_SABR:
                    node_v = _build_node_vol_curve_shifted_sabr(
                        node_t,
                        node_forward,
                        strike_rate=strike_key,
                        param_resolver=param_resolver,
                        pricing_model=pricing_model,
                    )
                case _:
                    raise ValueError(
                        f"Unsupported capfloor vol_interp_model: {data.pricing.vol_interp_model!r}"
                    )
            node_vol_cache[strike_key] = node_v
        sigma = _interp_node_vol_curve(node_t, node_v, expiry=expiry)
        match pricing_model:
            case CapFloorPricingModel.SHIFTED_BLACK:
                optionlet = _shifted_black_optionlet_value(
                    forward,
                    strike,
                    sigma,
                    expiry,
                    shift=model_shift,
                    is_call=is_call,
                )
            case CapFloorPricingModel.BACHELIER:
                optionlet = _normal_optionlet_value(
                    forward,
                    strike,
                    sigma,
                    expiry,
                    is_call=is_call,
                )
            case _:
                raise ValueError(f"Unsupported capfloor pricing_model: {data.pricing.pricing_model!r}")
        amount = row.amount
        if amount is None:
            amount = float(row.notional) * float(row.accrual_factor) * optionlet
        pv_future += sign * float(amount) * df

    pv = pv_fixed + pv_future
    return CapFloorPVResult(
        pv=float(pv),
        pv_future=float(pv_future),
        pv_fixed=float(pv_fixed),
        optionlet_count=optionlet_count,
    )


def _required_fixing_dates(
    rows: Sequence[CapFloorScheduleRow],
    *,
    as_of: date,
) -> list[date]:
    out: list[date] = []
    for row in rows:
        if row.payment_date <= as_of:
            continue
        if row.fixed_amount is not None or row.observed_rate is not None:
            continue
        if row.fixing_date is not None and row.fixing_date <= as_of:
            out.append(row.fixing_date)
    return out


def load_capfloor_pricing_data(
    provider: CapFloorDataProvider,
    *,
    run_id: str,
    trade_id: str,
    snapshot_id: str,
    pricing: CapFloorPricingInput,
) -> CapFloorPricingData:
    trade = provider.get_trade(trade_id)
    pricing_eff = _resolve_effective_pricing_input(provider, trade=trade, pricing=pricing)
    trade_cap = provider.get_trade_capfloor(trade_id)
    ref_rule = provider.get_ref_rate_rule(trade_cap.index_id)
    snapshot = provider.get_market_snapshot(snapshot_id)
    as_of = pricing_eff.as_of if pricing_eff.as_of is not None else snapshot.as_of

    schedule_rows = tuple(provider.get_schedule_capfloor(trade_id))
    if not schedule_rows:
        pay_cal = provider.get_business_calendar(trade_cap.pay_cal_id)
        fix_cal = provider.get_business_calendar(ref_rule.fixing_cal_id)
        schedule_rows = tuple(
            build_capfloor_schedule_rows(
                trade,
                trade_cap,
                ref_rule,
                payment_calendar=pay_cal,
                fixing_calendar=fix_cal,
                as_of=None,
                fixings=(),
            )
        )

    fixing_dates = _required_fixing_dates(schedule_rows, as_of=as_of)
    fixings: Sequence[HistoricalFixing] = ()
    if fixing_dates:
        fixings = provider.get_historical_fixings(
            trade_cap.index_id,
            min(fixing_dates),
            max(fixing_dates),
        )

    discount_curve = provider.get_yield_curve(pricing_eff.discount_curve_id, snapshot_id)
    forward_curve_id = pricing_eff.forward_curve_id or pricing_eff.discount_curve_id
    forward_curve = provider.get_yield_curve(forward_curve_id, snapshot_id)

    if pricing_eff.vol_quote_type is None:
        raise ValueError("vol_quote_type must be resolved before loading capfloor data.")
    vol_points = provider.get_vol_capfloor(
        snapshot_id=snapshot_id,
        ccy=trade_cap.ccy,
        index_tenor=trade_cap.index_tenor,
        quote_type=pricing_eff.vol_quote_type,
        ref_rate_id=trade_cap.index_id,
        surface_tag=pricing_eff.surface_tag,
    )
    if not vol_points:
        raise ValueError("vol_capfloor rows are missing for capfloor pricing.")

    if pricing_eff.model_tag is None:
        raise ValueError("model_tag must be resolved before loading capfloor data.")
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
        param_key=trade_cap.ccy,
    )
    index_rows = provider.get_model_params(
        snapshot_id=snapshot_id,
        model_tag=pricing_eff.model_tag,
        scope="INDEX",
        param_key=trade_cap.index_id,
    )
    model_rows = tuple(global_rows) + tuple(ccy_rows) + tuple(index_rows)

    return CapFloorPricingData(
        run_id=run_id,
        trade=trade,
        trade_capfloor=trade_cap,
        ref_rate_rule=ref_rule,
        snapshot=snapshot,
        discount_curve=discount_curve,
        forward_curve=forward_curve,
        vol_points=tuple(vol_points),
        model_param_rows=model_rows,
        schedule_rows=schedule_rows,
        fixings=tuple(fixings),
        pricing=pricing_eff,
        as_of=as_of,
    )
