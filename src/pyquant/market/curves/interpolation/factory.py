"""
Factory helpers for creating interpolators from configuration.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Type, Union

from .base import Interpolator, InterpolationError
from .cubic import NaturalCubicSplineInterpolator
from .linear import LinearInterpolator
from .log_linear import LogLinearInterpolator


Number = Union[int, float]
InterpolatorClass = Type[Interpolator]


_REGISTERED_INTERPOLATORS: Mapping[str, InterpolatorClass] = {
    "linear": LinearInterpolator,
    "log-linear": LogLinearInterpolator,
    "cubic": NaturalCubicSplineInterpolator,
    "natural_cubic": NaturalCubicSplineInterpolator,
}


def get_interpolator_class(kind: str) -> InterpolatorClass:
    """Return the interpolator class mapped to `kind`."""

    key = kind.lower()
    interpolator_cls = _REGISTERED_INTERPOLATORS.get(key)
    if interpolator_cls is None:
        known = ", ".join(sorted(_REGISTERED_INTERPOLATORS))
        raise InterpolationError(f"unknown interpolator '{kind}'. Known types: {known}")
    return interpolator_cls


def create_interpolator(
    kind: str,
    xs: Iterable[Number],
    ys: Iterable[Number],
    *,
    extrapolate: bool = False,
) -> Interpolator:
    """
    Factory function returning an interpolator instance for the given kind.
    """

    interpolator_cls = get_interpolator_class(kind)
    return interpolator_cls(xs, ys, extrapolate=extrapolate)
