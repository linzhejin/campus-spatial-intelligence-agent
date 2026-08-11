# T-022 Implementation Report — Scenario 3 Candidate POI Interaction Loop

**Date**: 2026-08-11
**Architecture Review**: N4
**Status**: COMPLETED (4/4 acceptance criteria passed)

---

## Summary

Implemented the scenario 3 candidate POI interaction loop for WHU-Walker. When a user specifies only a start POI + POI type (e.g., "从牌坊出发，想去赏樱的地方"), the system returns candidate POIs matching the type/keyword, displays them as clickable cards, and automatically replans the route when a candidate is selected.

---

## Files Modified

### 1. `api/routes.py` — Added `POST /api/candidates` endpoint

**Imports added**:
- `import networkx as nx` — for Dijkstra path computation
- `from spatial.routing import _path_length` — for calculating road-network path length

**New endpoint** (appended at end of file):
- Route: `POST /api/candidates`
- Input: `{"start": {"name": "..."}, "poi_type": "scenery", "keyword": "樱花"}`
- Output: `{"candidates": [{"name": "...", "type": "...", "scenery_score": N, "distance_m": N, "description": "..."}], "start": {...}}`
- Behavior:
  1. Resolves start POI by name (fuzzy match)
  2. Lists all POIs filtered by `poi_type` and `keyword`
  3. Excludes the start POI from candidates
  4. If no candidates found, returns `no_candidates` error (404)
  5. Computes road-network distance from start to each candidate (fallback: haversine)
  6. Sorts by `scenery_score` desc, then `distance_m` asc
  7. Returns top 5 candidates
- **AC-1 satisfied**: Backend returns candidates list with POI name, type, road network distance, scenery_score
- **AC-4 satisfied**: No candidates returns `no_candidates` error hint

### 2. `static/js/app.js` — Added candidate card UI (IIFE appended at end)

**Exported functions**:
- `window.showCandidateCards(candidates, startName)` — renders candidate cards in `#candidates-panel`
- `window.hideCandidateCards()` — clears the panel

**UI features**:
- Container: `#candidates-panel` div, inserted before the results section in the main content area
- Each card shows:
  - POI name with type badge (color-coded: scenery=pink, landmark=gold, study=blue, etc.)
  - Scenery score as stars (★/☆, 1-5 scale)
  - Road network distance (meters or km)
  - Description (truncated to 60 chars)
- Cards have hover effects (shadow, border color change, slight lift)
- Left-side gradient accent bar (pink-to-gold)
- Brand colors: `#E8929C` (cherry blossom pink), `#D4915C` (gold)
- Empty state: friendly message with dashed border when no candidates
- **AC-2 satisfied**: Frontend shows candidates as clickable cards with inline styles

**Click behavior**:
- Clicking a card constructs a query: `从{startName}到{candidateName}`
- Sets the input field value to the constructed query
- Programmatically clicks the submit button, triggering the existing route planning flow
- Panel fades to 30% opacity after selection for visual feedback
- **AC-3 satisfied**: Clicking a candidate auto-triggers route replanning

### 3. `scripts/T022_selfcheck.py` — Rewritten for new implementation

Updated to validate the 4 acceptance criteria:
1. `check1_backend_candidates_endpoint()` — endpoint exists, returns correct fields, has networkx import
2. `check2_frontend_candidate_cards()` — `window.showCandidateCards` exists, cards render with `.candidate-card` class, brand colors used
3. `check3_frontend_click_replanning()` — `onCandidateClick` sets input value, clicks submit button, uses start name
4. `check4_no_candidates_error_hint()` — backend `no_candidates` error code, frontend empty state message

---

## Acceptance Criteria Verification

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Backend returns candidates list | PASS | `POST /api/candidates` returns `{candidates: [{name, type, scenery_score, distance_m, description}]}` |
| 2 | Frontend shows clickable cards | PASS | `window.showCandidateCards()` renders `.candidate-card` elements with hover effects |
| 3 | Click auto-triggers replanning | PASS | `onCandidateClick()` fills input + clicks submit button, reusing existing flow |
| 4 | No candidates returns error | PASS | Backend 404 `no_candidates` + frontend "未找到匹配的候选 POI" empty state |

**Self-check result: 4/4 PASSED**

---

## Design Decisions

- **Network distance with haversine fallback**: If the road network is not initialized, haversine distance is used instead. This ensures the endpoint works even before network init.
- **Sort order**: Scenery score descending first, then distance ascending — prioritizes scenic spots nearby.
- **Top 5 limit**: Returns at most 5 candidates to avoid overwhelming the user.
- **Coordinate handling**: POI coordinates from `list_all_pois()` are flattened (GCJ-02), converted to WGS-84 for road network matching via `gcj02_to_wgs84()`.
- **Reuse existing route flow**: Candidate clicks reuse the existing `handleNlSubmit` path through programmatic button clicks, avoiding duplicate API call logic.
