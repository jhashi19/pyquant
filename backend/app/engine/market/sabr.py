from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np
from scipy.optimize import least_squares  # type: ignore[import-untyped]


class SabrVolType(Enum):
    LOGNORMAL = "LOGNORMAL"
    NORMAL = "NORMAL"


class SabrCalibrationPreset(Enum):
    FULL = "FULL"
    BETA_00 = "BETA_00"
    BETA_25 = "BETA_25"
    BETA_50 = "BETA_50"
    BETA_75 = "BETA_75"
    BETA_100 = "BETA_100"


@dataclass(frozen=True)
class SabrParams:
    alpha: float
    beta: float
    rho: float
    nu: float


@dataclass(frozen=True)
class SabrCalibrationResult:
    params: SabrParams
    success: bool
    status: int
    message: str
    nfev: int
    rmse: float
    residuals: np.ndarray
    model_vols: np.ndarray
    free_parameters: tuple[str, ...]
    fixed_parameters: dict[str, float]
    preset: SabrCalibrationPreset


_PARAMETER_NAMES = ("alpha", "beta", "rho", "nu")
_PRESET_FIXED: dict[SabrCalibrationPreset, dict[str, float]] = {
    SabrCalibrationPreset.FULL: {},
    SabrCalibrationPreset.BETA_00: {"beta": 0.0},
    SabrCalibrationPreset.BETA_25: {"beta": 0.25},
    SabrCalibrationPreset.BETA_50: {"beta": 0.5},
    SabrCalibrationPreset.BETA_75: {"beta": 0.75},
    SabrCalibrationPreset.BETA_100: {"beta": 1.0},
}
_EPS = 1e-12


def available_sabr_calibration_presets() -> tuple[str, ...]:
    return tuple(p.value for p in SabrCalibrationPreset)


