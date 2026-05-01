-- ============================================================
-- FULL PIPELINE TEST PLAYBOOK
-- Run each block in sequence. Wait for Celery workers between steps.
-- Assumes: PostgreSQL, Celery worker + Beat running.
-- Replace requisition_id = 1 with your actual JR id everywhere.
-- ============================================================

-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 0 — PREREQUISITES: verify company + recruiter exist  │
-- └────────────────────────────────────────────────────────────┘

SELECT company_id, name FROM companies LIMIT 5;
SELECT recruiter_id, email FROM recruiters LIMIT 5;

-- If empty, insert minimal test company + recruiter:
INSERT INTO companies (name, industry, size, website)
VALUES ('Test Corp', 'Technology', '50-200', 'https://testcorp.com')
ON CONFLICT DO NOTHING;

INSERT INTO recruiters (company_id, email, first_name, last_name, hashed_password)
SELECT c.company_id, 'recruiter@testcorp.com', 'Jane', 'Smith', 'hashed_pw_here'
FROM companies c WHERE c.name = 'Test Corp'
ON CONFLICT DO NOTHING;


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 1 — CREATE JOB REQUISITION                          │
-- │  (Do this via API or SQL below)                           │
-- └────────────────────────────────────────────────────────────┘

-- Via API: POST /api/v1/job-requisitions/
-- Or directly:
INSERT INTO job_requisitions (
    company_id, recruiter_id, job_title, department, seniority_level,
    employment_type, location_city, location_country, remote_available,
    min_years_experience, max_years_experience,
    key_responsibilities, full_job_description,
    posting_start_date, cv_collection_end_date,
    status, processing_status
)
SELECT
    c.company_id,
    r.recruiter_id,
    'Senior Python Engineer',
    'Engineering',
    'senior',
    'Full-time',
    'Cairo', 'Egypt', true,
    3, 7,
    'Design and build scalable backend services using Python and FastAPI. Lead technical decisions. Mentor junior engineers.',
    'We are looking for a Senior Python Engineer to join our platform team. You will build APIs, design data pipelines, lead code reviews, and collaborate with product managers.',
    CURRENT_DATE,
    CURRENT_DATE + INTERVAL '7 days',   -- CV collection window
    'Active',
    'idle'
FROM companies c
JOIN recruiters r ON r.company_id = c.company_id
WHERE c.name = 'Test Corp'
LIMIT 1
RETURNING requisition_id;

-- Save the returned id, use it below as :jr_id
-- Example: requisition_id = 1

-- Add required skills
INSERT INTO requisition_required_skills (requisition_id, skill_name, is_mandatory)
VALUES
    (1, 'Python',        true),
    (1, 'FastAPI',       true),
    (1, 'PostgreSQL',    true),
    (1, 'Docker',        false),
    (1, 'Redis',         false),
    (1, 'Celery',        false);

-- Create job posting (triggers LinkedIn publishing via Beat)
INSERT INTO job_postings (requisition_id, platform, posting_url, status, posted_at)
VALUES (1, 'LinkedIn', 'https://linkedin.com/jobs/test', 'published', NOW())
RETURNING posting_id;
-- Save posting_id (e.g. posting_id = 1)

-- Verify:
SELECT requisition_id, job_title, status, processing_status FROM job_requisitions WHERE requisition_id = 1;


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 2 — SIMULATE CANDIDATE APPLICATIONS                 │
-- │  (Best done via POST /api/v1/apply/{posting_id} with PDF) │
-- │  OR insert synthetic rows directly:                       │
-- └────────────────────────────────────────────────────────────┘

-- Insert 5 test candidates
INSERT INTO candidates (email, first_name, last_name, phone)
VALUES
    ('alice@example.com',   'Alice',   'Johnson',  '+201001111111'),
    ('bob@example.com',     'Bob',     'Williams', '+201002222222'),
    ('carol@example.com',   'Carol',   'Davis',    '+201003333333'),
    ('dan@example.com',     'Dan',     'Martinez', '+201004444444'),
    ('eve@example.com',     'Eve',     'Wilson',   '+201005555555')
ON CONFLICT (email) DO NOTHING;

