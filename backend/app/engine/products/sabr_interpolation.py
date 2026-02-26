from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import warnings
from typing import Iterable, Optional, Protocol, Sequence

import numpy as np

from app.engine.market.sabr import SabrParams, SabrVolType, sabr_implied_vol
from app.engine.market.yield_curve import YieldCurve
from app.engine.math.daycount import year_fraction
from app.engine.math.rate_conversion import Compounding, forward_rate_from_dfs
from app.engine.products.models.schedule_models import ModelParamRow, SabrInterpolationSpec
from app.engine.products.schedule_utils import add_tenor, parse_tenor

_VOL_FLOOR = 1e-10
_TRANSFORM_EPS = 1e-12


class _CapFloorPointLike(Protocol):
    x_years: float
    expiry_date: Optional[date]
    expiry_tenor: Optional[str]
    vol_daycount: str
    smile_type: str
    strike_rate: Optional[float]
    sigma: float


class _SwaptionPointLike(Protocol):
    x_years: float
    swap_tenor: str
    smile_type: str
    moneyness: Optional[float]
    sigma: float


def _round_years(x: float) -> float:
    return round(float(x), 12)


def _scope_rank(scope: str) -> int:
    key = scope.upper()
    if key == "GLOBAL":
        return 0
    if key == "CCY":
        return 1
    if key == "INDEX":
        return 2
    return -1


def _normalize_forward_rate_index_key(index_key: Optional[str]) -> str:
    if index_key is None:
        return ""
    return index_key.strip().upper()


def _linear_interp_flat(*, xq: float, x: np.ndarray, y: np.ndarray) -> float:
    if x.size == 0:
        raise ValueError("Interpolation node array is empty.")
    if xq <= float(x[0]):
        return float(y[0])
    if xq >= float(x[-1]):
        return float(y[-1])
    return float(np.interp(xq, x, y))


def _warn_and_clip_open_interval(
    *,
    value: np.ndarray,
    lower: float,
    upper: float,
    tol: float,
    name: str,
) -> np.ndarray:
    if np.any(value <= lower) or np.any(value >= upper):
        raise ValueError(f"{name} must stay in ({lower}, {upper}) for transformed interpolation.")
    clipped = np.clip(value, lower + _TRANSFORM_EPS, upper - _TRANSFORM_EPS)
    near = np.minimum(clipped - lower, upper - clipped)
    if np.any(near < tol):
        warnings.warn(
            f"{name} is near transform boundary; clipped with eps={_TRANSFORM_EPS:g}.",
            RuntimeWarning,
            stacklevel=2,
        )
    return clipped


def _warn_and_clip_positive(
    *,
    value: np.ndarray,
    tol: float,
    name: str,
) -> np.ndarray:
    if np.any(value <= 0.0):
        raise ValueError(f"{name} must be positive for log-space interpolation.")
    clipped = np.maximum(value, _TRANSFORM_EPS)
    if np.any(clipped < tol):
        warnings.warn(
            f"{name} is near zero; clipped with eps={_TRANSFORM_EPS:g}.",
            RuntimeWarning,
            stacklevel=2,
        )
    return clipped


def _expiry_years_from_param(
    row: ModelParamRow,
    *,
    as_of: date,
    daycount: str,
) -> Optional[float]:
    if row.x_years is not None:
        return float(row.x_years)
    if row.expiry_date is not None:
        return float(year_fraction(as_of, row.expiry_date, daycount))
    if row.expiry_tenor is not None:
        maturity = add_tenor(as_of, parse_tenor(row.expiry_tenor))
        return float(year_fraction(as_of, maturity, daycount))
    return None


def _swap_years_from_param(row: ModelParamRow) -> Optional[float]:
    if row.swap_tenor is None:
        return None
    tenor = parse_tenor(row.swap_tenor)
    return float(tenor.months / 12.0 + tenor.days / 365.0)


