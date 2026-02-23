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


class CapFloorQuoteVolType(Enum):
    LN_VOL = "LN_VOL"
    N_VOL = "N_VOL"
    SLN_VOL = "SLN_VOL"


@dataclass(frozen=True)
class MarketQuoteCapFloor:
    snapshot_id: str
    ccy: str
    ref_rate_id: str
    index_tenor: str
    cp_flag: str
    quote_kind: str
    expiry_tenor: Optional[str]
    expiry_date: Optional[date]
    x_years: float
    vol_daycount: str
    smile_type: str
    strike_rate: Optional[float]
    moneyness: Optional[float]
    quote_type: str
    sigma_mid: float
    quote_shift: Optional[float] = None
    sigma_bid: Optional[float] = None
    sigma_ask: Optional[float] = None
    annuity_factor: Optional[float] = None
    forward_rate: Optional[float] = None
    source_symbol: Optional[str] = None
    surface_tag: Optional[str] = None
    quote_id: Optional[str] = None


@dataclass(frozen=True)
class CapFloorTermVolQuote:
    x_years: float
    sigma: float
    strike_rate: float
    expiry_date: Optional[date] = None
    expiry_tenor: Optional[str] = None


@dataclass(frozen=True)
class VolCapFloorRowPayload:
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
    quote_shift: Optional[float]
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


def _normalize_quote_vol_type(quote_type: str) -> CapFloorQuoteVolType:
    key = quote_type.strip().upper()
    match key:
        case "LN_VOL":
            return CapFloorQuoteVolType.LN_VOL
        case "N_VOL":
            return CapFloorQuoteVolType.N_VOL
        case "SLN_VOL":
            return CapFloorQuoteVolType.SLN_VOL
        case _:
            raise ValueError(f"Unsupported capfloor quote_type: {quote_type!r}")


def _shifted_black_optionlet_value(
    forward_rate: float,
    strike_rate: float,
    sigma: float,
    expiry: float,
    *,
    shift: float,
) -> float:
    f = float(forward_rate + shift)
    k = float(strike_rate + shift)
    if f <= 0.0 or k <= 0.0:
        raise ValueError("Shifted Black requires forward+shift and strike+shift > 0.")
    if sigma <= 0.0 or expiry <= 0.0:
        return float(max(f - k, 0.0))

    std = sigma * np.sqrt(expiry)
    d1 = (np.log(f / k) + 0.5 * std * std) / std
    d2 = d1 - std
    return float(f * ndtr(d1) - k * ndtr(d2))


def _normal_optionlet_value(
    forward_rate: float,
    strike_rate: float,
    sigma: float,
    expiry: float,
) -> float:
    if sigma <= 0.0 or expiry <= 0.0:
        return float(max(forward_rate - strike_rate, 0.0))
    std = sigma * np.sqrt(expiry)
    x = (forward_rate - strike_rate) / std
    return float((forward_rate - strike_rate) * ndtr(x) + std * np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi))


def _optionlet_value_from_quote_vol(
    forward_rate: float,
    strike_rate: float,
    sigma: float,
    expiry: float,
    *,
    quote_type: CapFloorQuoteVolType,
    quote_shift: Optional[float],
) -> float:
    match quote_type:
        case CapFloorQuoteVolType.N_VOL:
            return _normal_optionlet_value(forward_rate, strike_rate, sigma, expiry)
        case CapFloorQuoteVolType.LN_VOL:
            return _shifted_black_optionlet_value(forward_rate, strike_rate, sigma, expiry, shift=0.0)
        case CapFloorQuoteVolType.SLN_VOL:
            if quote_shift is None:
                raise ValueError("quote_shift is required when quote_type='SLN_VOL'.")
            if quote_shift < 0.0:
                raise ValueError("quote_shift must be non-negative.")
            return _shifted_black_optionlet_value(
                forward_rate,
                strike_rate,
                sigma,
                expiry,
                shift=float(quote_shift),
            )
        case _:
            raise ValueError(f"Unsupported capfloor quote_type: {quote_type!r}")


