"""
Common helpers and base classes for curve interpolation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple, Union, overload

import numpy as np


Number = Union[int, float, np.floating]
ArrayLike = Union[Sequence[Number], np.ndarray]


class InterpolationError(ValueError):
    """Raised when interpolation cannot be performed."""


@dataclass(frozen=True)
class CurvePoints:
    """Container for x-y curve points ensured to be strictly increasing."""

    xs: np.ndarray
    ys: np.ndarray

    def __post_init__(self) -> None:
        self._validate_points()

    def _validate_points(self) -> None:
        if self.xs.shape != self.ys.shape:
            raise InterpolationError("x and y data length mismatch")
        if self.xs.ndim != 1:
            raise InterpolationError("x and y data must be one-dimensional sequences")
        if self.xs.size < 2:
            raise InterpolationError("at least two data points are required")
        if np.any(np.diff(self.xs) <= 0.0):
            raise InterpolationError("x data must be strictly increasing")


def build_curve_points(xs: Iterable[Number], ys: Iterable[Number]) -> CurvePoints:
    """
    Validate input sequences and return CurvePoints with float coercion.

    Raises:
        InterpolationError: if validation fails.
    """

    xs_arr = np.asarray(tuple(xs), dtype=np.float64)
    ys_arr = np.asarray(tuple(ys), dtype=np.float64)
    return CurvePoints(xs=xs_arr, ys=ys_arr)


class Interpolator(ABC):
    """
    Abstract base interpolator that provides scalar/sequence dispatch
    and extrapolation handling.
    """

    def __init__(self, xs: Iterable[Number], ys: Iterable[Number], *, extrapolate: bool = False) -> None:
        points = build_curve_points(xs, ys)
        self._xs = points.xs
        self._ys = points.ys
        self._extrapolate = extrapolate

    @property
    def xs(self) -> np.ndarray:
        return self._xs

    @property
    def ys(self) -> np.ndarray:
        return self._ys

    @property
    def extrapolate(self) -> bool:
        return self._extrapolate

    def __call__(self, x: Union[Number, ArrayLike]) -> Union[float, Tuple[float, ...]]:
        return self.interpolate(x)

    @overload
    def interpolate(self, x: Number) -> float: ...

    @overload
    def interpolate(self, x: Sequence[Number]) -> Tuple[float, ...]: ...

    def interpolate(self, x: Union[Number, ArrayLike]) -> Union[float, Tuple[float, ...]]:
        """Dispatch interpolation for scalar or sequences."""

        if np.isscalar(x):
            return self._interpolate_scalar(float(x))

        arr = np.asarray(x, dtype=np.float64)
        if arr.ndim != 1:
            raise InterpolationError("only one-dimensional sequences are supported for interpolation")

        return tuple(self._interpolate_array(arr))

    def _interpolate_array(self, xs: np.ndarray) -> np.ndarray:
        """Vectorized interpolation for numpy arrays."""

        return np.fromiter((self._interpolate_scalar(float(value)) for value in xs), dtype=np.float64)

    def _locate_segment(self, x: float) -> int:
        """
        Locate index j such that x is between xs[j] and xs[j+1].

        Returns:
            int: index of the left point of the segment.
        """

        xs = self._xs
        if x < float(xs[0]):
            if not self._extrapolate:
                raise InterpolationError(
                    f"value {x} lies below the interpolation domain [{xs[0]}, {xs[-1]}]"
                )
            return 0
        if x > float(xs[-1]):
            if not self._extrapolate:
                raise InterpolationError(
                    f"value {x} lies above the interpolation domain [{xs[0]}, {xs[-1]}]"
                )
            return len(xs) - 2
        idx = int(np.searchsorted(xs, x, side="right") - 1)
        if idx < 0:
            idx = 0
        if idx >= xs.size - 1:
            idx = xs.size - 2
        return idx

    @abstractmethod
    def _interpolate_scalar(self, x: float) -> float:
        """Interpolate a single value."""
        raise NotImplementedError
