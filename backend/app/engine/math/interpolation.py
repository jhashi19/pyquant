from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Union

import numpy as np
from scipy.interpolate import CubicSpline

from .rate_conversion import discount_factor, zero_rate_from_df


ArrayLike = Union[float, Iterable[float], np.ndarray]


def _to_numpy_1d(values: Iterable[float], name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be 1D.")
    return arr


def _sort_xy(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(x)
    x_sorted = x[order]
    y_sorted = y[order]
    if np.any(np.diff(x_sorted) <= 0.0):
        raise ValueError("x must be strictly increasing.")
    return x_sorted, y_sorted


def _flat_forward_extrapolate(
    x_nodes: np.ndarray, df_nodes: np.ndarray, xq: np.ndarray, *, side: str
) -> np.ndarray:
    if x_nodes.size < 1:
        raise ValueError("At least one node is required for extrapolation.")
    
    if x_nodes.size == 1:
        # Single node: assume constant zero rate (flat curve)
        t0 = x_nodes[0]
        r = -np.log(df_nodes[0]) / t0 if t0 > 0 else 0.0
        return np.exp(-r * xq)

    if side == "left":
        x0, x1 = x_nodes[0], x_nodes[1]
        df0, df1 = df_nodes[0], df_nodes[1]
    else:
        x0, x1 = x_nodes[-2], x_nodes[-1]
        df0, df1 = df_nodes[-2], df_nodes[-1]
    m = (np.log(df1) - np.log(df0)) / (x1 - x0)
    anchor_x = x1 if side == "right" else x0
    anchor_df = df1 if side == "right" else df0
    return anchor_df * np.exp(m * (xq - anchor_x))


def _linear_zero_extrapolate(
    x_nodes: np.ndarray, zero_nodes: np.ndarray, xq: np.ndarray, *, side: str
) -> np.ndarray:
    if x_nodes.size < 1:
        raise ValueError("At least one node is required for extrapolation.")
    
    if x_nodes.size == 1:
        return np.full_like(xq, zero_nodes[0])

    if side == "left":
        x0, x1 = x_nodes[0], x_nodes[1]
        z0, z1 = zero_nodes[0], zero_nodes[1]
    else:
        x0, x1 = x_nodes[-2], x_nodes[-1]
        z0, z1 = zero_nodes[-2], zero_nodes[-1]
    slope = (z1 - z0) / (x1 - x0)
    anchor_x = x1 if side == "right" else x0
    anchor_z = z1 if side == "right" else z0
    return anchor_z + slope * (xq - anchor_x)


class _SegmentCase:
    BASE_QUADRATIC = 0
    FLAT_LEFT = 1
    FLAT_RIGHT = 2
    TWO_SIDED = 3


def _validate_strictly_increasing(x: np.ndarray, name: str) -> None:
    if x.ndim != 1:
        raise ValueError(f"{name} must be 1-D array.")
    if np.any(~np.isfinite(x)):
        raise ValueError(f"{name} contains NaN/inf.")
    if np.any(np.diff(x) <= 0.0):
        raise ValueError(f"{name} must be strictly increasing.")


def _validate_df(df: np.ndarray, allow_negative_rates: bool) -> None:
    if df.ndim != 1:
        raise ValueError("df must be 1-D array.")
    if np.any(~np.isfinite(df)):
        raise ValueError("df contains NaN/inf.")
    if np.any(df <= 0.0):
        raise ValueError("df must be > 0 for all nodes (log defined).")
    if not allow_negative_rates:
        if np.any(np.diff(df) > 1e-14):
            raise ValueError(
                "df is not non-increasing. "
                "If negative rates are possible, set allow_negative_rates=True."
            )


def _discrete_forward_from_df(t: np.ndarray, df: np.ndarray) -> np.ndarray:
    dt = np.diff(t)
    lnP = np.log(df)
    return -(lnP[1:] - lnP[:-1]) / dt


def _node_forward_from_discrete_forward(t: np.ndarray, f_d: np.ndarray) -> np.ndarray:
    n = len(t) - 1
    if len(f_d) != n:
        raise ValueError("f_d length mismatch.")

    f = np.empty(n + 1, dtype=float)
    if n == 1:
        f[0] = f_d[0]
        f[1] = f_d[0]
        return f

    for i in range(1, n):
        w1 = (t[i] - t[i - 1]) / (t[i + 1] - t[i - 1])
        w2 = (t[i + 1] - t[i]) / (t[i + 1] - t[i - 1])
        f[i] = w1 * f_d[i] + w2 * f_d[i - 1]

    f[0] = f_d[0] - 0.5 * (f[1] - f_d[0])
    f[n] = f_d[n - 1] - 0.5 * (f[n - 1] - f_d[n - 1])
    return f


def _collar_node_forwards(
    f: np.ndarray,
    f_d: np.ndarray,
    allow_negative_rates: bool,
    cap_factor: float = 2.0,
) -> np.ndarray:
    n = len(f) - 1
    out = f.copy()
    floor = -np.inf if allow_negative_rates else 0.0

    out[0] = np.clip(out[0], floor, cap_factor * f_d[0])
    out[n] = np.clip(out[n], floor, cap_factor * f_d[n - 1])
    for i in range(1, n):
        cap = cap_factor * min(f_d[i - 1], f_d[i])
        out[i] = np.clip(out[i], floor, cap)
    return out


def _baseline_quadratic_coeffs(g0: float, g1: float) -> tuple[float, float, float]:
    K = g0
    L = -4.0 * g0 - 2.0 * g1
    M = 3.0 * (g0 + g1)
    return K, L, M


def _decide_segment_case(g0: float, g1: float) -> tuple[int, float, float]:
    if abs(g0) < 1e-16 and abs(g1) < 1e-16:
        return _SegmentCase.BASE_QUADRATIC, 0.0, 0.0

    gp0 = -4.0 * g0 - 2.0 * g1
    gp1 = 2.0 * g0 + 4.0 * g1
    direction = g1 - g0

    def _is_monotone() -> bool:
        if abs(direction) < 1e-16:
            return True
        if direction > 0.0:
            return gp0 >= -1e-16 and gp1 >= -1e-16
        return gp0 <= 1e-16 and gp1 <= 1e-16

    if g0 * g1 > 0.0:
        s = g0 + g1
        eta = 0.5 if abs(s) < 1e-16 else g1 / s
        eta = float(np.clip(eta, 1e-10, 1.0 - 1e-10))
        A = -0.5 * (eta * g0 + (1.0 - eta) * g1)
        return _SegmentCase.TWO_SIDED, eta, A

    if _is_monotone():
        return _SegmentCase.BASE_QUADRATIC, 0.0, 0.0

    if direction > 0.0:
        if gp0 < 0.0:
            denom = g1 - g0
            eta = 1.0 + 3.0 * g0 / denom
            eta = float(np.clip(eta, 1e-10, 1.0 - 1e-10))
            return _SegmentCase.FLAT_LEFT, eta, 0.0
        denom = g1 - g0
        eta = 3.0 * g1 / denom
        eta = float(np.clip(eta, 1e-10, 1.0 - 1e-10))
        return _SegmentCase.FLAT_RIGHT, eta, 0.0

    if gp0 > 0.0:
        denom = g1 - g0
        eta = 1.0 + 3.0 * g0 / denom
        eta = float(np.clip(eta, 1e-10, 1.0 - 1e-10))
        return _SegmentCase.FLAT_LEFT, eta, 0.0
    denom = g1 - g0
    eta = 3.0 * g1 / denom
    eta = float(np.clip(eta, 1e-10, 1.0 - 1e-10))
    return _SegmentCase.FLAT_RIGHT, eta, 0.0


@dataclass(frozen=True)
class _MonotoneConvexSpline:
    """Hagan–West Monotone Convex interpolation on DF space (vectorized evaluation)."""

    t: np.ndarray
    df_nodes: np.ndarray
    dt: np.ndarray
    f_d: np.ndarray
    g0: np.ndarray
    g1: np.ndarray
    case: np.ndarray
    eta: np.ndarray
    A: np.ndarray

    @classmethod
    def from_discount_factors(
        cls,
        x: np.ndarray,
        df_nodes: np.ndarray,
        *,
        allow_negative_rates: bool = True,
        cap_factor: float = 2.0,
    ) -> "_MonotoneConvexSpline":
        t = np.asarray(x, dtype=float)
        df = np.asarray(df_nodes, dtype=float)

        _validate_strictly_increasing(t, "x")
        _validate_df(df, allow_negative_rates=allow_negative_rates)

        if t[0] > 0.0:
            t = np.concatenate(([0.0], t))
            df = np.concatenate(([1.0], df))

        dt = np.diff(t)
        f_d = _discrete_forward_from_df(t, df)
        f = _node_forward_from_discrete_forward(t, f_d)
        f = _collar_node_forwards(
            f, f_d, allow_negative_rates=allow_negative_rates, cap_factor=cap_factor
        )

        n = len(t) - 1
        g0 = np.empty(n, dtype=float)
        g1 = np.empty(n, dtype=float)
        case = np.empty(n, dtype=int)
        eta = np.empty(n, dtype=float)
        A = np.empty(n, dtype=float)

        for i in range(1, len(t)):
            fd = float(f_d[i - 1])
            fL = float(f[i - 1])
            fR = float(f[i])
            g0_i = fL - fd
            g1_i = fR - fd
            case_i, eta_i, A_i = _decide_segment_case(g0_i, g1_i)
            g0[i - 1] = g0_i
            g1[i - 1] = g1_i
            case[i - 1] = case_i
            eta[i - 1] = eta_i
            A[i - 1] = A_i

        return cls(
            t=t,
            df_nodes=df,
            dt=dt,
            f_d=f_d,
            g0=g0,
            g1=g1,
            case=case,
            eta=eta,
            A=A,
        )

    def df(self, xq: np.ndarray) -> np.ndarray:
        xq_arr = np.asarray(xq, dtype=float)
        scalar = xq_arr.ndim == 0
        x_flat = xq_arr.reshape(-1)

        idx = np.searchsorted(self.t, x_flat, side="right") - 1
        idx = np.clip(idx, 0, self.dt.size - 1)

        t_left = self.t[idx]
        dt = self.dt[idx]
        x = (x_flat - t_left) / dt
        x = np.clip(x, 0.0, 1.0)

        fd = self.f_d[idx]
        g0 = self.g0[idx]
        g1 = self.g1[idx]
        eta = self.eta[idx]
        A = self.A[idx]
        case = self.case[idx]

        I_g = np.zeros_like(x)

        mask = case == _SegmentCase.BASE_QUADRATIC
        if np.any(mask):
            K, L, M = _baseline_quadratic_coeffs(g0[mask], g1[mask])
            xm = x[mask]
            I_g[mask] = K * xm + 0.5 * L * xm * xm + (1.0 / 3.0) * M * xm * xm * xm

        mask = case == _SegmentCase.FLAT_LEFT
        if np.any(mask):
            idx_mask = np.where(mask)[0]
            xm = x[idx_mask]
            et = eta[idx_mask]
            g0m = g0[idx_mask]
            g1m = g1[idx_mask]
            left = xm <= et
            if np.any(left):
                I_g[idx_mask[left]] = g0m[left] * xm[left]
            if np.any(~left):
                dx = xm[~left] - et[~left]
                I_g[idx_mask[~left]] = g0m[~left] * xm[~left] + (
                    (g1m[~left] - g0m[~left]) * (dx ** 3) / (3.0 * (1.0 - et[~left]) ** 2)
                )

        mask = case == _SegmentCase.FLAT_RIGHT
        if np.any(mask):
            idx_mask = np.where(mask)[0]
            xm = x[idx_mask]
            et = eta[idx_mask]
            g0m = g0[idx_mask]
            g1m = g1[idx_mask]
            left = xm <= et
            if np.any(left):
                term = (et[left] ** 3 - (et[left] - xm[left]) ** 3) / 3.0
                I_g[idx_mask[left]] = g1m[left] * xm[left] + (
                    (g0m[left] - g1m[left]) * term / (et[left] ** 2)
                )
            if np.any(~left):
                I_eta = (2.0 / 3.0) * g1m[~left] * et[~left] + (1.0 / 3.0) * g0m[
                    ~left
                ] * et[~left]
                I_g[idx_mask[~left]] = I_eta + g1m[~left] * (xm[~left] - et[~left])

        mask = case == _SegmentCase.TWO_SIDED
        if np.any(mask):
            idx_mask = np.where(mask)[0]
            xm = x[idx_mask]
            et = eta[idx_mask]
            g0m = g0[idx_mask]
            g1m = g1[idx_mask]
            Am = A[idx_mask]
            left = xm <= et
            if np.any(left):
                term = (et[left] ** 3 - (et[left] - xm[left]) ** 3) / 3.0
                I_g[idx_mask[left]] = Am[left] * xm[left] + (
                    (g0m[left] - Am[left]) * term / (et[left] ** 2)
                )
            if np.any(~left):
                I_eta = et[~left] * ((2.0 / 3.0) * Am[~left] + (1.0 / 3.0) * g0m[~left])
                dx = xm[~left] - et[~left]
                I_g[idx_mask[~left]] = (
                    I_eta
                    + Am[~left] * dx
                    + (g1m[~left] - Am[~left]) * (dx ** 3) / (3.0 * (1.0 - et[~left]) ** 2)
                )

        integral = fd * (x_flat - t_left) + dt * I_g
        df_left = self.df_nodes[idx]
        df_vals = df_left * np.exp(-integral)

        if scalar:
            return df_vals[0]
        return df_vals.reshape(xq_arr.shape)


@dataclass(frozen=True)
class CurveInterpolator:
    x: np.ndarray
    df_nodes: np.ndarray
    zero_nodes: np.ndarray
    input_kind: str  # "DF" or "ZERO"
    compounding: str
    interp_method: str
    extrap_left: str
    extrap_right: str
    _spline: Optional[object] = None

    @classmethod
    def from_nodes(
        cls,
        x: Iterable[float],
        *,
        df_nodes: Optional[Iterable[float]] = None,
        zero_nodes: Optional[Iterable[float]] = None,
        compounding: str = "CONTINUOUS",
        interp_method: str = "LOG_LINEAR",
        extrap_left: str = "FLAT_FWD",
        extrap_right: str = "FLAT_FWD",
    ) -> "CurveInterpolator":
        if (df_nodes is None) == (zero_nodes is None):
            raise ValueError("Provide exactly one of df_nodes or zero_nodes.")

        x_arr = _to_numpy_1d(x, "x")
        if np.any(x_arr < 0.0):
            raise ValueError("x must be non-negative.")

        if df_nodes is not None:
            df_arr = _to_numpy_1d(df_nodes, "df_nodes")
            x_arr, df_arr = _sort_xy(x_arr, df_arr)
            if np.any(df_arr <= 0.0):
                raise ValueError("Discount factors must be positive.")
            zero_arr = np.array(
                [zero_rate_from_df(df, t, compounding) for df, t in zip(df_arr, x_arr)]
            )
            input_kind = "DF"
        else:
            zero_arr = _to_numpy_1d(zero_nodes, "zero_nodes")
            x_arr, zero_arr = _sort_xy(x_arr, zero_arr)
            df_arr = np.array(
                [discount_factor(r, t, compounding) for r, t in zip(zero_arr, x_arr)]
            )
            input_kind = "ZERO"

        method = interp_method.upper()
        spline = None
        if method == "CUBIC_SPLINE":
            spline = CubicSpline(x_arr, zero_arr, extrapolate=False)
        elif method == "MONOTONE_CONVEX_SPLINE":
            spline = _MonotoneConvexSpline.from_discount_factors(x_arr, df_arr)

        return cls(
            x=x_arr,
            df_nodes=df_arr,
            zero_nodes=zero_arr,
            input_kind=input_kind,
            compounding=compounding,
            interp_method=method,
            extrap_left=extrap_left.upper(),
            extrap_right=extrap_right.upper(),
            _spline=spline,
        )

    def value(self, xq: ArrayLike) -> np.ndarray:
        if self.input_kind == "DF":
            return self.df(xq)
        return self.zero_rate(xq)

    def df(self, xq: ArrayLike) -> np.ndarray:
        xq_arr = np.asarray(xq, dtype=float)
        scalar = xq_arr.ndim == 0
        x_flat = xq_arr.reshape(-1)

        result = np.empty_like(x_flat, dtype=float)
        left_mask = x_flat < self.x[0]
        right_mask = x_flat > self.x[-1]
        mid_mask = ~(left_mask | right_mask)

        if np.any(mid_mask):
            result[mid_mask] = self._interp_df(x_flat[mid_mask])
        if np.any(left_mask):
            result[left_mask] = self._extrapolate(x_flat[left_mask], side="left")
        if np.any(right_mask):
            result[right_mask] = self._extrapolate(x_flat[right_mask], side="right")

        if scalar:
            return result[0]
        return result.reshape(xq_arr.shape)

    def zero_rate(self, xq: ArrayLike) -> np.ndarray:
        df_vals = self.df(xq)
        xq_arr = np.asarray(xq, dtype=float)
        df_arr = np.asarray(df_vals, dtype=float).reshape(-1)
        rates = np.array(
            [
                zero_rate_from_df(df, t, self.compounding)
                for df, t in zip(df_arr, xq_arr.reshape(-1))
            ]
        ).reshape(xq_arr.shape)
        if xq_arr.ndim == 0:
            return rates[0]
        return rates

    def _interp_df(self, xq: np.ndarray) -> np.ndarray:
        method = self.interp_method
        if method == "LOG_LINEAR":
            log_df = np.log(self.df_nodes)
            log_vals = np.interp(xq, self.x, log_df)
            return np.exp(log_vals)
        if method == "MONOTONE_CONVEX_SPLINE":
            if self._spline is None:
                raise ValueError("Spline interpolator is not initialized.")
            return self._spline.df(xq)

        zero_vals = self._interp_zero(xq)
        return np.array(
            [discount_factor(r, t, self.compounding) for r, t in zip(zero_vals, xq)]
        )

    def _interp_zero(self, xq: np.ndarray) -> np.ndarray:
        method = self.interp_method
        if method == "LINEAR":
            return np.interp(xq, self.x, self.zero_nodes)
        if method == "CUBIC_SPLINE":
            if self._spline is None:
                raise ValueError("Spline interpolator is not initialized.")
            return self._spline(xq)
        raise ValueError(f"Unsupported interpolation method: {method!r}")

    def _extrapolate(self, xq: np.ndarray, *, side: str) -> np.ndarray:
        method = self.extrap_left if side == "left" else self.extrap_right
        if method == "FLAT_FWD":
            return _flat_forward_extrapolate(self.x, self.df_nodes, xq, side=side)
        if method == "FLAT_ZERO":
            idx = 0 if side == "left" else -1
            z = self.zero_nodes[idx]
            return discount_factor(z, xq, self.compounding)
        if method == "LINEAR":
            zeros = _linear_zero_extrapolate(self.x, self.zero_nodes, xq, side=side)
            return discount_factor(zeros, xq, self.compounding)
        raise ValueError(f"Unsupported extrapolation method: {method!r}")