def _validate_strip_inputs(
    quotes: Sequence[CapFloorTermVolQuote],
    forwards: Sequence[float],
    discount_factors: Sequence[float],
    accrual_factors: Sequence[float],
) -> None:
    n = len(quotes)
    if n == 0:
        raise ValueError("quotes must be non-empty.")
    if len(forwards) != n or len(discount_factors) != n or len(accrual_factors) != n:
        raise ValueError("quotes, forwards, discount_factors, accrual_factors must have same length.")

    x = np.asarray([q.x_years for q in quotes], dtype=float)
    if np.any(~np.isfinite(x)) or np.any(x <= 0.0):
        raise ValueError("quote x_years must be positive and finite.")
    if np.any(np.diff(x) <= 0.0):
        raise ValueError("quote x_years must be strictly increasing.")

    sigma = np.asarray([q.sigma for q in quotes], dtype=float)
    if np.any(~np.isfinite(sigma)) or np.any(sigma <= 0.0):
        raise ValueError("quote sigma must be positive and finite.")

    strike = np.asarray([q.strike_rate for q in quotes], dtype=float)
    if np.any(~np.isfinite(strike)):
        raise ValueError("quote strike_rate must be finite.")
    if np.max(np.abs(strike - strike[0])) > 1e-12:
        raise ValueError("term quote strike_rate must be constant across maturities for stripping.")

    fwd = np.asarray(forwards, dtype=float)
    dfs = np.asarray(discount_factors, dtype=float)
    accr = np.asarray(accrual_factors, dtype=float)
    if np.any(~np.isfinite(fwd)) or np.any(~np.isfinite(dfs)) or np.any(~np.isfinite(accr)):
        raise ValueError("forwards/discount_factors/accrual_factors must be finite.")
    if np.any(dfs <= 0.0):
        raise ValueError("discount_factors must be positive.")
    if np.any(accr <= 0.0):
        raise ValueError("accrual_factors must be positive.")


def _validate_strip_inputs_annuity(
    quotes: Sequence[CapFloorTermVolQuote],
    forwards: Sequence[float],
    annuity_factors: Sequence[float],
) -> None:
    n = len(quotes)
    if n == 0:
        raise ValueError("quotes must be non-empty.")
    if len(forwards) != n or len(annuity_factors) != n:
        raise ValueError("quotes, forwards, annuity_factors must have same length.")

    x = np.asarray([q.x_years for q in quotes], dtype=float)
    if np.any(~np.isfinite(x)) or np.any(x <= 0.0):
        raise ValueError("quote x_years must be positive and finite.")
    if np.any(np.diff(x) <= 0.0):
        raise ValueError("quote x_years must be strictly increasing.")

    sigma = np.asarray([q.sigma for q in quotes], dtype=float)
    if np.any(~np.isfinite(sigma)) or np.any(sigma <= 0.0):
        raise ValueError("quote sigma must be positive and finite.")

    strike = np.asarray([q.strike_rate for q in quotes], dtype=float)
    if np.any(~np.isfinite(strike)):
        raise ValueError("quote strike_rate must be finite.")
    if np.max(np.abs(strike - strike[0])) > 1e-12:
        raise ValueError("term quote strike_rate must be constant across maturities for stripping.")

    fwd = np.asarray(forwards, dtype=float)
    annuity = np.asarray(annuity_factors, dtype=float)
    if np.any(~np.isfinite(fwd)) or np.any(~np.isfinite(annuity)):
        raise ValueError("forwards/annuity_factors must be finite.")
    if np.any(annuity <= 0.0):
        raise ValueError("annuity_factors must be positive.")


def _strip_capfloor_term_core(
    quotes: Sequence[CapFloorTermVolQuote],
    forwards: np.ndarray,
    annuity: np.ndarray,
    *,
    quote_type: CapFloorQuoteVolType,
    quote_shift: Optional[float],
) -> np.ndarray:
    x = np.asarray([q.x_years for q in quotes], dtype=float)
    cap_sigma = np.asarray([q.sigma for q in quotes], dtype=float)
    strike = float(quotes[0].strike_rate)

    stripped = np.empty_like(cap_sigma)
    for n in range(cap_sigma.size):
        target_cap = 0.0
        for i in range(n + 1):
            target_cap += annuity[i] * _optionlet_value_from_quote_vol(
                forwards[i],
                strike,
                cap_sigma[n],
                x[i],
                quote_type=quote_type,
                quote_shift=quote_shift,
            )

        prev_caplets = 0.0
        for i in range(n):
            prev_caplets += annuity[i] * _optionlet_value_from_quote_vol(
                forwards[i],
                strike,
                stripped[i],
                x[i],
                quote_type=quote_type,
                quote_shift=quote_shift,
            )

        remaining = target_cap - prev_caplets
        if remaining < -1e-12:
            raise ValueError("Inconsistent term quote set: remaining optionlet value is negative.")

        scale = annuity[n]
        if scale <= 0.0:
            raise ValueError("Invalid annuity scale for stripping.")

        def _obj(sigma_n: float) -> float:
            return scale * _optionlet_value_from_quote_vol(
                forwards[n],
                strike,
                sigma_n,
                x[n],
                quote_type=quote_type,
                quote_shift=quote_shift,
            ) - remaining

        lower = _VOL_FLOOR
        upper = 5.0
        f_low = _obj(lower)
        f_high = _obj(upper)
        while f_high < 0.0 and upper < 20.0:
            upper *= 2.0
            f_high = _obj(upper)
        if f_low > 1e-10:
            stripped[n] = lower
            continue
        if f_high < 0.0:
            raise ValueError("Failed to bracket root while stripping capfloor term vol.")
        stripped[n] = float(brentq(_obj, lower, upper, xtol=1e-12, maxiter=200))

    return stripped