-- Insert CVs for each candidate
INSERT INTO candidate_cvs (candidate_id, file_path, extracted_text)
SELECT
    c.candidate_id,
    '/uploads/cvs/test_' || c.candidate_id || '.pdf',
    CASE c.email
        WHEN 'alice@example.com' THEN
            'Experienced Python developer with 5 years building FastAPI microservices. PostgreSQL, Redis, Docker, Celery expert. Led team of 4 engineers. Built payment processing pipeline handling 10k req/s.'
        WHEN 'bob@example.com' THEN
            'Python engineer 3 years experience. Django, Flask background. Some FastAPI exposure. MySQL primary database. Learning Docker. Good at unit testing.'
        WHEN 'carol@example.com' THEN
            'Senior backend engineer 7 years. Python FastAPI expert. Designed distributed systems. PostgreSQL, Redis, Kafka. AWS certified. Open source contributor.'
        WHEN 'dan@example.com' THEN
            'Junior Python developer 1 year. Django REST framework. SQLite and MySQL. Basic Docker knowledge. Computer Science graduate 2024.'
        WHEN 'eve@example.com' THEN
            'Full stack developer 4 years. Python FastAPI on backend. PostgreSQL, Redis. Celery for async tasks. React frontend. Strong system design background.'
    END
FROM candidates c
WHERE c.email IN (
    'alice@example.com','bob@example.com','carol@example.com',
    'dan@example.com','eve@example.com'
);

-- Create applications
INSERT INTO applications (candidate_id, posting_id, status, applied_at)
SELECT c.candidate_id, 1, 'Applied', NOW()
FROM candidates c
WHERE c.email IN (
    'alice@example.com','bob@example.com','carol@example.com',
    'dan@example.com','eve@example.com'
)
ON CONFLICT DO NOTHING
RETURNING application_id, candidate_id;


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 3 — TRIGGER CV RANKING                              │
-- │  Push cv_collection_end_date into past → Beat fires       │
-- └────────────────────────────────────────────────────────────┘

UPDATE job_requisitions
SET
    cv_collection_end_date = CURRENT_DATE - INTERVAL '1 day',
    status                 = 'Active',
    processing_status      = 'idle'
WHERE requisition_id = 1;

-- Wait 5 minutes for scan_and_dispatch_cv_ranking to fire.
-- Then verify:
SELECT
    a.application_id,
    c.first_name || ' ' || c.last_name AS candidate,
    sar.match_percentage,
    sar.generated_at
FROM applications a
JOIN candidates c ON c.candidate_id = a.candidate_id
LEFT JOIN semantic_analysis_reports sar ON sar.application_id = a.application_id
WHERE a.posting_id = 1
ORDER BY sar.match_percentage DESC NULLS LAST;

-- JR should be: status='ranked'
SELECT status, processing_status FROM job_requisitions WHERE requisition_id = 1;


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 4 — SHORTLIST TOP CANDIDATES                        │
-- │  (Via API: POST /api/v1/shortlist/ or SQL)                │
-- └────────────────────────────────────────────────────────────┘

-- Shortlist the top 3 by CV match (the system will invite them for assessment)
INSERT INTO shortlisted_candidates (application_id, requisition_id, shortlisted_at, shortlisted_by)
SELECT
    a.application_id,
    1,
    NOW(),
    (SELECT recruiter_id FROM recruiters LIMIT 1)
FROM applications a
JOIN semantic_analysis_reports sar ON sar.application_id = a.application_id
WHERE a.posting_id = 1
ORDER BY sar.match_percentage DESC NULLS LAST
LIMIT 3
ON CONFLICT DO NOTHING;

-- Mark JR as shortlist_notified so the assessment scanner can fire
UPDATE job_requisitions
SET shortlist_notified = true, status = 'ranked', processing_status = 'idle'
WHERE requisition_id = 1;

-- Verify shortlist:
SELECT sc.application_id, c.first_name, sar.match_percentage
FROM shortlisted_candidates sc
JOIN applications a ON a.application_id = sc.application_id
JOIN candidates c ON c.candidate_id = a.candidate_id
LEFT JOIN semantic_analysis_reports sar ON sar.application_id = sc.application_id
WHERE sc.requisition_id = 1
ORDER BY sar.match_percentage DESC;


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 5 — TRIGGER ASSESSMENT GENERATION                   │
-- │  scan_and_dispatch_assessment fires every 2 min           │
-- └────────────────────────────────────────────────────────────┘

-- Assessment scanner triggers on: status IN ('ranked') AND shortlist_notified=true
-- The JR is already in the right state. Wait 2 minutes.

