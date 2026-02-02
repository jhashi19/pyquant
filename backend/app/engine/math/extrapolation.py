from __future__ import annotations

import numpy as np

from .interpolation import DF_AT_ZERO_EPS, EPS


def flat_forward_extrapolate(
    x_nodes: np.ndarray, df_nodes: np.ndarray, xq: np.ndarray, *, side: str
) -> np.ndarray:
    if x_nodes.size < 1:
        raise ValueError("At least one node is required for extrapolation.")

    if x_nodes.size == 1:
        t0 = x_nodes[0]
        if abs(t0) < EPS and abs(df_nodes[0] - 1.0) > DF_AT_ZERO_EPS:
            raise ValueError("Single-node extrapolation at t=0 requires df=1.")
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


def linear_zero_extrapolate(
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