def _solve_alpha_from_atm_vol_1d(
    *,
    atm_sigma: float,
    forward: float,
    expiry: float,
    beta: float,
    rho: float,
    nu: float,
    shift: float,
    vol_type: SabrVolType,
    newton_tol: float,
    newton_max_iter: int,
) -> float:
    expiry_eff = float(max(expiry, _VOL_FLOOR))
    f_shifted = float(max(forward + shift, _TRANSFORM_EPS))
    alpha = float(max(atm_sigma * np.power(f_shifted, 1.0 - beta), _VOL_FLOOR))
    strike = float(forward)

    def _sigma(alpha_val: float) -> float:
        params = SabrParams(
            alpha=float(max(alpha_val, _VOL_FLOOR)),
            beta=float(beta),
            rho=float(rho),
            nu=float(nu),
            shift=float(shift),
        )
        return float(
            np.asarray(
                sabr_implied_vol(
                    np.asarray([strike], dtype=float),
                    forward,
                    expiry_eff,
                    params,
                    vol_type=vol_type,
                ),
                dtype=float,
            )[0]
        )

    def _f(alpha_val: float) -> float:
        return _sigma(alpha_val) - atm_sigma

    for _ in range(max(newton_max_iter, 1)):
        fx = _f(alpha)
        if abs(fx) <= newton_tol:
            return float(max(alpha, _VOL_FLOOR))
        h = max(alpha * 1e-5, 1e-8)
        lo_probe = max(alpha - h, _VOL_FLOOR)
        hi_probe = alpha + h
        denom = hi_probe - lo_probe
        if denom <= 0.0:
            break
        dfx = (_f(hi_probe) - _f(lo_probe)) / denom
        if not np.isfinite(dfx) or abs(dfx) < 1e-14:
            break
        alpha_new = alpha - fx / dfx
        if not np.isfinite(alpha_new) or alpha_new <= 0.0:
            break
        if abs(alpha_new - alpha) <= newton_tol * max(1.0, abs(alpha)):
            return float(max(alpha_new, _VOL_FLOOR))
        alpha = float(alpha_new)

    lower = _VOL_FLOOR
    f_lower = _f(lower)
    if abs(f_lower) <= newton_tol:
        return lower
    upper = max(alpha, 1e-4)
    f_upper = _f(upper)
    for _ in range(60):
        if f_lower * f_upper <= 0.0:
            break
        upper *= 2.0
        f_upper = _f(upper)
    if f_lower * f_upper > 0.0:
        warnings.warn(
            "Failed to bracket alpha root from ATM condition; using last Newton iterate.",
            RuntimeWarning,
            stacklevel=2,
        )
        return float(max(alpha, _VOL_FLOOR))

    for _ in range(max(2 * newton_max_iter, 20)):
        mid = 0.5 * (lower + upper)
        f_mid = _f(mid)
        if abs(f_mid) <= newton_tol or (upper - lower) <= newton_tol * max(1.0, abs(mid)):
            return float(max(mid, _VOL_FLOOR))
        if f_lower * f_mid <= 0.0:
            upper = mid
        else:
            lower = mid
            f_lower = f_mid
    return float(max(0.5 * (lower + upper), _VOL_FLOOR))


def _build_capfloor_param_curve_by_expiry(
    rows: Iterable[ModelParamRow],
    *,
    param_name: str,
    as_of: date,
    forward_daycount: str,
) -> tuple[np.ndarray, np.ndarray, Optional[float]]:
    node_best: dict[float, tuple[int, float]] = {}
    base_best: Optional[tuple[int, float]] = None
    target = param_name.lower()

    for row in rows:
        if row.param_name.lower() != target:
            continue
        if row.strike_rate is not None or row.moneyness is not None or row.swap_tenor is not None:
            continue
        scope_rank = _scope_rank(row.scope)
        value = float(row.param_val)
        expiry = _expiry_years_from_param(row, as_of=as_of, daycount=forward_daycount)
        if expiry is None:
            if base_best is None or scope_rank >= base_best[0]:
                base_best = (scope_rank, value)
            continue
        key = _round_years(expiry)
        prev = node_best.get(key)
        if prev is None or scope_rank >= prev[0]:
            node_best[key] = (scope_rank, value)

    if node_best:
        x_sorted = np.asarray(sorted(node_best.keys()), dtype=float)
        y_sorted = np.asarray([node_best[float(k)][1] for k in x_sorted], dtype=float)
    else:
        x_sorted = np.asarray([], dtype=float)
        y_sorted = np.asarray([], dtype=float)

    base = None if base_best is None else float(base_best[1])
    return x_sorted, y_sorted, base


