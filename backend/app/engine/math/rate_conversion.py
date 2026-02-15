from __future__ import annotations

from enum import Enum
from typing import Iterable, Union, overload

import numpy as np

class Compounding(Enum):
    CONTINUOUS = "CONTINUOUS"
    SIMPLE = "SIMPLE"
    DISCRETE = "DISCRETE"


# Support both scalar float and numpy arrays
RateLike = Union[float, Iterable[float], np.ndarray]
ArrayLike = Union[Iterable[float], np.ndarray]
ArrayLikeOrScalar = Union[float, ArrayLike]


def _normalize_compounding(compounding: Compounding | str) -> Compounding:
    if isinstance(compounding, Compounding):
        return compounding
    key = str(compounding).strip().upper()
    match key:
        case "CONT" | "CONTINUOUS":
            return Compounding.CONTINUOUS
        case "SIMP" | "SIMPLE" | "SIMPLE_RATE":
            return Compounding.SIMPLE
        case "DISC" | "DISCRETE":
            return Compounding.DISCRETE
        case _:
            raise ValueError(f"Unsupported compounding: {compounding!r}")


def _validate_freq(freq: int) -> int:
    if freq is None:
        raise ValueError("compounding_freq is required for discrete compounding.")
    if freq <= 0:
        raise ValueError("compounding_freq must be positive for discrete compounding.")
    return freq


