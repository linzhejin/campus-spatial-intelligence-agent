# Manager Vision Flow Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators submit vision media without selecting a rough map point and give every finished task clear retry and local-clear actions.

**Architecture:** Make `anchor_gcj` optional at the HTTP boundary and preserve nullable anchors through storage and analysis. Keep completed jobs as server-side audit records; implement clearing as a frontend session filter, while leaving the exact-road selection safeguard in the existing confirmed-candidate transfer flow.

**Tech Stack:** Flask/Python, PostgreSQL repository layer, vanilla JavaScript, HTML/CSS, Node.js test runner, pytest.

## Global Constraints

- Recognition must not require or prompt for a rough observation point.
- Publishing a road event must still require a snapped road segment and human field confirmation.
- Clearing a result is local and non-destructive; no delete API is added.
- Existing jobs with `anchor_gcj` remain readable.
- Detector thresholds and model behavior remain unchanged.

---

### Task 1: Make vision-job location optional

**Files:**
- Modify: `tests/test_vision_jobs.py`
- Modify: `api/routes.py:2197-2266`
- Modify: `vision/engine.py:201-319`
- Modify: `vision/worker.py`

**Interfaces:**
- Consumes: multipart `POST /api/manager/vision-jobs` with required `media` and optional paired `lng`/`lat` fields.
- Produces: queued job with `anchor_gcj: null` when coordinates are absent; existing coordinate validation remains active when either coordinate field is supplied.

- [ ] **Step 1: Write the failing API test**