def _capfloor_node_anchor_date(row: _CapFloorPointLike, *, as_of: date) -> Optional[date]:
    if row.expiry_date is not None:
        return row.expiry_date
    if row.expiry_tenor is not None:
        return add_tenor(as_of, parse_tenor(row.expiry_tenor))
    return None


def _resolve_capfloor_node_forward_rate(
    rows: Sequence[_CapFloorPointLike],
    *,
    node_expiry: float,
    as_of: date,
    forward_curve: YieldCurve,
    forward_daycount: str,
    index_daycount: str,
    index_tenor: str,
) -> float:
    if not rows:
        raise ValueError("vol_capfloor rows are required for node forward calculation.")
    anchor = _capfloor_node_anchor_date(rows[0], as_of=as_of)
    tenor = parse_tenor(index_tenor)

    if anchor is not None:
        start_date = anchor
        end_date = add_tenor(start_date, tenor)
        t_start = float(max(year_fraction(as_of, start_date, forward_daycount), 0.0))
        t_end = float(max(year_fraction(as_of, end_date, forward_daycount), 0.0))
        accrual = float(max(year_fraction(start_date, end_date, index_daycount), _VOL_FLOOR))
    else:
        tenor_end = add_tenor(as_of, tenor)
        delta_fwd = float(max(year_fraction(as_of, tenor_end, forward_daycount), _VOL_FLOOR))
        delta_idx = float(max(year_fraction(as_of, tenor_end, index_daycount), _VOL_FLOOR))
        t_start = float(max(node_expiry, 0.0))
        t_end = t_start + delta_fwd
        accrual = delta_idx

    df_start = float(np.asarray(forward_curve.df(t_start)))
    df_end = float(np.asarray(forward_curve.df(t_end)))
    return float(forward_rate_from_dfs(df_start, df_end, accrual, Compounding.SIMPLE))


def _build_capfloor_sabr_node_grid(
    grouped_rows: dict[float, list[_CapFloorPointLike]],
    *,
    as_of: date,
    forward_curve: YieldCurve,
    forward_daycount: str,
    index_daycount: str,
    index_tenor: str,
) -> tuple[np.ndarray, np.ndarray]:
    node_t = np.asarray(sorted(grouped_rows.keys()), dtype=float)
    node_forward = np.empty_like(node_t, dtype=float)
    for i, t in enumerate(node_t):
        node_rows = grouped_rows[float(t)]
        node_forward[i] = _resolve_capfloor_node_forward_rate(
            node_rows,
            node_expiry=float(t),
            as_of=as_of,
            forward_curve=forward_curve,
            forward_daycount=forward_daycount,
            index_daycount=index_daycount,
            index_tenor=index_tenor,
        )
    return node_t, node_forward


def _build_capfloor_atm_total_variance_nodes(
    *,
    node_t: np.ndarray,
    node_forward: np.ndarray,
    grouped_rows: dict[float, list[_CapFloorPointLike]],
) -> tuple[np.ndarray, np.ndarray]:
    if node_t.size == 0:
        raise ValueError("vol_capfloor rows are required for ATM total variance interpolation.")
    total_var = np.empty_like(node_t, dtype=float)
    for i in range(node_t.size):
        t = float(node_t[i])
        rows = grouped_rows[float(t)]
        atm_rows = [r for r in rows if r.smile_type.strip().upper() == "ATM"]
        sigma: Optional[float] = None
        if atm_rows:
            sigma = float(atm_rows[0].sigma)
        else:
            strike_rows = [r for r in rows if r.strike_rate is not None]
            if strike_rows:
                fwd = float(node_forward[i])
                nearest = min(strike_rows, key=lambda r: abs(float(r.strike_rate) - fwd))
                sigma = float(nearest.sigma)
        if sigma is None:
            raise ValueError(f"ATM (or strike-nearest) vol is required at capfloor expiry node t={t}.")
        sigma = max(float(sigma), _VOL_FLOOR)
        total_var[i] = float((sigma * sigma) * max(t, _VOL_FLOOR))
    return node_t, total_var