def strip_capfloor_term_quotes(
    quotes: Sequence[CapFloorTermVolQuote],
    forwards: Sequence[float],
    discount_factors: Sequence[float],
    accrual_factors: Sequence[float],
    *,
    quote_type: str = "LN_VOL",
    quote_shift: Optional[float] = None,
) -> np.ndarray:
    _validate_strip_inputs(quotes, forwards, discount_factors, accrual_factors)
    fwd = np.asarray(forwards, dtype=float)
    dfs = np.asarray(discount_factors, dtype=float)
    accr = np.asarray(accrual_factors, dtype=float)
    annuity = dfs * accr
    return _strip_capfloor_term_core(
        quotes,
        fwd,
        annuity,
        quote_type=_normalize_quote_vol_type(quote_type),
        quote_shift=quote_shift,
    )


def strip_capfloor_term_quotes_from_annuity(
    quotes: Sequence[CapFloorTermVolQuote],
    forwards: Sequence[float],
    annuity_factors: Sequence[float],
    *,
    quote_type: str = "LN_VOL",
    quote_shift: Optional[float] = None,
) -> np.ndarray:
    _validate_strip_inputs_annuity(quotes, forwards, annuity_factors)
    fwd = np.asarray(forwards, dtype=float)
    annuity = np.asarray(annuity_factors, dtype=float)
    return _strip_capfloor_term_core(
        quotes,
        fwd,
        annuity,
        quote_type=_normalize_quote_vol_type(quote_type),
        quote_shift=quote_shift,
    )


def build_vol_capfloor_rows_from_stripped(
    *,
    snapshot_id: str,
    ccy: str,
    ref_rate_id: Optional[str],
    index_tenor: str,
    quotes: Sequence[CapFloorTermVolQuote],
    stripped_optionlet_vols: Sequence[float],
    quote_type: str = "LN_VOL",
    quote_shift: Optional[float] = None,
    vol_daycount: str = "ACT/365F",
    sabr_shift: float = 0.0,
    source_symbol: Optional[str] = None,
    surface_tag: Optional[str] = None,
) -> list[VolCapFloorRowPayload]:
    if len(quotes) != len(stripped_optionlet_vols):
        raise ValueError("quotes and stripped_optionlet_vols must have same length.")
    qtype = _normalize_quote_vol_type(quote_type)
    if qtype == CapFloorQuoteVolType.SLN_VOL:
        if quote_shift is None:
            raise ValueError("quote_shift is required when quote_type='SLN_VOL'.")
        if quote_shift < 0.0:
            raise ValueError("quote_shift must be non-negative.")
    elif quote_shift is not None:
        raise ValueError("quote_shift must be NULL for quote_type in {'LN_VOL','N_VOL'}.")
    if sabr_shift < 0.0:
        raise ValueError("sabr_shift must be non-negative.")

    payload: list[VolCapFloorRowPayload] = []
    for i, quote in enumerate(quotes):
        sigma = float(stripped_optionlet_vols[i])
        if sigma <= 0.0 or not np.isfinite(sigma):
            raise ValueError("stripped optionlet vol must be positive and finite.")
        payload.append(
            VolCapFloorRowPayload(
                snapshot_id=snapshot_id,
                ccy=ccy,
                ref_rate_id=ref_rate_id,
                index_tenor=index_tenor,
                expiry_tenor=quote.expiry_tenor,
                expiry_date=quote.expiry_date,
                x_years=float(quote.x_years),
                vol_daycount=vol_daycount,
                smile_type="STRIKE",
                strike_rate=float(quote.strike_rate),
                quote_type=quote_type,
                quote_shift=quote_shift,
                sigma=sigma,
                sabr_shift=float(sabr_shift),
                source_symbol=source_symbol,
                surface_tag=surface_tag,
            )
        )
    return payload


