import math

import numpy as np
import pytest

from src.pyquant.market.curves.interpolation import (
    InterpolationError,
    LinearInterpolator,
    LogLinearInterpolator,
    NaturalCubicSplineInterpolator,
    create_interpolator,
)


def test_linear_interpolator_matches_nodes() -> None:
    interpolator = LinearInterpolator([0.0, 1.0, 2.0], [10.0, 20.0, 30.0])
    assert interpolator(0.0) == pytest.approx(10.0)
    assert interpolator(1.0) == pytest.approx(20.0)
    assert interpolator(2.0) == pytest.approx(30.0)


def test_linear_interpolator_intermediate_value() -> None:
    interpolator = LinearInterpolator([0.0, 2.0], [0.0, 10.0])
    assert interpolator(1.0) == pytest.approx(5.0)


def test_linear_interpolator_numpy_array_input() -> None:
    interpolator = LinearInterpolator([0.0, 1.0, 2.0], [0.0, 1.0, 2.0])
    xs = np.array([0.0, 0.5, 1.5, 2.0])
    expected = np.array([0.0, 0.5, 1.5, 2.0])
    result = np.array(interpolator(xs))
    assert result == pytest.approx(expected)


def test_linear_interpolator_extrapolation_disabled() -> None:
    interpolator = LinearInterpolator([0.0, 1.0], [0.0, 1.0])
    with pytest.raises(InterpolationError):
        _ = interpolator(-0.1)


def test_linear_interpolator_extrapolation_enabled() -> None:
    interpolator = LinearInterpolator([0.0, 1.0], [0.0, 1.0], extrapolate=True)
    assert interpolator(-0.5) == pytest.approx(-0.5)
    assert interpolator(1.5) == pytest.approx(1.5)


def test_log_linear_requires_positive_values() -> None:
    with pytest.raises(InterpolationError):
        LogLinearInterpolator([0.0, 1.0], [1.0, 0.0])


def test_log_linear_interpolation_basic() -> None:
    interpolator = LogLinearInterpolator([0.0, 1.0], [1.0, math.e])
    assert interpolator(0.0) == pytest.approx(1.0)
    assert interpolator(1.0) == pytest.approx(math.e)
    assert interpolator(0.5) == pytest.approx(math.sqrt(math.e))


def test_log_linear_numpy_array_input() -> None:
    interpolator = LogLinearInterpolator([0.0, 1.0], [1.0, math.e])
    xs = np.array([0.0, 0.25, 0.5, 1.0])
    expected = np.array(
        [1.0, math.exp(0.25), math.exp(0.5), math.e]
    )
    result = np.array(interpolator(xs))
    assert result == pytest.approx(expected)


def test_natural_cubic_spline_matches_cubic_polynomial() -> None:
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [x ** 3 - 2 * x ** 2 + x for x in xs]
    interpolator = NaturalCubicSplineInterpolator(xs, ys)
    for x in [0.5, 1.2, 2.5]:
        expected = x ** 3 - 2 * x ** 2 + x
        assert interpolator(x) == pytest.approx(expected, rel=1e-5, abs=1e-8)


def test_factory_creates_requested_interpolator() -> None:
    interpolator = create_interpolator("linear", [0.0, 1.0], [0.0, 1.0])
    assert isinstance(interpolator, LinearInterpolator)

    interpolator = create_interpolator("log-linear", [0.0, 1.0], [1.0, math.e])
    assert isinstance(interpolator, LogLinearInterpolator)

    interpolator = create_interpolator("cubic", [0.0, 1.0, 2.0], [0.0, 1.0, 0.0])
    assert isinstance(interpolator, NaturalCubicSplineInterpolator)

    with pytest.raises(InterpolationError):
        create_interpolator("unknown", [0.0, 1.0], [0.0, 1.0])