class CapFloorAtmSabrInterpolator:
    def __init__(
        self,
        rows: Iterable[ModelParamRow],
        *,
        grouped_rows: dict[float, list[_CapFloorPointLike]],
        as_of: date,
        forward_curve: YieldCurve,
        forward_daycount: str,
        index_daycount: str,
        index_tenor: str,
        vol_type: SabrVolType,
        interpolation_spec: SabrInterpolationSpec,
        fallback_shift: Optional[float],
        forward_rate_index_key: Optional[str] = None,
    ) -> None:
        self._vol_type = vol_type
        self._spec = interpolation_spec
        self._alpha_cache_enabled = bool(interpolation_spec.alpha_cache_enabled)
        self._fallback_shift = fallback_shift
        self._forward_rate_index_key = _normalize_forward_rate_index_key(forward_rate_index_key)
        self._boundary_tol = float(max(interpolation_spec.boundary_warn_tol, _TRANSFORM_EPS))
        self._alpha_cache: dict[tuple[str, float, float], float] = {}

        node_t, node_forward = _build_capfloor_sabr_node_grid(
            grouped_rows,
            as_of=as_of,
            forward_curve=forward_curve,
            forward_daycount=forward_daycount,
            index_daycount=index_daycount,
            index_tenor=index_tenor,
        )
        self._atm_t, self._atm_total_var = _build_capfloor_atm_total_variance_nodes(
            node_t=node_t,
            node_forward=node_forward,
            grouped_rows=grouped_rows,
        )

        self._beta_x, self._beta_y, self._beta_base = _build_capfloor_param_curve_by_expiry(
            rows, param_name="beta", as_of=as_of, forward_daycount=forward_daycount
        )
        self._nu_x, self._nu_y, self._nu_base = _build_capfloor_param_curve_by_expiry(
            rows, param_name="nu", as_of=as_of, forward_daycount=forward_daycount
        )
        self._rho_x, self._rho_y, self._rho_base = _build_capfloor_param_curve_by_expiry(
            rows, param_name="rho", as_of=as_of, forward_daycount=forward_daycount
        )
        self._shift_x, self._shift_y, self._shift_base = _build_capfloor_param_curve_by_expiry(
            rows, param_name="shift", as_of=as_of, forward_daycount=forward_daycount
        )

    def _interp_beta(self, *, expiry: float) -> float:
        strategy = self._spec.beta_strategy.strip().upper()
        if strategy == "FIXED":
            if self._spec.beta_fixed_value is None:
                raise ValueError("SABR interpolation spec requires beta_fixed_value for FIXED strategy.")
            beta = float(self._spec.beta_fixed_value)
            if not (0.0 <= beta <= 1.0):
                raise ValueError("beta_fixed_value must be in [0,1].")
            return beta
        if strategy != "INTERPOLATE_LOGIT":
            raise ValueError(f"Unsupported beta_strategy: {self._spec.beta_strategy!r}")
        if self._beta_x.size > 0:
            beta_raw = np.asarray(self._beta_y, dtype=float)
        else:
            if self._beta_base is None:
                raise ValueError("model_param is missing SABR parameter: beta.")
            beta_raw = np.asarray([self._beta_base], dtype=float)
        beta_clip = _warn_and_clip_open_interval(
            value=beta_raw, lower=0.0, upper=1.0, tol=self._boundary_tol, name="beta"
        )
        beta_logit = np.log(beta_clip / (1.0 - beta_clip))
        if self._beta_x.size > 0:
            yq = _linear_interp_flat(xq=float(expiry), x=self._beta_x, y=beta_logit)
        else:
            yq = float(beta_logit[0])
        return float(1.0 / (1.0 + np.exp(-yq)))

    def _interp_nu(self, *, expiry: float) -> float:
        if self._nu_x.size > 0:
            raw = np.asarray(self._nu_y, dtype=float)
        else:
            if self._nu_base is None:
                raise ValueError("model_param is missing SABR parameter: nu.")
            raw = np.asarray([self._nu_base], dtype=float)
        nu_raw = _warn_and_clip_positive(value=raw, tol=self._boundary_tol, name="nu")
        nu_log = np.log(nu_raw)
        if self._nu_x.size > 0:
            yq = _linear_interp_flat(xq=float(expiry), x=self._nu_x, y=nu_log)
        else:
            yq = float(nu_log[0])
        return float(np.exp(yq))

    def _interp_rho(self, *, expiry: float) -> float:
        if self._rho_x.size > 0:
            raw = np.asarray(self._rho_y, dtype=float)
        else:
            if self._rho_base is None:
                raise ValueError("model_param is missing SABR parameter: rho.")
            raw = np.asarray([self._rho_base], dtype=float)
        rho_raw = _warn_and_clip_open_interval(
            value=raw, lower=-1.0, upper=1.0, tol=self._boundary_tol, name="rho"
        )
        rho_t = np.arctanh(rho_raw)
        if self._rho_x.size > 0:
            yq = _linear_interp_flat(xq=float(expiry), x=self._rho_x, y=rho_t)
        else:
            yq = float(rho_t[0])
        return float(np.tanh(yq))

    def _interp_shift(self, *, expiry: float) -> float:
        if self._shift_x.size > 0:
            return _linear_interp_flat(xq=float(expiry), x=self._shift_x, y=self._shift_y)
        if self._shift_base is not None:
            return float(self._shift_base)
        if self._fallback_shift is not None:
            return float(self._fallback_shift)
        return 0.0

    def _interp_atm_sigma(self, *, expiry: float) -> float:
        total_var = _linear_interp_flat(xq=float(expiry), x=self._atm_t, y=self._atm_total_var)
        return float(np.sqrt(max(total_var, _VOL_FLOOR) / max(expiry, _VOL_FLOOR)))

    def resolve(self, *, expiry: float, forward: float) -> SabrParams:
        key = (
            self._forward_rate_index_key,
            _round_years(expiry),
            _round_years(forward),
        )
        beta = self._interp_beta(expiry=expiry)
        nu = self._interp_nu(expiry=expiry)
        rho = self._interp_rho(expiry=expiry)
        shift = self._interp_shift(expiry=expiry)
        alpha = self._alpha_cache.get(key) if self._alpha_cache_enabled else None
        if alpha is None:
            atm_sigma = self._interp_atm_sigma(expiry=expiry)
            alpha = _solve_alpha_from_atm_vol_1d(
                atm_sigma=atm_sigma,
                forward=float(forward),
                expiry=float(expiry),
                beta=float(beta),
                rho=float(rho),
                nu=float(nu),
                shift=float(shift),
                vol_type=self._vol_type,
                newton_tol=float(max(self._spec.newton_tol, _TRANSFORM_EPS)),
                newton_max_iter=int(max(self._spec.newton_max_iter, 1)),
            )
            if self._alpha_cache_enabled:
                self._alpha_cache[key] = float(alpha)
        return SabrParams(
            alpha=float(alpha),
            beta=float(beta),
            rho=float(rho),
            nu=float(nu),
            shift=float(shift),
        )