def _as_1d_float_array(values: Iterable[float] | np.ndarray | float, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim == 0:
        return arr.reshape(1)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be a scalar or 1D array.")
    return arr


def _normalize_vol_type(vol_type: SabrVolType | str) -> SabrVolType:
    if isinstance(vol_type, SabrVolType):
        return vol_type
    key = str(vol_type).strip().upper()
    match key:
        case "LOGNORMAL" | "BLACK":
            return SabrVolType.LOGNORMAL
        case "NORMAL" | "BACHELIER":
            return SabrVolType.NORMAL
        case _:
            raise ValueError(f"Unsupported SABR vol type: {vol_type!r}")


def _normalize_preset(preset: SabrCalibrationPreset | str) -> SabrCalibrationPreset:
    if isinstance(preset, SabrCalibrationPreset):
        return preset
    key = str(preset).strip().upper()
    for item in SabrCalibrationPreset:
        if item.value == key:
            return item
    raise ValueError(f"Unsupported SABR calibration preset: {preset!r}")


def _check_inputs(forward: float, expiry: float, strikes: np.ndarray, vols: Optional[np.ndarray]) -> None:
    if not np.isfinite(forward):
        raise ValueError("forward must be finite.")
    if expiry <= 0.0 or not np.isfinite(expiry):
        raise ValueError("expiry must be positive and finite.")
    if strikes.ndim != 1 or strikes.size == 0:
        raise ValueError("strikes must be a non-empty 1D array.")
    if not np.all(np.isfinite(strikes)):
        raise ValueError("strikes must be finite.")
    if vols is not None:
        if vols.ndim != 1 or vols.size != strikes.size:
            raise ValueError("market_vols must be a 1D array with the same length as strikes.")
        if not np.all(np.isfinite(vols)):
            raise ValueError("market_vols must be finite.")
        if np.any(vols <= 0.0):
            raise ValueError("market_vols must be positive.")


def _validate_params(params: SabrParams) -> None:
    if params.alpha <= 0.0:
        raise ValueError("alpha must be positive.")
    if params.nu <= 0.0:
        raise ValueError("nu must be positive.")
    if not (0.0 <= params.beta <= 1.0):
        raise ValueError("beta must be in [0, 1].")
    if not (-1.0 < params.rho < 1.0):
        raise ValueError("rho must be in (-1, 1).")


def _z_over_xz(z: np.ndarray, rho: float) -> np.ndarray:
    sqrt_term = np.sqrt(np.maximum(1.0 - 2.0 * rho * z + z * z, _EPS))
    num = sqrt_term + z - rho
    den = 1.0 - rho
    xz = np.log(np.maximum(num / den, _EPS))
    out = np.ones_like(z)
    mask = np.abs(z) > 1e-8
    out[mask] = z[mask] / xz[mask]
    return out


def _sabr_lognormal_vol(forward: float, strikes: np.ndarray, expiry: float, params: SabrParams) -> np.ndarray:
    _validate_params(params)
    if forward <= 0.0 or np.any(strikes <= 0.0):
        raise ValueError("LOGNORMAL SABR requires strictly positive forward and strikes.")

    alpha = params.alpha
    beta = params.beta
    rho = params.rho
    nu = params.nu

    fk = forward * strikes
    log_fk = np.log(forward / strikes)
    one_minus_beta = 1.0 - beta
    fk_pow = np.power(fk, 0.5 * one_minus_beta)
    z = (nu / alpha) * fk_pow * log_fk
    z_xz = _z_over_xz(z, rho)

    log_fk2 = log_fk * log_fk
    log_fk4 = log_fk2 * log_fk2
    denom = fk_pow * (
        1.0
        + (one_minus_beta * one_minus_beta / 24.0) * log_fk2
        + (one_minus_beta**4 / 1920.0) * log_fk4
    )
    base = alpha / np.maximum(denom, _EPS)

    corr = (
        (one_minus_beta * one_minus_beta / 24.0) * (alpha * alpha) / np.maximum(np.power(fk, one_minus_beta), _EPS)
        + (rho * beta * nu * alpha / 4.0) / np.maximum(fk_pow, _EPS)
        + ((2.0 - 3.0 * rho * rho) / 24.0) * nu * nu
    )
    vols = base * z_xz * (1.0 + corr * expiry)

    atm_mask = np.abs(log_fk) < 1e-12
    if np.any(atm_mask):
        f_pow = np.power(forward, one_minus_beta)
        atm_corr = (
            (one_minus_beta * one_minus_beta / 24.0) * (alpha * alpha) / np.maximum(f_pow * f_pow, _EPS)
            + (rho * beta * nu * alpha / 4.0) / np.maximum(f_pow, _EPS)
            + ((2.0 - 3.0 * rho * rho) / 24.0) * nu * nu
        )
        vols[atm_mask] = (alpha / np.maximum(f_pow, _EPS)) * (1.0 + atm_corr * expiry)
    return vols


def _sabr_normal_vol(forward: float, strikes: np.ndarray, expiry: float, params: SabrParams) -> np.ndarray:
    _validate_params(params)
    if forward <= 0.0 or np.any(strikes <= 0.0):
        raise ValueError("NORMAL SABR implementation currently requires positive forward and strikes.")

    alpha = params.alpha
    beta = params.beta
    rho = params.rho
    nu = params.nu

    fk = forward * strikes
    log_fk = np.log(forward / strikes)
    beta_factor = 1.0 - beta
    fk_pow = np.power(fk, 0.5 * beta_factor)
    z = (nu / alpha) * fk_pow * log_fk
    z_xz = _z_over_xz(z, rho)

    log_fk2 = log_fk * log_fk
    log_fk4 = log_fk2 * log_fk2
    kpow = np.power(fk, 0.5 * beta)
    denom = 1.0 + (beta * (2.0 - beta) / 24.0) * log_fk2 + ((beta * (2.0 - beta)) ** 2 / 1920.0) * log_fk4
    base = alpha * kpow / np.maximum(denom, _EPS)

    corr = (
        -(beta * (2.0 - beta) / 24.0) * (alpha * alpha) / np.maximum(np.power(fk, beta_factor), _EPS)
        + (rho * beta * nu * alpha / 4.0) / np.maximum(fk_pow, _EPS)
        + ((2.0 - 3.0 * rho * rho) / 24.0) * nu * nu
    )
    vols = base * z_xz * (1.0 + corr * expiry)

    atm_mask = np.abs(log_fk) < 1e-12
    if np.any(atm_mask):
        f_pow_beta = np.power(forward, beta)
        f_pow_one_minus_beta = np.power(forward, beta_factor)
        atm_corr = (
            -(beta * (2.0 - beta) / 24.0) * (alpha * alpha) / np.maximum(f_pow_one_minus_beta * f_pow_one_minus_beta, _EPS)
            + (rho * beta * nu * alpha / 4.0) / np.maximum(f_pow_one_minus_beta, _EPS)
            + ((2.0 - 3.0 * rho * rho) / 24.0) * nu * nu
        )
        vols[atm_mask] = alpha * f_pow_beta * (1.0 + atm_corr * expiry)
    return vols


def sabr_implied_vol(
    strikes: Iterable[float] | np.ndarray,
    forward: float,
    expiry: float,
    params: SabrParams,
    *,
    vol_type: SabrVolType | str = SabrVolType.LOGNORMAL,
) -> np.ndarray:
    strike_arr = _as_1d_float_array(strikes, "strikes")
    _check_inputs(forward, expiry, strike_arr, None)
    vtype = _normalize_vol_type(vol_type)
    match vtype:
        case SabrVolType.LOGNORMAL:
            return _sabr_lognormal_vol(forward, strike_arr, expiry, params)
        case SabrVolType.NORMAL:
            return _sabr_normal_vol(forward, strike_arr, expiry, params)
        case _:
            raise ValueError(f"Unsupported SABR vol type: {vol_type!r}")


def _sigmoid(x: float) -> float:
    if x >= 0:
        ex = np.exp(-x)
        return float(1.0 / (1.0 + ex))
    ex = np.exp(x)
    return float(ex / (1.0 + ex))


def _logit(p: float) -> float:
    p_clip = float(np.clip(p, 1e-8, 1.0 - 1e-8))
    return float(np.log(p_clip / (1.0 - p_clip)))


def _pack_initial_value(name: str, value: float) -> float:
    match name:
        case "alpha" | "nu":
            return float(np.log(max(value, 1e-8)))
        case "beta":
            return _logit(value)
        case "rho":
            return float(np.arctanh(np.clip(value, -0.999999, 0.999999)))
        case _:
            raise ValueError(f"Unknown SABR parameter: {name!r}")


def _unpack_value(name: str, x: float) -> float:
    match name:
        case "alpha" | "nu":
            return float(np.exp(x))
        case "beta":
            return _sigmoid(x)
        case "rho":
            return float(np.tanh(x))
        case _:
            raise ValueError(f"Unknown SABR parameter: {name!r}")


def _default_initial_guess(
    *,
    strikes: np.ndarray,
    market_vols: np.ndarray,
    forward: float,
    vol_type: SabrVolType,
    fixed: Mapping[str, float],
) -> dict[str, float]:
    idx_atm = int(np.argmin(np.abs(strikes - forward)))
    vol_atm = float(market_vols[idx_atm])
    beta = float(fixed.get("beta", 0.5))
    alpha_guess = vol_atm
    if vol_type == SabrVolType.LOGNORMAL:
        alpha_guess = vol_atm * np.power(max(forward, 1e-8), 1.0 - beta)
    elif vol_type == SabrVolType.NORMAL:
        alpha_guess = vol_atm / np.power(max(forward, 1e-8), beta)
    return {
        "alpha": max(alpha_guess, 1e-6),
        "beta": np.clip(beta, 0.0, 1.0),
        "rho": 0.0,
        "nu": 0.5,
    }


def calibrate_sabr(
    strikes: Iterable[float] | np.ndarray,
    market_vols: Iterable[float] | np.ndarray,
    forward: float,
    expiry: float,
    *,
    vol_type: SabrVolType | str = SabrVolType.LOGNORMAL,
    preset: SabrCalibrationPreset | str = SabrCalibrationPreset.BETA_50,
    fixed_params: Optional[Mapping[str, float]] = None,
    initial_guess: Optional[Mapping[str, float]] = None,
    weights: Optional[Sequence[float] | np.ndarray] = None,
    ftol: float = 1e-12,
    xtol: float = 1e-12,
    gtol: float = 1e-12,
    max_nfev: int = 5000,
) -> SabrCalibrationResult:
    strike_arr = _as_1d_float_array(strikes, "strikes")
    market_arr = _as_1d_float_array(market_vols, "market_vols")
    _check_inputs(forward, expiry, strike_arr, market_arr)
    vtype = _normalize_vol_type(vol_type)
    preset_tag = _normalize_preset(preset)

    fixed: dict[str, float] = dict(_PRESET_FIXED[preset_tag])
    if fixed_params is not None:
        for key, value in fixed_params.items():
            if key not in _PARAMETER_NAMES:
                raise ValueError(f"Unsupported fixed parameter: {key!r}")
            fixed[key] = float(value)

    defaults = _default_initial_guess(
        strikes=strike_arr,
        market_vols=market_arr,
        forward=forward,
        vol_type=vtype,
        fixed=fixed,
    )
    if initial_guess is not None:
        for key, value in initial_guess.items():
            if key not in _PARAMETER_NAMES:
                raise ValueError(f"Unsupported initial guess parameter: {key!r}")
            defaults[key] = float(value)

    for key, value in fixed.items():
        defaults[key] = float(value)

    params_probe = SabrParams(
        alpha=defaults["alpha"],
        beta=defaults["beta"],
        rho=defaults["rho"],
        nu=defaults["nu"],
    )
    _validate_params(params_probe)

    free_names = tuple(name for name in _PARAMETER_NAMES if name not in fixed)
    if len(free_names) == 0:
        model = sabr_implied_vol(strike_arr, forward, expiry, params_probe, vol_type=vtype)
        residuals = model - market_arr
        rmse = float(np.sqrt(np.mean(residuals * residuals)))
        return SabrCalibrationResult(
            params=params_probe,
            success=True,
            status=0,
            message="No free parameter to calibrate.",
            nfev=0,
            rmse=rmse,
            residuals=residuals,
            model_vols=model,
            free_parameters=free_names,
            fixed_parameters=fixed,
            preset=preset_tag,
        )

    if strike_arr.size < len(free_names):
        raise ValueError(
            "Levenberg-Marquardt requires number of observations >= number of free parameters."
        )

    if weights is None:
        w_sqrt = np.ones_like(market_arr)
    else:
        w = np.asarray(weights, dtype=float)
        if w.shape != market_arr.shape:
            raise ValueError("weights must have the same shape as market_vols.")
        if np.any(w <= 0.0) or not np.all(np.isfinite(w)):
            raise ValueError("weights must be positive and finite.")
        w_sqrt = np.sqrt(w)

    x0 = np.array([_pack_initial_value(name, defaults[name]) for name in free_names], dtype=float)

    def _build_params(x: np.ndarray) -> SabrParams:
        values = dict(defaults)
        for i, name in enumerate(free_names):
            values[name] = _unpack_value(name, float(x[i]))
        return SabrParams(
            alpha=float(values["alpha"]),
            beta=float(values["beta"]),
            rho=float(values["rho"]),
            nu=float(values["nu"]),
        )

    def _residual(x: np.ndarray) -> np.ndarray:
        p = _build_params(x)
        model = sabr_implied_vol(strike_arr, forward, expiry, p, vol_type=vtype)
        return (model - market_arr) * w_sqrt

    opt = least_squares(
        _residual,
        x0,
        method="lm",
        ftol=ftol,
        xtol=xtol,
        gtol=gtol,
        max_nfev=max_nfev,
    )

    calibrated = _build_params(opt.x)
    model_vols = sabr_implied_vol(strike_arr, forward, expiry, calibrated, vol_type=vtype)
    residuals = model_vols - market_arr
    rmse = float(np.sqrt(np.mean(residuals * residuals)))
    return SabrCalibrationResult(
        params=calibrated,
        success=bool(opt.success),
        status=int(opt.status),
        message=str(opt.message),
        nfev=int(opt.nfev),
        rmse=rmse,
        residuals=residuals,
        model_vols=model_vols,
        free_parameters=free_names,
        fixed_parameters=fixed,
        preset=preset_tag,
    )
