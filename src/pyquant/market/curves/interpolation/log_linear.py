"""
Log-linear interpolation (linear in log-space).
"""

from __future__ import annotations

import math
from typing import Iterable, Union

import numpy as np

from .base import InterpolationError, Interpolator


Number = Union[int, float]


class LogLinearInterpolator(Interpolator):
    """Linear interpolation applied to logarithms of y-values."""

    def __init__(self, xs: Iterable[Number], ys: Iterable[Number], *, extrapolate: bool = False) -> None:
        super().__init__(xs, ys, extrapolate=extrapolate)
        if np.any(self._ys <= 0.0):
            raise InterpolationError("log-linear interpolation requires positive y values")

    def _interpolate_scalar(self, x: float) -> float:
        xs = self._xs
        ys = self._ys
        idx = self._locate_segment(x)

        x_left = float(xs[idx])
        y_left = float(ys[idx])

        if x == x_left:
            return y_left

        x_right = float(xs[idx + 1])
        y_right = float(ys[idx + 1])

        log_left = math.log(y_left)
        log_right = math.log(y_right)

        if x_right == x_left:
            return y_left

        weight = (x - x_left) / (x_right - x_left)
        log_value = log_left + weight * (log_right - log_left)
        return math.exp(log_value)

    def _interpolate_array(self, xs: np.ndarray) -> np.ndarray:
        grid = self._xs
        values = self._ys
        indices = np.clip(np.searchsorted(grid, xs, side="right") - 1, 0, grid.size - 2)
        x_left = grid[indices]
        x_right = grid[indices + 1]
        y_left = values[indices]
        y_right = values[indices + 1]

        exact_left = np.isclose(xs, x_left)
        denom = x_right - x_left
        with np.errstate(divide="ignore", invalid="ignore"):
            weights = np.where(denom != 0.0, (xs - x_left) / denom, 0.0)

        log_left = np.log(y_left)
        log_right = np.log(y_right)
        log_values = log_left + weights * (log_right - log_left)
        result = np.exp(log_values)
        result = np.where(exact_left, y_left, result)
        return result
