# End-to-End SQL Test Playbook
## Full Pipeline Time-Travel Testing Guide

> **All table and column names are taken directly from the SQLAlchemy models.**  
> Replace `{JOB_ID}` with your `job_requisitions.requisition_id` throughout.  
> Run queries in psql, DBeaver, or any PostgreSQL client connected to the dev database.

---

## Table of Contents
1. [Pre-Flight Smoke Check](#0-pre-flight-smoke-check)
2. [Phase 0 — Identify Your Test Job](#phase-0--identify-your-test-job)
3. [Phase 1 — CV Collection → CV Ranking](#phase-1--cv-collection--cv-ranking)
4. [Phase 2 — CV Ranking → Assessment Generation](#phase-2--cv-ranking--assessment-generation)
5. [Phase 3 — Assessment Deadline → Technical Interview Invitations](#phase-3--assessment-deadline--technical-interview-invitations)
6. [Phase 4 — Technical Interview Scoring (Manual API)](#phase-4--technical-interview-scoring-manual-api)
7. [Phase 5 — HR Interview Scoring (Manual API)](#phase-5--hr-interview-scoring-manual-api)
8. [Phase 6 — Interview Deadline → Final Ranking](#phase-6--interview-deadline--final-ranking)
9. [Acceptance Checklist](#acceptance-checklist)
10. [Full Reset / Rollback](#full-reset--rollback)

---

## 0. Pre-Flight Smoke Check

Run these before touching any test data to verify services are alive.

```sql
-- Confirm Celery Beat tasks are registered (run in application shell, not SQL)
-- celery -A core.celery inspect registered

-- Count applications already in the system
SELECT
    jr.requisition_id,
    jr.job_title,
    jr.status,
    jr.processing_status,
    COUNT(a.application_id) AS applications
FROM job_requisitions jr
LEFT JOIN job_postings jp ON jp.requisition_id = jr.requisition_id
LEFT JOIN applications a  ON a.posting_id = jp.posting_id
GROUP BY jr.requisition_id, jr.job_title, jr.status, jr.processing_status
ORDER BY jr.requisition_id DESC
LIMIT 20;
```

**Expected:** At least one JR with `applications > 0` and `processing_status = 'idle'`.

---

## Phase 0 — Identify Your Test Job

```sql
-- Find a good candidate JR: has applications, not already ranked
SELECT
    jr.requisition_id                                       AS job_id,
    jr.job_title,
    jr.status,
    jr.processing_status,
    jr.cv_collection_end_date,
    jr.shortlist_notified,
    jr.interview_notified,
    jr.interview_deadline,
    jp.posting_id,
    COUNT(a.application_id)                                 AS total_applications,
    COUNT(sar.report_id)                                    AS ranked_cvs
FROM job_requisitions jr
LEFT JOIN job_postings            jp  ON jp.requisition_id = jr.requisition_id
LEFT JOIN applications            a   ON a.posting_id = jp.posting_id
LEFT JOIN semantic_analysis_reports sar ON sar.application_id = a.application_id
GROUP BY jr.requisition_id, jr.job_title, jr.status, jr.processing_status,
         jr.cv_collection_end_date, jr.shortlist_notified, jr.interview_notified,
         jr.interview_deadline, jp.posting_id
ORDER BY total_applications DESC;
```

Note your chosen `requisition_id` — use it as `{JOB_ID}` everywhere below.

---

## Phase 1 — CV Collection → CV Ranking

### What the scanner does
`scan_and_dispatch_cv_ranking` (every 5 min) fires when:
- `job_requisitions.status IN ('published', 'Active')`
- `job_requisitions.cv_collection_end_date <= CURRENT_DATE`
- `job_requisitions.processing_status IN ('idle', 'error')`

On success it sets `status = 'ranked'` and `processing_status = 'idle'`, then dispatches shortlist emails.

### Step 1A — Inspect current state
```sql
SELECT
    requisition_id,
    status,
    processing_status,
    cv_collection_end_date,
    shortlist_notified
FROM job_requisitions
WHERE requisition_id = {JOB_ID};
```

### Step 1B — Trigger: push cv_collection_end_date into the past
```sql
UPDATE job_requisitions
SET
    cv_collection_end_date = CURRENT_DATE - INTERVAL '1 day',
    status                 = 'Active',        -- or 'published', both work
    processing_status      = 'idle'
WHERE requisition_id = {JOB_ID};
```

Wait up to **5 minutes** for `scan_and_dispatch_cv_ranking` to fire.

### Step 1C — Verify: CV ranking complete
```sql
-- JR should be 'ranked'
SELECT status, processing_status, shortlist_notified
FROM job_requisitions
WHERE requisition_id = {JOB_ID};
```
Expected: `status = 'ranked'`, `processing_status = 'idle'`, `shortlist_notified = true`

```sql
-- semantic_analysis_reports populated
SELECT
    sar.report_id,
    sar.application_id,
    sar.semantic_score,
    sar.rank,
    LEFT(sar.shap_summary, 80)  AS shap_preview,
    sar.created_at
FROM semantic_analysis_reports sar
JOIN applications a ON a.application_id = sar.application_id
JOIN job_postings jp ON jp.posting_id = a.posting_id
WHERE jp.requisition_id = {JOB_ID}
ORDER BY sar.rank;
```
Expected: one row per ranked CV, `semantic_score` populated, `rank` starting at 1.

```sql
-- assessment_leaderboard created (shortlisted top candidates)
SELECT
    al.leaderboard_id,
    al.application_id,
    al.rank,
    al.reject,
    c.first_name || ' ' || c.last_name AS candidate_name
FROM assessment_leaderboard al
JOIN applications a ON a.application_id = al.application_id
JOIN candidates   c ON c.candidate_id = a.candidate_id
WHERE al.jr_id = {JOB_ID}
ORDER BY al.rank;
```
Expected: top N candidates listed (N = `technical_assessment_configs.candidates_to_advance` or default 20), `reject = false`.

### Step 1D — Debug: if still stuck
```sql
-- Check for pipeline error
SELECT status, processing_status FROM job_requisitions WHERE requisition_id = {JOB_ID};
-- If processing_status = 'error', reset and retry:
UPDATE job_requisitions
SET processing_status = 'idle'
WHERE requisition_id = {JOB_ID};

-- Check applications exist
SELECT COUNT(*) FROM applications a
JOIN job_postings jp ON jp.posting_id = a.posting_id
WHERE jp.requisition_id = {JOB_ID};

-- Verify cv_collection_end_date is actually in the past
SELECT cv_collection_end_date, CURRENT_DATE,
       cv_collection_end_date <= CURRENT_DATE AS trigger_condition
FROM job_requisitions WHERE requisition_id = {JOB_ID};
```

---

## Phase 2 — CV Ranking → Assessment Generation

### What the scanner does
`scan_and_dispatch_assessment` (every 60 sec) fires when:
- `status = 'ranked'`
- `shortlist_notified = true`
- A `technical_assessment_configs` row exists for this JR

On success it sets `status = 'assessment_sent'`.

### Step 2A — Check prerequisites
```sql
-- Confirm shortlist was sent and config exists
SELECT
    jr.status,
    jr.shortlist_notified,
    tac.config_id,
    tac.assessment_deadline,
    tac.pool_report
FROM job_requisitions jr
LEFT JOIN technical_assessment_configs tac ON tac.requisition_id = jr.requisition_id
WHERE jr.requisition_id = {JOB_ID};
```

If `config_id IS NULL`, create one manually (or via UI "Configure Assessment").

### Step 2B — Trigger: ensure conditions are met
```sql
-- If shortlist_notified is still false (email service issue), force it:
UPDATE job_requisitions
SET shortlist_notified = true
WHERE requisition_id = {JOB_ID} AND status = 'ranked';
```

Wait up to **60 seconds** for `scan_and_dispatch_assessment` to fire.

### Step 2C — Verify: assessments sent
```sql
SELECT status, processing_status, shortlist_notified
FROM job_requisitions
WHERE requisition_id = {JOB_ID};
```
Expected: `status = 'assessment_sent'`

```sql
-- Candidate assessments created
SELECT
    ca.candidate_assessment_id,
    ca.application_id,
    ca.status,
    ca.score,
    ca.completed_at
FROM candidate_assessments ca
JOIN applications a ON a.application_id = ca.application_id
JOIN job_postings jp ON jp.posting_id = a.posting_id
WHERE jp.requisition_id = {JOB_ID}
ORDER BY ca.candidate_assessment_id;
```

---

## Phase 3 — Assessment Deadline → Technical Interview Invitations

### What the scanner does
`scan_and_dispatch_assessment_ranking` (every 5 min) fires when:
- `job_requisitions.status = 'assessment_sent'`
- `technical_assessment_configs.assessment_deadline <= NOW()`
- `technical_assessment_configs.pool_report IS NULL`

On success: runs relative grading → sets `status = 'assessment_ranked'` → dispatches `send_interview_invitations` → sets `status = 'interview_pending'`, `interview_notified = true`, and `interview_deadline = now + 7 days`.

### Step 3A — Inspect assessment scores
```sql
-- See who completed their assessment before triggering deadline
SELECT
    ca.application_id,
    ca.status,
    ca.score,
    ca.completed_at,
    c.first_name || ' ' || c.last_name AS name
FROM candidate_assessments ca
JOIN applications a ON a.application_id = ca.application_id
JOIN candidates   c ON c.candidate_id = a.candidate_id
JOIN job_postings jp ON jp.posting_id = a.posting_id
WHERE jp.requisition_id = {JOB_ID}
ORDER BY ca.score DESC NULLS LAST;
```

### Step 3B — Trigger: push assessment deadline into the past
```sql
UPDATE technical_assessment_configs
SET
    assessment_deadline = NOW() - INTERVAL '1 minute',
    pool_report         = NULL          -- must be NULL or scanner skips it
WHERE requisition_id = {JOB_ID};

-- Also ensure JR is in the right status (should already be 'assessment_sent')
UPDATE job_requisitions
SET status = 'assessment_sent', processing_status = 'idle'
WHERE requisition_id = {JOB_ID};
```

Wait up to **5 minutes** for `scan_and_dispatch_assessment_ranking`.

### Step 3C — Verify: leaderboard ranked + interviews scheduled
```sql
-- assessment_leaderboard updated with final rank and scores
SELECT
    al.leaderboard_id,
    al.application_id,
    al.rank,
    al.score,
    al.reject,
    c.first_name || ' ' || c.last_name AS candidate_name
FROM assessment_leaderboard al
JOIN applications a ON a.application_id = al.application_id
JOIN candidates   c ON c.candidate_id = a.candidate_id
WHERE al.jr_id = {JOB_ID}
ORDER BY al.rank;
```

```sql
-- technical_interview_sessions created for top candidates
SELECT
    tis.session_id,
    tis.application_id,
    tis.status,
    tis.scheduled_at,
    tis.overall_score,
    c.first_name || ' ' || c.last_name AS candidate_name,
    c.email
FROM technical_interview_sessions tis
JOIN applications a ON a.application_id = tis.application_id
JOIN candidates   c ON c.candidate_id = a.candidate_id
JOIN job_postings jp ON jp.posting_id = a.posting_id
WHERE jp.requisition_id = {JOB_ID}
ORDER BY tis.session_id;
```
Expected: rows with `status = 'Scheduled'`, `overall_score = NULL` (not scored yet).

```sql
-- JR flags
SELECT
    status,
    processing_status,
    interview_notified,
    interview_deadline
FROM job_requisitions
WHERE requisition_id = {JOB_ID};
```
Expected: `status = 'interview_pending'`, `interview_notified = true`, `interview_deadline` set ~7 days from now.

### Step 3D — Debug
```sql
-- Check pool_report is NULL (required for scanner to fire)
SELECT pool_report, assessment_deadline, NOW() AS current_time,
       assessment_deadline <= NOW() AS deadline_passed
FROM technical_assessment_configs
WHERE requisition_id = {JOB_ID};

-- Check relative grading ran (pool_report set after grading)
SELECT pool_report IS NOT NULL AS grading_done
FROM technical_assessment_configs
WHERE requisition_id = {JOB_ID};
```

---

## Phase 4 — Technical Interview Scoring (Manual via API)

Technical interviews are conducted during the `interview_pending` window.  
HR staff submit scores via the API after each interview completes.

### Step 4A — Get candidate list (API call)
```
GET /api/v1/jobs/{JOB_ID}/technical-interview/candidates
```
Note each candidate's `id` field (this is `application_id`).

### Step 4B — Schedule an interview (optional)
```
POST /api/v1/jobs/{JOB_ID}/technical-interview/schedule
{
  "candidateId": <application_id>,
  "scheduledDate": "2026-05-01",
  "scheduledTime": "10:00",
  "interviewerName": "Jane Smith",
  "type": "Technical"
}
```

### Step 4C — Submit scores after interview
```
POST /api/v1/jobs/{JOB_ID}/technical-interview/submit-scores
{
  "candidateId": <application_id>,
  "technicalScore": 85,
  "problemSolving": 80,
  "systemDesign": 75,
  "coding": 90,
  "communication": 70,
  "feedback": "Strong coding skills, decent system design.",
  "transcript": "Interviewer: Explain recursion...\nCandidate: ..."
}
```
Including `transcript` enables CodeBERT / RoBERTa-QA AI scoring on the next ranking run.

### Step 4D — Verify scores persisted
```sql
SELECT
    tis.session_id,
    tis.application_id,
    tis.status,
    tis.overall_score,
    tis.interviewer_name,
    tis.transcript IS NOT NULL   AS has_transcript,
    tis.codebert_score,
    tis.roberta_depth_score,
    tis.nli_technical_score,
    tis.tfidf_technical_score,
    c.first_name || ' ' || c.last_name AS candidate_name
FROM technical_interview_sessions tis
JOIN applications a ON a.application_id = tis.application_id
JOIN candidates   c ON c.candidate_id = a.candidate_id
JOIN job_postings jp ON jp.posting_id = a.posting_id
WHERE jp.requisition_id = {JOB_ID}
ORDER BY tis.overall_score DESC NULLS LAST;
```

### Step 4E — Simulate "all interviews done" for time-travel
To bypass waiting for real interviews, mark sessions as Completed with fake scores:
```sql
-- WARNING: test data only — replaces any real scores
UPDATE technical_interview_sessions
SET
    status        = 'Completed',
    overall_score = (50 + RANDOM() * 50)::NUMERIC(6,2),
    ended_at      = NOW()
WHERE application_id IN (
    SELECT a.application_id
    FROM applications a
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
);
```

---

## Phase 5 — HR Interview Scoring (Manual via API)

HR interviews run in parallel with or after technical interviews, during the same `interview_pending` window. Scores are submitted manually.

### Step 5A — Get candidate list (API call)
```
GET /api/v1/jobs/{JOB_ID}/hr-interview/candidates
```

### Step 5B — Schedule an HR interview (optional)
```
POST /api/v1/jobs/{JOB_ID}/hr-interview/schedule
{
  "candidateId": <application_id>,
  "scheduledDate": "2026-05-02",
  "scheduledTime": "14:00",
  "interviewerName": "HR Manager"
}
```

### Step 5C — Submit HR scores
```
POST /api/v1/jobs/{JOB_ID}/hr-interview/submit-scores
{
  "candidateId": <application_id>,
  "cultureFit": 88,
  "communication": 82,
  "leadership": 75,
  "motivation": 90,
  "teamwork": 85,
  "feedback": "Excellent culture fit, strong communicator.",
  "transcript": "HR: Tell me about a conflict you resolved...\nCandidate: ..."
}
```
Including `transcript` enables Go-Emotions + Sentiment AI scoring in the next ranking run.

### Step 5D — Verify HR scores persisted
```sql
SELECT
    his.session_id,
    his.application_id,
    his.status,
    his.overall_score,
    his.interviewer_name,
    his.transcript IS NOT NULL   AS has_transcript,
    his.emotion_score,
    his.sentiment_score,
    his.nli_align_score,
    his.semantic_depth_score,
    c.first_name || ' ' || c.last_name AS candidate_name
FROM hr_interview_sessions his
JOIN applications a ON a.application_id = his.application_id
JOIN candidates   c ON c.candidate_id = a.candidate_id
JOIN job_postings jp ON jp.posting_id = a.posting_id
WHERE jp.requisition_id = {JOB_ID}
ORDER BY his.overall_score DESC NULLS LAST;
```

### Step 5E — Simulate HR interviews for time-travel
```sql
UPDATE hr_interview_sessions
SET
    status        = 'Completed',
    overall_score = (50 + RANDOM() * 50)::NUMERIC(6,2),
    ended_at      = NOW()
WHERE application_id IN (
    SELECT a.application_id
    FROM applications a
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
);
```

---

## Phase 6 — Interview Deadline → Final Ranking

### What the scanner does
`scan_and_dispatch_final_ranking` (every 5 min) fires when:
- `job_requisitions.status = 'interview_pending'`
- `job_requisitions.interview_deadline <= NOW()`
- `job_requisitions.processing_status IN ('idle', 'error')`
- No `final_rankings` row exists for this posting yet

The `compute_final_ranking` worker runs the 6-node LangGraph pipeline:  
`gather_applications → compute_weights → score_candidates → sort_and_rank → persist_rankings → send_decisions`

Weights: **Screening 20% / Assessment 25% / Technical Interview 30% / HR Interview 25%**

On success: `jr.status = 'ranking_complete'`

### Step 6A — Trigger: push interview_deadline into the past
```sql
UPDATE job_requisitions
SET
    interview_deadline = NOW() - INTERVAL '1 minute',
    processing_status  = 'idle',
    status             = 'interview_pending'   -- must still be interview_pending
WHERE requisition_id = {JOB_ID};
```

Wait up to **5 minutes** for `scan_and_dispatch_final_ranking`.

Alternatively, trigger immediately via the API:
```
POST /api/v1/jobs/{JOB_ID}/trigger-ranking
```

### Step 6B — Monitor pipeline progress
```sql
-- Watch JR status change
SELECT
    status,
    processing_status,
    interview_deadline,
    NOW() AS current_time
FROM job_requisitions
WHERE requisition_id = {JOB_ID};
```
During run: `processing_status = 'processing'`  
After run: `status = 'ranking_complete'`, `processing_status = 'idle'`

### Step 6C — Verify final_rankings populated
```sql
SELECT
    fr.ranking_id,
    fr.application_id,
    fr.final_rank,
    fr.weighted_total_score,
    fr.semantic_score,
    fr.assessment_score,
    fr.technical_interview_score,
    fr.hr_interview_score,
    fr.final_recommendation,
    fr.final_status,
    fr.red_flag,
    fr.red_flag_reason,
    fr.shap_summary IS NOT NULL  AS has_shap,
    c.first_name || ' ' || c.last_name AS candidate_name
FROM final_rankings fr
JOIN applications a ON a.application_id = fr.application_id
JOIN candidates   c ON c.candidate_id = a.candidate_id
WHERE fr.posting_id = (
    SELECT posting_id FROM job_postings WHERE requisition_id = {JOB_ID} LIMIT 1
)
ORDER BY fr.final_rank;
```
Expected:
- One row per candidate who had a technical interview session
- `weighted_total_score` = 0.20×semantic + 0.25×assessment + 0.30×technical + 0.25×hr  
- `final_rank` starts at 1 (top candidate)
- `final_recommendation` ∈ {`'Top Candidate'`, `'Strong Hire'`, `'Hire'`, `'Maybe'`, `'No Hire'`}

### Step 6D — Check red-flag detection
```sql
-- RF-1: Interview Collapse (CV star who bombed tech interview)
SELECT
    fr.final_rank,
    fr.semantic_score,
    fr.technical_interview_score,
    fr.red_flag,
    fr.red_flag_reason,
    c.first_name || ' ' || c.last_name AS candidate_name
FROM final_rankings fr
JOIN applications a ON a.application_id = fr.application_id
JOIN candidates   c ON c.candidate_id = a.candidate_id
WHERE fr.posting_id = (
    SELECT posting_id FROM job_postings WHERE requisition_id = {JOB_ID} LIMIT 1
)
AND fr.red_flag = true
ORDER BY fr.final_rank;
```

Red-flag rules (from `score_candidates.py`):
- **RF-1 Interview Collapse:** `semantic_score >= 80 AND technical_interview_score < 40`
- **RF-2 Cheating Suspicion:** `assessment_score − technical_interview_score > 30`

To create a test RF-1 candidate:
```sql
-- Seed artificial scores on one session to trigger RF-1
UPDATE final_rankings
SET
    semantic_score            = 85.00,
    technical_interview_score = 35.00,
    red_flag                  = true,
    red_flag_reason           = 'RF-1: Interview Collapse'
WHERE application_id = (
    SELECT al.application_id FROM assessment_leaderboard al
    WHERE al.jr_id = {JOB_ID}
    ORDER BY al.rank LIMIT 1
);
```

### Step 6E — Verify SHAP explanation
```sql
SELECT
    fr.final_rank,
    fr.shap_json,
    fr.shap_summary,
    c.first_name || ' ' || c.last_name AS candidate_name
FROM final_rankings fr
JOIN applications a ON a.application_id = fr.application_id
JOIN candidates   c ON c.candidate_id = a.candidate_id
WHERE fr.posting_id = (
    SELECT posting_id FROM job_postings WHERE requisition_id = {JOB_ID} LIMIT 1
)
AND fr.shap_summary IS NOT NULL
ORDER BY fr.final_rank
LIMIT 5;
```

### Step 6F — Verify via API (golden path)
```
GET /api/v1/jobs/{JOB_ID}/final-ranking
```
Should return array of candidates ordered by `finalRank` with `overallScore`, `recommendation`, `redFlag`.

```
GET /api/v1/jobs/{JOB_ID}/final-ranking/{candidateId}/shap-report
```
Should return `shapScreening`, `shapAssessment`, `shapTechInterview`, `shapHrInterview` φ values.

### Step 6G — Debug: if final_rankings is empty after run
```sql
-- Confirm technical_interview_sessions exist
SELECT COUNT(*) AS sessions,
       COUNT(overall_score) AS scored_sessions
FROM technical_interview_sessions tis
JOIN applications a ON a.application_id = tis.application_id
JOIN job_postings jp ON jp.posting_id = a.posting_id
WHERE jp.requisition_id = {JOB_ID};

-- Check if FinalRanking already existed (anti-loop guard blocks re-run)
SELECT COUNT(*) FROM final_rankings
WHERE posting_id = (SELECT posting_id FROM job_postings WHERE requisition_id = {JOB_ID});

-- Confirm processing_status returned to idle (not stuck 'processing')
SELECT status, processing_status FROM job_requisitions WHERE requisition_id = {JOB_ID};
-- If stuck: UPDATE job_requisitions SET processing_status='idle' WHERE requisition_id={JOB_ID};
```

---

## Acceptance Checklist

Run this block after Phase 6 completes. All conditions must be true.

```sql
WITH
  jr_row AS (
    SELECT * FROM job_requisitions WHERE requisition_id = {JOB_ID}
  ),
  posting_row AS (
    SELECT * FROM job_postings WHERE requisition_id = {JOB_ID} LIMIT 1
  ),
  sar_count AS (
    SELECT COUNT(*) AS n FROM semantic_analysis_reports sar
    JOIN applications a ON a.application_id = sar.application_id
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
  ),
  tis_count AS (
    SELECT COUNT(*) AS n, COUNT(overall_score) AS scored FROM technical_interview_sessions tis
    JOIN applications a ON a.application_id = tis.application_id
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
  ),
  his_count AS (
    SELECT COUNT(*) AS n, COUNT(overall_score) AS scored FROM hr_interview_sessions his
    JOIN applications a ON a.application_id = his.application_id
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
  ),
  fr_count AS (
    SELECT COUNT(*) AS n, COUNT(weighted_total_score) AS scored,
           COUNT(CASE WHEN red_flag THEN 1 END) AS red_flags
    FROM final_rankings WHERE posting_id = (SELECT posting_id FROM posting_row)
  )
SELECT
  (SELECT status FROM jr_row) = 'ranking_complete'           AS "✓ JR status=ranking_complete",
  (SELECT processing_status FROM jr_row) = 'idle'            AS "✓ processing_status=idle",
  (SELECT shortlist_notified FROM jr_row)                    AS "✓ shortlist_notified=true",
  (SELECT interview_notified FROM jr_row)                    AS "✓ interview_notified=true",
  (SELECT n FROM sar_count) > 0                              AS "✓ semantic_analysis_reports populated",
  (SELECT n FROM tis_count) > 0                              AS "✓ technical_interview_sessions created",
  (SELECT n FROM his_count) > 0                              AS "✓ hr_interview_sessions created",
  (SELECT n FROM fr_count) > 0                              AS "✓ final_rankings populated",
  (SELECT scored FROM fr_count) = (SELECT n FROM fr_count)  AS "✓ all candidates scored",
  (SELECT red_flags FROM fr_count) >= 0                     AS "✓ red-flag check ran";
```

**All columns must show `true`** for a clean end-to-end pass.

---

## Score Reference

| Column (final_rankings) | Source | Weight |
|---|---|---|
| `semantic_score` | `semantic_analysis_reports.semantic_score` | 20% |
| `assessment_score` | `assessment_leaderboard.score` | 25% |
| `technical_interview_score` | `technical_interview_sessions.overall_score` | 30% |
| `hr_interview_score` | `hr_interview_sessions.overall_score` | 25% |
| `weighted_total_score` | Σ above × weights | — |

**`final_recommendation` thresholds:**

| Score | Label |
|---|---|
| ≥ 85 | `Top Candidate` |
| ≥ 75 | `Strong Hire` |
| ≥ 60 | `Hire` |
| ≥ 45 | `Maybe` |
| < 45 | `No Hire` |

---

## Full Reset / Rollback

Use this to wipe a test JR back to zero and start again.

```sql
-- ⚠️  DESTRUCTIVE — test data only
BEGIN;

-- Identify the posting
WITH posting AS (
    SELECT posting_id FROM job_postings WHERE requisition_id = {JOB_ID}
)

-- 1. Final rankings
DELETE FROM final_rankings
WHERE posting_id IN (SELECT posting_id FROM posting);

-- 2. HR interview sessions + scores
DELETE FROM hr_interview_scores
WHERE session_id IN (
    SELECT his.session_id FROM hr_interview_sessions his
    JOIN applications a ON a.application_id = his.application_id
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
);
DELETE FROM hr_interview_sessions
WHERE application_id IN (
    SELECT a.application_id FROM applications a
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
);

-- 3. Technical interview sessions + scores
DELETE FROM technical_interview_scores
WHERE session_id IN (
    SELECT tis.session_id FROM technical_interview_sessions tis
    JOIN applications a ON a.application_id = tis.application_id
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
);
DELETE FROM technical_interview_sessions
WHERE application_id IN (
    SELECT a.application_id FROM applications a
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
);

-- 4. Assessment leaderboard + candidate assessments
DELETE FROM assessment_leaderboard WHERE jr_id = {JOB_ID};
DELETE FROM candidate_assessments
WHERE application_id IN (
    SELECT a.application_id FROM applications a
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
);

-- 5. Assessment pool report
UPDATE technical_assessment_configs
SET pool_report = NULL,
    assessment_deadline = NOW() + INTERVAL '7 days'
WHERE requisition_id = {JOB_ID};

-- 6. CV screening reports
DELETE FROM semantic_analysis_reports
WHERE application_id IN (
    SELECT a.application_id FROM applications a
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = {JOB_ID}
);

-- 7. Reset JR to clean starting state
UPDATE job_requisitions
SET
    status               = 'Active',
    processing_status    = 'idle',
    shortlist_notified   = false,
    interview_notified   = false,
    interview_deadline   = NULL,
    cv_collection_end_date = CURRENT_DATE + INTERVAL '30 days'
WHERE requisition_id = {JOB_ID};

COMMIT;

-- Confirm reset
SELECT status, processing_status, shortlist_notified, interview_notified, interview_deadline
FROM job_requisitions WHERE requisition_id = {JOB_ID};
```

After rollback, return to [Phase 1](#phase-1--cv-collection--cv-ranking) and start again.
