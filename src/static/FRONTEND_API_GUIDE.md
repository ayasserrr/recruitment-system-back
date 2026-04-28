# Recruitment System — Frontend API Integration Guide

## Overview

This backend exposes a **4-stage AI recruitment pipeline** via a RESTful JSON API. All authenticated endpoints require a Bearer token obtained from the auth endpoints.

```
Base URL: http://localhost:8000   (or your deployed backend URL)
Auth:     Authorization: Bearer <token>
Content:  Content-Type: application/json
```

### Pipeline Weights (enforced by the AI pipeline — never re-compute on the frontend)
| Stage | Component | Weight |
|-------|-----------|--------|
| 1 | CV Screening (Semantic Match) | 20% |
| 2 | Technical Assessment (Online Test) | 25% |
| 3 | Technical Interview (Deep Q&A / Code Logic) | 30% |
| 4 | HR Interview (Behavioral / Soft Skills) | 25% |

---

## 1. Authentication

### POST `/api/v1/auth/signup`
Create a new company account.

**Request body:**
```json
{
  "email": "hr@company.com",
  "password": "SecurePass123",
  "name": "Acme Corp",
  "first_name": "Sara",
  "last_name": "Ahmed"
}
```

**Response `200`:**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "company": {
    "id": 1,
    "name": "Acme Corp",
    "email": "hr@company.com"
  }
}
```

### POST `/api/v1/auth/login`
Login to an existing account.

**Request body:**
```json
{
  "email": "hr@company.com",
  "password": "SecurePass123"
}
```

**Response:** Same shape as signup — store `access_token` and attach it as `Authorization: Bearer <token>` on every subsequent request.

---

## 2. Jobs (Dashboard)

### GET `/api/v1/jobs`
List all jobs for the authenticated company with per-stage pipeline counts.

**Query params:** `page=1&page_size=20`

**Response `200`:**
```json
{
  "count": 12,
  "results": [
    {
      "id": 42,
      "jobTitle": "Senior Backend Engineer",
      "department": "Engineering",
      "posted": "2026-04-01",
      "status": "Final Stage",
      "cvs": 148,
      "newToday": 3,
      "semantic": 148,
      "assessment": 40,
      "techInterview": 15,
      "hrInterview": 8,
      "finalCandidates": 3,
      "selectedPlatforms": ["LinkedIn", "Indeed"],
      "postingStartDate": "2026-04-01",
      "postingEndDate": "2026-04-30",
      "requiredSkills": ["Python", "FastAPI"],
      "preferredSkills": ["LangGraph", "Redis"],
      "assessmentCandidatesToAdvance": 40,
      "technicalInterviewCandidatesToAdvance": 15,
      "hrInterviewCandidatesToAdvance": 8,
      "technicalInterviewDuration": "45 minutes",
      "hrInterviewDuration": "30 minutes"
    }
  ]
}
```

**`status` display values** (mapped from internal DB status):
- `"Posted"` → job created, not yet collecting CVs
- `"CV Collection"` → active, accepting applications
- `"In Progress"` → assessment or interview stage running
- `"Final Stage"` → ranking complete
- `"Closed"` → past end date or manually closed

### GET `/api/v1/jobs/{jobId}`
Single job detail. Same shape as a single item in the list above.

### PATCH `/api/v1/jobs/{jobId}`
Update job status.

**Request body:**
```json
{ "status": "Closed" }
```

Valid values: `"active"`, `"published"`, `"closed"`, `"draft"`.

**Response:**
```json
{ "id": 42, "status": "Closed", "message": "Status updated." }
```

### DELETE `/api/v1/jobs/{jobId}`
Permanently delete a job. Returns `204 No Content`.

---

## 3. Job Pipeline Timeline

### GET `/api/v1/jobs/{jobId}/pipeline`
Returns the posting lifecycle for display as a visual timeline.

**Response `200`:**
```json
{
  "jobId": 42,
  "jobTitle": "Senior Backend Engineer",
  "postingEndDate": "2026-04-30",
  "stages": [
    {
      "step": "Job Post Created",
      "status": "completed",
      "date": "2026-04-01",
      "time": "09:00"
    },
    {
      "step": "Bias Detection",
      "status": "completed",
      "date": "2026-04-01"
    },
    {
      "step": "Posted to Platforms",
      "status": "completed",
      "platforms": ["LinkedIn", "Indeed"],
      "date": "2026-04-01"
    },
    {
      "step": "Receiving CVs",
      "status": "active",
      "count": 148,
      "newToday": 3
    },
    {
      "step": "Closed",
      "status": "pending",
      "scheduledDate": "2026-04-30"
    }
  ]
}
```

`status` per stage: `"completed"` | `"active"` | `"pending"`

---

## 4. Stage 1 — Semantic / CV Screening

### GET `/api/v1/jobs/{jobId}/semantic`
Fetch semantic analysis results and per-candidate scores.

**Response `200`:**
```json
{
  "jobId": 42,
  "processingTime": "2m 14s",
  "stats": {
    "totalCandidates": 148,
    "processed": 148,
    "highMatch": 42,
    "mediumMatch": 71,
    "lowMatch": 35,
    "avgScore": 67.4
  },
  "candidates": [
    {
      "id": 101,
      "name": "Layla Hassan",
      "score": 88.5,
      "match": "Excellent",
      "skills": ["Python", "FastAPI", "PostgreSQL"],
      "email": "layla@example.com",
      "phone": "+20 100 000 0000",
      "experience": "5+ years",
      "education": "Bachelor's in Computer Science",
      "summary": "Backend engineer with strong API design skills...",
      "projects": ["E-Commerce Platform", "Real-Time Chat App"]
    }
  ]
}
```

**`match` label values:** `"Excellent"` (≥85) | `"Very Good"` (≥70) | `"Good"` (≥55) | `"Average"` (≥40) | `"Fair"` (<40)

### POST `/api/v1/jobs/{jobId}/semantic/run`
Trigger the semantic analysis Celery task.

**No request body.**

**Response `200`:**
```json
{ "taskId": "uuid-string", "status": "running" }
```

### GET `/api/v1/jobs/{jobId}/semantic/status`
Poll processing progress.

**Response `200`:**
```json
{ "status": "running", "progress": 72 }
```

`status`: `"pending"` | `"running"` | `"completed"` | `"failed"` — `progress`: 0–100.

**How to poll:**
```ts
let interval = setInterval(async () => {
  const res = await api.get(`/api/v1/jobs/${jobId}/semantic/status`);
  if (res.status === "completed" || res.status === "failed") {
    clearInterval(interval);
    // reload candidates list
  }
}, 3000);
```

---

## 5. Stage 2 — Technical Assessment

### GET `/api/v1/jobs/{jobId}/assessment`
Assessment overview stats.

**Response `200`:**
```json
{
  "id": 7,
  "jobTitle": "Senior Backend Engineer",
  "totalCandidates": 40,
  "sent": 40,
  "completed": 32,
  "pending": 8,
  "deadline": "2026-04-20",
  "status": "active",
  "avgScore": 73.2,
  "duration": "60 minutes",
  "questions": 25,
  "passingScore": 70.0
}
```

`status`: `"pending"` | `"active"` | `"completed"`

### GET `/api/v1/jobs/{jobId}/assessment/candidates`
Ranked list of assessment candidates.

**Response `200` (array):**
```json
[
  {
    "id": 101,
    "name": "Layla Hassan",
    "score": 84.5,
    "technical": "Very Good",
    "problemSolving": "Very Good",
    "timeSpent": "48 min",
    "status": "passed",
    "codingScore": null,
    "theoryScore": null,
    "completed": "2026-04-15",
    "shapSummary": "Strong performance in algorithm questions. Slight weakness in system design.",
    "email": "layla@example.com",
    "phone": "+20 100 000 0000",
    "experience": "5+ years",
    "education": "Bachelor's in Computer Science",
    "summary": "...",
    "projects": ["E-Commerce Platform"],
    "skills": ["Python", "FastAPI"]
  }
]
```

`status`: `"passed"` | `"failed"` | `"pending"`

### POST `/api/v1/jobs/{jobId}/assessment/send-invitations`
Send assessment links to a set of candidates.

**Request body:**
```json
{ "candidateIds": [101, 102, 105] }
```

**Response `200`:**
```json
{ "sent": 3, "failed": 0, "message": "3 invitation(s) sent successfully." }
```

---

## 6. Stage 3 — Technical Interview

### GET `/api/v1/jobs/{jobId}/technical-interview`
Overview stats for the technical interview stage.

**Response `200`:**
```json
{
  "id": 3,
  "jobTitle": "Senior Backend Engineer",
  "scheduled": 15,
  "completed": 10,
  "pending": 5,
  "avgScore": 7.4,
  "nextInterview": "2026-04-28 14:00",
  "interviewers": ["Ahmed Karim", "Sara Nour"],
  "duration": "45 minutes",
  "passingScore": 7.0,
  "status": "active"
}
```

`status`: `"pending"` | `"active"` | `"completed"`

### GET `/api/v1/jobs/{jobId}/technical-interview/candidates`
List of candidates with interview results + AI ensemble scores.

**Response `200` (array):**
```json
[
  {
    "id": 101,
    "name": "Layla Hassan",
    "technicalScore": 8.5,
    "problemSolving": 8.0,
    "systemDesign": 7.5,
    "coding": 9.0,
    "communication": 8.0,
    "overall": 8.2,
    "codebertScore": 0.812,
    "robertaDepthScore": 0.743,
    "nliTechnicalScore": 0.869,
    "tfidfTechnicalScore": 0.654,
    "shapSummary": "CodeBERT detects strong semantic depth. RoBERTa-QA shows precise technical answers. DeBERTa NLI confirms alignment with job requirements.",
    "hasTranscript": true,
    "status": "Completed",
    "interviewer": "Ahmed Karim",
    "date": "2026-04-25",
    "feedback": "Excellent system design skills. Strong problem solver.",
    "email": "layla@example.com",
    "phone": "+20 100 000 0000",
    "experience": "5+ years",
    "education": "Bachelor's in Computer Science",
    "summary": "...",
    "projects": ["E-Commerce Platform"],
    "skills": ["Python", "FastAPI"]
  }
]
```

**AI ensemble model scores (0–1 scale per model):**
- `codebertScore` — CodeBERT semantic depth (weight: 40%)
- `robertaDepthScore` — RoBERTa-QA extractive answer quality (weight: 30%)
- `nliTechnicalScore` — DeBERTa NLI technical alignment with JD (weight: 20%)
- `tfidfTechnicalScore` — TF-IDF bigram keyword coverage (weight: 10%)
- `hasTranscript` — `true` when a transcript has been uploaded; enables AI scoring on next ranking run

### POST `/api/v1/jobs/{jobId}/technical-interview/schedule`
Schedule an interview session for a candidate.

**Request body:**
```json
{
  "candidateId": 101,
  "scheduledDate": "2026-04-28",
  "scheduledTime": "14:00",
  "interviewerName": "Ahmed Karim",
  "type": "ai-conducted"
}
```

**Response `201`:**
```json
{ "message": "Interview scheduled.", "sessionId": 55 }
```

### POST `/api/v1/jobs/{jobId}/technical-interview/submit-scores`
Submit human-assessed scores. Optionally attach transcript to enable CodeBERT + RoBERTa-QA analysis on the next ranking run.

**Request body:**
```json
{
  "candidateId": 101,
  "technicalScore": 8.5,
  "problemSolving": 8.0,
  "systemDesign": 7.5,
  "coding": 9.0,
  "communication": 8.0,
  "feedback": "Strong candidate with excellent coding skills.",
  "transcript": "Interviewer: Can you explain async/await in Python? Candidate: Sure, async/await..."
}
```

All score fields are floats, expected in range **1–10**.

**Response `200`:**
```json
{ "message": "Scores submitted.", "candidateId": 101, "overall": 8.2 }
```

### PATCH `/api/v1/jobs/{jobId}/technical-interview/{candidateId}/transcript`
Upload or replace a raw interview transcript for AI scoring. Can be submitted separately from scores (e.g., after a LiveKit voice interview ends).

**Request body:**
```json
{
  "transcript": "Interviewer: Walk me through how you'd design a rate limiter. Candidate: I would use a sliding window algorithm with Redis..."
}
```

**Response `200`:**
```json
{ "message": "Transcript saved.", "candidateId": 101 }
```

---

## 7. Stage 4 — HR Interview

### GET `/api/v1/jobs/{jobId}/hr-interview`
Overview stats for the HR interview stage.

**Response `200`:**
```json
{
  "id": 2,
  "jobTitle": "Senior Backend Engineer",
  "scheduled": 8,
  "completed": 6,
  "pending": 2,
  "avgScore": 7.8,
  "nextInterview": "2026-04-29 10:00",
  "interviewer": "Sara Nour",
  "duration": "30 minutes",
  "passingScore": 7.0,
  "status": "active"
}
```

### GET `/api/v1/jobs/{jobId}/hr-interview/candidates`
List of HR interview candidates with human + AI ensemble scores.

**Response `200` (array):**
```json
[
  {
    "id": 101,
    "name": "Layla Hassan",
    "cultureFit": 9.0,
    "communication": 8.5,
    "leadership": 7.5,
    "motivation": 9.0,
    "teamwork": 8.0,
    "overall": 8.4,
    "emotionScore": 0.821,
    "sentimentScore": 0.774,
    "nliAlignScore": 0.803,
    "semanticDepthScore": 0.755,
    "shapSummary": "Go-Emotions detects high positivity. Sentiment analysis confirms professional tone. DeBERTa NLI strongly aligns with company values.",
    "hasTranscript": true,
    "status": "Completed",
    "interviewer": "Sara Nour",
    "date": "2026-04-28",
    "feedback": "Outstanding cultural fit. Highly motivated.",
    "email": "layla@example.com",
    "phone": "+20 100 000 0000",
    "experience": "5+ years",
    "education": "Bachelor's in Computer Science",
    "summary": "...",
    "projects": [],
    "skills": ["Python", "Team Leadership"]
  }
]
```

**AI ensemble model scores (0–1 scale per model):**
- `emotionScore` — Go-Emotions positive emotion aggregate (weight: 35%)
- `sentimentScore` — RoBERTa-Sentiment tone / professionalism (weight: 30%)
- `nliAlignScore` — DeBERTa NLI responsibility alignment with JD (weight: 20%)
- `semanticDepthScore` — BGE semantic depth vs JD (weight: 15%)

### POST `/api/v1/jobs/{jobId}/hr-interview/schedule`
Same shape as technical interview schedule endpoint.

**Request body:**
```json
{
  "candidateId": 101,
  "scheduledDate": "2026-04-29",
  "scheduledTime": "10:00",
  "interviewerName": "Sara Nour",
  "type": "human"
}
```

**Response `201`:**
```json
{ "message": "HR interview scheduled.", "sessionId": 21 }
```

### POST `/api/v1/jobs/{jobId}/hr-interview/submit-scores`
Submit human HR scores. Include `transcript` to enable Go-Emotions + Sentiment AI analysis.

**Request body:**
```json
{
  "candidateId": 101,
  "cultureFit": 9.0,
  "communication": 8.5,
  "leadership": 7.5,
  "motivation": 9.0,
  "teamwork": 8.0,
  "feedback": "Outstanding cultural fit.",
  "transcript": "Interviewer: Tell me about a time you led a team. Candidate: In my previous role..."
}
```

All score fields are floats in range **1–10**.

**Response `200`:**
```json
{ "message": "HR scores submitted.", "candidateId": 101, "overall": 8.4 }
```

### PATCH `/api/v1/jobs/{jobId}/hr-interview/{candidateId}/transcript`
Upload or replace the HR interview transcript.

**Request body:**
```json
{ "transcript": "Interviewer: How do you handle conflict in a team? Candidate: I believe in..." }
```

**Response `200`:**
```json
{ "message": "Transcript saved.", "candidateId": 101 }
```

---

## 8. Final Ranking

### POST `/api/v1/jobs/{jobId}/trigger-ranking`
Kick off the 8-node LangGraph pipeline in the background. The pipeline:
1. Gathers all applications
2. Computes active weights (redistributes proportionally for missing stages)
3. Runs CodeBERT + RoBERTa-QA on tech transcripts
4. Runs Go-Emotions + Sentiment + DeBERTa on HR transcripts
5. Scores each candidate with 20/25/30/25 weighting
6. Applies red-flag rules (RF-1 and RF-2)
7. Persists rankings + SHAP values to DB
8. Sends hire/no-hire decision emails

**No request body.**

**Response `200`:**
```json
{
  "message": "Final ranking pipeline started in background.",
  "requisitionId": 42,
  "status": "started"
}
```

`status`: `"started"` | `"already_running"`

**Polling strategy:** After triggering, poll `GET /api/v1/jobs/{jobId}` every 5 seconds. When `status` becomes `"Final Stage"`, fetch the ranking list.

### GET `/api/v1/jobs/{jobId}/final-ranking`
Ranked candidate list. Sorted by `finalRank` ascending (rank 1 = best).

**Response `200` (array):**
```json
[
  {
    "id": 101,
    "name": "Layla Hassan",
    "email": "layla@example.com",
    "finalRank": 1,
    "overallScore": 86.75,
    "semanticScore": 88.5,
    "assessmentScore": 84.5,
    "technicalScore": 89.2,
    "hrScore": 84.0,
    "recommendation": "Top Candidate",
    "hireProbability": 95,
    "applicationStatus": "Shortlisted",
    "redFlag": false,
    "redFlagReason": null,
    "shapSummary": "Technical interview contribution dominates (φ=+0.121). HR interview adds positive weight (φ=+0.085). Screening is above average (φ=+0.017)."
  },
  {
    "id": 202,
    "name": "Omar Saleh",
    "email": "omar@example.com",
    "finalRank": 5,
    "overallScore": 61.3,
    "semanticScore": 91.0,
    "assessmentScore": 88.0,
    "technicalScore": 32.5,
    "hrScore": 70.0,
    "recommendation": "No Hire",
    "hireProbability": 30,
    "applicationStatus": "Applied",
    "redFlag": true,
    "redFlagReason": "RF-2: Assessment score (88.0) − Technical Interview score (32.5) = 55.5 pts — possible cheating suspicion.",
    "shapSummary": "Strong CV and assessment undermined by interview collapse. Red flag applied."
  }
]
```

**`recommendation` values:** `"Top Candidate"` | `"Strong Hire"` | `"Hire"` | `"Maybe"` | `"No Hire"`

**Red-flag rules:**
- **RF-1 (Interview Collapse):** `semanticScore ≥ 80 AND technicalScore < 40`
- **RF-2 (Cheating Suspicion):** `assessmentScore − technicalScore > 30 pts`

When `redFlag` is `true`, always show `redFlagReason` prominently in the UI.

### GET `/api/v1/jobs/{jobId}/final-ranking/{candidateId}/shap-report`
Full SHAP explainability breakdown for one candidate.

**Response `200`:**
```json
{
  "candidateId": 101,
  "candidateName": "Layla Hassan",
  "overallScore": 86.75,
  "finalRank": 1,
  "shapScreening": 0.017,
  "shapAssessment": 0.044,
  "shapTechInterview": 0.121,
  "shapHrInterview": 0.085,
  "shapSummary": "Technical interview contribution dominates (φ=+0.121)...",
  "weightScreening": 0.20,
  "weightAssessment": 0.25,
  "weightTechInterview": 0.30,
  "weightHrInterview": 0.25,
  "redFlag": false,
  "redFlagReason": null
}
```

**SHAP values (φ):** Each φ value represents the signed contribution of that stage to the final score above/below the baseline of a 50/100 average candidate. Positive = above baseline, negative = below. Useful for rendering a waterfall chart.

### POST `/api/v1/jobs/{jobId}/final-ranking/{candidateId}/shortlist`
Shortlist a candidate from the final ranking.

**Request body (optional note):**
```json
{ "note": "Excellent culture fit and technical skills." }
```

**Response `200`:**
```json
{ "status": "Shortlisted" }
```

### POST `/api/v1/jobs/{jobId}/final-ranking/{candidateId}/offer`
Send a formal job offer email to the candidate.

**Request body:**
```json
{
  "position": "Senior Backend Engineer",
  "salary": "25,000 EGP / month",
  "startDate": "2026-05-15",
  "department": "Engineering",
  "reportingTo": "CTO",
  "benefits": "Health insurance, annual bonus, remote work",
  "contractType": "Full-time",
  "location": "Cairo, Egypt",
  "notes": "Looking forward to having you on the team!"
}
```

**Response `200`:**
```json
{
  "message": "Offer extended. Email sent.",
  "candidateId": 101,
  "applicationStatus": "Offer Extended"
}
```

---

## 9. Error Handling

All endpoints return consistent error shapes:

```json
{ "detail": "Candidate application not found." }
```

| HTTP Status | Meaning |
|-------------|---------|
| 400 | Bad request / validation error |
| 401 | Missing or invalid token |
| 404 | Resource not found |
| 422 | Pydantic validation error (check request body) |
| 500 | Internal server error |

---

## 10. Using the TypeScript Client

The file `api-client.ts` (in the same directory) is a ready-to-use typed API client. Copy it into your frontend `src/lib/` or `src/services/` directory.

```ts
import { RecruitmentAPI } from "@/lib/api-client";