Add a test that posts a valid PNG without `lng` or `lat`, asserts HTTP 202, and asserts the public job has `anchor_gcj is None`. Extend coordinate-validation coverage so a partial coordinate pair is rejected and an out-of-campus pair remains rejected.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pytest -q tests/test_vision_jobs.py -k "location or upload"`

Expected: the coordinate-free upload fails with `invalid_anchor` before the implementation change.

- [ ] **Step 3: Implement optional anchor parsing**

In `manager_vision_jobs_create`, treat two absent/blank coordinate fields as `anchor_gcj = None`. If exactly one field is present, return `invalid_anchor`. If both are present, parse and apply the existing campus bounding-box validation. Pass the nullable value to `vision_repository.create_job`.

Update the analysis/worker type and call path so `analyze_media(..., anchor_gcj=None)` is valid. Return `location_precision: "not_provided"` and guidance that the exact road is selected during human review when the anchor is null; retain the existing guidance for historical anchored jobs.

- [ ] **Step 4: Run focused backend tests and verify GREEN**

Run: `pytest -q tests/test_vision_jobs.py tests/test_vision_media_pipeline.py tests/test_vision_engine_limits.py`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the backend behavior**

Run: `git add api/routes.py vision/engine.py vision/worker.py tests/test_vision_jobs.py && git commit -m "fix(vision): allow uploads without rough location"`

---

### Task 2: Remove pre-analysis map selection from the upload UI

**Files:**
- Modify: `tests/js/manager-vision-readiness.test.js`
- Modify: `static/manager.html:115-141`
- Modify: `static/js/manager.js:1-95, 301-317, 696-720, 787-805`
- Modify: `static/css/manager.css`

**Interfaces:**
- Consumes: inference readiness plus a valid selected file.
- Produces: enabled “开始识别” button and multipart upload containing media and stabilization state only.

- [ ] **Step 1: Write failing frontend tests**

Change the upload-readiness tests so selecting a valid file enables submission without `pick-anchor` or a map click. Assert the request `FormData` has no `lng` or `lat`. Assert the guidance says the file is ready and that the exact road will be selected only after review.

- [ ] **Step 2: Run the focused JavaScript tests and verify RED**

Run: `node --test tests/js/manager-vision-readiness.test.js`

Expected: the submit button remains disabled because `state.anchor` is missing.

- [ ] **Step 3: Implement the shortened upload flow**

Remove `pick-anchor` and `selected-anchor` from the HTML, event registration, guidance, readiness condition, and upload payload. Remove the vision-only branch from `handleMapClick`; preserve road selection behavior. Update the disclaimer and selected-media fallback message so neither instructs the operator to mark an area. Bump the manager JS/CSS asset version.

- [ ] **Step 4: Run the frontend tests and verify GREEN**

Run: `node --test tests/js/manager-vision-readiness.test.js`

Expected: all tests pass.

- [ ] **Step 5: Commit the upload-flow change**

Run: `git add static/manager.html static/js/manager.js static/css/manager.css tests/js/manager-vision-readiness.test.js && git commit -m "fix(manager): remove vision preselection step"`

---

### Task 3: Add explicit terminal state, retry, clear, and restore actions

**Files:**
- Modify: `tests/js/manager-vision-readiness.test.js`
- Modify: `static/manager.html:115-141`
- Modify: `static/js/manager.js:580-693`
- Modify: `static/css/manager.css`

**Interfaces:**
- Consumes: completed/failed vision job records and `sessionStorage` key `managerDismissedVisionJobs`.
- Produces: terminal-state copy, “重新选择影像”, “清除此结果”, and “显示已清除结果” controls.

- [ ] **Step 1: Write failing rendering tests**

For a completed image job with `candidates: []`, assert the card contains “分析已结束” and “未生成可复核的路况候选”, plus retry and clear buttons. Click retry and assert the upload form scrolls into view and the file input is activated. Click clear, poll again, and assert the job stays hidden. Click restore and assert the job returns. Add a storage-failure case that proves in-memory dismissal still works.

- [ ] **Step 2: Run the rendering tests and verify RED**

Run: `node --test tests/js/manager-vision-readiness.test.js`

Expected: terminal copy and action buttons are absent.

- [ ] **Step 3: Implement terminal actions**

Add safe session-storage helpers backed by an in-memory `Set`. Filter dismissed job IDs in `refreshVisionJobs`. Render the terminal explanation only for `completed` jobs with no candidates. Reuse the retry and clear action row for failed jobs. Add a restore control to the vision-job header area and show it only when at least one result is hidden. Retry calls the file input click method and scrolls the upload card into view; clear never calls an API.

- [ ] **Step 4: Add responsive styling**

Style the terminal panel and action row using the existing paper/pine palette. Ensure buttons wrap below 560 px and retain visible keyboard focus.

- [ ] **Step 5: Run the frontend suite and verify GREEN**

Run: `node --test tests/js/*.test.js`

Expected: all JavaScript tests pass with zero failures.

- [ ] **Step 6: Commit the recovery controls**

Run: `git add static/manager.html static/js/manager.js static/css/manager.css tests/js/manager-vision-readiness.test.js && git commit -m "fix(manager): make vision completion recoverable"`

---

### Task 4: Regression verification and deployment

**Files:**
- Verify: `static/manager.html`
- Verify: `static/js/manager.js`
- Verify: `static/css/manager.css`
- Verify: `api/routes.py`
- Verify: `vision/engine.py`

**Interfaces:**
- Consumes: the complete implementation from Tasks 1–3.
- Produces: verified production behavior at `https://whuspati.online/manager`.

- [ ] **Step 1: Run automated regression tests**

Run: `node --test tests/js/*.test.js`

Run: `pytest -q tests/test_vision_jobs.py tests/test_vision_analysis.py tests/test_vision_media_pipeline.py tests/test_vision_engine_limits.py tests/test_vision_camera_motion.py tests/test_road_conditions.py`

Expected: all selected JavaScript and Python tests pass.

- [ ] **Step 2: Run repository integrity checks**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 3: Verify the browser workflow locally**

At desktop and mobile widths, verify selecting a file immediately enables recognition, no rough-location control appears, a completed empty-candidate task explains the terminal state, retry reopens selection, clear hides the card, restore returns it, and confirmed candidates still transfer to exact-road selection.

- [ ] **Step 4: Review the complete branch diff**

Confirm only the planned manager UI, vision API/engine, tests, and asset version changed. Confirm no detector thresholds, routing safeguards, or unrelated user files changed.

- [ ] **Step 5: Integrate and deploy**

Fast-forward the verified commits into the primary branch, push the configured remotes, update the production checkout, restart the application and vision worker services, and confirm the public health endpoint and manager asset versions.

- [ ] **Step 6: Verify production**

Open the public manager page, repeat the shortened upload and terminal recovery flow, and record the deployed commit plus service health in the handoff.
