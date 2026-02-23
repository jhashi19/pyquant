from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional


class PricingModelScope(Enum):
    GLOBAL = "GLOBAL"
    CCY = "CCY"


@dataclass(frozen=True)
class PricingModelConfig:
    profile_id: str
    product: str
    scope: str
    scope_key: str
    pricing_model: str
    vol_interp_model: Optional[str] = None
    model_tag: Optional[str] = None
    vol_quote_type: Optional[str] = None
    surface_tag: Optional[str] = None


def _scope_rank(scope: str) -> int:
    key = scope.strip().upper()
    if key == PricingModelScope.GLOBAL.value:
        return 0
    if key == PricingModelScope.CCY.value:
        return 1
    return -1


def resolve_pricing_model_config(
    rows: Iterable[PricingModelConfig],
    *,
    ccy: str,
) -> Optional[PricingModelConfig]:
    ccy_key = ccy.strip().upper()
    best_rank = -1
    best: Optional[PricingModelConfig] = None
    for row in rows:
        scope = row.scope.strip().upper()
        scope_key = row.scope_key.strip().upper()
        if scope == PricingModelScope.CCY.value and scope_key != ccy_key:
            continue
        if scope == PricingModelScope.GLOBAL.value and scope_key != "GLOBAL":
            continue
        rank = _scope_rank(scope)
        if rank > best_rank:
            best_rank = rank
            best = row
    return best