-- OR force immediately by setting assessment_generated=false, status='ranked':
UPDATE job_requisitions
SET status = 'ranked', processing_status = 'idle', assessment_generated = false
WHERE requisition_id = 1;

-- After ~2 min, verify assessment was created:
SELECT
    ta.config_id, ta.assessment_deadline, ta.pool_report IS NOT NULL AS has_report
FROM technical_assessment_configs ta
WHERE ta.requisition_id = 1;

SELECT at2.template_id, at2.title, COUNT(atq.question_id) AS q_count
FROM assessment_templates at2
JOIN assessment_template_questions atq ON atq.template_id = at2.template_id
WHERE at2.requisition_id = 1
GROUP BY at2.template_id, at2.title;

-- JR should be: status='assessment_sent', assessment_generated=true
SELECT status, assessment_generated FROM job_requisitions WHERE requisition_id = 1;


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 6 — SIMULATE ASSESSMENT SUBMISSIONS                 │
-- │  (Use API or insert directly)                             │
-- └────────────────────────────────────────────────────────────┘

-- Get template_id for this JR:
SELECT template_id FROM assessment_templates WHERE requisition_id = 1 LIMIT 1;

-- Insert CandidateAssessment rows for each shortlisted candidate
INSERT INTO candidate_assessments (
    candidate_id, application_id, template_id,
    status, score, started_at, submitted_at
)
SELECT
    c.candidate_id,
    sc.application_id,
    (SELECT template_id FROM assessment_templates WHERE requisition_id = 1 LIMIT 1),
    'Submitted',
    (50 + RANDOM() * 50)::NUMERIC(6,2),     -- random score 50-100
    NOW() - INTERVAL '1 hour',
    NOW()
FROM shortlisted_candidates sc
JOIN applications a ON a.application_id = sc.application_id
JOIN candidates c ON c.candidate_id = a.candidate_id
WHERE sc.requisition_id = 1
ON CONFLICT DO NOTHING;

-- Verify assessments:
SELECT ca.assessment_id, c.first_name, ca.score, ca.status
FROM candidate_assessments ca
JOIN candidates c ON c.candidate_id = ca.candidate_id
WHERE ca.application_id IN (
    SELECT application_id FROM shortlisted_candidates WHERE requisition_id = 1
);


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 7 — TRIGGER ASSESSMENT RANKING                      │
-- │  Push deadline into past → scan_and_dispatch_assessment_ranking │
-- └────────────────────────────────────────────────────────────┘

UPDATE technical_assessment_configs
SET
    assessment_deadline = NOW() - INTERVAL '1 minute',
    pool_report         = NULL
WHERE requisition_id = 1;

UPDATE job_requisitions
SET status = 'assessment_sent', processing_status = 'idle'
WHERE requisition_id = 1;

-- Wait up to 5 min → grading pipeline runs → AssessmentLeaderboard populated.
-- Verify:
SELECT
    al.rank,
    c.first_name || ' ' || c.last_name AS candidate,
    al.final_score,
    al.segment,
    al.reject
FROM assessment_leaderboards al
JOIN candidates c ON c.candidate_id = al.candidate_id
WHERE al.jr_id = 1
ORDER BY al.rank;

-- JR should be: status='interview_pending', interview_notified=true
SELECT status, interview_notified, interview_deadline, processing_status
FROM job_requisitions WHERE requisition_id = 1;

-- Verify tech sessions created:
SELECT
    tis.session_id,
    c.first_name,
    tis.status,
    tis.scheduled_at
FROM technical_interview_sessions tis
JOIN applications a ON a.application_id = tis.application_id
JOIN candidates c ON c.candidate_id = a.candidate_id
WHERE a.posting_id = 1;


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 8 — SIMULATE TECH INTERVIEWS COMPLETED              │
-- │  (Skips actual LiveKit — injects realistic scores)        │
-- └────────────────────────────────────────────────────────────┘