@dataclass(frozen=True)
class _ParamNode:
    expiry_years: float
    swap_years: float
    value: float


@dataclass(frozen=True)
class _Interp2DCache:
    y_axis: np.ndarray
    x_axes: tuple[np.ndarray, ...]
    v_axes: tuple[np.ndarray, ...]

    def evaluate(self, *, expiry: float, swap_years: float) -> float:
        xq = _round_years(expiry)
        yq = _round_years(swap_years)
        x_values_by_y = np.empty(self.y_axis.size, dtype=float)
        for i in range(self.y_axis.size):
            xs = self.x_axes[i]
            vs = self.v_axes[i]
            x_values_by_y[i] = float(np.interp(xq, xs, vs, left=vs[0], right=vs[-1]))
        return float(
            np.interp(
                yq,
                self.y_axis,
                x_values_by_y,
                left=x_values_by_y[0],
                right=x_values_by_y[-1],
            )
        )


def _build_interp_2d_cache(nodes: Sequence[_ParamNode]) -> Optional[_Interp2DCache]:
    if not nodes:
        return None
    by_y: dict[float, list[tuple[float, float]]] = {}
    for n in nodes:
        by_y.setdefault(_round_years(n.swap_years), []).append((_round_years(n.expiry_years), n.value))

    y_axis = np.array(sorted(by_y.keys()), dtype=float)
    x_axes: list[np.ndarray] = []
    v_axes: list[np.ndarray] = []
    for y_key in y_axis:
        pairs = sorted(by_y[float(y_key)], key=lambda p: p[0])
        dedup: dict[float, float] = {}
        for px, pv in pairs:
            dedup[px] = pv
        x_sorted = sorted(dedup.keys())
        x_axes.append(np.asarray(x_sorted, dtype=float))
        v_axes.append(np.asarray([dedup[k] for k in x_sorted], dtype=float))
    return _Interp2DCache(y_axis=y_axis, x_axes=tuple(x_axes), v_axes=tuple(v_axes))