@overload
def discount_factor(
    rate: float,
    year_fraction: float,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> float: ...

@overload
def discount_factor(
    rate: float,
    year_fraction: ArrayLike,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> np.ndarray: ...

@overload
def discount_factor(
    rate: ArrayLike,
    year_fraction: float,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> np.ndarray: ...

@overload
def discount_factor(
    rate: ArrayLike,
    year_fraction: ArrayLike,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> np.ndarray: ...

def discount_factor(
    rate: RateLike,
    year_fraction: ArrayLikeOrScalar,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> float | np.ndarray:
    # Note: We use np.asarray to support both scalar and array inputs implicitly via numpy broadcasting.
    # Explicit checks for 0.0 are removed to allow vectorization; exp(0) is 1.0 anyway.
    if rate is None:
        raise ValueError("Rate is required.")

    # Convert inputs to numpy arrays/scalars for consistent math operations.
    # year_fraction is from the discounting start date (valuation/anchor date) to the end date
    # that this rate applies to (e.g., cashflow/maturity date).
    r = np.asarray(rate)
    t = np.asarray(year_fraction)

    if np.any(t < 0):
        raise ValueError("Year fraction must be non-negative for discount factors.")

    comp = _normalize_compounding(compounding)
    match comp:
        case Compounding.CONTINUOUS:
            return np.exp(-r * t)
        case Compounding.SIMPLE:
            return 1.0 / (1.0 + r * t)
        case Compounding.DISCRETE:
            freq = _validate_freq(freq)
            return 1.0 / np.power(1.0 + r / freq, freq * t)
        case _:
            raise ValueError(f"Unsupported compounding: {compounding!r}")


@overload
def zero_rate_from_df(
    df: float,
    year_fraction: float,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> float: ...

@overload
def zero_rate_from_df(
    df: float,
    year_fraction: ArrayLike,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> np.ndarray: ...

@overload
def zero_rate_from_df(
    df: ArrayLike,
    year_fraction: float,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> np.ndarray: ...

@overload
def zero_rate_from_df(
    df: ArrayLike,
    year_fraction: ArrayLike,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> np.ndarray: ...

def zero_rate_from_df(
    df: ArrayLikeOrScalar,
    year_fraction: ArrayLikeOrScalar,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> float | np.ndarray:
    d = np.asarray(df)
    # year_fraction is from the DF start date (valuation/anchor date) to the DF end date
    # that df represents.
    t = np.asarray(year_fraction)

    if np.any(d <= 0.0):
        raise ValueError("Discount factor must be positive.")

    # Avoid division by zero for t=0. Where t=0, zero rate is technically undefined or limit-dependent.
    # Here we return 0.0 for t=0 to maintain compatibility, using np.divide with where/out or masking.
    # For simplicity in vectorization, we can use a mask.

    comp = _normalize_compounding(compounding)

    # Mask for safe division
    with np.errstate(divide='ignore', invalid='ignore'):
        match comp:
            case Compounding.CONTINUOUS:
                z = -np.log(d) / t
            case Compounding.SIMPLE:
                z = (1.0 / d - 1.0) / t
            case Compounding.DISCRETE:
                freq = _validate_freq(freq)
                z = freq * (np.power(d, -1.0 / (freq * t)) - 1.0)
            case _:
                raise ValueError(f"Unsupported compounding: {compounding!r}")

    # Handle t=0 case (replace NaNs/Infs resulting from div by zero with 0.0)
    if np.ndim(z) == 0:
        return 0.0 if t == 0 else float(z)

    z = np.where(t == 0, 0.0, z)
    return z


@overload
def convert_rate(
    rate: float,
    year_fraction: float,
    from_compounding: Compounding | str,
    to_compounding: Compounding | str,
    *,
    from_freq: int = 1,
    to_freq: int = 1,
) -> float: ...

@overload
def convert_rate(
    rate: ArrayLike,
    year_fraction: ArrayLikeOrScalar,
    from_compounding: Compounding | str,
    to_compounding: Compounding | str,
    *,
    from_freq: int = 1,
    to_freq: int = 1,
) -> np.ndarray: ...

def convert_rate(
    rate: RateLike,
    year_fraction: ArrayLikeOrScalar,
    from_compounding: Compounding | str,
    to_compounding: Compounding | str,
    *,
    from_freq: int = 1,
    to_freq: int = 1,
) -> float | np.ndarray:
    # year_fraction is the same start/end period for both rate representations being converted.
    df = discount_factor(rate, year_fraction, from_compounding, freq=from_freq)
    return zero_rate_from_df(df, year_fraction, to_compounding, freq=to_freq)


@overload
def forward_rate_from_dfs(
    df_start: float,
    df_end: float,
    year_fraction: float,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> float: ...

@overload
def forward_rate_from_dfs(
    df_start: ArrayLike,
    df_end: ArrayLike,
    year_fraction: ArrayLikeOrScalar,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> np.ndarray: ...

def forward_rate_from_dfs(
    df_start: ArrayLikeOrScalar,
    df_end: ArrayLikeOrScalar,
    year_fraction: ArrayLikeOrScalar,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> float | np.ndarray:
    # year_fraction is from the start date associated with df_start to the end date associated
    # with df_end (i.e., the forward accrual period).
    t = np.asarray(year_fraction)
    ds = np.asarray(df_start)
    de = np.asarray(df_end)

    if np.any(ds <= 0.0) or np.any(de <= 0.0):
        raise ValueError("Discount factors must be positive.")

    comp = _normalize_compounding(compounding)
    df_ratio = de / ds

    # Reuse zero_rate logic logic effectively (since fwd rate is essentially zero rate over the period)
    # But implementing directly for clarity and speed
    with np.errstate(divide='ignore', invalid='ignore'):
        match comp:
            case Compounding.CONTINUOUS:
                r = -np.log(df_ratio) / t
            case Compounding.SIMPLE:
                r = (1.0 / df_ratio - 1.0) / t
            case Compounding.DISCRETE:
                freq = _validate_freq(freq)
                r = freq * (np.power(df_ratio, -1.0 / (freq * t)) - 1.0)
            case _:
                raise ValueError(f"Unsupported compounding: {compounding!r}")

    if np.ndim(r) == 0:
        return 0.0 if t == 0 else float(r)
    return np.where(t == 0, 0.0, r)


@overload
def df_from_forward_rate(
    df_start: float,
    forward_rate: float,
    year_fraction: float,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> float: ...

@overload
def df_from_forward_rate(
    df_start: ArrayLike,
    forward_rate: ArrayLike,
    year_fraction: ArrayLikeOrScalar,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> np.ndarray: ...

def df_from_forward_rate(
    df_start: ArrayLikeOrScalar,
    forward_rate: ArrayLikeOrScalar,
    year_fraction: ArrayLikeOrScalar,
    compounding: Compounding | str,
    *,
    freq: int = 1,
) -> float | np.ndarray:
    # t can be array
    # year_fraction is the forward rate accrual period: start date corresponds to df_start,
    # end date corresponds to the returned df_end.
    t = np.asarray(year_fraction)
    f = np.asarray(forward_rate)
    ds = np.asarray(df_start)

    comp = _normalize_compounding(compounding)
    match comp:
        case Compounding.CONTINUOUS:
            return ds * np.exp(-f * t)
        case Compounding.SIMPLE:
            return ds / (1.0 + f * t)
        case Compounding.DISCRETE:
            freq = _validate_freq(freq)
            return ds / np.power(1.0 + f / freq, freq * t)
        case _:
            raise ValueError(f"Unsupported compounding: {compounding!r}")
