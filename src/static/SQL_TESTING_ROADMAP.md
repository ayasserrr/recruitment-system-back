# SQL Time-Travel Testing Roadmap
# Full End-to-End Manual Test — PostgreSQL Queries

---

## ⚠️ Before Anything: Clean Slate

**Yes — you can run this. It wipes every table except Alembic migration history.**
Only run this on your LOCAL / DEV database. Never on production.

```sql
-- WIPE EVERYTHING (local dev only)
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
        AND tablename <> 'alembic_version'
    ) LOOP
        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE';
    END LOOP;
END $$;
```

After running this, you'll start with an empty database. Run `alembic upgrade head` if you need
to re-apply migrations (you don't — the tables still exist, only the data was wiped).

---

## Lookup Helper Queries
Run these first to find the IDs you need for the rest of the roadmap.

```sql
-- 1. Find your job's posting_id and current status
SELECT
    jr.requisition_id   AS job_id,
    jr.status           AS jr_status,
    jr.processing_status,
    jp.posting_id,
    jp.state            AS posting_state
FROM job_requisitions jr
LEFT JOIN job_postings jp ON jp.requisition_id = jr.requisition_id
WHERE jr.requisition_id = {job_id};

-- 2. Find all candidates + their application_id for this job
SELECT
    c.candidate_id,
    c.first_name,
    c.last_name,
    c.email,
    a.application_id,
    a.status           AS app_status
FROM applications a
JOIN job_postings jp ON jp.posting_id = a.posting_id
JOIN candidates c ON c.candidate_id = a.candidate_id
WHERE jp.requisition_id = {job_id}
ORDER BY a.application_id;

-- 3. Find assessment_id for a candidate (run after Phase 2 setup)
SELECT ca.assessment_id, ca.status, ca.total_score, ca.passed
FROM candidate_assessments ca
JOIN applications a ON a.application_id = ca.application_id
JOIN job_postings jp ON jp.posting_id = a.posting_id
WHERE jp.requisition_id = {job_id}
AND a.candidate_id = {candidate_id};

-- 4. Find all assessment answers for a candidate (run after Phase 2)
SELECT aa.answer_id, aa.question_id, aa.ensemble_score, aa.nli_score
FROM assessment_answers aa
JOIN candidate_assessments ca ON ca.assessment_id = aa.assessment_id
JOIN applications a ON a.application_id = ca.application_id
WHERE a.candidate_id = {candidate_id};

-- 5. Find technical interview session for a candidate
SELECT tis.session_id, tis.status, tis.overall_score, tis.codebert_score
FROM technical_interview_sessions tis
JOIN applications a ON a.application_id = tis.application_id
WHERE a.candidate_id = {candidate_id};

-- 6. Find HR interview session for a candidate
SELECT his.session_id, his.status, his.overall_score, his.emotion_score
FROM hr_interview_sessions his
JOIN applications a ON a.application_id = his.application_id
WHERE a.candidate_id = {candidate_id};
```

---

## Phase 0 — Create Job & Upload CVs (UI)

1. **Login** → go to dashboard → click **"Create Job"**
2. Fill in job title, department, required skills → submit
3. Go to the job page → click **"Post Job"** to create the posting
4. Upload CVs from the UI (use the public apply link or bulk upload)
5. Come back here and run the Lookup Helpers to get your IDs

After uploading CVs you'll have rows in `applications`. Each row has an `application_id`.
Write down your `{job_id}` (= `requisition_id`) and all `{candidate_id}` values.

---

## Phase 1 — CV Screening → Fast-Forward to Assessment

### Step 1A: Fake the semantic AI scores (skips Celery processing)

Run once per candidate. Replace `{application_id}` with each candidate's value.

```sql
-- INSERT a semantic report if it doesn't exist yet
INSERT INTO semantic_analysis_reports (
    application_id,
    match_percentage,
    ai_insights,
    recommendation_summary,
    strengths,
    weaknesses,
    rank_in_pool
)
VALUES (
    {application_id},
    82.50,                          -- score 0–100 (this is what shows in the UI)
    'Strong match on required Python and FastAPI skills.',
    'Recommended for assessment stage.',
    'FastAPI, PostgreSQL, Docker',
    'Limited cloud experience',
    1                               -- rank among all candidates for this job
)
ON CONFLICT (application_id) DO UPDATE
    SET match_percentage      = 82.50,
        ai_insights           = 'Strong match on required Python and FastAPI skills.',
        recommendation_summary = 'Recommended for assessment stage.',
        strengths             = 'FastAPI, PostgreSQL, Docker',
        weaknesses            = 'Limited cloud experience',
        rank_in_pool          = 1;
```

Repeat with different scores for each candidate. Typical score spread for realistic data:
- Candidate 1: 88.50 (Excellent)
- Candidate 2: 74.00 (Very Good)
- Candidate 3: 61.00 (Good)
- Candidate 4: 44.50 (Average)

### Step 1B: Close CV Collection → advance to Assessment stage

```sql
-- Set CV collection end date to yesterday → marks the stage as closed
UPDATE job_requisitions
SET
    cv_collection_end_date = CURRENT_DATE - INTERVAL '1 day',
    status                 = 'ranked',       -- tells the backend: CVs are ranked
    processing_status      = 'idle',         -- prevents Celery re-trigger
    updated_at             = NOW()
WHERE requisition_id = {job_id};
```

**What the UI shows now:** Job status = "CV Collection" (the Celery scanner will pick up
`ranked` status and may dispatch the assessment phase automatically, depending on your config).

**To immediately see it as "In Progress" in the UI:**
```sql
UPDATE job_requisitions
SET status = 'assessment_sent', updated_at = NOW()
WHERE requisition_id = {job_id};
```

---

## Phase 2 — Assessment → Fast-Forward to Technical Interview

### Step 2A: Make sure candidates have assessment records

If the assessment invitations were not sent from the UI, create the records manually.
First get the `config_id`:

```sql
SELECT config_id FROM technical_assessment_configs WHERE requisition_id = {job_id};
-- note the config_id → use it below as {config_id}
```

Then for each candidate:

```sql
-- Create the assessment record (skip if already exists from UI)
INSERT INTO candidate_assessments (
    application_id,
    config_id,
    status,
    started_at,
    submitted_at,
    total_score,
    passing_score,
    passed
)
VALUES (
    {application_id},
    {config_id},
    'Submitted',
    NOW() - INTERVAL '2 hours',
    NOW() - INTERVAL '1 hour',
    84.50,    -- total score 0–100
    70.00,    -- passing threshold
    TRUE
)
ON CONFLICT (application_id) DO UPDATE
    SET status       = 'Submitted',
        submitted_at = NOW() - INTERVAL '1 hour',
        total_score  = 84.50,
        passed       = TRUE;
```

### Step 2B: Fill ensemble scores on all answers for that assessment

Get `{assessment_id}` from the Lookup Helper #3 above, then:

```sql
-- Fill AI ensemble scores on every answer row for this assessment
UPDATE assessment_answers
SET
    nli_score            = 0.8720,   -- DeBERTa NLI entailment (0–1)
    semantic_bge_score   = 0.8150,   -- BGE cosine similarity (0–1)
    semantic_mpnet_score = 0.7930,   -- MPNet cosine similarity (0–1)
    roberta_qa_score     = 0.8430,   -- RoBERTa QA blend (0–1)
    tfidf_score          = 0.6540,   -- TF-IDF coverage (0–1)
    ensemble_score       = 0.8120,   -- composite (0–1)
    score_awarded        = 8.50,     -- raw score for this answer
    shap_json            = '{"nli": 0.12, "bge": 0.09, "mpnet": 0.08, "roberta": 0.11, "tfidf": -0.03}'
WHERE assessment_id = {assessment_id};
```

### Step 2C: Create the assessment leaderboard entry

This is what the assessment candidates page reads from.

```sql
INSERT INTO assessment_leaderboards (
    jr_id,
    candidate_id,
    application_id,
    assessment_id,
    rank,
    final_score,
    segment,
    reject,
    avg_depth
)
VALUES (
    {job_id},
    {candidate_id},
    {application_id},
    {assessment_id},
    1,        -- rank (1 = best)
    84.50,    -- final_score 0–100 (this is what the assessment candidates table shows)
    'Top',    -- segment label
    FALSE,
    0.8120    -- avg_depth (average ensemble score)
)
ON CONFLICT DO NOTHING;
```

### Step 2D: Create the assessment report (populates shapSummary in the UI)

```sql
INSERT INTO assessment_reports (
    assessment_id,
    overall_score,
    ai_feedback,
    strengths,
    weaknesses,
    rank_in_pool,
    recommendation,
    shap_summary
)
VALUES (
    {assessment_id},
    84.50,
    'Strong performance across all question types. Excels in algorithm design.',
    'Data structures, time complexity analysis',
    'Minor gaps in system design questions',
    1,
    'Advance to Technical Interview',
    'BGE semantic model contributes most (φ=+0.09). RoBERTa QA confirms precise answers (φ=+0.11). TF-IDF slightly below average (φ=-0.03).'
)
ON CONFLICT (assessment_id) DO UPDATE
    SET overall_score = 84.50,
        shap_summary  = 'BGE semantic model contributes most (φ=+0.09). RoBERTa QA confirms precise answers (φ=+0.11). TF-IDF slightly below average (φ=-0.03).';
```

### Step 2E: Close Assessment → advance to Technical Interview

```sql
-- Set assessment deadline to the past
UPDATE technical_assessment_configs
SET assessment_deadline = NOW() - INTERVAL '1 day'
WHERE requisition_id = {job_id};

-- Advance the JR status
UPDATE job_requisitions
SET
    status            = 'assessment_ranked',
    processing_status = 'idle',
    updated_at        = NOW()
WHERE requisition_id = {job_id};
```

**UI now shows:** Assessment stage = "completed", Technical Interview = "active"

---

## Phase 3 — Technical Interview → Fast-Forward to HR Interview

### Step 3A: Create interview config if it doesn't exist

```sql
INSERT INTO technical_interview_configs (
    requisition_id,
    interview_type,
    duration_minutes,
    scoring_system,
    candidates_to_advance
)
VALUES ({job_id}, 'ai-conducted', 45, '1-10', 5)
ON CONFLICT (requisition_id) DO NOTHING;

-- Get the config_id you just created:
SELECT config_id FROM technical_interview_configs WHERE requisition_id = {job_id};
-- note it as {tech_config_id}
```

### Step 3B: Insert a completed session with transcript + AI ensemble scores

Run for each candidate:

```sql
INSERT INTO technical_interview_sessions (
    application_id,
    config_id,
    status,
    scheduled_at,
    started_at,
    ended_at,
    interviewer_name,
    overall_score,
    mode,
    transcript,
    codebert_score,
    roberta_depth_score,
    nli_technical_score,
    tfidf_technical_score,
    shap_json,
    shap_summary,
    summary,
    recommendation
)
VALUES (
    {application_id},
    {tech_config_id},
    'Completed',
    NOW() - INTERVAL '2 days',
    NOW() - INTERVAL '2 days' + INTERVAL '5 minutes',
    NOW() - INTERVAL '2 days' + INTERVAL '50 minutes',
    'Ahmed Karim',
    8.20,   -- overall human score (average of 5 sub-scores, 1–10 scale)
    'ai-conducted',

    -- Mock transcript (the AI models process this text)
    'Interviewer: Can you explain how you would design a rate limiter?
Candidate: I would use a sliding window algorithm backed by Redis. The key insight is using a sorted set where each entry is a timestamp. To check the limit, count entries within the last N seconds using ZRANGEBYSCORE, then add the current timestamp. This gives O(log N) per request.
Interviewer: What is the time complexity of merge sort?
Candidate: O(n log n) in all cases — best, average, and worst. Space complexity is O(n) because of the auxiliary array.',

    -- AI ensemble scores (0–1 scale, populated by tech_interview_node)
    0.8120,   -- codebert_score    (CodeBERT semantic depth, 40% weight)
    0.7430,   -- roberta_depth_score (RoBERTa-QA extraction, 30% weight)
    0.8690,   -- nli_technical_score (DeBERTa NLI alignment, 20% weight)
    0.6540,   -- tfidf_technical_score (TF-IDF coverage, 10% weight)

    -- SHAP JSON (per-model contributions)
    '{"codebert": 0.124, "roberta": 0.073, "nli": 0.074, "tfidf": 0.015}',

    -- SHAP narrative (shows in the UI drawer)
    'CodeBERT detects strong semantic depth in system design answer (φ=+0.124). RoBERTa-QA confirms precise technical extraction (φ=+0.073). DeBERTa NLI strongly aligns with job requirements (φ=+0.074). TF-IDF keyword coverage is adequate (φ=+0.015).',

    -- JSON summary (stores human sub-scores for the UI)
    '{"sub_scores": {"technicalScore": 8.5, "problemSolving": 8.0, "systemDesign": 7.5, "coding": 9.0, "communication": 8.0}, "feedback": "Strong system design skills. Excellent coding under pressure."}',

    'Advance'
)
ON CONFLICT (application_id) DO UPDATE
    SET status               = 'Completed',
        overall_score        = 8.20,
        transcript           = EXCLUDED.transcript,
        codebert_score       = 0.8120,
        roberta_depth_score  = 0.7430,
        nli_technical_score  = 0.8690,
        tfidf_technical_score = 0.6540,
        shap_json            = EXCLUDED.shap_json,
        shap_summary         = EXCLUDED.shap_summary,
        summary              = EXCLUDED.summary,
        ended_at             = NOW();
```

**To test RF-1 (interview collapse red flag)** — use this for one candidate
(high CV score but terrible interview):

```sql
-- Candidate with interview collapse: semanticScore ≥ 80 AND technicalScore < 40
UPDATE technical_interview_sessions
SET
    overall_score         = 3.20,   -- very low interview score
    codebert_score        = 0.2800,
    roberta_depth_score   = 0.1900,
    nli_technical_score   = 0.3100,
    tfidf_technical_score = 0.2200,
    status                = 'Completed',
    summary               = '{"sub_scores": {"technicalScore": 3.0, "problemSolving": 3.5, "systemDesign": 2.5, "coding": 4.0, "communication": 3.5}, "feedback": "Could not answer basic algorithm questions."}'
WHERE application_id = {application_id_of_red_flag_candidate};
```

### Step 3C: Advance to HR Interview stage

```sql
UPDATE job_requisitions
SET
    status            = 'interview_pending',
    processing_status = 'idle',
    updated_at        = NOW()
WHERE requisition_id = {job_id};
```

---

## Phase 4 — HR Interview → Fast-Forward to Final Ranking

### Step 4A: Create HR interview config if it doesn't exist

```sql
INSERT INTO hr_interview_configs (
    requisition_id,
    interview_type,
    duration_minutes,
    scoring_system,
    candidates_to_advance
)
VALUES ({job_id}, 'human', 30, '1-10', 3)
ON CONFLICT (requisition_id) DO NOTHING;

SELECT config_id FROM hr_interview_configs WHERE requisition_id = {job_id};
-- note as {hr_config_id}
```

### Step 4B: Insert completed HR session with AI ensemble scores

```sql
INSERT INTO hr_interview_sessions (
    application_id,
    config_id,
    status,
    scheduled_at,
    started_at,
    ended_at,
    interviewer_name,
    overall_score,
    transcript,
    emotion_score,
    sentiment_score,
    nli_align_score,
    semantic_depth_score,
    shap_json,
    shap_summary,
    summary,
    recommendation
)
VALUES (
    {application_id},
    {hr_config_id},
    'Completed',
    NOW() - INTERVAL '1 day',
    NOW() - INTERVAL '1 day' + INTERVAL '5 minutes',
    NOW() - INTERVAL '1 day' + INTERVAL '35 minutes',
    'Sara Nour',
    8.40,   -- overall human score (1–10)

    -- Mock HR transcript
    'Interviewer: Tell me about a time you led a team through a difficult project.
Candidate: In my previous role, we had a critical deadline for a payment integration. I organized daily standups, broke down blockers immediately, and kept stakeholders informed. We delivered two days early despite the team being understaffed. It was a lesson in clear communication and prioritization.
Interviewer: How do you handle conflict between team members?
Candidate: I believe in addressing it directly but privately first. I listen to both sides, identify the root cause — which is often a misunderstanding or unclear ownership — and mediate a concrete resolution.',

    -- AI ensemble scores (0–1 scale, populated by hr_analysis_node)
    0.8210,   -- emotion_score       (Go-Emotions positivity, 35% weight)
    0.7740,   -- sentiment_score     (RoBERTa professionalism, 30% weight)
    0.8030,   -- nli_align_score     (DeBERTa values alignment, 20% weight)
    0.7550,   -- semantic_depth_score (BGE depth vs JD, 15% weight)

    '{"emotion": 0.098, "sentiment": 0.082, "nli": 0.061, "semantic": 0.038}',

    'Go-Emotions detects high positivity and confidence (φ=+0.098). Sentiment analysis confirms professional, composed tone (φ=+0.082). DeBERTa NLI strongly aligns with company values (φ=+0.061). BGE semantic depth is above average (φ=+0.038).',

    '{"sub_scores": {"cultureFit": 9.0, "communication": 8.5, "leadership": 7.5, "motivation": 9.0, "teamwork": 8.0}, "feedback": "Outstanding cultural fit. Highly motivated and collaborative."}',

    'Strong Hire'
)
ON CONFLICT (application_id) DO UPDATE
    SET status               = 'Completed',
        overall_score        = 8.40,
        transcript           = EXCLUDED.transcript,
        emotion_score        = 0.8210,
        sentiment_score      = 0.7740,
        nli_align_score      = 0.8030,
        semantic_depth_score = 0.7550,
        shap_json            = EXCLUDED.shap_json,
        shap_summary         = EXCLUDED.shap_summary,
        summary              = EXCLUDED.summary,
        ended_at             = NOW();
```

---

## Phase 5 — Final Ranking (Skip the Pipeline, Inject Directly)

You have two options here:

### Option A: Use the API trigger (recommended — uses the real pipeline)

Call from the UI or via curl:
```bash
curl -X POST http://localhost:8000/api/v1/jobs/{job_id}/trigger-ranking \
  -H "Authorization: Bearer {your_token}"
```

The pipeline will read all the scores you inserted above and compute `weighted_total_score`,
SHAP values, and red flags automatically.

### Option B: Inject directly into final_rankings (full fast-forward)

First get `{posting_id}`:
```sql
SELECT posting_id FROM job_postings WHERE requisition_id = {job_id};
```

Then for each candidate — this is the exact row the Final Ranking page reads:

```sql
-- Normal candidate (no red flag)
INSERT INTO final_rankings (
    application_id,
    posting_id,
    semantic_score,
    assessment_score,
    technical_interview_score,
    hr_interview_score,
    weighted_total_score,
    final_rank,
    final_recommendation,
    final_status,
    red_flag,
    red_flag_reason,
    shap_json,
    shap_summary
)
VALUES (
    {application_id},
    {posting_id},
    88.50,   -- Stage 1: CV screening score (0–100)
    84.50,   -- Stage 2: assessment score   (0–100)
    82.00,   -- Stage 3: tech interview     (0–100)
    84.00,   -- Stage 4: HR interview       (0–100)

    -- weighted_total = 0.20×88.50 + 0.25×84.50 + 0.30×82.00 + 0.25×84.00
    -- = 17.70 + 21.125 + 24.60 + 21.00 = 84.43
    84.43,

    1,        -- final_rank (1 = best)
    'Strong Hire',
    'Selected',
    FALSE,    -- no red flag
    NULL,

    '{"screening": 0.076, "assessment": 0.044, "tech_interview": 0.096, "hr_interview": 0.085}',

    'Technical interview is the strongest contributor (φ=+0.096). CV screening above average (φ=+0.076). HR interview adds positive weight (φ=+0.085). Assessment is solid (φ=+0.044).'
)
ON CONFLICT (application_id) DO UPDATE
    SET semantic_score             = 88.50,
        assessment_score           = 84.50,
        technical_interview_score  = 82.00,
        hr_interview_score         = 84.00,
        weighted_total_score       = 84.43,
        final_rank                 = 1,
        final_recommendation       = 'Strong Hire',
        final_status               = 'Selected',
        red_flag                   = FALSE,
        red_flag_reason            = NULL,
        shap_json                  = EXCLUDED.shap_json,
        shap_summary               = EXCLUDED.shap_summary;
```

**RF-1 candidate (Interview Collapse: screening ≥ 80 AND tech < 40):**
```sql
INSERT INTO final_rankings (
    application_id, posting_id,
    semantic_score, assessment_score, technical_interview_score, hr_interview_score,
    weighted_total_score, final_rank,
    final_recommendation, final_status,
    red_flag, red_flag_reason,
    shap_json, shap_summary
)
VALUES (
    {rf1_application_id}, {posting_id},
    91.00, 88.00, 32.00, 70.00,

    -- 0.20×91 + 0.25×88 + 0.30×32 + 0.25×70 = 18.2 + 22.0 + 9.6 + 17.5 = 67.30
    -- but red_flag penalty: score × 0.5 = 33.65 displayed for hire probability
    67.30,

    4, 'No Hire', 'Rejected',
    TRUE,
    'RF-1 — Interview Collapse: Strong CV score (91.0) but Technical Interview score (32.0) is critically below threshold (40). Possible performance anxiety or misrepresentation.',

    '{"screening": 0.205, "assessment": 0.095, "tech_interview": -0.054, "hr_interview": 0.100}',
    'High CV score undermined by critical interview failure (φ=−0.054). Red flag applied — hire probability reduced by 50%.'
)
ON CONFLICT (application_id) DO UPDATE
    SET red_flag        = TRUE,
        red_flag_reason = EXCLUDED.red_flag_reason,
        final_rank      = 4,
        final_recommendation = 'No Hire';
```

**RF-2 candidate (Cheating Suspicion: assessment − tech > 30 pts):**
```sql
INSERT INTO final_rankings (
    application_id, posting_id,
    semantic_score, assessment_score, technical_interview_score, hr_interview_score,
    weighted_total_score, final_rank,
    final_recommendation, final_status,
    red_flag, red_flag_reason,
    shap_json, shap_summary
)
VALUES (
    {rf2_application_id}, {posting_id},
    75.00, 93.00, 41.00, 68.00,

    -- 0.20×75 + 0.25×93 + 0.30×41 + 0.25×68 = 15.0 + 23.25 + 12.30 + 17.0 = 67.55
    67.55,

    5, 'No Hire', 'Rejected',
    TRUE,
    'RF-2 — Cheating Suspicion: Assessment score (93.0) minus Technical Interview score (41.0) = 52.0 points. Gap exceeds the 30-point threshold, suggesting possible external assistance during the assessment.',

    '{"screening": 0.050, "assessment": 0.215, "tech_interview": -0.044, "hr_interview": 0.090}',
    'Extreme gap between Assessment (93) and Technical Interview (41) triggered cheating flag (φ gap = 0.259). Red flag applied.'
)
ON CONFLICT (application_id) DO UPDATE
    SET red_flag        = TRUE,
        red_flag_reason = EXCLUDED.red_flag_reason,
        final_rank      = 5,
        final_recommendation = 'No Hire';
```

### Step 5B: Set job status to Final Stage (makes UI show the ranking page)

```sql
UPDATE job_requisitions
SET
    status            = 'ranking_complete',
    processing_status = 'idle',
    updated_at        = NOW()
WHERE requisition_id = {job_id};
```

**The UI now shows:** Job status = "Final Stage". The Final Ranking page will display all
candidates ranked by `weighted_total_score` DESC, with red flag badges on RF-1 and RF-2 candidates.

---

## Complete Verification Checklist

After running all phases, open the UI and verify each page:

### ✅ Stage 1 — Screening page (`/jobs/{id}/screening`)
- [ ] Stats cards show correct `totalCandidates`, `highMatch`, `avgScore`
- [ ] Each candidate shows correct score and match label (Excellent / Very Good / etc.)
- [ ] Candidate drawer shows skills, education, experience

### ✅ Stage 2 — Assessment page (`/jobs/{id}/assessment`)
- [ ] Overview shows `completed` count and `avgScore`
- [ ] Candidates table shows `score`, `status: passed/failed`, `timeSpent`
- [ ] `shapSummary` shows in the AI Insight tooltip/drawer

### ✅ Stage 3 — Technical Interview page (`/jobs/{id}/technical-interview`)
- [ ] Candidates show `overall` score and `status: Completed`
- [ ] `codebertScore`, `robertaDepthScore`, `nliTechnicalScore`, `tfidfTechnicalScore` are visible
- [ ] `hasTranscript: true` shown correctly
- [ ] `shapSummary` visible in the AI insight panel

### ✅ Stage 4 — HR Interview page (`/jobs/{id}/hr-interview`)
- [ ] Candidates show `cultureFit`, `communication`, `leadership`, `motivation`, `teamwork`
- [ ] `emotionScore`, `sentimentScore`, `nliAlignScore`, `semanticDepthScore` visible
- [ ] `hasTranscript: true` shown

### ✅ Stage 5 — Final Ranking page (`/jobs/{id}/final-ranking`)
- [ ] Candidates ordered by `finalRank` (1 first)
- [ ] `overallScore`, `semanticScore`, `assessmentScore`, `technicalScore`, `hrScore` all visible
- [ ] RF-1 candidate shows red "No Hire" badge with reason tooltip
- [ ] RF-2 candidate shows red "No Hire" badge with cheating reason
- [ ] Click "View SHAP Report" → drawer opens with 4 φ values (positive = green, negative = red)
- [ ] "Shortlist" button → `applicationStatus` changes to `"Shortlisted"`
- [ ] "Send Offer" button → fills form → status changes to `"Offer Extended"`

---

## Quick Score Reference

Use these values for realistic test data:

| Scenario | semantic | assessment | tech | hr | weighted total | result |
|---|---|---|---|---|---|---|
| Top Candidate | 92 | 89 | 91 | 88 | **90.20** | Strong Hire |
| Good Candidate | 78 | 75 | 73 | 76 | **75.25** | Hire |
| Borderline | 65 | 63 | 58 | 62 | **61.75** | Maybe |
| RF-1 (Collapse) | 88 | 85 | 32 | 71 | **67.85** | No Hire + 🚩 |
| RF-2 (Cheat) | 72 | 94 | 40 | 66 | **65.90** | No Hire + 🚩 |

**Manual weight check formula:**
```
weighted_total = (semantic × 0.20) + (assessment × 0.25) + (tech × 0.30) + (hr × 0.25)
```
