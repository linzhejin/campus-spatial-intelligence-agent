# T-027 Implementation Report: test_coord_transform.py

**Date**: 2026-08-11
**Status**: Complete
**File created**: `tests/test_coord_transform.py`

## Summary

Created comprehensive pytest test suite for `spatial/coord_transform.py` covering all acceptance criteria.

## Test Cases (42 parametrized variants, 10 test functions, 6 test classes)

| # | Test Function | Class | What It Validates |
|---|--------------|-------|-------------------|
| 1 | `test_gcj02_to_wgs84_known_point` | `TestGcj02ToWgs84` | Known GCJ-02 point (武大牌坊) converts with offset in [0.0001, 0.02) degrees |
| 2 | `test_gcj02_to_wgs84_offset_nonzero` | `TestGcj02ToWgs84` | 6 WHU landmarks all produce non-zero offsets (parametrized) |
| 3 | `test_wgs84_to_gcj02_known_point` | `TestWgs84ToGcj02` | Known WGS-84 point converts with reasonable offset |
| 4 | `test_wgs84_to_gcj02_consistency` | `TestWgs84ToGcj02` | wgs84_to_gcj02 inverts gcj02_to_wgs84 for 牌坊 |
| 5 | `test_roundtrip_gcj_to_wgs_to_gcj` | `TestRoundtripGcjWgsGcj` | 9 points: GCJ->WGS->GCJ error <= 0.0001 degrees (parametrized) |
| 6 | `test_roundtrip_wgs_to_gcj_to_wgs` | `TestRoundtripWgsGcjWgs` | 4 points: WGS->GCJ->WGS error <= 0.0001 degrees (parametrized) |
| 7 | `test_wuhan_university_landmark` | `TestWuhanUniversityLandmark` | 武大牌坊 WGS-84 is within 500m (~0.005 deg) of OSM reference |
| 8 | `test_out_of_china_no_transform_gcj_to_wgs` | `TestOutOfChinaNoTransform` | 5 non-China cities: gcj02_to_wgs84 returns input unchanged (parametrized) |
| 9 | `test_out_of_china_no_transform_wgs_to_gcj` | `TestOutOfChinaNoTransform` | 4 non-China cities: wgs84_to_gcj02 returns input unchanged (parametrized) |
| 10 | `test_china_coordinates_not_out` | `TestOutOfChinaHelper` | 5 points inside China bbox: `_out_of_china` returns False (parametrized) |
| bonus | `test_outside_china_coordinates` | `TestOutOfChinaHelper` | 5 points outside China bbox: `_out_of_china` returns True (parametrized) |

## Acceptance Criteria Verification

1. **Round-trip precision <= 0.0001 degrees**: PASS -- tested via 13 parametrized cases across both GCJ->WGS->GCJ and WGS->GCJ->WGS directions
2. **Tests cover gcj02_to_wgs84 and wgs84_to_gcj02**: PASS -- both functions tested with known-point, roundtrip, consistency, and out-of-China cases
3. **Known landmarks tested**: PASS -- 武大牌坊 verified against independent WGS-84 reference (OSM); 6 WHU landmarks used in parametrized tests
4. **pytest tests/ all pass (new tests)**: PASS -- 42/42 new tests pass

## Test Results

```
============================= 42 passed in 0.11s =============================
```

## Files Modified/Created

- **Created**: `tests/test_coord_transform.py` (only new file, no existing files modified)