def _quote_to_strike(row: MarketQuoteCapFloor) -> tuple[str, Optional[float]]:
    smile = row.smile_type.upper()
    if smile == "ATM":
        return "ATM", None
    if smile == "STRIKE":
        if row.strike_rate is None:
            raise ValueError("market_quote_capfloor STRIKE row requires strike_rate.")
        return "STRIKE", float(row.strike_rate)
    if smile == "MONEYNESS":
        if row.moneyness is None or row.forward_rate is None:
            raise ValueError(
                "market_quote_capfloor MONEYNESS row requires moneyness and forward_rate."
            )
        return "STRIKE", float(row.forward_rate + row.moneyness)
    raise ValueError(f"Unsupported smile_type: {row.smile_type!r}")


def _validate_market_quote_vol_type_and_shift(row: MarketQuoteCapFloor) -> None:
    qtype = _normalize_quote_vol_type(row.quote_type)
    if qtype == CapFloorQuoteVolType.SLN_VOL:
        if row.quote_shift is None:
            raise ValueError("market_quote_capfloor SLN_VOL row requires quote_shift.")
        if row.quote_shift < 0.0:
            raise ValueError("market_quote_capfloor quote_shift must be non-negative.")
    elif row.quote_shift is not None:
        raise ValueError("market_quote_capfloor quote_shift must be NULL for LN_VOL/N_VOL.")


def build_vol_capfloor_rows_from_market_quotes(
    quotes: Sequence[MarketQuoteCapFloor],
    *,
    sabr_shift: float = 0.0,
) -> list[VolCapFloorRowPayload]:
    if not quotes:
        return []
    if sabr_shift < 0.0:
        raise ValueError("sabr_shift must be non-negative.")

    payload: list[VolCapFloorRowPayload] = []

    # Group by surface metadata and stripping bucket.
    groups: dict[
        tuple[
            str,
            str,
            str,
            str,
            str,
            str,
            str,
            str,
            str,
            Optional[str],
            Optional[str],
            Optional[float],
            Optional[float],
            Optional[float],
        ],
        list[MarketQuoteCapFloor],
    ] = {}
    for row in quotes:
        _validate_market_quote_vol_type_and_shift(row)
        quote_kind = row.quote_kind.upper()
        smile = row.smile_type.upper()
        key = (
            row.snapshot_id,
            row.ccy,
            row.ref_rate_id,
            row.index_tenor,
            row.cp_flag.upper(),
            quote_kind,
            row.quote_type.upper(),
            row.vol_daycount,
            row.source_symbol,
            row.surface_tag,
            smile,
            row.strike_rate,
            row.moneyness,
            row.quote_shift,
        )
        groups.setdefault(key, []).append(row)

    for rows in groups.values():
        rows_sorted = sorted(rows, key=lambda r: (float(r.x_years), r.expiry_date or date.max))
        first = rows_sorted[0]
        quote_kind = first.quote_kind.upper()

        if quote_kind == "OPTIONLET_VOL":
            for row in rows_sorted:
                sigma = float(row.sigma_mid)
                if not np.isfinite(sigma) or sigma <= 0.0:
                    raise ValueError("market_quote_capfloor sigma_mid must be positive and finite.")
                smile_type, strike = _quote_to_strike(row)
                payload.append(
                    VolCapFloorRowPayload(
                        snapshot_id=row.snapshot_id,
                        ccy=row.ccy,
                        ref_rate_id=row.ref_rate_id,
                        index_tenor=row.index_tenor,
                        expiry_tenor=row.expiry_tenor,
                        expiry_date=row.expiry_date,
                        x_years=float(row.x_years),
                        vol_daycount=row.vol_daycount,
                        smile_type=smile_type,
                        strike_rate=strike,
                        quote_type=row.quote_type,
                        quote_shift=row.quote_shift,
                        sigma=sigma,
                        sabr_shift=float(sabr_shift),
                        source_symbol=row.source_symbol,
                        surface_tag=row.surface_tag,
                    )
                )
            continue

        if quote_kind != "TERM_VOL":
            raise ValueError(f"Unsupported quote_kind: {first.quote_kind!r}")

        if first.smile_type.upper() != "STRIKE":
            raise ValueError("TERM_VOL stripping currently supports only smile_type='STRIKE'.")
        if first.strike_rate is None:
            raise ValueError("TERM_VOL row requires strike_rate.")

        term_quotes: list[CapFloorTermVolQuote] = []
        forwards: list[float] = []
        annuities: list[float] = []
        for row in rows_sorted:
            if row.forward_rate is None or row.annuity_factor is None:
                raise ValueError(
                    "TERM_VOL stripping requires forward_rate and annuity_factor on each quote row."
                )
            term_quotes.append(
                CapFloorTermVolQuote(
                    x_years=float(row.x_years),
                    sigma=float(row.sigma_mid),
                    strike_rate=float(row.strike_rate),
                    expiry_date=row.expiry_date,
                    expiry_tenor=row.expiry_tenor,
                )
            )
            forwards.append(float(row.forward_rate))
            annuities.append(float(row.annuity_factor))

        stripped = strip_capfloor_term_quotes_from_annuity(
            term_quotes,
            forwards,
            annuities,
            quote_type=first.quote_type,
            quote_shift=first.quote_shift,
        )
        payload.extend(
            build_vol_capfloor_rows_from_stripped(
                snapshot_id=first.snapshot_id,
                ccy=first.ccy,
                ref_rate_id=first.ref_rate_id,
                index_tenor=first.index_tenor,
                quotes=term_quotes,
                stripped_optionlet_vols=stripped,
                quote_type=first.quote_type,
                quote_shift=first.quote_shift,
                vol_daycount=first.vol_daycount,
                sabr_shift=float(sabr_shift),
                source_symbol=first.source_symbol,
                surface_tag=first.surface_tag,
            )
        )

    return payload


