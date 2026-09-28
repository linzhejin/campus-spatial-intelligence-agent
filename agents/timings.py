"""Stable timing schema used by route responses and research exports."""

from __future__ import annotations


TIMING_KEYS = (
    "agent",
    "poi_resolution",
    "graph_prepare",
    "path_search",
    "response_build",
)


def normalize_timings(value=None, **overrides) -> dict:
    """Return the exact public timing schema with finite non-negative values."""
    source = value if isinstance(value, dict) else {}
    result = {}
    for key in TIMING_KEYS:
        raw = overrides[key] if key in overrides else source.get(key, 0.0)
        try:
            number = float(raw)
        except (TypeError, ValueError):
            number = 0.0
        result[key] = round(max(0.0, number), 3)
    return result


def add_timings(*values) -> dict:
    """Add one or more stage timing dictionaries without changing the schema."""
    total = {key: 0.0 for key in TIMING_KEYS}
    for value in values:
        normalized = normalize_timings(value)
        for key in TIMING_KEYS:
            total[key] += normalized[key]
    return normalize_timings(total)