UPDATE technical_interview_sessions
SET
    status             = 'Completed',
    overall_score      = (40 + RANDOM() * 55)::NUMERIC(6,2),
    -- Inject ensemble scores so score_candidates_node uses composite
    nli_technical_score   = (0.40 + RANDOM() * 0.45)::NUMERIC(6,4),
    codebert_score        = (0.35 + RANDOM() * 0.50)::NUMERIC(6,4),
    tfidf_technical_score = (0.30 + RANDOM() * 0.55)::NUMERIC(6,4),
    roberta_depth_score   = (0.35 + RANDOM() * 0.45)::NUMERIC(6,4),
    transcript         = 'Interviewer: Tell me about your experience with FastAPI. Candidate: I have built several production APIs with FastAPI including payment gateways and data processing pipelines. I prefer FastAPI over Flask for its async support and automatic OpenAPI generation.',
    ended_at           = NOW()
WHERE application_id IN (
    SELECT a.application_id FROM applications a
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = 1
)
AND status = 'Scheduled';

-- One candidate as no-show (optional, tests that path):
UPDATE technical_interview_sessions
SET status = 'No-show', overall_score = 0, ended_at = NOW()
WHERE session_id = (
    SELECT tis.session_id
    FROM technical_interview_sessions tis
    JOIN applications a ON a.application_id = tis.application_id
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = 1 AND tis.status = 'Completed'
    ORDER BY tis.overall_score ASC
    LIMIT 1
);


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 9 — TRIGGER HR INTERVIEW INVITATIONS                │
-- │  Push tech deadline into past → scanner fires             │
-- └────────────────────────────────────────────────────────────┘

UPDATE job_requisitions
SET
    interview_deadline    = NOW() - INTERVAL '1 minute',
    hr_interview_notified = false,
    status                = 'interview_pending',
    processing_status     = 'idle'
WHERE requisition_id = 1;

-- Wait up to 5 min → scan_and_dispatch_hr_interviews fires.
-- Verify HR sessions created:
SELECT
    his.session_id,
    c.first_name,
    his.status,
    his.scheduled_at
FROM hr_interview_sessions his
JOIN applications a ON a.application_id = his.application_id
JOIN candidates c ON c.candidate_id = a.candidate_id
WHERE a.posting_id = 1;

-- JR should be: status='hr_interview_pending', hr_interview_notified=true
SELECT status, hr_interview_notified, hr_interview_deadline
FROM job_requisitions WHERE requisition_id = 1;


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 10 — SIMULATE HR INTERVIEWS COMPLETED               │
-- └────────────────────────────────────────────────────────────┘

UPDATE hr_interview_sessions
SET
    status               = 'Completed',
    overall_score        = (45 + RANDOM() * 50)::NUMERIC(6,2),
    -- Inject ensemble scores so score_candidates_node uses composite
    nli_align_score      = (0.45 + RANDOM() * 0.45)::NUMERIC(6,4),
    semantic_depth_score = (0.40 + RANDOM() * 0.45)::NUMERIC(6,4),
    sentiment_score      = (0.50 + RANDOM() * 0.40)::NUMERIC(6,4),
    emotion_score        = (0.30 + RANDOM() * 0.50)::NUMERIC(6,4),   -- diagnostic only
    transcript           = 'Interviewer: Tell me about a time you led a team through a difficult deadline. Candidate: At my previous company I led a 4-person team to deliver a payment integration in 3 weeks. I broke tasks down by component, held daily standups, and personally unblocked two critical blockers. We shipped on time.',
    ended_at             = NOW()
WHERE application_id IN (
    SELECT a.application_id FROM applications a
    JOIN job_postings jp ON jp.posting_id = a.posting_id
    WHERE jp.requisition_id = 1
)
AND status = 'Scheduled';


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 11 — TRIGGER FINAL RANKING                          │
-- │  Push HR deadline into past → final ranking scanner       │
-- └────────────────────────────────────────────────────────────┘

UPDATE job_requisitions
SET
    hr_interview_deadline = NOW() - INTERVAL '1 minute',
    status                = 'hr_interview_pending',
    processing_status     = 'idle'
WHERE requisition_id = 1;

-- Wait up to 5 min → scan_and_dispatch_final_ranking fires → compute_final_ranking runs.
-- Verify final rankings:
SELECT
    fr.final_rank,
    c.first_name || ' ' || c.last_name         AS candidate,
    fr.semantic_score                           AS cv_pct,
    fr.technical_interview_score                AS tech_pct,
    fr.hr_interview_score                       AS hr_pct,
    fr.weighted_total_score                     AS total,
    fr.risk_score,
    fr.red_flag,
    fr.final_recommendation,
    LEFT(fr.shap_summary, 120)                  AS shap_preview