def _expiry_key(x_years: float) -> float:
    return round(float(x_years), 12)


def calibrate_shifted_sabr_params_by_expiry(
    vol_rows: Sequence[VolCapFloorRowPayload],
    *,
    forwards_by_expiry: Mapping[float, float],
    model_tag: str = "SABR_SHIFTED",
    scope: str = "INDEX",
    param_key: str,
    vol_type: SabrVolType | str = SabrVolType.LOGNORMAL,
    preset: SabrCalibrationPreset | str = SabrCalibrationPreset.BETA_50,
    fixed_params: Optional[Mapping[str, float]] = None,
) -> list[ModelParamRowPayload]:
    if not vol_rows:
        return []

    scope_upper = scope.strip().upper()
    if scope_upper not in {"GLOBAL", "CCY", "INDEX", "PAIR"}:
        raise ValueError(f"Unsupported model_param scope: {scope!r}")

    fwd_map = {_expiry_key(k): float(v) for k, v in forwards_by_expiry.items()}
    if not fwd_map:
        raise ValueError("forwards_by_expiry must be non-empty.")

    grouped: dict[float, list[VolCapFloorRowPayload]] = {}
    for row in vol_rows:
        if row.smile_type.upper() != "STRIKE" or row.strike_rate is None:
            continue
        grouped.setdefault(_expiry_key(row.x_years), []).append(row)
    if not grouped:
        raise ValueError("SABR calibration requires strike-based optionlet vols.")

    snapshot_ids = {row.snapshot_id for row in vol_rows}
    if len(snapshot_ids) != 1:
        raise ValueError("vol_rows must belong to a single snapshot_id.")
    snapshot_id = next(iter(snapshot_ids))

    payload: list[ModelParamRowPayload] = []
    for expiry_key in sorted(grouped.keys()):
        rows = grouped[expiry_key]
        forward = fwd_map.get(expiry_key)
        if forward is None:
            raise ValueError(f"Missing forward rate for expiry x_years={expiry_key}.")

        shift_set = {float(row.sabr_shift) for row in rows}
        if len(shift_set) != 1:
            raise ValueError(f"sabr_shift must be unique per expiry. x_years={expiry_key}.")
        sabr_shift = next(iter(shift_set))

        strikes = np.asarray([float(row.strike_rate) for row in rows], dtype=float)
        sigmas = np.asarray([float(row.sigma) for row in rows], dtype=float)
        if np.any(sigmas <= 0.0) or np.any(~np.isfinite(sigmas)):
            raise ValueError(f"Invalid sigma found for expiry x_years={expiry_key}.")

        first = rows[0]
        result = calibrate_sabr(
            strikes,
            sigmas,
            float(forward),
            float(first.x_years),
            vol_type=vol_type,
            preset=preset,
            shift=float(sabr_shift),
            fixed_params=fixed_params,
        )
        if not result.success:
            raise ValueError(
                f"SABR calibration failed at expiry x_years={expiry_key}: {result.message}"
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
                    swap_tenor=None,
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
