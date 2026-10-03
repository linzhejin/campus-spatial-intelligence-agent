-- An audited review must identify which candidate in the immutable analysis
-- result was confirmed. NULL is retained for dismissed/legacy reviews.
ALTER TABLE manager_vision_job
    ADD COLUMN IF NOT EXISTS review_candidate_index INTEGER
    CHECK (review_candidate_index IS NULL OR review_candidate_index >= 0);