const api = new RecruitmentAPI(
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  localStorage.getItem("token") ?? ""
);

// ── Jobs list page ──────────────────────────────────────────────────────────
const jobs = await api.get<JobListResponse>("/api/v1/jobs?page=1&page_size=20");

// ── Trigger ranking ─────────────────────────────────────────────────────────
await api.triggerRanking(42);

// ── Poll until done ─────────────────────────────────────────────────────────
const poll = setInterval(async () => {
  const job = await api.get<JobListItem>("/api/v1/jobs/42");
  if (job.status === "Final Stage") {
    clearInterval(poll);
    const ranking = await api.getFinalRanking(42);
    // render ranking table
  }
}, 5000);

// ── SHAP waterfall chart ────────────────────────────────────────────────────
const shap = await api.getSHAPReport(42, 101);
// shap.shapScreening, shap.shapAssessment, shap.shapTechInterview, shap.shapHrInterview

// ── Submit tech scores + transcript in one call ─────────────────────────────
await api.submitTechScores(42, {
  candidateId: 101,
  technicalScore: 8.5,
  problemSolving: 8.0,
  systemDesign: 7.5,
  coding: 9.0,
  communication: 8.0,
  transcript: fullTranscriptText,
});

// ── Or upload transcript separately after LiveKit session ───────────────────
await api.updateTechTranscript(42, 101, fullTranscriptText);
await api.updateHRTranscript(42, 101, hrTranscriptText);

