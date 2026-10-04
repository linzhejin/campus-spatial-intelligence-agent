# Manager Vision Flow Recovery Design

## Goal

Make aerial-image analysis a self-contained workflow that never requires the operator to scroll to the road map before recognition and never leaves a finished task looking stuck.

## Confirmed User Flow

1. The operator selects an image or short video in the vision workspace.
2. The operator can start recognition immediately. A rough observation point is not requested and is not required by the upload API.
3. The task card progresses through queued, running, and a clearly explained terminal state.
4. A completed task with no review candidate explicitly says that analysis has ended and that no actionable candidate was produced. It offers actions to select another file and remove the finished card from the current view.
5. A completed task with review candidates continues through human review.
6. Only after a candidate is confirmed and transferred to the road-event workflow does the operator select the exact road segment. The road selection remains mandatory before publishing an event.

## Approaches Considered

### Chosen: Remove the pre-analysis location step

The rough point does not affect detector inference and is not a ground coordinate for any detection box. Removing it from the upload contract eliminates unnecessary navigation and prevents operators from assuming the point improves recognition accuracy.

### Rejected: Open a location-map dialog inside the vision workspace

This would avoid scrolling but retain a step with no effect on recognition. It would also duplicate the existing map interaction and create additional responsive and accessibility work.

### Rejected: Automatically scroll to the existing map and back

This keeps the confusing dependency and risks disorienting the operator, especially on smaller screens.

## Interface Design

### Upload card

- Remove the rough-location button and the selected-anchor summary from the upload form.
- Keep file preview, format validation, stabilization option, and the primary “开始识别” action.
- Explain that location is selected later only when a verified result is converted into a road event.
- Enable the submit button when inference is ready, a valid file is selected, and no upload is already in progress.

### Task cards

- Queued and running tasks retain their existing progress states.
- A completed task with an empty `candidates` list shows a terminal panel: analysis is complete, no reviewable road-condition candidate was produced, and vehicle counts alone do not indicate congestion.
- The terminal panel provides:
  - “重新选择影像”: focuses/opens the file chooser in the upload card and scrolls the upload card into view only when needed.
  - “清除此结果”: hides the task from the current browser view. This is a non-destructive local dismissal; it does not delete audit data or media from the server.
- Candidate and review flows remain unchanged.
- Failed tasks keep their error message and also provide the retry/clear actions where possible.

## API and Data Compatibility

- `POST /api/manager/vision-jobs` no longer requires `lng` or `lat`.
- New jobs store `anchor_gcj` as `null`.
- Existing jobs with an anchor remain readable and displayable.
- The analysis engine accepts a nullable anchor and returns the same result structure; location guidance must not claim that an anchor exists when it is absent.
- Road-event publication continues to require a snapped road segment and human field confirmation. Removing the rough point does not weaken publication safeguards.

## Local Dismissal Behavior

- Dismissed job IDs are stored in browser session storage so polling does not immediately bring the cards back.
- Dismissal lasts for the current browser tab session. Refreshing the task list through a dedicated “显示已清除结果” control restores them.
- No delete endpoint is introduced because completed vision jobs are audit records.

## Error Handling

- Submitting without a file remains blocked with a clear message.
- Unsupported or oversized files retain the existing validation messages.
- A retry action never resubmits the old file automatically; the browser requires the operator to choose a file again.
- If session storage is unavailable, local dismissal still works in memory for the current page lifetime.

## Accessibility and Responsive Behavior

- All new controls are real buttons with visible focus states.
- Terminal-state text is announced as ordinary card content and does not rely on color alone.
- Action buttons wrap on narrow screens without covering the preview or metrics.

## Verification

- Frontend tests prove that a valid file can be submitted without an anchor.
- API tests prove that missing coordinates are accepted and stored as a null anchor while out-of-range coordinates remain rejected when coordinates are supplied.
- Rendering tests prove that completed empty-candidate tasks show terminal copy plus retry and clear controls.
- Rendering tests prove that clearing hides the task during polling and restoring cleared results makes it visible again.
- Existing candidate review and road-publication tests remain green.
- Desktop and mobile browser checks verify the complete select → recognize → terminal action flow without manual scrolling to the road map.

## Scope Boundaries

- No automatic geolocation, EXIF extraction, or image-to-map registration is added.
- No server-side deletion of vision jobs or media is added.
- No change is made to the detector thresholds or model behavior.
- No claim is made that arbitrary screenshots are valid aerial imagery.
