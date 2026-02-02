from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Union

import numpy as np

from .rate_conversion import discount_factor, zero_rate_from_df



ArrayLike = Union[float, Iterable[float], np.ndarray]

EPS = 1e-16
ETA_EPS = 1e-10
DF_INC_EPS = 1e-14
DF_AT_ZERO_EPS = 1e-10


class InterpMethod(Enum):
    LOG_LINEAR = "LOG_LINEAR"
    LINEAR = "LINEAR"
    CUBIC_SPLINE = "CUBIC_SPLINE"
    MONOTONE_CONVEX = "MONOTONE_CONVEX"


class ExtrapMethod(Enum):
    FLAT_FWD = "FLAT_FWD"
    FLAT_ZERO = "FLAT_ZERO"
    LINEAR = "LINEAR"


def normalize_interp_method(method: InterpMethod | str) -> InterpMethod:
    if isinstance(method, InterpMethod):
        return method
    key = str(method).strip().upper()
    match key:
        case "LOG_LINEAR" | "LOG_LINEAR_DF":
            return InterpMethod.LOG_LINEAR
        case "LINEAR" | "LINEAR_ZERO":
            return InterpMethod.LINEAR
        case "CUBIC_SPLINE" | "CUBIC_SPLINE_ZERO":
            return InterpMethod.CUBIC_SPLINE
        case "MONOTONE_CONVEX" | "MONOTONE_CONVEX_SPLINE" | "MONOTONE_CONVEX_SPLINE_ZERO":
            return InterpMethod.MONOTONE_CONVEX
        case _:
            raise ValueError(f"Unsupported interpolation method: {method!r}")


def normalize_extrap_method(method: ExtrapMethod | str) -> ExtrapMethod:
    if isinstance(method, ExtrapMethod):
        return method
    key = str(method).strip().upper()
    match key:
        case "FLAT_FWD":
            return ExtrapMethod.FLAT_FWD
        case "FLAT_ZERO":
            return ExtrapMethod.FLAT_ZERO
        case "LINEAR" | "LINEAR_ZERO":
            return ExtrapMethod.LINEAR
        case _:
            raise ValueError(f"Unsupported extrapolation method: {method!r}")


def _ensure_exactly_one(df_nodes: Optional[Iterable[float]], zero_nodes: Optional[Iterable[float]]) -> None:
    if (df_nodes is None) == (zero_nodes is None):
        raise ValueError("Provide exactly one of df_nodes or zero_nodes.")