def _build_swaption_param_nodes_2d(
    rows: Sequence[ModelParamRow],
    *,
    name: str,
    as_of: date,
    daycount: str,
) -> tuple[list[_ParamNode], Optional[float]]:
    base_best: Optional[tuple[int, float]] = None
    keyed: dict[tuple[float, float], tuple[int, float]] = {}

    for row in rows:
        if row.param_name.lower() != name:
            continue
        scope_rank = _scope_rank(row.scope)
        value = float(row.param_val)
        expiry = _expiry_years_from_param(row, as_of=as_of, daycount=daycount)
        swap_years = _swap_years_from_param(row)
        if expiry is None or swap_years is None:
            if base_best is None or scope_rank >= base_best[0]:
                base_best = (scope_rank, value)
            continue
        key = (_round_years(expiry), _round_years(swap_years))
        prev = keyed.get(key)
        if prev is None or scope_rank >= prev[0]:
            keyed[key] = (scope_rank, value)

    nodes = [_ParamNode(expiry_years=k[0], swap_years=k[1], value=v[1]) for k, v in keyed.items()]
    base = None if base_best is None else float(base_best[1])
    return nodes, base


def _swaption_tenor_to_years(tenor: str) -> float:
    t = parse_tenor(tenor)
    return float(t.months / 12.0 + t.days / 365.0)


def _build_swaption_atm_total_var_surface(vol_points: Sequence[_SwaptionPointLike]) -> list[_ParamNode]:
    out: dict[tuple[float, float], float] = {}
    for row in vol_points:
        smile = row.smile_type.strip().upper()
        if smile not in {"ATM", "MONEYNESS"}:
            continue
        if smile == "MONEYNESS":
            if row.moneyness is None or abs(float(row.moneyness)) > 1e-12:
                continue
        t = float(row.x_years)
        if t <= 0.0:
            continue
        key = (_round_years(t), _round_years(_swaption_tenor_to_years(row.swap_tenor)))
        tv = float(max(row.sigma, _VOL_FLOOR) ** 2 * max(t, _VOL_FLOOR))
        prev = out.get(key)
        if prev is None:
            out[key] = tv
            continue
        if abs(prev - tv) > 1e-12:
            raise ValueError("vol_swaption ATM total variance must be unique per (expiry, swap_tenor) node.")
    return [_ParamNode(expiry_years=k[0], swap_years=k[1], value=v) for k, v in out.items()]


