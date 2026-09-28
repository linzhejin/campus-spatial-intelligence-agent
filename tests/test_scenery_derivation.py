import pytest

from scripts.network.derive_scenery_attributes import (
    scenery_confidence,
    scenery_level,
    scenery_score,
)


def test_v1_formula_uses_declared_weights():
    score, coverage = scenery_score({
        "greenery": 1.0,
        "shade": 0.8,
        "water": 0.6,
        "heritage": 0.4,
        "quietness": 0.2,
        "seasonality": 0.0,
    })
    assert score == pytest.approx(0.25 + 0.16 + 0.09 + 0.06 + 0.03)
    assert coverage == pytest.approx(1.0)


def test_missing_components_are_renormalized_not_zeroed():
    score, coverage = scenery_score({"greenery": 1.0, "water": 0.0})
    assert score == pytest.approx(0.25 / (0.25 + 0.15))
    assert coverage == pytest.approx(0.40)


def test_no_components_stays_unknown():
    assert scenery_score({}) == (None, 0.0)


@pytest.mark.parametrize(
    "score,level",
    [(0.0, 1), (0.199, 1), (0.2, 2), (0.4, 3), (0.6, 4), (0.8, 5), (1.0, 5)],
)
def test_level_mapping_is_versioned_and_bounded(score, level):
    assert scenery_level(score) == level


def test_confidence_requires_coverage_and_direct_geometry():
    assert scenery_confidence(0.0, direct_sources=0) == "unknown"
    assert scenery_confidence(0.79, direct_sources=3) == "low"
    assert scenery_confidence(0.8, direct_sources=0) == "low"
    assert scenery_confidence(0.8, direct_sources=1) == "medium"
