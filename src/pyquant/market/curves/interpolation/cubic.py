"""
Natural cubic spline interpolation.
"""

from __future__ import annotations

from typing import Iterable, Union

import numpy as np
from scipy.linalg import solve_tridiagonal

from .base import InterpolationError, Interpolator


Number = Union[int, float]


class NaturalCubicSplineInterpolator(Interpolator):
    """
    Natural cubic spline where second derivatives vanish at both ends.
    """

    def __init__(self, xs: Iterable[Number], ys: Iterable[Number], *, extrapolate: bool = False) -> None:
        super().__init__(xs, ys, extrapolate=extrapolate)
        self._prepare_coefficients()

    def _prepare_coefficients(self) -> None:
        xs = self._xs
        ys = self._ys
        n = xs.size

        if n < 2:
            raise InterpolationError("cubic spline requires at least two points")
        if n == 2:
            # fall back to linear behaviour with zero curvature
            self._a = ys.copy()
            slope = (ys[1] - ys[0]) / (xs[1] - xs[0])
            self._b = np.array([slope], dtype=np.float64)
            self._c = np.zeros(2, dtype=np.float64)
            self._d = np.zeros(1, dtype=np.float64)
            return

        h = np.diff(xs)
        if np.any(h <= 0.0):
            raise InterpolationError("x data must be strictly increasing")

        # Construct vectors for the tridiagonal system Ac = rhs
        rhs = np.zeros(n, dtype=np.float64)
        slopes = (ys[1:] - ys[:-1]) / h
        rhs[1:-1] = 3.0 * (slopes[1:] - slopes[:-1])

        lower = np.zeros(n - 1, dtype=np.float64)
        diag = np.ones(n, dtype=np.float64)
        upper = np.zeros(n - 1, dtype=np.float64)

        for i in range(1, n - 1):
            lower[i - 1] = h[i - 1]
            diag[i] = 2.0 * (h[i - 1] + h[i])
            upper[i] = h[i]

        c = solve_tridiagonal(lower, diag, upper, rhs)

        a = ys.copy()
        b = np.empty(n - 1, dtype=np.float64)
        d = np.empty(n - 1, dtype=np.float64)

        for i in range(n - 1):
            b[i] = slopes[i] - (h[i] * (2.0 * c[i] + c[i + 1]) / 3.0)
            d[i] = (c[i + 1] - c[i]) / (3.0 * h[i])

        self._a = a
        self._b = b
        self._c = c
        self._d = d

    def _interpolate_scalar(self, x: float) -> float:
        xs = self._xs
        idx = self._locate_segment(x)
        x_left = float(xs[idx])
        dx = x - x_left

        a = float(self._a[idx])
        b = float(self._b[idx])
        c = float(self._c[idx])
        d = float(self._d[idx])
        return ((d * dx + c) * dx + b) * dx + a

    def _interpolate_array(self, xs: np.ndarray) -> np.ndarray:
        grid = self._xs
        idx = np.clip(np.searchsorted(grid, xs, side="right") - 1, 0, grid.size - 2)
        dx = xs - grid[idx]

        a = self._a[idx]
        b = self._b[idx]
        c = self._c[idx]
        d = self._d[idx]

        return ((d * dx + c) * dx + b) * dx + a
