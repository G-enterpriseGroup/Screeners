"""Fresh StockAnalysis portfolio renderer with stale-if-error classification cache.

V5 layers the persistent public classification cache on top of the V4/V3
Bloomberg chart renderer without changing chart math or portfolio values.
"""

from __future__ import annotations

import src.stockanalysis_portfolio as _base
import src.stockanalysis_portfolio_v3 as _v3
import src.stockanalysis_portfolio_v4 as _v4
from src.stockanalysis_cache import cached_classification, cached_lookthrough, cache_status


_ORIGINAL_CLASSIFICATION = _base.stockanalysis_classification
_ORIGINAL_LOOKTHROUGH = _base._cached_sector_lookthrough


def _safe_classification(symbol: str, security_type: str = ""):
    return cached_classification(_ORIGINAL_CLASSIFICATION, symbol, security_type)


def _safe_lookthrough(symbol: str, security_type: str = ""):
    return cached_lookthrough(_ORIGINAL_LOOKTHROUGH, symbol, security_type)


def render_stockanalysis_portfolio(*args, **kwargs) -> None:
    """Render the Bloomberg exposure panels using last-known-good classifications."""
    previous_base_class = _base.stockanalysis_classification
    previous_base_lookthrough = _base._cached_sector_lookthrough
    previous_v3_class = _v3.stockanalysis_classification
    previous_v3_lookthrough = _v3._cached_sector_lookthrough

    _base.stockanalysis_classification = _safe_classification
    _base._cached_sector_lookthrough = _safe_lookthrough
    _v3.stockanalysis_classification = _safe_classification
    _v3._cached_sector_lookthrough = _safe_lookthrough
    try:
        return _v4.render_stockanalysis_portfolio(*args, **kwargs)
    finally:
        _base.stockanalysis_classification = previous_base_class
        _base._cached_sector_lookthrough = previous_base_lookthrough
        _v3.stockanalysis_classification = previous_v3_class
        _v3._cached_sector_lookthrough = previous_v3_lookthrough


__all__ = ["render_stockanalysis_portfolio", "cache_status"]
