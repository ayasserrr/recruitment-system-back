# AI-Powered Recruitment System — Complete Technical Specification

> **Graduation Project Documentation**
> Full pipeline: architecture, APIs, ML models, data flow, and engineering decisions.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Technology Stack](#2-technology-stack)
3. [System Architecture](#3-system-architecture)
4. [Database Design](#4-database-design)
5. [Pipeline Status Machine](#5-pipeline-status-machine)
6. [Stage 0 — Company Onboarding & Job Creation](#6-stage-0--company-onboarding--job-creation)
7. [Stage 1 — Candidate Application & CV Upload](#7-stage-1--candidate-application--cv-upload)
8. [Stage 2 — CV Screening / Semantic Analysis](#8-stage-2--cv-screening--semantic-analysis)
9. [Stage 3 — Shortlisting & Assessment Invitation](#9-stage-3--shortlisting--assessment-invitation)
10. [Stage 4 — Assessment Submission (Candidate Side)](#10-stage-4--assessment-submission-candidate-side)
11. [Stage 5 — Post-Deadline Assessment Grading (3-Phase Relative Grading)](#11-stage-5--post-deadline-assessment-grading-3-phase-relative-grading)
12. [Stage 6 — Technical Interview (Voice Agent)](#12-stage-6--technical-interview-voice-agent)
13. [Stage 7 — HR Interview (Voice Agent)](#13-stage-7--hr-interview-voice-agent)
14. [Stage 8 — Final Ranking](#14-stage-8--final-ranking)
15. [Stage 9 — Post-Ranking Deliverables](#15-stage-9--post-ranking-deliverables)
16. [Celery Beat Scanners](#16-celery-beat-scanners)
17. [API Reference Summary](#17-api-reference-summary)
18. [ML Models Reference Card](#18-ml-models-reference-card)
19. [Evaluation & Metrics](#19-evaluation--metrics)
20. [Anti-Loop & Idempotency Guarantees](#20-anti-loop--idempotency-guarantees)
21. [Engineering Decisions & Known Issues Fixed](#21-engineering-decisions--known-issues-fixed)

---

## 1. Project Overview

The **AI-Powered Recruitment System** is a full-stack, end-to-end automated hiring platform that replaces manual HR screening with a nine-stage intelligent pipeline. From the moment a company posts a job to the moment a hire decision email is dispatched, the system operates autonomously — collecting CVs, scoring candidates, administering AI voice interviews, and producing explainable ranked recommendations.

**Key characteristics:**

- **Zero-human-in-the-loop for screening** — the pipeline advances automatically based on dates and thresholds.
- **Multi-modal AI** — combines dense vector retrieval, cross-encoder reranking, live voice interaction (STT/TTS), NLI, sentiment analysis, and code-vocabulary models.
- **Explainability-first** — every final score is accompanied by SHAP breakdown values relative to the pool median.
- **Idempotent by design** — boolean flags and a processing lock ensure every stage runs exactly once per job.

---

## 2. Technology Stack

| Layer | Technology | Version / Notes |
| --- | --- | --- |
| Language | Python | 3.11+ |
| API Framework | FastAPI + Uvicorn | Async, OpenAPI auto-docs |
| ORM | SQLAlchemy | 2.0, declarative mapping |
| DB Migrations | Alembic | Version-controlled schema |
| Primary Database | PostgreSQL | `recruitment_system_db` |
| Knowledge Database | SQLite | `knowledge_db/` — embedded, independently versionable |
| Vector Store | Qdrant | Optional CV embedding retrieval |
| Task Queue | Celery 5 | Distributed worker pool |
| Message Broker | Redis | Celery broker + result backend |
| AI Orchestration | LangGraph | Stateful multi-node DAG pipelines |
| Voice Interviews | LiveKit Agents SDK | WebRTC rooms, server-side agent |
| LLM (API) | OpenAI GPT-4o / GPT-4o-mini | Called via `httpx` (not official SDK) |
| STT | OpenAI Whisper-1 | Via LiveKit plugin |
| TTS | OpenAI Alloy | Via LiveKit plugin |
| VAD | Silero VAD | Server-side voice activity detection |
| Local ML Models | HuggingFace Transformers | CPU/GPU inference, loaded at startup |
| PDF Parsing | PyMuPDF (fitz) | Raw text extraction from CVs |
| NLP (offline) | spaCy | Lemmatization for keyword matching |
| Linear Algebra | scikit-learn | TF-IDF, cosine similarity |
| Frontend | React (TypeScript) | Company dashboard + candidate portal |
| Scheduling | Celery Beat | Cron-like periodic tasks |
| Auth | JWT + HMAC | API auth + signed email tokens |

---

## 3. System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                        FRONTEND (React / TypeScript)                   │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐ │
│  │  Company         │  │  Candidate       │  │  Interview Room      │ │
│  │  Dashboard       │  │  Portal          │  │  (LiveKit WebRTC)    │ │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────┬───────────┘ │
└───────────┼─────────────────────┼────────────────────────┼────────────┘
            │  REST (HTTPS)       │  REST (HTTPS)          │  WebRTC
            ▼                     ▼                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        BACKEND (FastAPI + Uvicorn)                     │
│                        30+ REST Endpoints                              │
│                                                                        │
│   ┌─────────────────────────────────────────────────────────────┐      │
│   │              LiveKit Voice Agent (separate process)         │      │
│   │   Whisper-1 STT → GPT-4o LLM → Alloy TTS → Silero VAD     │      │
│   └─────────────────────────────────────────────────────────────┘      │
│                              │                                         │
│   ┌──────────────────────────▼──────────────────────────────────┐      │
│   │                  Celery Workers + Beat Scheduler            │      │
│   │   ┌────────────┐  ┌─────────────────┐  ┌────────────────┐  │      │
│   │   │  CV Rank   │  │  Assessment     │  │  Final Rank    │  │      │
│   │   │  Worker    │  │  Grading Worker │  │  Worker        │  │      │
│   │   └────────────┘  └─────────────────┘  └────────────────┘  │      │
│   └─────────────────────────────────────────────────────────────┘      │
└─────────────────────┬──────────────────────┬───────────────────────────┘
                      │                      │
          ┌───────────▼──────┐   ┌───────────▼──────┐
          │   PostgreSQL     │   │   Redis           │
          │   (main store)   │   │   (broker +       │
          │                  │   │    result store)  │
          └───────────┬──────┘   └──────────────────┘
                      │
          ┌───────────▼───────────────────────────┐
          │  SQLite Knowledge DB  │  Qdrant         │
          │  (skill taxonomy,     │  (CV vector     │
          │   question bank,      │   embeddings,   │
          │   MiniLM embeddings)  │   optional)     │
          └───────────────────────────────────────┘
```

**Design rationale:**

- FastAPI handles all HTTP traffic synchronously (request/response).
- Heavy ML work is dispatched to Celery workers to avoid blocking the API thread.
- LangGraph pipelines run inside Celery workers — each pipeline is a stateful DAG where node failures can be retried independently.
- LiveKit Agents run as a separate OS process connected to the same PostgreSQL DB.

---

## 4. Database Design

### 4.1 PostgreSQL — `recruitment_system_db`

The primary relational store. Every piece of business data lives here.

| Category | Tables |
|---|---|
| Tenants | `companies`, `recruiters` |
| Jobs | `job_requisitions`, `job_postings`, `posting_platforms` |
| Candidates | `candidates`, `candidate_cvs`, `cv_experiences`, `cv_educations`, `cv_projects`, `cv_skills` |
| Applications | `applications`, `shortlisted_candidates`, `pipeline_stage_logs` |
| CV Screening | `semantic_analysis_reports`, `semantic_matched_skills` |
| Assessment | `assessment_templates`, `assessment_template_questions`, `technical_assessment_configs`, `candidate_assessments`, `assessment_answers`, `assessment_reports`, `assessment_leaderboard`, `generated_assessment_questions`, `assessment_question_sets` |
| Technical Interview | `technical_interview_configs`, `technical_interview_sessions`, `technical_interview_reports`, `technical_interview_scores` |
| HR Interview | `hr_interview_configs`, `hr_interview_sessions`, `hr_interview_reports`, `hr_interview_scores` |
| Final Ranking | `final_rankings` |
| Knowledge Base | `kb_topics`, `kb_questions`, `kb_question_embeddings`, `jr_knowledge_gaps`, `jr_question_selections` |
| Auth / Social | `company_social_auth`, `requisition_required_skills`, `requisition_languages` |

**Key columns on `job_requisitions`:**

```
status              — pipeline stage (DRAFT → RANKING_COMPLETE)
processing_status   — lock column: idle | processing | error
shortlist_notified  — boolean anti-loop flag
interview_notified  — boolean anti-loop flag
hr_interview_notified — boolean anti-loop flag
cv_collection_end_date, assessment_deadline,
interview_deadline, hr_interview_deadline  — stage cutoff dates
```

**Key columns on `final_rankings`:**

```
semantic_score, assessment_score,
technical_interview_score, hr_interview_score,
weighted_total_score    — per-stage and composite scores
final_rank              — integer rank within JR pool
final_recommendation    — Strong Hire | Hire | Potential Hire | No Hire | Review Required
red_flag                — boolean
risk_score              — float [0, 1]
shap_json               — JSON: {cv, assessment, tech, hr} SHAP φ values
summary                 — template-generated narrative (zero LLM)
```

### 4.2 SQLite — `knowledge_db/`

Embedded knowledge base used by the assessment and interview graphs. Kept separate from PostgreSQL so it can be seeded, versioned, or swapped independently.

| Table | Purpose |
|---|---|
| `kb_topics` | Hierarchical skill taxonomy (e.g., Backend → Python → FastAPI) |
| `kb_questions` | 200+ expert-written interview questions per topic |
| `kb_question_embeddings` | 384-dim MiniLM-L6-v2 embeddings for each question |

---

## 5. Pipeline Status Machine

Every `JobRequisition` moves through these statuses automatically, driven by Beat scanners and worker task outcomes:

```
DRAFT
  │
  │ (company publishes the job)
  ▼
ACTIVE / PUBLISHED
  │
  │ cv_collection_end_date passes
  │ Beat: scan_and_dispatch_cv_ranking (every 5 min)
  ▼
RANKED
  │
  │ shortlist emails sent → shortlist_notified = true
  │ Beat: scan_and_dispatch_assessment (every 2 min)
  ▼
ASSESSMENT_SENT
  │
  │ assessment_deadline passes
  │ Beat: scan_and_dispatch_assessment_ranking (every 5 min)
  ▼
ASSESSMENT_RANKED
  │
  │ relative grading done → send_interview_invitations dispatched immediately
  ▼
INTERVIEW_PENDING
  │
  │ all tech sessions Completed / No-show  OR  interview_deadline passes
  │ Beat: scan_and_dispatch_hr_interviews (every 5 min)
  ▼
HR_INTERVIEW_PENDING
  │
  │ all HR sessions terminal  OR  hr_interview_deadline passes
  │ Beat: scan_and_dispatch_final_ranking (every 5 min)
  ▼
RANKING_COMPLETE  ✓
```

Each transition is guarded by:
1. **Boolean flags** — set to `true` before dispatching, checked at task start.
2. **Processing lock** — `processing_status` column transitions `idle → processing → idle/error`.
3. **Status guard** at the start of every worker — idempotent if re-run.

---

## 6. Stage 0 — Company Onboarding & Job Creation

**Actor:** Company HR user via dashboard.

> **No ML models used in this stage.** Registration, job creation, and LinkedIn posting are pure API and rule-based operations.

### 6.1 API Routes

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/auth/signup` | Register company account |
| `POST` | `/api/v1/auth/login` | Authenticate, get JWT |
| `POST` | `/api/v1/auth/linkedin/callback` | OAuth LinkedIn connection |
| `POST` | `/api/v1/jobs` | Create job requisition |
| `PUT` | `/api/v1/jobs/{id}` | Update JR details |
| `POST` | `/api/v1/jobs/{id}/publish` | Move status → ACTIVE |

### 6.2 Flow

```
Company registers
      │
      ▼
Fills job requisition form
  (title, responsibilities, required skills,
   salary range, deadlines, posting platforms)
      │
      ▼
POST /api/v1/jobs → JobRequisition row created (status=DRAFT)
      │
      ▼
POST /api/v1/jobs/{id}/publish → status=ACTIVE
      │
      ▼
Optional: LinkedIn OAuth
  POST /api/v1/auth/linkedin/callback
  → company_social_auth row created
```

### 6.3 Auto LinkedIn Post (LangGraph)

**Beat task:** `scan_and_dispatch_scheduled_posts` — every 60 seconds.

**Trigger condition:** `posting_start_date <= today` AND `linkedin_posted = false`.

**Worker task:** `process_linkedin_publishing`

**LangGraph — 4 nodes:**

```
context_loader
      │
      ▼
preflight_check       ← verifies OAuth token validity
      │
      ▼
api_publisher         ← calls LinkedIn REST Posts API v202601
      │
      ▼
finalizer             ← marks linkedin_posted = true
```

No AI model used — purely rule-based LinkedIn API call.

---

## 7. Stage 1 — Candidate Application & CV Upload

**Actor:** Candidate via public apply page.

> **No ML models used in this stage.** PDF parsing (fitz/PyMuPDF) and structured field extraction are rule-based text operations — no inference is run.

### 7.1 API Routes

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/apply` | Submit application form |
| `POST` | `/api/v1/data/upload/{company_id}/{job_id}/{candidate_id}` | Upload CV PDF |
| `GET` | `/api/v1/jobs/{id}/postings` | View job details (public) |

### 7.2 Flow

```
Candidate visits apply page
      │
      ▼
Fills form (name, email, phone, cover letter)
      │
      ▼
POST /api/v1/apply
  → Candidate row created
  → Application row created (status = "Applied")
      │
      ▼
POST /api/v1/data/upload/...
  → CV PDF saved to: uploads/cvs/{candidate_id}/{uuid}.pdf
  → PDF parsed via fitz (PyMuPDF)
  → Raw text stored in candidate_cvs.extracted_text
  → Structured fields extracted:
      cv_experiences   (company, role, dates, description)
      cv_skills        (skill name, proficiency)
      cv_projects      (name, description, technologies)
      cv_educations    (institution, degree, dates)
```

**No AI at this stage** — pure data ingestion and structured parsing.

---

## 8. Stage 2 — CV Screening / Semantic Analysis

**Trigger:** `cv_collection_end_date <= now` AND `status IN (ACTIVE, PUBLISHED)`.

**Beat task:** `scan_and_dispatch_cv_ranking` — every 5 minutes.

**Worker task:** `process_cv_ranking(requisition_id)`

**Lock acquired:** `acquire_jr_lock()` → `processing_status = processing`

**Models used in this stage:**

| Model | Type | Purpose | Source | Where it runs |
| --- | --- | --- | --- | --- |
| `BAAI/bge-large-en-v1.5` | Pre-trained bi-encoder (1024-dim) | CV-JD semantic similarity | HuggingFace | Local inference |
| `BAAI/bge-reranker-v2-m3` | Pre-trained cross-encoder | CV-JD relevance reranking (primary) | HuggingFace | Local inference |
| `cross-encoder/ms-marco-MiniLM-L-12-v2` | Pre-trained cross-encoder | Fallback reranker (faster, lighter) | HuggingFace | Local inference |
| `GPT-4o-mini` | Large language model (LLM) | Project depth analysis, bias corrections, qualitative validation | OpenAI API | Remote API call |

### 8.1 Embedding Architecture — Dual-Model

The embedding service uses two complementary models:

**Bi-Encoder: `BAAI/bge-large-en-v1.5`**
- Produces 1024-dimensional dense vectors
- JD text encoded with query prefix: `"Represent this sentence for searching relevant passages: "`
- CV text encoded without prefix (asymmetric retrieval)
- Scores → cosine similarity on L2-normalised vectors
- MTEB average: 64.2 — optimised for long-document retrieval

**Cross-Encoder: `BAAI/bge-reranker-v2-m3`**
- Takes `(query, passage)` pairs, outputs a relevance logit
- Logit converted to probability: `score = sigmoid(logit)` → [0, 1]
- Primary reranker; fallback: `cross-encoder/ms-marco-MiniLM-L-12-v2` (faster, 130MB)
- MTEB MRR@10: 0.885

**Hybrid score formula:**

```
hybrid_score = 0.70 × CE_probability + 0.30 × skill_overlap_ratio
```

Skill overlap prevents semantic drift — a CV that talks about unrelated topics but sounds similar in embedding space is penalised.

**CE label thresholds:**

| CE Probability | Label |
|---|---|
| > 0.65 | `good_fit` |
| > 0.35 | `partial_fit` |
| ≤ 0.35 | `no_fit` |

**Optional Qdrant:** If Qdrant is running, CV embeddings are upserted and top-K retrieval narrows the candidate pool before full scoring.

### 8.2 LangGraph — 6 Nodes

```
context_gatherer_node
      │  Loads JD text, required skills, all Applications + extracted CV text
      ▼
deterministic_scoring_node
      │  Rule-based scoring with 5 bias corrections (see below)
      ▼
llm_qualitative_node
      │  GPT-4o-mini: project depth analysis, deployment context
      ▼
genai_validator_node
      │  Evidence extraction, validates claimed skills against CV text
      ▼
final_ranker_node
      │  Tiered pool-relative labels, computes match_percentage
      ▼
persistence_node
      │  Upserts SemanticAnalysisReport + SemanticMatchedSkill rows
      │  Sets JR.status = RANKED
```

### 8.3 Deterministic Bias Corrections (5 Fixes)

These are applied in `deterministic_scoring_node` before any LLM call:

| Fix | Name | Logic |
| --- | --- | --- |
| FIX-A | AI/ML Background Detection | 65+ AI/ML keyword list; matching CVs get score bonus |
| FIX-B | Implicit Teamwork Scoring | Project descriptions with team indicators get soft bonus |
| FIX-C | Education Floor Enforcement | 10-tier hierarchy enforced; below minimum → penalty |
| FIX-D | Keyword-Stuffing Penalty | Inflated skills lists (>50 synonyms detected) → score reduction |
| FIX-E | Deployment Context Scoring | Production deployments (cloud, Docker, CI/CD) vs toy projects |

50+ skill synonym groups used to normalise variations (e.g., "ML" = "Machine Learning" = "machine-learning").

### 8.4 Pool-Relative Labels

After scoring, candidates are labelled relative to the pool — not absolute thresholds:

| Condition | Label |
|---|---|
| Rank = 1 | Top Candidate |
| Score within 10 pts of rank 1 | Strong Runner-Up |
| Score ≥ 70 AND top 33% of pool | Strong Hire |
| Score ≥ 55 AND top 60% of pool | Hire |
| Score ≥ 40 | Maybe |
| Score < 40 | No Hire |

### 8.5 Output

`SemanticAnalysisReport` per candidate with:
- `match_percentage` — 0–100 composite score
- `hr_explanation_json` — strengths, skill gaps, recommendation text
- `rank_in_pool` — integer rank within this JR's candidate pool
- `recommendation_summary` — includes "Pool Label: `<label>`" for downstream metrics

---

## 9. Stage 3 — Shortlisting & Assessment Invitation

**Trigger:** `status = RANKED` AND `shortlist_notified = false`.

**Beat task:** `scan_and_dispatch_assessment` — every 2 minutes.

**Worker tasks:** `send_shortlist_emails` → then `process_assessment_generation`

**Models used in this stage:**

| Model | Type | Purpose | Source | Where it runs |
| --- | --- | --- | --- | --- |
| `GPT-4o-mini` | Large language model (LLM) | Generates assessment questions grounded in the knowledge base | OpenAI API | Remote API call |
| `all-MiniLM-L6-v2` | Pre-trained sentence encoder (384-dim) | Embeds KB questions for semantic retrieval during question selection | HuggingFace | Local inference |

> **Note:** Models are only invoked in **AI Full Generate** and **Template-Based** modes. In Manual mode this stage has no model calls.

### 9.1 Assessment Creation — Three Modes

Before assessments are sent, the company selects how questions are sourced. The choice is made on the job configuration page in the dashboard and stored on the `technical_assessment_configs` row. Regardless of which mode is chosen, the system handles both **sending** and **grading** automatically.

| Mode | Who writes the questions | What the system does |
| --- | --- | --- |
| **AI Full Generate** | GPT-4o-mini, grounded in the KB | Generates 15 questions from skill gaps; sends; grades all answers |
| **Template-Based** | Company provides a question template; AI generates similar questions in the same style and difficulty | Adapts the template to each JR's skills; sends; grades all answers |
| **Manual (Company-Provided)** | Company writes all questions directly in the dashboard | Imports questions as-is; sends; grades all answers |

In all three modes the grading pipeline (Stage 5) is identical — keyword coverage scoring, pool-relative depth scoring, and tiebreaking — because every question, regardless of origin, is linked to `required_keywords` before the assessment is dispatched.

### 9.2 Shortlisting

```
Select top 100 candidates by rank_in_pool
      │
      ▼
Application.status → Shortlisted
ShortlistedCandidate rows created
      │
      ▼
Send shortlist notification emails in batches of 10
      │
      ▼
JR.shortlist_notified = true
JR.status = ASSESSMENT_SENT
```

### 9.3 Assessment Generation — LangGraph (4 Nodes, AI Full Generate Mode)

```
load_context
      │  Loads JR required skills, knowledge gaps from jr_knowledge_gaps,
      │  available kb_topics matching the JR skill set
      ▼
generate_questions
      │  GPT-4o-mini generates 15 questions grounded in the KB
      │  Each question references a kb_topic and required_keywords
      │  from the corresponding kb_questions expert row
      ▼
create_assessments
      │  CandidateAssessment rows created per shortlisted candidate
      │  generated_assessment_questions rows linked with template_question_id FK
      ▼
send_invitations
      │  HMAC-signed, tokenised assessment links emailed to each candidate
```

**Why KB-grounded questions?**
Answers are graded against expert-written `required_keywords` from `kb_questions`, not free-form LLM judgement. This prevents hallucinated scoring criteria and ensures the same standard applies to every candidate in the pool.

---

## 10. Stage 4 — Assessment Submission (Candidate Side)

> **No ML models used in this stage.** MCQ answers are graded by exact letter-match. Open-ended answers are saved raw and deferred to Stage 5.

**Routes:**

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/assessment/{id}?token=...` | Load assessment (token-validated) |
| `POST` | `/api/v1/assessment/{id}/submit` | Submit answers |

### 10.1 Two-Tier Grading on Submit

| Question Type | Action on Submit | When Final Score Assigned |
| --- | --- | --- |
| MCQ | Exact letter-match (A/B/C/D) | Immediately — `score_awarded` set |
| Open-ended | Raw answer saved, `score_awarded = NULL` | After deadline — by relative grading pipeline |

MCQ is graded immediately so `mcq_scores` are available to the relative grading pipeline as a baseline. Open-ended is deferred — the HTTP response returns in milliseconds instead of 30+ seconds.

`CandidateAssessment.status → Submitted`

---

## 11. Stage 5 — Post-Deadline Assessment Grading (3-Phase Relative Grading)

**Trigger:** `assessment_deadline <= now` AND `status = ASSESSMENT_SENT`.

**Beat task:** `scan_and_dispatch_assessment_ranking` — every 5 minutes.

**Worker chain:** `process_assessment_ranking` → `run_relative_grading`

**Models used in this stage:**

| Model | Type | Purpose | Source | Where it runs |
| --- | --- | --- | --- | --- |
| `spaCy` (en_core_web_sm) | Pre-trained NLP pipeline | Lemmatization — normalises candidate answer text before keyword matching | spaCy / HuggingFace | Local inference |
| `GPT-4o-mini` | Large language model (LLM) | Semantic keyword check fallback (when lemmatizer misses a match); pairwise tiebreaking | OpenAI API | Remote API call |

### 11.1 Phase 1 — Keyword Coverage Scoring

```
For each open-ended answer:
  1. spaCy lemmatizer normalises candidate answer text
  2. Check each required_keyword for the question:
       – Lemma match first (offline, fast)
       – Fallback: GPT-4o-mini semantic check for keywords
         not caught by lemmatizer
  3. keyword_score = fraction of keywords covered
```

**Why lemmatization first?** Avoids calling the LLM for simple vocabulary variants ("running" → "run", "databases" → "database"). GPT-4o-mini is only invoked when the lemmatizer fails to match.

### 11.2 Phase 2 — Pool-Relative Depth Scoring

```
DepthScorer:
  1. Collects all candidate answers for each question across the pool
  2. Scores each answer's depth relative to the strongest answer in the pool
  3. Outputs comparative_score ∈ [0, 1] per (candidate, question)
```

This ensures that scoring is competitive — a mediocre answer in a strong pool ranks lower than the same answer in a weak pool.

### 11.3 Phase 3 — Final Score & Ranking

```
final_score = mean(comparative_scores across all questions)

Tiebreaker (for candidates within 0.01 of each other):
  GPT-4o-mini compares the two near-equal answers for the
  first concept question and declares a winner

Rejection condition (AND, not OR):
  final_score < 0.50  AND  avg_depth < 0.50
```

**Output — `AssessmentLeaderboard` row per candidate:**

| Column | Description |
|---|---|
| `final_score` | 0–1 composite relative score |
| `rank` | Integer rank within this JR pool |
| `reject` | Boolean — true only when both thresholds fail |
| `segment` | `Human Review` / `Shortlist` / `Auto-Reject` |

`JR.status → ASSESSMENT_RANKED`

Immediately dispatches: `send_interview_invitations.delay(requisition_id)`

---

## 12. Stage 6 — Technical Interview (Voice Agent)

**Trigger:** Auto-dispatched immediately after assessment ranking completes.

**Worker task:** `send_interview_invitations(requisition_id)`

Top 20 candidates (by leaderboard rank, non-rejected) receive `TechnicalInterviewSession` rows and signed email invitation links. `JR.interview_deadline = now + 7 days`.

**Models used in this stage:**

| Model | Type | Purpose | Source | Where it runs |
| --- | --- | --- | --- | --- |
| `all-MiniLM-L6-v2` | Pre-trained sentence encoder (384-dim) | Retrieves personalised questions from KB by comparing candidate skill gaps to question embeddings | HuggingFace | Local inference |
| Silero VAD | Pre-trained voice activity detector | Detects when the candidate is speaking / silent; triggers silence nudge at 25 s | Silero / ONNX | Local inference |
| `openai/whisper-1` | Pre-trained ASR (speech-to-text) | Transcribes candidate speech in real time | OpenAI API | Remote API call |
| `GPT-4o` | Large language model (LLM) | Drives the live interview agent — asks questions, follows up, calls tools | OpenAI API | Remote API call |
| `openai/alloy` (TTS) | Pre-trained neural TTS | Converts agent text responses to voice | OpenAI API | Remote API call |
| `cross-encoder/nli-deberta-v3-small` | Pre-trained NLI cross-encoder | Scores technical depth of transcript vs JD responsibilities (35% of ensemble) | HuggingFace | Local inference |
| `microsoft/codebert-base` | Pre-trained code-aware encoder | Measures code / system-design vocabulary alignment (30% of ensemble) | HuggingFace | Local inference |
| `sklearn TF-IDF` (bigrams) | Classical ML (no pre-training) | Technical keyword coverage across the transcript (20% of ensemble) | scikit-learn | Local inference |
| `cardiffnlp/twitter-roberta-base-sentiment-latest` | Pre-trained sentiment classifier | Communication clarity and professionalism of answers (15% of ensemble) | HuggingFace | Local inference |

### 12.1 Interview Mode — Two Options

The company selects the interview mode on the job configuration page before invitations are sent. The mode is stored on `technical_interview_configs`.

| Mode | Who conducts the interview | What the system does |
| --- | --- | --- |
| **AI Voice Agent** | LiveKit voice agent (automated) | Runs the full structured interview, scores answers via ensemble models, persists results |
| **Human Specialist** | A company-assigned interviewer joins the LiveKit room instead of the agent | System sends the invitation, provides the interviewer with the candidate's CV and question set, and generates an automated feedback report after the session ends |

**Human Specialist flow:**
When this mode is selected the agent process does not start. The interviewer joins the room directly, conducts the session in their own style, and marks it complete. Once the session is closed, the same post-interview ensemble scoring pipeline runs on the recorded transcript — producing a structured feedback report (scores, SHAP breakdown, strengths/weaknesses) that is saved to `TechnicalInterviewReport` and visible in the dashboard.

### 12.2 Question Personalisation (3-Tier Fallback)

```
Tier 1 — Knowledge Base Retrieval (primary, zero LLM):
  Load candidate's skill gaps from SemanticAnalysisReport
  Query kb_question_embeddings via MiniLM-L6-v2 (384-dim) cosine similarity
  Return 5 gap-targeted questions from kb_questions
  Log selections to jr_question_selections for audit

Tier 2 — FocusedInterviewerPersona templates (fallback):
  Template-based questions generated from CV metadata
  (role, years of experience, top skills)

Tier 3 — Generic hardcoded questions (last resort)
```

### 12.3 LiveKit Voice Agent

| Component | Technology | Config |
| --- | --- | --- |
| VAD | Silero | `min_silence_duration=0.6s`, `activation_threshold=0.5` |
| STT | OpenAI Whisper-1 | Via LiveKit plugin |
| LLM | GPT-4o | `temperature=0.2` — low for deterministic technical reasoning |
| TTS | OpenAI Alloy | Via LiveKit plugin |

**Interview structure (enforced via system prompt):**

```
1. Greeting
2. Warm-up (1 soft-skills question)
3. Technical Q1 → Q5 (from KB retrieval)
     After each answer:
       record_answer_score(question_num, score, rationale)  ← tool call
4. Farewell phrase → end_interview()  ← tool call
```

**Silence watchdog:** Nudges the candidate after 25 seconds of silence. Resets on any speech event detected by Silero VAD.

### 12.4 Post-Interview Ensemble Scoring

The LLM's per-question scores are **discarded**. Instead, the full transcript is scored by a 4-model ensemble:

| Model | Weight | Measures |
| --- | --- | --- |
| `cross-encoder/nli-deberta-v3-small` | 35% | Technical depth — entailment vs JD responsibilities |
| `microsoft/codebert-base` | 30% | Code / system-design vocabulary alignment |
| `sklearn TF-IDF` (bigrams) | 20% | Technical keyword coverage |
| `cardiffnlp/twitter-roberta-base-sentiment-latest` | 15% | Communication clarity & professionalism |

**Composite formula:**

```
overall_score = 0.35 × nli_score
              + 0.30 × codebert_score
              + 0.20 × tfidf_score
              + 0.15 × roberta_score

(all individual scores on [0, 1] scale, result × 100)
```

SHAP analytical values are computed per model component and stored as JSON on `TechnicalInterviewSession`.

### 12.5 Completion & Dispatch

| Event | Action |
|---|---|
| `end_interview()` tool call | `_persist_completion()` → `maybe_dispatch_final_ranking.delay()` |
| Candidate disconnects | 15-second grace period → No-show status → same dispatch |
| Deadline expires | Beat scanner `scan_and_dispatch_hr_interviews` picks up the JR |

When **all** technical sessions are `Completed` or `No-show` → `send_hr_interview_invitations` auto-fires.

---

## 13. Stage 7 — HR Interview (Voice Agent)

**Trigger:** All tech sessions terminal OR `interview_deadline` passes.

**Worker task:** `send_hr_interview_invitations(requisition_id)`

Top 10 candidates (by `overall_score DESC`, `Completed` sessions only) receive `HRInterviewSession` rows and email invitations. `JR.hr_interview_deadline = now + 5 days`.

**Models used in this stage:**

| Model | Type | Purpose | Source | Where it runs |
| --- | --- | --- | --- | --- |
| Silero VAD | Pre-trained voice activity detector | Silence detection and nudge triggering | Silero / ONNX | Local inference |
| `openai/whisper-1` | Pre-trained ASR (speech-to-text) | Transcribes candidate speech in real time | OpenAI API | Remote API call |
| `GPT-4o` | Large language model (LLM) | Drives the HR behavioural agent (STAR-method persona) | OpenAI API | Remote API call |
| `openai/alloy` (TTS) | Pre-trained neural TTS | Agent voice output | OpenAI API | Remote API call |
| `cross-encoder/nli-deberta-v3-small` | Pre-trained NLI cross-encoder | Role relevance — measures whether behavioural answers align with JD (55% of ensemble) | HuggingFace | Local inference |
| `BAAI/bge-large-en-v1.5` | Pre-trained bi-encoder (1024-dim) | Semantic depth of answers vs JD (35% of ensemble) | HuggingFace | Local inference |
| `cardiffnlp/twitter-roberta-base-sentiment-latest` | Pre-trained sentiment classifier | Professionalism and tone (10% of ensemble) | HuggingFace | Local inference |
| `SamLowe/roberta-base-go_emotions` | Pre-trained multi-label emotion classifier | 28-class emotion analysis — diagnostic only, not in composite score | HuggingFace | Local inference |

### 13.1 Interview Mode — Two Options

The same two-mode choice available in Stage 6 applies here, stored on `hr_interview_configs`.

| Mode | Who conducts the interview | What the system does |
| --- | --- | --- |
| **AI Voice Agent** | LiveKit voice agent with STAR-method persona | Runs the full behavioural interview, scores via HR ensemble, persists results |
| **Human Specialist** | A company HR specialist or senior manager joins the room | System sends the invitation and candidate brief; generates a structured feedback report (HR composite score, Go-Emotions diagnostic, sentiment breakdown) saved to `HRInterviewReport` after the session closes |

The human-specialist path is common for final-round HR conversations where a senior stakeholder wants a personal touch — the automated feedback report ensures the outcome is still consistently documented and feeds into the final ranking composite.

### 13.2 Same LiveKit Infrastructure, Different Persona

The voice stack is identical to Stage 6. The system prompt switches to a **behavioural STAR-method** interviewer persona:

```
1. Greeting
2. "Tell me about yourself" warm-up
3. Q1: Communication
4. Q2: Teamwork
5. Q3: Leadership
6. Q4: Adaptability
7. Q5: Problem-solving
8. Farewell → end_interview()
```

### 13.3 Post-HR-Interview Ensemble Scoring

| Model | Weight | Measures | Note |
| --- | --- | --- | --- |
| `cross-encoder/nli-deberta-v3-small` | 55% | Role relevance — answers match JD | Primary signal |
| `BAAI/bge-large-en-v1.5` (bi-encoder) | 35% | Semantic depth vs JD | Secondary |
| `cardiffnlp/twitter-roberta-base-sentiment-latest` | 10% | Professionalism / tone | Tertiary |
| `SamLowe/roberta-base-go_emotions` | — | 28-class emotion analysis | Diagnostic only |

**Why is Go-Emotions excluded from the composite?**
The model was trained on Reddit comments. In professional interview contexts, its emotion classifications showed systematic bias against certain communication styles. It is still computed and stored on the session for human review — but excluded from the numeric score.

**HR composite formula:**

```
hr_composite = 0.55 × nli_align_score
             + 0.35 × semantic_depth_score
             + 0.10 × sentiment_score

(all [0, 1] scale, result × 100)
```

**Completion:** Same pattern — `maybe_dispatch_final_ranking_after_hr.delay()` fires when all HR sessions are terminal.

---

## 14. Stage 8 — Final Ranking

**Trigger:** All HR sessions terminal OR `hr_interview_deadline` passes.

**Worker task:** `compute_final_ranking(requisition_id)`

**Lock:** `acquire_jr_lock()` — prevents duplicate runs.

> **No new ML models are invoked in this stage.** The ranking graph reads scores already persisted by Stages 2, 5, 6, and 7, then applies the weighted formula, sigmoid risk scoring, and SHAP computation — all in pure Python arithmetic.

### 14.1 LangGraph — 8 Nodes

```
gather_applications_node
      │  Loads all Applications with SA reports
      ▼
compute_weights_node
      │  Detects job seniority from title keywords
      │  Selects role-aware weight table
      ▼
tech_interview_node
      │  Loads TechnicalInterviewSession ensemble scores
      ▼
hr_analysis_node
      │  Loads HRInterviewSession ensemble scores
      ▼
score_candidates_node
      │  4-component weighted scoring
      │  + Cross-phase SHAP values
      │  + Probabilistic risk scoring
      ▼
sort_and_rank_node
      │  Final sort by weighted_total (red-flag candidates pushed to bottom)
      │  Assign final_rank
      │  Assign recommendation label
      ▼
persist_rankings_node
      │  Upserts FinalRanking rows
      │  JR.status = RANKING_COMPLETE
      ▼
send_decisions_node
      │  Hire / no-hire email to every candidate
```

### 14.2 Role-Aware Weights (compute_weights_node)

Seniority is detected from the job title using keyword matching:

| Level | Keywords | CV | Assessment | Tech | HR |
| --- | --- | --- | --- | --- | --- |
| Junior | junior, entry, graduate, intern, trainee | 20% | 35% | 25% | 20% |
| Mid | (default) | 20% | 25% | 30% | 25% |
| Senior | senior, lead, principal, staff | 15% | 20% | 35% | 30% |
| Staff / Principal | head, director, vp, chief, architect | 10% | 15% | 40% | 35% |

Junior roles weight structured assessment highest — compensating for limited live-performance signal from early-career candidates. Staff roles weight live technical performance highest.

### 14.3 Composite Score (score_candidates_node)

```
weighted_total = w_cv   × cv_score
              + w_a    × assessment_score    (if has_assessment)
              + w_ti   × tech_score          (if has_tech_interview)
              + w_hr   × hr_score            (if has_hr_interview)
```

**Missing stages:** If a stage was not conducted, its weight is redistributed proportionally across the stages that are present.

### 14.4 Probabilistic Risk Score

Replaces hard binary red-flag thresholds with sigmoid-based continuous scoring:

```
RF-1 — Interview Collapse:
  risk_1 = sigmoid(cv_score − 80) × sigmoid(40 − tech_score)
  Fires when: CV is high (≥80) AND live tech is low (≤40)
  Interpretation: candidate may have had their CV written for them

RF-2 — Cheating Suspicion:
  risk_2 = sigmoid(assessment_score − tech_score − 30)
  Fires when: written assessment beats live interview by 30+ points

risk_score = min(1.0, risk_1 + risk_2)     ∈ [0, 1]

soft_penalty:
  weighted_total = weighted_total × (1 − 0.15 × risk_score)
  Applied to ALL candidates (0 risk → no penalty, 1.0 risk → 15% penalty)

red_flag = risk_score ≥ 0.65
```

**Why continuous instead of binary?** Binary thresholds create cliff edges — a candidate scoring 79 gets no penalty while 80 gets flagged. Sigmoid scoring distributes the penalty smoothly.

### 14.5 Pool-Median SHAP Explainability

Each candidate's contribution of each stage to their final score, relative to the pool median:

```
φ_cv         = w_cv   × (cv_score / 100   − median_cv / 100)
φ_assessment = w_a    × (assess / 100     − median_assess / 100)
φ_tech       = w_tech × (tech / 100       − median_tech / 100)
φ_hr         = w_hr   × (hr / 100         − median_hr / 100)
```

- **Positive φ** → above the pool median for that stage (strength)
- **Negative φ** → below the pool median (weakness)

Stored as JSON on `final_rankings.shap_json`.

### 14.6 Recommendation Labels (sort_and_rank_node)

| Rank | Label |
|---|---|
| 1–2 | Strong Hire |
| 3–5 | Hire |
| 6–10 | Potential Hire |
| 11+ | No Hire |
| red_flag = true | Review Required |

---

## 15. Stage 9 — Post-Ranking Deliverables

> **No ML models used in this stage.** All outputs (narrative summaries, emails, Excel report) are generated from stored scores and templates — zero LLM calls.

Automated immediately after `persist_rankings_node` completes.

### 15.1 Hire / No-Hire Emails (send_decisions_node)

Every candidate receives a personalised email:
- **Strong Hire / Hire / Potential Hire** — congratulatory, next steps
- **No Hire** — polite rejection with encouragement
- **Review Required (red-flag)** — placed in human review queue, not auto-rejected

### 15.2 Per-Candidate Narrative Summary

**Zero LLM** — pure template generation.

Combines:
- All 4 phase scores
- SHAP φ values
- Tier label
- Strengths / weaknesses extracted from tech and HR sub-reports

Stored in `final_rankings.summary`.

### 15.3 Excel HR Report

Full candidate rankings table with:
- All 4 stage scores
- Composite weighted total
- SHAP breakdown per stage
- Recommendation label
- Risk score and red-flag flag

Available for download from the company dashboard.

**API routes for final ranking:**

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/jobs/{jobId}/final-ranking` | Ranked candidate list |
| `GET` | `/api/v1/jobs/{jobId}/final-ranking/{candidateId}/shap-report` | Per-candidate SHAP detail |
| `POST` | `/api/v1/jobs/{jobId}/trigger-ranking` | Manual trigger (override) |
| `POST` | `/api/v1/jobs/{jobId}/final-ranking/{candidateId}/shortlist` | Shortlist action |
| `POST` | `/api/v1/jobs/{jobId}/final-ranking/{candidateId}/offer` | Send offer |

---

## 16. Celery Beat Scanners

All scanners are registered in `celery_config.py` with `beat_schedule`. Every scanner is idempotent — running it twice in a row produces no side effects.

| Scanner | Interval | Trigger Condition | Dispatches |
| --- | --- | --- | --- |
| `scan_and_dispatch_scheduled_posts` | 60 sec | `posting_start_date <= today` AND `linkedin_posted = false` | `process_linkedin_publishing` |
| `scan_and_dispatch_cv_ranking` | 5 min | `cv_collection_end_date <= now` AND `status IN (active, published)` | `process_cv_ranking` |
| `scan_and_dispatch_assessment` | 2 min | `status = ranked` AND `shortlist_notified = true` | `process_assessment_generation` |
| `scan_and_dispatch_assessment_ranking` | 5 min | `status = assessment_sent` AND `deadline <= now` AND no pool report yet | `process_assessment_ranking` |
| `scan_and_expire_assessments` | 30 min | Assessments > 72 hours old, not submitted | Expire stale assessments |
| `scan_and_dispatch_hr_interviews` | 5 min | `status = interview_pending` AND `interview_deadline <= now` | `send_hr_interview_invitations` |
| `scan_and_dispatch_final_ranking` | 5 min | `status = hr_interview_pending` AND `hr_deadline <= now` | `compute_final_ranking` |

---

## 17. API Reference Summary

### Auth & Company

```
POST   /api/v1/auth/signup
POST   /api/v1/auth/login
POST   /api/v1/auth/linkedin/callback
GET    /api/v1/auth/linkedin/status
```

### Jobs & Postings

```
POST   /api/v1/jobs
GET    /api/v1/jobs
GET    /api/v1/jobs/{id}
PUT    /api/v1/jobs/{id}
POST   /api/v1/jobs/{id}/publish
GET    /api/v1/jobs/{id}/postings
```

### Applications & Candidates

```
POST   /api/v1/apply
POST   /api/v1/data/upload/{company_id}/{job_id}/{candidate_id}
GET    /api/v1/jobs/{id}/applications
GET    /api/v1/applications/{id}
```

### CV Screening

```
GET    /api/v1/jobs/{id}/semantic-reports
GET    /api/v1/jobs/{id}/semantic-reports/{candidate_id}
```

### Assessment

```
GET    /api/v1/assessment/{id}?token=...
POST   /api/v1/assessment/{id}/submit
GET    /api/v1/jobs/{id}/assessment-leaderboard
GET    /api/v1/jobs/{id}/assessment-reports/{candidate_id}
```

### Interviews

```
GET    /api/v1/jobs/{id}/technical-interviews
GET    /api/v1/jobs/{id}/technical-interviews/{session_id}
GET    /api/v1/jobs/{id}/hr-interviews
GET    /api/v1/jobs/{id}/hr-interviews/{session_id}
```

### Final Ranking

```
GET    /api/v1/jobs/{jobId}/final-ranking
GET    /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shap-report
POST   /api/v1/jobs/{jobId}/trigger-ranking
POST   /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shortlist
POST   /api/v1/jobs/{jobId}/final-ranking/{candidateId}/offer
```

---

## 18. ML Models Reference Card

| Model | Task | Stage(s) | Size | Inference |
| --- | --- | --- | --- | --- |
| `BAAI/bge-large-en-v1.5` | CV-JD semantic similarity (bi-encoder) | 2, 7 | 1.3 GB | Local |
| `BAAI/bge-reranker-v2-m3` | CV-JD cross-encoder reranking | 2 | 560 MB | Local |
| `cross-encoder/ms-marco-MiniLM-L-12-v2` | CE reranker fallback | 2 | 130 MB | Local |
| `all-MiniLM-L6-v2` | Question bank KB retrieval (384-dim) | 3, 6 | 90 MB | Local |
| `cross-encoder/nli-deberta-v3-small` | Technical / HR depth scoring (NLI) | 6, 7, 8 | 180 MB | Local |
| `microsoft/codebert-base` | Code vocabulary alignment | 6 | 500 MB | Local |
| `cardiffnlp/twitter-roberta-base-sentiment-latest` | Tone / professionalism | 6, 7 | 500 MB | Local |
| `SamLowe/roberta-base-go_emotions` | 28-class emotion analysis (diagnostic) | 7 | 500 MB | Local |
| `sklearn TF-IDF` (bigrams) | Technical keyword coverage | 6 | < 1 MB | Local |
| `GPT-4o-mini` | CV reranking, bias corrections, question generation, tiebreaking, keyword fallback | 2, 3, 5 | API | OpenAI |
| `GPT-4o` | Live interview LLM (voice reasoning) | 6, 7 | API | OpenAI |
| `openai/whisper-1` | Speech-to-text (STT) | 6, 7 | API | OpenAI |
| `openai/alloy` (TTS) | Agent voice output | 6, 7 | API | OpenAI |
| `silero-vad` | Voice activity detection | 6, 7 | ~1 MB | Local |
| `spaCy` | Lemmatization for keyword scoring | 5 | ~50 MB | Local |

**Total local model footprint:** ~3.8 GB (without Qdrant index).

---

## 19. Evaluation & Metrics

### 19.1 CV Ranking Metrics (`ranking_evaluation_service.py`)

Offline evaluation against ground-truth labels using `SemanticAnalysisReport.recommendation_summary`:

| Metric | Description |
|---|---|
| NDCG@K | Normalised Discounted Cumulative Gain — measures ranking quality |
| MRR | Mean Reciprocal Rank — how early the first relevant result appears |
| Recall@K | Fraction of relevant candidates in top-K |
| Precision@K | Fraction of top-K that are relevant |
| F1@K | Harmonic mean of Precision@K and Recall@K |

**Graded relevance:**
- `good_fit` → 2.0
- `partial_fit` → 1.0
- `no_fit` → 0.0

**Binary relevance (for MRR, Recall, Precision, F1):**
- `good_fit` → 1.0
- otherwise → 0.0

### 19.2 Assessment Grading Quality

| Measure | Method |
|---|---|
| Keyword coverage | spaCy lemma match + GPT-4o-mini semantic fallback |
| Pool-relative depth | Normalised against strongest answer per question |
| Tiebreaker consistency | GPT-4o-mini pairwise comparison |

### 19.3 Interview Scoring Reliability

Each ensemble model provides an independent signal. SHAP per model component is stored — making it possible to audit which model dominated the score on any given session.

---

## 20. Anti-Loop & Idempotency Guarantees

Multiple safeguards prevent any stage from executing more than once per job requisition:

### 20.1 Processing Lock

```python
# Pseudocode
def acquire_jr_lock(jr_id):
    jr = db.get(JobRequisition, jr_id)
    if jr.processing_status != "idle":
        raise AlreadyProcessing()
    jr.processing_status = "processing"
    db.commit()
    # ... work ...
    jr.processing_status = "idle"
    db.commit()
```

If a worker crashes mid-pipeline, `processing_status` remains `"processing"` until a manual reset or an error handler fires.

### 20.2 Boolean Flags

```
shortlist_notified     — set true before sending shortlist emails
interview_notified     — set true before sending tech interview invitations
hr_interview_notified  — set true before sending HR interview invitations
```

Each Beat scanner checks the flag first. The flag is set atomically before dispatching the Celery task, not after — ensuring that even if the scanner fires twice before the worker starts, only one task is dispatched.

### 20.3 Status Guards

Every worker task begins with:

```python
jr = db.get(JobRequisition, requisition_id)
if jr.status != expected_status:
    return  # already completed or wrong stage
```

---

## 21. Engineering Decisions & Known Issues Fixed

| Issue | Root Cause | Fix Applied |
| --- | --- | --- |
| `submit_assessment` blocked HTTP thread 10–30s | Called GPT-4o-mini synchronously on the request thread | Deferred open-ended grading to post-deadline pipeline; MCQ only on submit |
| `RoomInputOptions` deprecated in LiveKit Agents SDK | SDK API change | Replaced with `RoomOptions` |
| Browser blocked agent audio (no sound in interview) | Browser autoplay policy requires user gesture before AudioContext runs | Added `AudioContext.resume()` unlock button on frontend |
| `_generate_final_reports` used wrong field name `cv_score` | Column is named `semantic_score` | Fixed field name |
| `ranking_row.rank` — no such column | Column is named `final_rank` | Fixed column name |
| `FinalRanking` missing `summary` and `risk_score` columns | Schema not updated after logic addition | Added columns + Alembic migration |
| Enum definitions duplicated between `src/enums/` and `src/models/enums/` | Two separate enum definitions diverged | Consolidated in `src/enums/`; `models/enums/` is now a compatibility shim |
| Go-Emotions (Reddit-trained) in HR composite — bias risk | Model trained on social media, not professional dialogue | Removed from composite, kept as diagnostic column |
| Hard red-flag thresholds (binary at exact cut-offs) | Cliff-edge behaviour: score 79 vs 80 produced disproportionate outcomes | Replaced with sigmoid continuous risk scoring |
| SHAP baselines fixed at 50% | Arbitrary baseline; candidates compared to a phantom average | Changed to pool-median baselines |
| Role-aware weights absent — all seniority levels treated identically | Single weight table for all JRs | Added seniority keyword detection and a 4-level weight table |

---

*Document generated from codebase inspection and implementation notes.*
*Project stack: FastAPI · LangGraph · Celery · LiveKit · PostgreSQL · Redis · HuggingFace Transformers · OpenAI GPT-4o*
