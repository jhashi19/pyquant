from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from app.engine.math.interpolation import ArrayLike, CurveInterpolator


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
        interp_method: str = "LOG_LINEAR",
        extrap_left: str = "FLAT_FWD",
        extrap_right: str = "FLAT_FWD",
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
        )
        return cls(interpolator=interpolator, curve_id=curve_id, ccy=ccy)

    def df(self, xq: ArrayLike) -> ArrayLike:
        return self.interpolator.df(xq)

    def zero_rate(self, xq: ArrayLike) -> ArrayLike:
        return self.interpolator.zero_rate(xq)

    def value(self, xq: ArrayLike) -> ArrayLike:
        return self.interpolator.value(xq)
