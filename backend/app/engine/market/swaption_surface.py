from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Mapping, Optional, Sequence

import numpy as np
from scipy.optimize import brentq  # type: ignore[import-untyped]
from scipy.special import ndtr  # type: ignore[import-untyped]

from app.engine.market.sabr import (
    SabrCalibrationPreset,
    SabrVolType,
    calibrate_sabr,
)

_VOL_FLOOR = 1e-12


class SwaptionQuoteVolType(Enum):
    LN_VOL = "LN_VOL"
    N_VOL = "N_VOL"
    SLN_VOL = "SLN_VOL"


@dataclass(frozen=True)
class MarketQuoteSwaption:
    snapshot_id: str
    ccy: str
    ref_rate_id: str
    index_tenor: str
    cp_flag: str
    expiry_tenor: Optional[str]
    expiry_date: Optional[date]
    swap_tenor: str
    x_years: float
    vol_daycount: str
    smile_type: str
    strike_rate: Optional[float]
    moneyness: Optional[float]
    forward_rate: Optional[float]
    quote_type: str
    sigma_mid: float
    quote_shift: Optional[float] = None
    sigma_bid: Optional[float] = None
    sigma_ask: Optional[float] = None
    source_symbol: Optional[str] = None
    surface_tag: Optional[str] = None
    quote_id: Optional[str] = None


@dataclass(frozen=True)
class VolSwaptionRowPayload:
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
    sigma: float
    sabr_shift: float
    source_symbol: Optional[str] = None
    surface_tag: Optional[str] = None


@dataclass(frozen=True)
class ModelParamRowPayload:
    snapshot_id: str
    model_tag: str
    scope: str
    param_key: str
    expiry_tenor: Optional[str]
    expiry_date: Optional[date]
    x_years: Optional[float]
    swap_tenor: Optional[str]
    strike_rate: Optional[float]
    moneyness: Optional[float]
    param_name: str
    param_val: float
    param_unit: Optional[str] = None
    source_symbol: Optional[str] = None
    note: Optional[str] = None


def _normalize_quote_vol_type(quote_type: str) -> SwaptionQuoteVolType:
    key = quote_type.strip().upper()
    match key:
        case "LN_VOL":
            return SwaptionQuoteVolType.LN_VOL
        case "N_VOL":
            return SwaptionQuoteVolType.N_VOL
        case "SLN_VOL":
            return SwaptionQuoteVolType.SLN_VOL
        case _:
            raise ValueError(f"Unsupported swaption quote_type: {quote_type!r}")


def _is_call(cp_flag: str) -> bool:
    key = cp_flag.strip().upper()
    if key == "C":
        return True
    if key == "P":
        return False
    raise ValueError("cp_flag must be 'C' or 'P'.")


def _resolve_strike(row: MarketQuoteSwaption) -> Optional[float]:
    smile = row.smile_type.strip().upper()
    if smile == "ATM":
        if row.forward_rate is None:
            return None
        return float(row.forward_rate)
    if smile == "STRIKE":
        if row.strike_rate is None:
            raise ValueError("market_quote_swaption STRIKE row requires strike_rate.")
        return float(row.strike_rate)
    if smile == "MONEYNESS":
        if row.moneyness is None:
            raise ValueError("market_quote_swaption MONEYNESS row requires moneyness.")
        if row.forward_rate is None:
            raise ValueError("market_quote_swaption MONEYNESS row requires forward_rate.")
        return float(row.forward_rate + row.moneyness)
    raise ValueError(f"Unsupported smile_type: {row.smile_type!r}")


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


def _quote_option_value(
    *,
    quote_type: SwaptionQuoteVolType,
    sigma: float,
    forward_rate: float,
    strike_rate: float,
    expiry: float,
    is_call: bool,
    quote_shift: Optional[float],
) -> float:
    match quote_type:
        case SwaptionQuoteVolType.N_VOL:
            return _normal_option_value(
                forward_rate,
                strike_rate,
                sigma,
                expiry,
                is_call=is_call,
            )
        case SwaptionQuoteVolType.LN_VOL:
            return _shifted_black_option_value(
                forward_rate,
                strike_rate,
                sigma,
                expiry,
                shift=0.0,
                is_call=is_call,
            )
        case SwaptionQuoteVolType.SLN_VOL:
            if quote_shift is None:
                raise ValueError("quote_shift is required when quote_type='SLN_VOL'.")
            if quote_shift < 0.0:
                raise ValueError("quote_shift must be non-negative.")
            return _shifted_black_option_value(
                forward_rate,
                strike_rate,
                sigma,
                expiry,
                shift=float(quote_shift),
                is_call=is_call,
            )
        case _:
            raise ValueError(f"Unsupported swaption quote_type: {quote_type!r}")


