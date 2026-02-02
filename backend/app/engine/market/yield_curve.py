from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, overload

import numpy as np
from scipy.interpolate import CubicSpline

from app.engine.math.extrapolation import flat_forward_extrapolate, linear_zero_extrapolate
from app.engine.math.interpolation import (
    ArrayLike,
    ExtrapMethod,
    InterpMethod,
    MonotoneConvexSpline,
    normalize_extrap_method,
    normalize_interp_method,
    validate_curve_inputs,
)
from app.engine.math.rate_conversion import discount_factor, zero_rate_from_df


@dataclass(frozen=True)
class CurveInterpolator:
    x: np.ndarray
    df_nodes: np.ndarray
    zero_nodes: np.ndarray
    input_kind: str  # "DF" or "ZERO"
    compounding: str
    interp_method: InterpMethod
    extrap_left: ExtrapMethod
    extrap_right: ExtrapMethod
    _spline: Optional[object] = None

    @classmethod
    def from_nodes(
        cls,
        x: Iterable[float],
        *,
        df_nodes: Optional[Iterable[float]] = None,
        zero_nodes: Optional[Iterable[float]] = None,
        compounding: str = "CONTINUOUS",
        interp_method: str = "MONOTONE_CONVEX",
        extrap_left: str = "FLAT_FWD",
        extrap_right: str = "FLAT_FWD",
        allow_negative_rates: bool = True,
        cap_factor: float = 2.0,
    ) -> "CurveInterpolator":
        x_arr, df_arr, zero_arr, input_kind = validate_curve_inputs(
            x,
            df_nodes=df_nodes,
            zero_nodes=zero_nodes,
            compounding=compounding,
            allow_negative_rates=allow_negative_rates,
        )

        method = normalize_interp_method(interp_method)
        spline = None
        if method == InterpMethod.CUBIC_SPLINE:
            spline = CubicSpline(x_arr, zero_arr, extrapolate=False)
        elif method == InterpMethod.MONOTONE_CONVEX:
            spline = MonotoneConvexSpline.from_discount_factors(
                x_arr, df_arr, allow_negative_rates=allow_negative_rates, cap_factor=cap_factor
            )

        return cls(
            x=x_arr,
            df_nodes=df_arr,
            zero_nodes=zero_arr,
            input_kind=input_kind,
            compounding=compounding,
            interp_method=method,
            extrap_left=normalize_extrap_method(extrap_left),
            extrap_right=normalize_extrap_method(extrap_right),
            _spline=spline,
        )

    @overload
    def df(self, xq: float) -> float: ...
    @overload
    def df(self, xq: Iterable[float] | np.ndarray) -> np.ndarray: ...

    def df(self, xq: ArrayLike) -> float | np.ndarray:
        xq_arr = np.asarray(xq, dtype=float)
        scalar = xq_arr.ndim == 0
        x_flat = xq_arr.reshape(-1)

        result = np.empty_like(x_flat, dtype=float)
        left_mask = x_flat < self.x[0]
        right_mask = x_flat > self.x[-1]
        mid_mask = ~(left_mask | right_mask)

        if np.any(mid_mask):
            result[mid_mask] = self._interp(x_flat[mid_mask], output="DF")
        if np.any(left_mask):
            result[left_mask] = self._extrapolate(x_flat[left_mask], side="left")
        if np.any(right_mask):
            result[right_mask] = self._extrapolate(x_flat[right_mask], side="right")

        if scalar:
            return float(result[0])
        return result.reshape(xq_arr.shape)

    @overload
    def zero_rate(self, xq: float) -> float: ...
    @overload
    def zero_rate(self, xq: Iterable[float] | np.ndarray) -> np.ndarray: ...

    def zero_rate(self, xq: ArrayLike) -> float | np.ndarray:
        df_vals = self.df(xq)
        xq_arr = np.asarray(xq, dtype=float)
        rates = zero_rate_from_df(df_vals, xq_arr, self.compounding)
        if np.ndim(rates) == 0:
            return float(rates)
        return rates

    def _interp(self, xq: np.ndarray, *, output: str) -> np.ndarray:
        method = self.interp_method
        match method:
            case InterpMethod.LOG_LINEAR:
                log_df = np.log(self.df_nodes)
                log_vals = np.interp(xq, self.x, log_df)
                df_vals = np.exp(log_vals)
                if output == "DF":
                    return df_vals
                return zero_rate_from_df(df_vals, xq, self.compounding)
            case InterpMethod.MONOTONE_CONVEX:
                if self._spline is None:
                    raise ValueError("Spline interpolator is not initialized.")
                df_vals = self._spline.df(xq)
                if output == "DF":
                    return df_vals
                return zero_rate_from_df(df_vals, xq, self.compounding)
            case InterpMethod.LINEAR:
                zero_vals = np.interp(xq, self.x, self.zero_nodes)
                if output == "ZERO":
                    return zero_vals
                return discount_factor(zero_vals, xq, self.compounding)
            case InterpMethod.CUBIC_SPLINE:
                if self._spline is None:
                    raise ValueError("Spline interpolator is not initialized.")
                zero_vals = self._spline(xq)
                if output == "ZERO":
                    return zero_vals
                return discount_factor(zero_vals, xq, self.compounding)
            case _:
                raise ValueError(f"Unsupported interpolation method: {method!r}")

    def _extrapolate(self, xq: np.ndarray, *, side: str) -> np.ndarray:
        method = self.extrap_left if side == "left" else self.extrap_right
        match method:
            case ExtrapMethod.FLAT_FWD:
                return flat_forward_extrapolate(self.x, self.df_nodes, xq, side=side)
            case ExtrapMethod.FLAT_ZERO:
                idx = 0 if side == "left" else -1
                z = self.zero_nodes[idx]
                return discount_factor(z, xq, self.compounding)
            case ExtrapMethod.LINEAR:
                zeros = linear_zero_extrapolate(self.x, self.zero_nodes, xq, side=side)
                return discount_factor(zeros, xq, self.compounding)
            case _:
                raise ValueError(f"Unsupported extrapolation method: {method!r}")