def _as_1d_array(values: Iterable[float], name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be 1D.")
    return arr


def _validate_non_negative_x(x: np.ndarray) -> None:
    if np.any(x < 0.0):
        raise ValueError("x must be non-negative.")


def _sort_by_x(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(x)
    return x[order], y[order]


def validate_curve_inputs(
    x: Iterable[float],
    *,
    df_nodes: Optional[Iterable[float]] = None,
    zero_nodes: Optional[Iterable[float]] = None,
    compounding: str = "CONTINUOUS",
    allow_negative_rates: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    _ensure_exactly_one(df_nodes, zero_nodes)
    x_arr = _as_1d_array(x, "x")
    _validate_non_negative_x(x_arr)

    if df_nodes is not None:
        df_arr = _as_1d_array(df_nodes, "df_nodes")
        x_arr, df_arr = _sort_by_x(x_arr, df_arr)
        _validate_strictly_increasing(x_arr, "x")
        _validate_df(df_arr, allow_negative_rates=allow_negative_rates)
        zero_arr = zero_rate_from_df(df_arr, x_arr, compounding)
        return x_arr, df_arr, zero_arr, "DF"

    zero_arr = _as_1d_array(zero_nodes, "zero_nodes")
    x_arr, zero_arr = _sort_by_x(x_arr, zero_arr)
    _validate_strictly_increasing(x_arr, "x")
    df_arr = discount_factor(zero_arr, x_arr, compounding)
    _validate_df(df_arr, allow_negative_rates=allow_negative_rates)
    return x_arr, df_arr, zero_arr, "ZERO"


def _validate_strictly_increasing(x: np.ndarray, name: str) -> None:
    if np.any(~np.isfinite(x)):
        raise ValueError(f"{name} contains NaN/inf.")
    if np.any(np.diff(x) <= 0.0):
        raise ValueError(f"{name} must be strictly increasing.")


def _validate_df(df: np.ndarray, allow_negative_rates: bool) -> None:
    if np.any(~np.isfinite(df)):
        raise ValueError("df contains NaN/inf.")
    if np.any(df <= 0.0):
        raise ValueError("df must be > 0 for all nodes (log defined).")
    if not allow_negative_rates:
        if np.any(np.diff(df) > DF_INC_EPS):
            raise ValueError(
                "df is not non-increasing. "
                "If negative rates are possible, set allow_negative_rates=True."
            )


@dataclass(frozen=True)
class MonotoneConvexSpline:
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

    class _SegmentCase:
        BASE_QUADRATIC = 0
        FLAT_LEFT = 1
        FLAT_RIGHT = 2
        TWO_SIDED = 3

    @staticmethod
    def _discrete_forward_from_df(t: np.ndarray, df: np.ndarray) -> np.ndarray:
        dt = np.diff(t)
        lnP = np.log(df)
        return -(lnP[1:] - lnP[:-1]) / dt

    @staticmethod
    def _node_forward_from_discrete_forward(t: np.ndarray, f_d: np.ndarray) -> np.ndarray:
        n = len(t) - 1
        if len(f_d) != n:
            raise ValueError("f_d length mismatch.")

        f = np.empty(n + 1, dtype=float)
        if n == 1:
            f[0] = f_d[0]
            f[1] = f_d[0]
            return f

        dt_prev = t[1:n] - t[0 : n - 1]
        dt_next = t[2 : n + 1] - t[1:n]
        denom = t[2 : n + 1] - t[0 : n - 1]
        w1 = dt_prev / denom
        w2 = dt_next / denom
        f[1:n] = w1 * f_d[1:] + w2 * f_d[:-1]

        f[0] = f_d[0] - 0.5 * (f[1] - f_d[0])
        f[n] = f_d[n - 1] - 0.5 * (f[n - 1] - f_d[n - 1])
        return f

    @staticmethod
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
        if n > 1:
            caps = cap_factor * np.minimum(f_d[:-1], f_d[1:])
            out[1:n] = np.clip(out[1:n], floor, caps)
        return out

    @staticmethod
    def _baseline_quadratic_coeffs(
        g0: np.ndarray, g1: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        g0_arr = np.asarray(g0, dtype=float)
        g1_arr = np.asarray(g1, dtype=float)
        K = g0_arr
        L = -4.0 * g0_arr - 2.0 * g1_arr
        M = 3.0 * (g0_arr + g1_arr)
        return K, L, M

    @classmethod
    def _compute_segment_params_vectorized(
        cls, g0: np.ndarray, g1: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        g0_arr = np.asarray(g0, dtype=float)
        g1_arr = np.asarray(g1, dtype=float)
        if g0_arr.shape != g1_arr.shape:
            raise ValueError("g0 and g1 must have the same shape.")

        n = g0_arr.size
        case = np.full(n, cls._SegmentCase.BASE_QUADRATIC, dtype=int)
        eta = np.zeros(n, dtype=float)
        A = np.zeros(n, dtype=float)

        zero_mask = (np.abs(g0_arr) < EPS) & (np.abs(g1_arr) < EPS)
        same_sign = (g0_arr * g1_arr > 0.0) & ~zero_mask
        if np.any(same_sign):
            s = g0_arr + g1_arr
            eta_ss = np.where(np.abs(s) < EPS, 0.5, g1_arr / s)
            eta_ss = np.clip(eta_ss, ETA_EPS, 1.0 - ETA_EPS)
            A_ss = -0.5 * (eta_ss * g0_arr + (1.0 - eta_ss) * g1_arr)
            case[same_sign] = cls._SegmentCase.TWO_SIDED
            eta[same_sign] = eta_ss[same_sign]
            A[same_sign] = A_ss[same_sign]

        remaining = ~same_sign & ~zero_mask
        if np.any(remaining):
            gp0 = -4.0 * g0_arr - 2.0 * g1_arr
            gp1 = 2.0 * g0_arr + 4.0 * g1_arr
            direction = g1_arr - g0_arr

            monotone = remaining & (np.abs(direction) < EPS)
            pos = remaining & (direction > 0.0)
            neg = remaining & (direction < 0.0)
            monotone |= pos & (gp0 >= -EPS) & (gp1 >= -EPS)
            monotone |= neg & (gp0 <= EPS) & (gp1 <= EPS)

            non_mono = remaining & ~monotone
            if np.any(non_mono):
                denom = g1_arr - g0_arr
                flat_left = non_mono & (
                    ((direction > 0.0) & (gp0 < 0.0)) | ((direction < 0.0) & (gp0 > 0.0))
                )
                flat_right = non_mono & ~flat_left

                if np.any(flat_left):
                    eta_left = 1.0 + 3.0 * g0_arr / denom
                    eta_left = np.clip(eta_left, ETA_EPS, 1.0 - ETA_EPS)
                    case[flat_left] = cls._SegmentCase.FLAT_LEFT
                    eta[flat_left] = eta_left[flat_left]

                if np.any(flat_right):
                    eta_right = 3.0 * g1_arr / denom
                    eta_right = np.clip(eta_right, ETA_EPS, 1.0 - ETA_EPS)
                    case[flat_right] = cls._SegmentCase.FLAT_RIGHT
                    eta[flat_right] = eta_right[flat_right]

        return case, eta, A

    @classmethod
    def from_discount_factors(
        cls,
        x: np.ndarray,
        df_nodes: np.ndarray,
        *,
        allow_negative_rates: bool = True,
        cap_factor: float = 2.0,
    ) -> "MonotoneConvexSpline":
        t = np.asarray(x, dtype=float)
        df = np.asarray(df_nodes, dtype=float)

        if t.size < 2:
            raise ValueError("Monotone convex interpolation requires at least two nodes.")

        if t[0] > 0.0:
            t = np.concatenate(([0.0], t))
            df = np.concatenate(([1.0], df))

        dt = np.diff(t)
        f_d = cls._discrete_forward_from_df(t, df)
        f = cls._node_forward_from_discrete_forward(t, f_d)
        f = cls._collar_node_forwards(
            f, f_d, allow_negative_rates=allow_negative_rates, cap_factor=cap_factor
        )

        # Vectorized calculation of g0, g1
        # f_d has size n (intervals), f has size n+1 (nodes)
        # interval i (0 to n-1) uses f[i] and f[i+1]
        g0 = f[:-1] - f_d
        g1 = f[1:] - f_d
        
        case, eta, A = cls._compute_segment_params_vectorized(g0, g1)

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

        mask = case == self._SegmentCase.BASE_QUADRATIC
        if np.any(mask):
            K, L, M = self._baseline_quadratic_coeffs(g0[mask], g1[mask])
            xm = x[mask]
            I_g[mask] = K * xm + 0.5 * L * xm * xm + (1.0 / 3.0) * M * xm * xm * xm

        mask = case == self._SegmentCase.FLAT_LEFT
        if np.any(mask):
            xm = x[mask]
            et = eta[mask]
            g0m = g0[mask]
            g1m = g1[mask]
            out = np.empty_like(xm)
            left = xm <= et
            if np.any(left):
                out[left] = g0m[left] * xm[left]
            if np.any(~left):
                dx = xm[~left] - et[~left]
                out[~left] = g0m[~left] * xm[~left] + (
                    (g1m[~left] - g0m[~left]) * (dx ** 3) / (3.0 * (1.0 - et[~left]) ** 2)
                )
            I_g[mask] = out

        mask = case == self._SegmentCase.FLAT_RIGHT
        if np.any(mask):
            xm = x[mask]
            et = eta[mask]
            g0m = g0[mask]
            g1m = g1[mask]
            out = np.empty_like(xm)
            left = xm <= et
            if np.any(left):
                term = (et[left] ** 3 - (et[left] - xm[left]) ** 3) / 3.0
                out[left] = g1m[left] * xm[left] + (
                    (g0m[left] - g1m[left]) * term / (et[left] ** 2)
                )
            if np.any(~left):
                I_eta = (2.0 / 3.0) * g1m[~left] * et[~left] + (1.0 / 3.0) * g0m[
                    ~left
                ] * et[~left]
                out[~left] = I_eta + g1m[~left] * (xm[~left] - et[~left])
            I_g[mask] = out

        mask = case == self._SegmentCase.TWO_SIDED
        if np.any(mask):
            xm = x[mask]
            et = eta[mask]
            g0m = g0[mask]
            g1m = g1[mask]
            Am = A[mask]
            out = np.empty_like(xm)
            left = xm <= et
            if np.any(left):
                term = (et[left] ** 3 - (et[left] - xm[left]) ** 3) / 3.0
                out[left] = Am[left] * xm[left] + (
                    (g0m[left] - Am[left]) * term / (et[left] ** 2)
                )
            if np.any(~left):
                I_eta = et[~left] * ((2.0 / 3.0) * Am[~left] + (1.0 / 3.0) * g0m[~left])
                dx = xm[~left] - et[~left]
                out[~left] = (
                    I_eta
                    + Am[~left] * dx
                    + (g1m[~left] - Am[~left]) * (dx ** 3) / (3.0 * (1.0 - et[~left]) ** 2)
                )
            I_g[mask] = out

        integral = fd * (x_flat - t_left) + dt * I_g
        df_left = self.df_nodes[idx]
        df_vals = df_left * np.exp(-integral)

        if scalar:
            return df_vals[0]
        return df_vals.reshape(xq_arr.shape)
