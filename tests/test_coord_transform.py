"""
Tests for spatial.coord_transform — GCJ-02 ⇄ WGS-84 coordinate conversion.

Covers:
- gcj02_to_wgs84 / wgs84_to_gcj02 known-point conversions
- Roundtrip precision (≤ 0.0001°)
- 武大牌坊 landmark verification
- Out-of-China no-transform behaviour
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from spatial.coord_transform import (
    _out_of_china,
    gcj02_to_wgs84,
    wgs84_to_gcj02,
)


# ---- Reference data ----------------------------------------------------------

# 武大牌坊 GCJ-02 coordinates (from project POI data; 高德/腾讯坐标系)
PAIFANG_GCJ02_LNG = 114.35806
PAIFANG_GCJ02_LAT = 30.53318

# Other WHU landmarks (GCJ-02, same source)
WHU_POINTS = [
    (114.35806, 30.53318),  # 牌坊
    (114.36480, 30.53850),  # 樱花大道
    (114.36400, 30.53950),  # 樱顶
    (114.36450, 30.53900),  # 老图书馆
    (114.36200, 30.53450),  # 梅园
    (114.36300, 30.53800),  # 桂园
]


# =============================================================================
# Test case 1: gcj02_to_wgs84 known point
# =============================================================================

class TestGcj02ToWgs84:
    def test_gcj02_to_wgs84_known_point(self):
        """
        Convert a known GCJ-02 point (武大牌坊) to WGS-84 and verify the
        offset is in a physically reasonable range for China (50–2000 m).
        """
        wgs_lng, wgs_lat = gcj02_to_wgs84(PAIFANG_GCJ02_LNG, PAIFANG_GCJ02_LAT)

        offset_lng = abs(PAIFANG_GCJ02_LNG - wgs_lng)
        offset_lat = abs(PAIFANG_GCJ02_LAT - wgs_lat)

        # GCJ-02 offset inside China is non-zero and < 0.02° (~2 km)
        assert 0.0001 < offset_lng < 0.02, (
            f"Longitude offset {offset_lng}° out of expected range (0.0001–0.02°)"
        )
        assert 0.0001 < offset_lat < 0.02, (
            f"Latitude offset {offset_lat}° out of expected range (0.0001–0.02°)"
        )

    @pytest.mark.parametrize("lng,lat", WHU_POINTS)
    def test_gcj02_to_wgs84_offset_nonzero(self, lng, lat):
        """Every WHU landmark GCJ-02 coordinate should shift when converted."""
        wgs_lng, wgs_lat = gcj02_to_wgs84(lng, lat)
        assert (wgs_lng != lng) or (wgs_lat != lat), (
            f"Expected non-zero offset for ({lng}, {lat})"
        )


# =============================================================================
# Test case 2: wgs84_to_gcj02 known point
# =============================================================================

class TestWgs84ToGcj02:
    def test_wgs84_to_gcj02_known_point(self):
        """
        Convert a known WGS-84 point near WHU to GCJ-02 and verify the
        offset is in a physically reasonable range.
        """
        wgs_lng, wgs_lat = 114.3515, 30.5347
        gcj_lng, gcj_lat = wgs84_to_gcj02(wgs_lng, wgs_lat)

        offset_lng = abs(gcj_lng - wgs_lng)
        offset_lat = abs(gcj_lat - wgs_lat)

        assert 0.0001 < offset_lng < 0.02, (
            f"Longitude offset {offset_lng}° out of expected range (0.0001–0.02°)"
        )
        assert 0.0001 < offset_lat < 0.02, (
            f"Latitude offset {offset_lat}° out of expected range (0.0001–0.02°)"
        )

    def test_wgs84_to_gcj02_consistency(self):
        """
        wgs84_to_gcj02 applied to the output of gcj02_to_wgs84 on 牌坊
        should return the original GCJ-02 coordinate.
        """
        wgs_lng, wgs_lat = gcj02_to_wgs84(PAIFANG_GCJ02_LNG, PAIFANG_GCJ02_LAT)
        gcj_lng, gcj_lat = wgs84_to_gcj02(wgs_lng, wgs_lat)
        assert abs(gcj_lng - PAIFANG_GCJ02_LNG) < 0.0001
        assert abs(gcj_lat - PAIFANG_GCJ02_LAT) < 0.0001


# =============================================================================
# Test case 3: Roundtrip GCJ-02 → WGS-84 → GCJ-02
# =============================================================================

class TestRoundtripGcjWgsGcj:
    """GCJ-02 → WGS-84 → GCJ-02 roundtrip precision."""

    @pytest.mark.parametrize("lng,lat", [
        *WHU_POINTS,
        (116.39723, 39.90872),   # 北京 (GCJ-02 approx)
        (121.47370, 31.23037),   # 上海 (GCJ-02 approx)
        (113.26443, 23.12911),   # 广州 (GCJ-02 approx)
    ])
    def test_roundtrip_gcj_to_wgs_to_gcj(self, lng, lat):
        """
        GCJ-02 → WGS-84 → GCJ-02: error ≤ 0.0001 degrees.
        This is the primary precision acceptance criterion.
        """
        wgs_lng, wgs_lat = gcj02_to_wgs84(lng, lat)
        gcj_lng, gcj_lat = wgs84_to_gcj02(wgs_lng, wgs_lat)

        assert abs(gcj_lng - lng) < 0.0001, (
            f"Longitude drift {abs(gcj_lng - lng):.2e}° exceeds 0.0001° for ({lng}, {lat})"
        )
        assert abs(gcj_lat - lat) < 0.0001, (
            f"Latitude drift {abs(gcj_lat - lat):.2e}° exceeds 0.0001° for ({lng}, {lat})"
        )


# =============================================================================
# Test case 4: Roundtrip WGS-84 → GCJ-02 → WGS-84
# =============================================================================

class TestRoundtripWgsGcjWgs:
    """WGS-84 → GCJ-02 → WGS-84 roundtrip precision."""

    @pytest.mark.parametrize("lng,lat", [
        (114.3515, 30.5347),    # WHU 牌坊 approximate WGS-84
        (116.3912, 39.9065),    # 北京 WGS-84
        (121.4675, 31.2280),    # 上海 WGS-84
        (113.2583, 23.1262),    # 广州 WGS-84
    ])
    def test_roundtrip_wgs_to_gcj_to_wgs(self, lng, lat):
        """
        WGS-84 → GCJ-02 → WGS-84: error ≤ 0.0001 degrees.
        """
        gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
        wgs_lng, wgs_lat = gcj02_to_wgs84(gcj_lng, gcj_lat)

        assert abs(wgs_lng - lng) < 0.0001, (
            f"Longitude drift {abs(wgs_lng - lng):.2e}° exceeds 0.0001° for ({lng}, {lat})"
        )
        assert abs(wgs_lat - lat) < 0.0001, (
            f"Latitude drift {abs(wgs_lat - lat):.2e}° exceeds 0.0001° for ({lng}, {lat})"
        )


# =============================================================================
# Test case 5: 武大牌坊 landmark verification
# =============================================================================

class TestWuhanUniversityLandmark:
    """武大牌坊 (WHU main gate) landmark coordinate verification."""

    def test_wuhan_university_landmark(self):
        """
        武大牌坊 GCJ-02 → WGS-84 conversion verified against independently
        known WGS-84 position (OSM / satellite imagery).

        Tolerance: ~500 m ≈ 0.005° at 30.5° latitude.
        """
        wgs_lng, wgs_lat = gcj02_to_wgs84(PAIFANG_GCJ02_LNG, PAIFANG_GCJ02_LAT)

        # Independent reference — WHU main gate in WGS-84
        expected_lng = 114.3515
        expected_lat = 30.5347

        assert abs(wgs_lng - expected_lng) < 0.005, (
            f"Longitude {wgs_lng} differs from expected {expected_lng} "
            f"by {abs(wgs_lng - expected_lng)}°"
        )
        assert abs(wgs_lat - expected_lat) < 0.005, (
            f"Latitude {wgs_lat} differs from expected {expected_lat} "
            f"by {abs(wgs_lat - expected_lat)}°"
        )


# =============================================================================
# Test case 6: Out-of-China — no transform
# =============================================================================

class TestOutOfChinaNoTransform:
    """Coordinates outside China should pass through unchanged."""

    @pytest.mark.parametrize("lng,lat,description", [
        (-74.0060, 40.7128, "New York"),
        (139.6503, 35.6762, "Tokyo"),
        (-0.1276, 51.5074,  "London"),
        (0.0, 0.0,           "Atlantic Ocean (0,0)"),
        (150.0, -33.0,       "Sydney approx"),
    ])
    def test_out_of_china_no_transform_gcj_to_wgs(self, lng, lat, description):
        """gcj02_to_wgs84 returns input unchanged for non-China coordinates."""
        result = gcj02_to_wgs84(lng, lat)
        assert result == (lng, lat), (
            f"{description} ({lng}, {lat}) was transformed to {result}"
        )

    @pytest.mark.parametrize("lng,lat,description", [
        (-74.0060, 40.7128, "New York"),
        (139.6503, 35.6762, "Tokyo"),
        (-0.1276, 51.5074,  "London"),
        (0.0, 0.0,           "Atlantic Ocean (0,0)"),
    ])
    def test_out_of_china_no_transform_wgs_to_gcj(self, lng, lat, description):
        """wgs84_to_gcj02 returns input unchanged for non-China coordinates."""
        result = wgs84_to_gcj02(lng, lat)
        assert result == (lng, lat), (
            f"{description} ({lng}, {lat}) was transformed to {result}"
        )


# =============================================================================
# Bonus: _out_of_china helper
# =============================================================================

class TestOutOfChinaHelper:
    """Tests for the _out_of_china boundary-check helper."""

    @pytest.mark.parametrize("lng,lat", [
        (114.35806, 30.53318),   # WHU
        (116.39723, 39.90872),   # Beijing
        (121.47370, 31.23037),   # Shanghai
        (73.66, 3.86),           # SW corner of China bbox
        (135.05, 53.55),         # NE corner of China bbox
    ])
    def test_china_coordinates_not_out(self, lng, lat):
        """Coordinates inside China bounding box return False."""
        assert _out_of_china(lng, lat) is False

    @pytest.mark.parametrize("lng,lat", [
        (-74.0060, 40.7128),    # New York
        (139.6503, 35.6762),    # Tokyo
        (0.0, 0.0),             # Atlantic
        (73.65, 30.0),          # Just west of China bbox
        (135.06, 30.0),         # Just east of China bbox
    ])
    def test_outside_china_coordinates(self, lng, lat):
        """Coordinates outside China bounding box return True."""
        assert _out_of_china(lng, lat) is True