FROM final_rankings fr
JOIN applications a   ON a.application_id  = fr.application_id
JOIN candidates c     ON c.candidate_id    = a.candidate_id
WHERE fr.posting_id = 1
ORDER BY fr.final_rank;

-- JR should be: status='ranking_complete'
SELECT status, processing_status FROM job_requisitions WHERE requisition_id = 1;


-- ┌────────────────────────────────────────────────────────────┐
-- │  STEP 12 — FULL VERIFICATION QUERIES                      │
-- └────────────────────────────────────────────────────────────┘

-- Complete pipeline funnel view:
SELECT
    c.first_name || ' ' || c.last_name AS candidate,
    a.status                            AS app_status,
    sar.match_percentage                AS cv_score,
    ca.score                            AS assessment_score,
    al.final_score * 100                AS leaderboard_score,
    al.rank                             AS assessment_rank,
    al.reject                           AS rejected_after_assess,
    tis.overall_score                   AS tech_score,
    tis.status                          AS tech_status,
    his.overall_score                   AS hr_score,
    his.status                          AS hr_status,
    fr.weighted_total_score             AS final_total,
    fr.final_rank,
    fr.final_recommendation,
    fr.red_flag,
    fr.risk_score
FROM applications a
JOIN job_postings jp        ON jp.posting_id = a.posting_id
JOIN candidates c           ON c.candidate_id = a.candidate_id
LEFT JOIN semantic_analysis_reports sar ON sar.application_id = a.application_id
LEFT JOIN candidate_assessments ca      ON ca.application_id  = a.application_id
LEFT JOIN assessment_leaderboards al    ON al.application_id  = a.application_id AND al.jr_id = jp.requisition_id
LEFT JOIN technical_interview_sessions tis ON tis.application_id = a.application_id
LEFT JOIN hr_interview_sessions his        ON his.application_id = a.application_id
LEFT JOIN final_rankings fr                ON fr.application_id  = a.application_id
WHERE jp.requisition_id = 1
ORDER BY COALESCE(fr.final_rank, 999), sar.match_percentage DESC NULLS LAST;


-- SHAP breakdown per candidate:
SELECT
    c.first_name,
    fr.final_rank,
    fr.risk_score,
    fr.shap_summary
FROM final_rankings fr
JOIN applications a ON a.application_id = fr.application_id
JOIN candidates c ON c.candidate_id = a.candidate_id
WHERE fr.posting_id = 1
ORDER BY fr.final_rank;


-- ┌────────────────────────────────────────────────────────────┐
-- │  RESET — Full teardown for JR 1 (for re-running tests)    │
-- └────────────────────────────────────────────────────────────┘

-- Run in this order to respect FK constraints:
DELETE FROM final_rankings WHERE posting_id = 1;
DELETE FROM hr_interview_sessions
    WHERE application_id IN (
        SELECT application_id FROM applications a
        JOIN job_postings jp ON jp.posting_id = a.posting_id
        WHERE jp.requisition_id = 1);
DELETE FROM technical_interview_sessions
    WHERE application_id IN (
        SELECT application_id FROM applications a
        JOIN job_postings jp ON jp.posting_id = a.posting_id
        WHERE jp.requisition_id = 1);
DELETE FROM assessment_leaderboards WHERE jr_id = 1;
DELETE FROM candidate_assessments
    WHERE application_id IN (
        SELECT application_id FROM applications a
        JOIN job_postings jp ON jp.posting_id = a.posting_id
        WHERE jp.requisition_id = 1);
DELETE FROM semantic_analysis_reports
    WHERE application_id IN (
        SELECT application_id FROM applications a
        JOIN job_postings jp ON jp.posting_id = a.posting_id
        WHERE jp.requisition_id = 1);
DELETE FROM shortlisted_candidates WHERE requisition_id = 1;
DELETE FROM applications
    WHERE posting_id IN (SELECT posting_id FROM job_postings WHERE requisition_id = 1);

UPDATE job_requisitions
SET
    status                = 'Active',
    processing_status     = 'idle',
    assessment_generated  = false,
    interview_notified    = false,
    interview_deadline    = NULL,
    hr_interview_notified = false,
    hr_interview_deadline = NULL,
    shortlist_notified    = false,
    cv_collection_end_date = CURRENT_DATE + INTERVAL '7 days'
WHERE requisition_id = 1;