def _implied_shifted_black_vol(
    *,
    target_option_value: float,
    forward_rate: float,
    strike_rate: float,
    expiry: float,
    shift: float,
    is_call: bool,
) -> float:
    intrinsic = _shifted_black_option_value(
        forward_rate,
        strike_rate,
        0.0,
        expiry,
        shift=shift,
        is_call=is_call,
    )
    if target_option_value <= intrinsic + 1e-14:
        return _VOL_FLOOR

    def _objective(sigma: float) -> float:
        return _shifted_black_option_value(
            forward_rate,
            strike_rate,
            sigma,
            expiry,
            shift=shift,
            is_call=is_call,
        ) - target_option_value

    lower = _VOL_FLOOR
    upper = 1.0
    f_upper = _objective(upper)
    while f_upper < 0.0 and upper < 20.0:
        upper *= 2.0
        f_upper = _objective(upper)
    if f_upper < 0.0:
        raise ValueError("Failed to bracket shifted-black implied vol for swaption quote.")
    return float(brentq(_objective, lower, upper, xtol=1e-12, maxiter=200))


def normalize_swaption_quote_to_sln(
    row: MarketQuoteSwaption,
    *,
    normalized_quote_shift: float,
    sabr_shift: Optional[float] = None,
) -> VolSwaptionRowPayload:
    if normalized_quote_shift < 0.0:
        raise ValueError("normalized_quote_shift must be non-negative.")
    qtype = _normalize_quote_vol_type(row.quote_type)
    if qtype != SwaptionQuoteVolType.SLN_VOL and row.quote_shift is not None:
        raise ValueError("quote_shift must be NULL for quote_type in {'LN_VOL','N_VOL'}.")
    if row.sigma_mid <= 0.0 or not np.isfinite(row.sigma_mid):
        raise ValueError("sigma_mid must be positive and finite.")
    if row.x_years <= 0.0 or not np.isfinite(row.x_years):
        raise ValueError("x_years must be positive and finite.")

    strike = _resolve_strike(row)
    is_call = _is_call(row.cp_flag)

    if strike is None or row.forward_rate is None:
        # ATM row without forward cannot be converted from N/LN. Keep sigma only for SLN quotes.
        if qtype != SwaptionQuoteVolType.SLN_VOL:
            raise ValueError(
                "forward_rate is required to convert LN_VOL/N_VOL swaption quote to normalized SLN_VOL."
            )
        sigma_sln = float(row.sigma_mid)
    else:
        forward = float(row.forward_rate)
        strike_rate = float(strike)
        target_value = _quote_option_value(
            quote_type=qtype,
            sigma=float(row.sigma_mid),
            forward_rate=forward,
            strike_rate=strike_rate,
            expiry=float(row.x_years),
            is_call=is_call,
            quote_shift=row.quote_shift,
        )
        sigma_sln = _implied_shifted_black_vol(
            target_option_value=target_value,
            forward_rate=forward,
            strike_rate=strike_rate,
            expiry=float(row.x_years),
            shift=float(normalized_quote_shift),
            is_call=is_call,
        )

    resolved_sabr_shift = float(normalized_quote_shift if sabr_shift is None else sabr_shift)
    if resolved_sabr_shift < 0.0:
        raise ValueError("sabr_shift must be non-negative.")

    return VolSwaptionRowPayload(
        snapshot_id=row.snapshot_id,
        ccy=row.ccy,
        ref_rate_id=row.ref_rate_id,
        index_tenor=row.index_tenor,
        expiry_tenor=row.expiry_tenor,
        expiry_date=row.expiry_date,
        swap_tenor=row.swap_tenor,
        x_years=float(row.x_years),
        vol_daycount=row.vol_daycount,
        smile_type=row.smile_type.strip().upper(),
        strike_rate=row.strike_rate,
        moneyness=row.moneyness,
        quote_type="SLN_VOL",
        quote_shift=float(normalized_quote_shift),
        sigma=float(sigma_sln),
        sabr_shift=resolved_sabr_shift,
        source_symbol=row.source_symbol,
        surface_tag=row.surface_tag,
    )


def build_vol_swaption_rows_from_market_quotes(
    quotes: Sequence[MarketQuoteSwaption],
    *,
    normalized_quote_shift: float,
    sabr_shift: Optional[float] = None,
) -> list[VolSwaptionRowPayload]:
    if not quotes:
        return []
    return [
        normalize_swaption_quote_to_sln(
            row,
            normalized_quote_shift=normalized_quote_shift,
            sabr_shift=sabr_shift,
        )
        for row in quotes
    ]


def _expiry_key(x_years: float) -> float:
    return round(float(x_years), 12)