@dataclass(frozen=True)
class YieldCurve:
    interpolator: CurveInterpolator
    curve_id: Optional[str] = None
    ccy: Optional[str] = None

    @classmethod
    def from_nodes(
        cls,
        x: Iterable[float],
        *,
        df_nodes: Optional[Iterable[float]] = None,
        zero_nodes: Optional[Iterable[float]] = None,
        compounding: str = "CONTINUOUS",
        interp_method: str = "MONOTONE_CONVEX",
        extrap_left: str = "FLAT_FWD",
        extrap_right: str = "FLAT_FWD",
        allow_negative_rates: bool = True,
        cap_factor: float = 2.0,
        curve_id: Optional[str] = None,
        ccy: Optional[str] = None,
    ) -> "YieldCurve":
        interpolator = CurveInterpolator.from_nodes(
            x,
            df_nodes=df_nodes,
            zero_nodes=zero_nodes,
            compounding=compounding,
            interp_method=interp_method,
            extrap_left=extrap_left,
            extrap_right=extrap_right,
            allow_negative_rates=allow_negative_rates,
            cap_factor=cap_factor,
        )
        return cls(interpolator=interpolator, curve_id=curve_id, ccy=ccy)

    def df(self, xq: ArrayLike) -> ArrayLike:
        return self.interpolator.df(xq)

    def zero_rate(self, xq: ArrayLike) -> ArrayLike:
        return self.interpolator.zero_rate(xq)

    def value(self, xq: ArrayLike) -> ArrayLike:
        if self.interpolator.input_kind == "DF":
            return self.df(xq)
        return self.zero_rate(xq)