class SwaptionAtmSabrInterpolator:
    def __init__(
        self,
        rows: Sequence[ModelParamRow],
        *,
        as_of: date,
        daycount: str,
        vol_type: SabrVolType,
        interpolation_spec: SabrInterpolationSpec,
        vol_points: Sequence[_SwaptionPointLike],
        vol_shift_surface: dict[tuple[float, float], float],
        forward_rate_index_key: Optional[str] = None,
    ) -> None:
        self._vol_type = vol_type
        self._spec = interpolation_spec
        self._alpha_cache_enabled = bool(interpolation_spec.alpha_cache_enabled)
        self._forward_rate_index_key = _normalize_forward_rate_index_key(forward_rate_index_key)
        self._boundary_tol = float(max(interpolation_spec.boundary_warn_tol, _TRANSFORM_EPS))
        self._alpha_cache: dict[tuple[str, float, float, float], float] = {}
        self._base: dict[str, float] = {}
        self._cache: dict[str, _Interp2DCache] = {}
        self._cache_transformed: dict[str, _Interp2DCache] = {}

        vol_shift_nodes = [_ParamNode(expiry_years=k[0], swap_years=k[1], value=v) for k, v in vol_shift_surface.items()]
        self._vol_shift_cache = _build_interp_2d_cache(vol_shift_nodes)
        atm_tv_nodes = _build_swaption_atm_total_var_surface(vol_points)
        self._atm_tv_cache = _build_interp_2d_cache(atm_tv_nodes)
        if self._atm_tv_cache is None:
            raise ValueError("vol_swaption must contain ATM (or moneyness=0) nodes for ATM total variance interpolation.")

        for name in ("beta", "rho", "nu", "shift"):
            nodes, base = _build_swaption_param_nodes_2d(rows, name=name, as_of=as_of, daycount=daycount)
            cache = _build_interp_2d_cache(nodes)
            if cache is not None:
                self._cache[name] = cache
                if name == "nu":
                    self._cache_transformed[name] = _Interp2DCache(
                        y_axis=cache.y_axis,
                        x_axes=cache.x_axes,
                        v_axes=tuple(
                            np.log(
                                _warn_and_clip_positive(
                                    value=np.asarray(axis, dtype=float),
                                    tol=self._boundary_tol,
                                    name="nu",
                                )
                            )
                            for axis in cache.v_axes
                        ),
                    )
                elif name == "rho":
                    self._cache_transformed[name] = _Interp2DCache(
                        y_axis=cache.y_axis,
                        x_axes=cache.x_axes,
                        v_axes=tuple(
                            np.arctanh(
                                _warn_and_clip_open_interval(
                                    value=np.asarray(axis, dtype=float),
                                    lower=-1.0,
                                    upper=1.0,
                                    tol=self._boundary_tol,
                                    name="rho",
                                )
                            )
                            for axis in cache.v_axes
                        ),
                    )
                elif name == "beta":
                    self._cache_transformed[name] = _Interp2DCache(
                        y_axis=cache.y_axis,
                        x_axes=cache.x_axes,
                        v_axes=tuple(
                            np.log(beta_clip / (1.0 - beta_clip))
                            for beta_clip in (
                                _warn_and_clip_open_interval(
                                    value=np.asarray(axis, dtype=float),
                                    lower=0.0,
                                    upper=1.0,
                                    tol=self._boundary_tol,
                                    name="beta",
                                )
                                for axis in cache.v_axes
                            )
                        ),
                    )
            if base is not None:
                self._base[name] = float(base)

    def _select_linear(self, *, name: str, expiry: float, swap_years: float) -> Optional[float]:
        cache = self._cache.get(name)
        if cache is not None:
            return cache.evaluate(expiry=expiry, swap_years=swap_years)
        return self._base.get(name)

    def _select_shift(self, *, expiry: float, swap_years: float) -> float:
        shift = self._select_linear(name="shift", expiry=expiry, swap_years=swap_years)
        if shift is not None:
            return float(shift)
        if self._vol_shift_cache is not None:
            return float(self._vol_shift_cache.evaluate(expiry=expiry, swap_years=swap_years))
        return 0.0

    def _select_nu(self, *, expiry: float, swap_years: float) -> float:
        cache = self._cache_transformed.get("nu")
        base = self._base.get("nu")
        if cache is not None:
            return float(np.exp(cache.evaluate(expiry=expiry, swap_years=swap_years)))
        if base is not None:
            nu_base = _warn_and_clip_positive(value=np.asarray([base], dtype=float), tol=self._boundary_tol, name="nu")
            return float(nu_base[0])
        raise ValueError("model_param is missing SABR parameter: nu.")

    def _select_rho(self, *, expiry: float, swap_years: float) -> float:
        cache = self._cache_transformed.get("rho")
        base = self._base.get("rho")
        if cache is not None:
            return float(np.tanh(cache.evaluate(expiry=expiry, swap_years=swap_years)))
        if base is not None:
            rho_base = _warn_and_clip_open_interval(
                value=np.asarray([base], dtype=float),
                lower=-1.0,
                upper=1.0,
                tol=self._boundary_tol,
                name="rho",
            )
            return float(rho_base[0])
        raise ValueError("model_param is missing SABR parameter: rho.")

    def _select_beta(self, *, expiry: float, swap_years: float) -> float:
        strategy = self._spec.beta_strategy.strip().upper()
        if strategy == "FIXED":
            if self._spec.beta_fixed_value is None:
                raise ValueError("SABR interpolation spec requires beta_fixed_value for FIXED strategy.")
            beta = float(self._spec.beta_fixed_value)
            if not (0.0 <= beta <= 1.0):
                raise ValueError("beta_fixed_value must be in [0,1].")
            return beta
        if strategy != "INTERPOLATE_LOGIT":
            raise ValueError(f"Unsupported beta_strategy: {self._spec.beta_strategy!r}")
        cache = self._cache_transformed.get("beta")
        base = self._base.get("beta")
        if cache is not None:
            beta_t = cache.evaluate(expiry=expiry, swap_years=swap_years)
            return float(1.0 / (1.0 + np.exp(-beta_t)))
        if base is not None:
            beta_base = _warn_and_clip_open_interval(
                value=np.asarray([base], dtype=float),
                lower=0.0,
                upper=1.0,
                tol=self._boundary_tol,
                name="beta",
            )
            return float(beta_base[0])
        raise ValueError("model_param is missing SABR parameter: beta.")

    def resolve(self, *, expiry: float, swap_years: float, forward: float) -> SabrParams:
        beta = self._select_beta(expiry=expiry, swap_years=swap_years)
        nu = self._select_nu(expiry=expiry, swap_years=swap_years)
        rho = self._select_rho(expiry=expiry, swap_years=swap_years)
        shift = self._select_shift(expiry=expiry, swap_years=swap_years)
        key = (
            self._forward_rate_index_key,
            _round_years(expiry),
            _round_years(swap_years),
            _round_years(forward),
        )
        alpha = self._alpha_cache.get(key) if self._alpha_cache_enabled else None
        if alpha is None:
            atm_tv = float(self._atm_tv_cache.evaluate(expiry=expiry, swap_years=swap_years))
            atm_sigma = float(np.sqrt(max(atm_tv, _VOL_FLOOR) / max(expiry, _VOL_FLOOR)))
            alpha = _solve_alpha_from_atm_vol_1d(
                atm_sigma=atm_sigma,
                forward=float(forward),
                expiry=float(expiry),
                beta=float(beta),
                rho=float(rho),
                nu=float(nu),
                shift=float(shift),
                vol_type=self._vol_type,
                newton_tol=float(max(self._spec.newton_tol, _TRANSFORM_EPS)),
                newton_max_iter=int(max(self._spec.newton_max_iter, 1)),
            )
            if self._alpha_cache_enabled:
                self._alpha_cache[key] = float(alpha)

        return SabrParams(
            alpha=float(alpha),
            beta=float(beta),
            rho=float(rho),
            nu=float(nu),
            shift=float(shift),
        )