def _forward_key(expiry_years: float, swap_tenor: str) -> tuple[float, str]:
    return (_expiry_key(expiry_years), swap_tenor.strip().upper())


def calibrate_shifted_sabr_params_for_swaption_cube(
    vol_rows: Sequence[VolSwaptionRowPayload],
    *,
    forwards_by_node: Mapping[tuple[float, str], float],
    model_tag: str = "SABR_SHIFTED",
    scope: str = "INDEX",
    param_key: str,
    preset: SabrCalibrationPreset | str = SabrCalibrationPreset.BETA_50,
    fixed_params: Optional[Mapping[str, float]] = None,
) -> list[ModelParamRowPayload]:
    if not vol_rows:
        return []

    scope_upper = scope.strip().upper()
    if scope_upper not in {"GLOBAL", "CCY", "INDEX", "PAIR"}:
        raise ValueError(f"Unsupported model_param scope: {scope!r}")

    fwd_map = {
        (round(float(k[0]), 12), str(k[1]).strip().upper()): float(v)
        for k, v in forwards_by_node.items()
    }
    if not fwd_map:
        raise ValueError("forwards_by_node must be non-empty.")

    grouped: dict[tuple[float, str], list[VolSwaptionRowPayload]] = {}
    for row in vol_rows:
        key = _forward_key(row.x_years, row.swap_tenor)
        grouped.setdefault(key, []).append(row)

    snapshot_ids = {row.snapshot_id for row in vol_rows}
    if len(snapshot_ids) != 1:
        raise ValueError("vol_rows must belong to a single snapshot_id.")
    snapshot_id = next(iter(snapshot_ids))

    payload: list[ModelParamRowPayload] = []

    for key in sorted(grouped.keys()):
        rows = grouped[key]
        forward = fwd_map.get(key)
        if forward is None:
            raise ValueError(
                f"Missing forward rate for node x_years={key[0]}, swap_tenor={key[1]}."
            )

        first = rows[0]
        shift_set = {float(r.sabr_shift) for r in rows}
        if len(shift_set) != 1:
            raise ValueError(
                "sabr_shift must be unique per (expiry, swap_tenor) node. "
                f"x_years={key[0]}, swap_tenor={key[1]}"
            )
        sabr_shift = next(iter(shift_set))

        strikes: list[float] = []
        sigmas: list[float] = []
        for row in rows:
            smile = row.smile_type.upper()
            strike: Optional[float]
            if smile == "STRIKE":
                strike = None if row.strike_rate is None else float(row.strike_rate)
            elif smile == "MONEYNESS":
                strike = None if row.moneyness is None else float(forward + row.moneyness)
            else:
                strike = None
            if strike is None:
                continue
            sigma = float(row.sigma)
            if sigma <= 0.0 or not np.isfinite(sigma):
                raise ValueError(
                    "vol_swaption sigma must be positive and finite for SABR calibration. "
                    f"x_years={key[0]}, swap_tenor={key[1]}"
                )
            strikes.append(strike)
            sigmas.append(sigma)

        if len(strikes) < 3:
            raise ValueError(
                "SABR calibration requires at least 3 strike points per node. "
                f"x_years={key[0]}, swap_tenor={key[1]}"
            )

        strike_arr = np.asarray(strikes, dtype=float)
        sigma_arr = np.asarray(sigmas, dtype=float)
        result = calibrate_sabr(
            strike_arr,
            sigma_arr,
            float(forward),
            float(first.x_years),
            vol_type=SabrVolType.LOGNORMAL,
            preset=preset,
            shift=float(sabr_shift),
            fixed_params=fixed_params,
        )
        if not result.success:
            raise ValueError(
                "SABR calibration failed at "
                f"x_years={key[0]}, swap_tenor={key[1]}: {result.message}"
            )

        params = (
            ("alpha", float(result.params.alpha), None),
            ("beta", float(result.params.beta), None),
            ("rho", float(result.params.rho), None),
            ("nu", float(result.params.nu), None),
            ("shift", float(result.params.shift), "abs"),
        )
        note = f"rmse={result.rmse:.6g}; nfev={result.nfev}; preset={result.preset.value}"
        for param_name, param_val, param_unit in params:
            payload.append(
                ModelParamRowPayload(
                    snapshot_id=snapshot_id,
                    model_tag=model_tag,
                    scope=scope_upper,
                    param_key=param_key,
                    expiry_tenor=first.expiry_tenor,
                    expiry_date=first.expiry_date,
                    x_years=float(first.x_years),
                    swap_tenor=first.swap_tenor,
                    strike_rate=None,
                    moneyness=None,
                    param_name=param_name,
                    param_val=param_val,
                    param_unit=param_unit,
                    source_symbol=first.source_symbol,
                    note=note,
                )
            )

    return payload