// ── Shortlist + offer flow ──────────────────────────────────────────────────
await api.shortlistCandidate(42, 101, "Top pick for the role.");
await api.sendOffer(42, 101, {
  position: "Senior Backend Engineer",
  salary: "25,000 EGP / month",
  startDate: "2026-05-15",
  department: "Engineering",
  reportingTo: "CTO",
  benefits: "Health insurance, remote work",
  contractType: "Full-time",
  location: "Cairo, Egypt",
});
```

---

## 11. Recommended Page → Endpoint Mapping

| Frontend Page | Primary Endpoints |
|--------------|-------------------|
| Login / Register | `POST /api/v1/auth/login`, `/signup` |
| Jobs Dashboard | `GET /api/v1/jobs` |
| Job Detail / Timeline | `GET /api/v1/jobs/{id}`, `GET /api/v1/jobs/{id}/pipeline` |
| Stage 1 — Screening | `GET /api/v1/jobs/{id}/semantic`, `POST /semantic/run`, `GET /semantic/status` |
| Stage 2 — Assessment | `GET /api/v1/jobs/{id}/assessment`, `GET .../candidates`, `POST .../send-invitations` |
| Stage 3 — Tech Interview | `GET .../technical-interview`, `GET .../candidates`, `POST .../schedule`, `POST .../submit-scores`, `PATCH .../{candidateId}/transcript` |
| Stage 4 — HR Interview | `GET .../hr-interview`, `GET .../candidates`, `POST .../schedule`, `POST .../submit-scores`, `PATCH .../{candidateId}/transcript` |
| Final Ranking | `POST .../trigger-ranking`, `GET .../final-ranking`, `GET .../final-ranking/{id}/shap-report` |
| Candidate Actions | `POST .../shortlist`, `POST .../offer` |

---

## 12. Key Frontend Rules

1. **Never recompute weighted scores on the frontend.** Always use `overallScore` from the DB — it's calculated by the pipeline with the correct 20/25/30/25 weights and proportional redistribution for missing stages.

2. **All stage scores are on a 0–100 scale** in the final ranking response (`semanticScore`, `assessmentScore`, `technicalScore`, `hrScore`). The AI model raw scores in interview candidates (`codebertScore`, `emotionScore`, etc.) are 0–1 normalized floats — do not multiply these by 100 when displaying; show them as percentages by multiplying only in the UI label.

3. **Always check `redFlag`** before displaying recommendation. If `redFlag === true`, show `redFlagReason` prominently and override the visual styling regardless of `recommendation` or `hireProbability`.

4. **Transcripts enable AI scoring.** If `hasTranscript === false` on a candidate, show an "Upload Transcript" button that calls the `PATCH .../transcript` endpoint. AI ensemble scores (`codebertScore`, `emotionScore`, etc.) will be `null` until a ranking run processes the transcript.

5. **Trigger ranking once, poll job status.** After `POST /trigger-ranking`, do not call it again if response `status === "already_running"`. Poll `GET /api/v1/jobs/{id}` every 5 seconds; re-fetch the ranking list when `status` becomes `"Final Stage"`.
