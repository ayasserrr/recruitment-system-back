# Recruitment System — Complete Frontend Implementation Guide

> **Copy this document to your frontend repo.** Every section maps to a specific page or component and shows the exact API calls needed, the exact request bodies, and the exact response fields to render.

---

## Setup

```ts
// src/lib/api.ts  — paste this at the top of every project
const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = localStorage.getItem("token");
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Unknown error");
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}
```

---

## Page 1 — Login / Register

### Register (create company account)
```ts
// POST /api/v1/auth/signup
const body = {
  email: "hr@company.com",
  password: "SecurePass123",
  name: "Acme Corp",       // company name
  first_name: "Sara",
  last_name: "Ahmed",
};

const data = await apiFetch<{
  access_token: string;
  token_type: "bearer";
  company: { id: number; name: string; email: string };
}>("/api/v1/auth/signup", {
  method: "POST",
  body: JSON.stringify(body),
});

localStorage.setItem("token", data.access_token);
// → redirect to /dashboard
```

### Login
```ts
// POST /api/v1/auth/login
const body = {
  email: "hr@company.com",
  password: "SecurePass123",
};

const data = await apiFetch<{
  access_token: string;
  token_type: "bearer";
  company: { id: number; name: string; email: string };
}>("/api/v1/auth/login", {
  method: "POST",
  body: JSON.stringify(body),
});

localStorage.setItem("token", data.access_token);
// → redirect to /dashboard
```

**Error handling:** `401` → wrong credentials. `400` → email already registered (during signup).

---

## Page 2 — Jobs Dashboard (`/dashboard`)

### Load jobs list
```ts
// GET /api/v1/jobs?page=1&page_size=20
const data = await apiFetch<{
  count: number;
  results: JobListItem[];
}>("/api/v1/jobs?page=1&page_size=20");

// data.count  → total jobs (for pagination)
// data.results → array of jobs
```

**Each `JobListItem` has:**
```ts
interface JobListItem {
  id: number;
  jobTitle: string;
  department: string | null;
  posted: string | null;          // "YYYY-MM-DD"
  status: string;                 // see Status Values below
  cvs: number;                    // total applications
  newToday: number;               // applications today
  semantic: number;               // CVs processed by AI
  assessment: number;             // candidates sent assessment
  techInterview: number;          // technical interviews scheduled
  hrInterview: number;            // HR interviews scheduled
  finalCandidates: number;        // candidates marked "Selected"
  selectedPlatforms: string[];    // ["LinkedIn", "Indeed"]
  postingStartDate: string | null;
  postingEndDate: string | null;
  requiredSkills: string[];
  preferredSkills: string[];
  assessmentCandidatesToAdvance: number | null;
  technicalInterviewCandidatesToAdvance: number | null;
  hrInterviewCandidatesToAdvance: number | null;
  technicalInterviewDuration: string | null;  // "45 minutes"
  hrInterviewDuration: string | null;
}
```

**`status` display values — map these to UI badge colors:**
| Value returned | Show as | Color |
|---|---|---|
| `"Posted"` | Posted | Blue |
| `"CV Collection"` | Collecting CVs | Green |
| `"In Progress"` | In Progress | Yellow |
| `"Final Stage"` | Final Stage | Purple |
| `"Closed"` | Closed | Gray |

### Update job status
```ts
// PATCH /api/v1/jobs/{jobId}
await apiFetch(`/api/v1/jobs/${jobId}`, {
  method: "PATCH",
  body: JSON.stringify({ status: "Closed" }),
});
// Valid values: "active" | "published" | "closed" | "draft"
```

### Delete job
```ts
// DELETE /api/v1/jobs/{jobId}
await apiFetch(`/api/v1/jobs/${jobId}`, { method: "DELETE" });
// Returns 204 No Content — just remove from local state
```

---

## Page 3 — Job Detail / Timeline (`/jobs/[id]`)

### Load single job
```ts
// GET /api/v1/jobs/{jobId}
const job = await apiFetch<JobListItem>(`/api/v1/jobs/${jobId}`);
// Same shape as a single item in the jobs list
```

### Load pipeline timeline
```ts
// GET /api/v1/jobs/{jobId}/pipeline
const pipeline = await apiFetch<{
  jobId: number;
  jobTitle: string;
  postingEndDate: string | null;
  stages: Array<{
    step: string;
    status: "completed" | "active" | "pending";
    date: string | null;
    time: string | null;
    platforms: string[] | null;
    count: number | null;
    newToday: number | null;
    note: string | null;
    scheduledDate: string | null;
  }>;
}>(`/api/v1/jobs/${jobId}/pipeline`);

// pipeline.stages → render as a vertical timeline
// stage.status === "completed" → green checkmark
// stage.status === "active"    → pulsing / highlighted
// stage.status === "pending"   → gray / disabled
```

---

## Page 4 — Stage 1: CV Screening (`/jobs/[id]/screening`)

### Load screening results
```ts
// GET /api/v1/jobs/{jobId}/semantic
const data = await apiFetch<{
  jobId: number;
  processingTime: string | null;
  stats: {
    totalCandidates: number;
    processed: number;
    highMatch: number;    // score >= 70
    mediumMatch: number;  // 40 <= score < 70
    lowMatch: number;     // score < 40
    avgScore: number;
  };
  candidates: Array<{
    id: number;
    name: string;
    score: number;         // 0–100
    match: string;         // "Excellent" | "Very Good" | "Good" | "Average" | "Fair"
    skills: string[];
    email: string | null;
    phone: string | null;
    experience: string | null;    // "5+ years"
    education: string | null;     // "Bachelor's in Computer Science"
    summary: string | null;
    projects: string[];
  }>;
}>(`/api/v1/jobs/${jobId}/semantic`);

// data.stats   → show 4 stat cards
// data.candidates → render table, sorted by score desc (already sorted)
```

**`match` badge colors:**
| Value | Color |
|---|---|
| `"Excellent"` (≥85) | Green |
| `"Very Good"` (≥70) | Teal |
| `"Good"` (≥55) | Blue |
| `"Average"` (≥40) | Yellow |
| `"Fair"` (<40) | Red |

### Trigger semantic analysis (run AI on CVs)
```ts
// POST /api/v1/jobs/{jobId}/semantic/run
const task = await apiFetch<{
  taskId: string;
  status: "running";
}>(`/api/v1/jobs/${jobId}/semantic/run`, { method: "POST" });

// After triggering, poll progress:
const poll = setInterval(async () => {
  const progress = await apiFetch<{
    status: "pending" | "running" | "completed" | "failed";
    progress: number;  // 0–100
  }>(`/api/v1/jobs/${jobId}/semantic/status`);

  updateProgressBar(progress.progress);  // show % in UI

  if (progress.status === "completed" || progress.status === "failed") {
    clearInterval(poll);
    if (progress.status === "completed") {
      // reload candidate list
      await loadScreeningResults();
    }
  }
}, 3000);  // poll every 3 seconds
```

---

## Page 5 — Stage 2: Technical Assessment (`/jobs/[id]/assessment`)

### Load assessment overview
```ts
// GET /api/v1/jobs/{jobId}/assessment
const overview = await apiFetch<{
  id: number;
  jobTitle: string;
  totalCandidates: number;
  sent: number;
  completed: number;
  pending: number;
  deadline: string | null;      // "YYYY-MM-DD"
  status: "pending" | "active" | "completed";
  avgScore: number;
  duration: string;             // "60 minutes"
  questions: number;
  passingScore: number;         // 70.0
}>(`/api/v1/jobs/${jobId}/assessment`);

// If 404 → "No assessment configured for this job"
// Show: completed/totalCandidates progress bar, avgScore, deadline countdown
```

### Load assessment candidates
```ts
// GET /api/v1/jobs/{jobId}/assessment/candidates
const candidates = await apiFetch<Array<{
  id: number;
  name: string;
  score: number;               // 0–100
  technical: string;           // "Excellent" | "Very Good" | "Good" | "Average"
  problemSolving: string;
  timeSpent: string;           // "48 min" or "N/A"
  status: "passed" | "failed" | "pending";
  codingScore: number | null;
  theoryScore: number | null;
  completed: string | null;    // "YYYY-MM-DD"
  shapSummary: string | null;  // AI explanation text — show in tooltip/drawer
  email: string | null;
  phone: string | null;
  experience: string | null;
  education: string | null;
  summary: string | null;
  projects: string[];
  skills: string[];
}>>(`/api/v1/jobs/${jobId}/assessment/candidates`);

// sorted by rank ascending (rank 1 first)
// candidates with shapSummary → show info icon that opens an "AI Insight" tooltip
```

### Send assessment invitations
```ts
// POST /api/v1/jobs/{jobId}/assessment/send-invitations
// Call this when the HR user selects candidates from the screening list
// and clicks "Send Assessment"

const result = await apiFetch<{
  sent: number;
  failed: number;
  message: string;
}>(`/api/v1/jobs/${jobId}/assessment/send-invitations`, {
  method: "POST",
  body: JSON.stringify({
    candidateIds: [101, 102, 105],  // candidate_id values from screening list
  }),
});

// result.sent   → show "X invitations sent"
// result.failed → show warning if > 0
```

---

## Page 6 — Stage 3: Technical Interview (`/jobs/[id]/technical-interview`)

### Load overview
```ts
// GET /api/v1/jobs/{jobId}/technical-interview
const overview = await apiFetch<{
  id: number;
  jobTitle: string;
  scheduled: number;
  completed: number;
  pending: number;
  avgScore: number;
  nextInterview: string | null;  // "2026-04-28 14:00"
  interviewers: string[];
  duration: string;
  passingScore: number;          // 7.0 (out of 10)
  status: "pending" | "active" | "completed";
}>(`/api/v1/jobs/${jobId}/technical-interview`);
```

### Load candidates with AI ensemble scores
```ts
// GET /api/v1/jobs/{jobId}/technical-interview/candidates
const candidates = await apiFetch<Array<{
  id: number;
  name: string;

  // Human-submitted sub-scores (1–10 scale, null if not submitted yet)
  technicalScore: number | null;
  problemSolving: number | null;
  systemDesign: number | null;
  coding: number | null;
  communication: number | null;
  overall: number | null;        // average of the 5 above

  // AI model scores (0–1 float, null until transcript is analyzed)
  codebertScore: number | null;       // CodeBERT: semantic depth (40% weight)
  robertaDepthScore: number | null;   // RoBERTa-QA: answer precision (30%)
  nliTechnicalScore: number | null;   // DeBERTa NLI: JD alignment (20%)
  tfidfTechnicalScore: number | null; // TF-IDF: keyword coverage (10%)
  shapSummary: string | null;         // AI explanation — show in drawer
  hasTranscript: boolean;             // true = transcript uploaded

  status: string;        // "Scheduled" | "Completed" | "No-show" | "Cancelled"
  interviewer: string | null;
  date: string | null;   // "YYYY-MM-DD"
  feedback: string | null;

  email: string | null;
  phone: string | null;
  experience: string | null;
  education: string | null;
  summary: string | null;
  projects: string[];
  skills: string[];
}>>(`/api/v1/jobs/${jobId}/technical-interview/candidates`);

// UI logic:
// hasTranscript === false → show "Upload Transcript" button (yellow badge)
// codebertScore !== null  → show AI score bar next to human scores
// shapSummary !== null    → show "AI Insight" icon that opens a panel
```

### Schedule an interview
```ts
// POST /api/v1/jobs/{jobId}/technical-interview/schedule
const result = await apiFetch<{
  message: string;
  sessionId: number;
}>(`/api/v1/jobs/${jobId}/technical-interview/schedule`, {
  method: "POST",
  body: JSON.stringify({
    candidateId: 101,
    scheduledDate: "2026-04-28",   // "YYYY-MM-DD"
    scheduledTime: "14:00",        // "HH:MM" (24h)
    interviewerName: "Ahmed Karim",
    type: "ai-conducted",          // or "human"
  }),
});
// Calling again for the same candidateId will reschedule (not create duplicate)
```

### Submit human scores (+ optional transcript)
```ts
// POST /api/v1/jobs/{jobId}/technical-interview/submit-scores
const result = await apiFetch<{
  message: string;
  candidateId: number;
  overall: number;
}>(`/api/v1/jobs/${jobId}/technical-interview/submit-scores`, {
  method: "POST",
  body: JSON.stringify({
    candidateId: 101,
    technicalScore: 8.5,    // 1–10
    problemSolving: 8.0,
    systemDesign: 7.5,
    coding: 9.0,
    communication: 8.0,
    feedback: "Strong candidate with excellent coding skills.",
    transcript: fullTranscriptText,  // optional — enables AI scoring
  }),
});
// result.overall → computed average — update the row in the UI
```

### Upload transcript separately (e.g., after LiveKit session)
```ts
// PATCH /api/v1/jobs/{jobId}/technical-interview/{candidateId}/transcript
await apiFetch(
  `/api/v1/jobs/${jobId}/technical-interview/${candidateId}/transcript`,
  {
    method: "PATCH",
    body: JSON.stringify({
      transcript: "Interviewer: Can you explain async/await...\nCandidate: Sure...",
    }),
  }
);
// After this, hasTranscript becomes true
// AI scores will be populated after the next trigger-ranking run
```

---

## Page 7 — Stage 4: HR Interview (`/jobs/[id]/hr-interview`)

### Load overview
```ts
// GET /api/v1/jobs/{jobId}/hr-interview
const overview = await apiFetch<{
  id: number;
  jobTitle: string;
  scheduled: number;
  completed: number;
  pending: number;
  avgScore: number;
  nextInterview: string | null;
  interviewer: string | null;
  duration: string;
  passingScore: number;   // 7.0
  status: "pending" | "active" | "completed";
}>(`/api/v1/jobs/${jobId}/hr-interview`);
```

### Load candidates with AI ensemble scores
```ts
// GET /api/v1/jobs/{jobId}/hr-interview/candidates
const candidates = await apiFetch<Array<{
  id: number;
  name: string;

  // Human sub-scores (1–10 scale)
  cultureFit: number | null;
  communication: number | null;
  leadership: number | null;
  motivation: number | null;
  teamwork: number | null;
  overall: number | null;

  // AI model scores (0–1 float)
  emotionScore: number | null;        // Go-Emotions positivity (35% weight)
  sentimentScore: number | null;      // RoBERTa professionalism (30%)
  nliAlignScore: number | null;       // DeBERTa NLI company-values alignment (20%)
  semanticDepthScore: number | null;  // BGE depth vs JD (15%)
  shapSummary: string | null;
  hasTranscript: boolean;

  status: string;
  interviewer: string | null;
  date: string | null;
  feedback: string | null;

  email: string | null;
  phone: string | null;
  experience: string | null;
  education: string | null;
  summary: string | null;
  projects: string[];
  skills: string[];
}>>(`/api/v1/jobs/${jobId}/hr-interview/candidates`);
```

### Schedule HR interview
```ts
// POST /api/v1/jobs/{jobId}/hr-interview/schedule
await apiFetch(`/api/v1/jobs/${jobId}/hr-interview/schedule`, {
  method: "POST",
  body: JSON.stringify({
    candidateId: 101,
    scheduledDate: "2026-04-29",
    scheduledTime: "10:00",
    interviewerName: "Sara Nour",
    type: "human",
  }),
});
```

### Submit HR scores (+ optional transcript)
```ts
// POST /api/v1/jobs/{jobId}/hr-interview/submit-scores
const result = await apiFetch<{
  message: string;
  candidateId: number;
  overall: number;
}>(`/api/v1/jobs/${jobId}/hr-interview/submit-scores`, {
  method: "POST",
  body: JSON.stringify({
    candidateId: 101,
    cultureFit: 9.0,       // 1–10
    communication: 8.5,
    leadership: 7.5,
    motivation: 9.0,
    teamwork: 8.0,
    feedback: "Outstanding cultural fit. Highly motivated.",
    transcript: hrTranscriptText,  // optional
  }),
});
```

### Upload HR transcript separately
```ts
// PATCH /api/v1/jobs/{jobId}/hr-interview/{candidateId}/transcript
await apiFetch(
  `/api/v1/jobs/${jobId}/hr-interview/${candidateId}/transcript`,
  {
    method: "PATCH",
    body: JSON.stringify({
      transcript: "Interviewer: Tell me about a conflict you resolved...\nCandidate: In 2024...",
    }),
  }
);
```

---

## Page 8 — Final Ranking (`/jobs/[id]/final-ranking`)

### Step 1 — Trigger the ranking pipeline
```ts
// POST /api/v1/jobs/{jobId}/trigger-ranking
const trigger = await apiFetch<{
  message: string;
  requisitionId: number;
  status: "started" | "already_running";
}>(`/api/v1/jobs/${jobId}/trigger-ranking`, { method: "POST" });

if (trigger.status === "already_running") {
  showToast("Pipeline is already running. Please wait.");
}
```

### Step 2 — Poll job status until ranking is complete
```ts
// Poll GET /api/v1/jobs/{jobId} every 5 seconds
const poll = setInterval(async () => {
  const job = await apiFetch<JobListItem>(`/api/v1/jobs/${jobId}`);
  if (job.status === "Final Stage") {
    clearInterval(poll);
    await loadFinalRanking();   // → Step 3
  }
}, 5000);
```

### Step 3 — Load the ranked list
```ts
// GET /api/v1/jobs/{jobId}/final-ranking
const ranking = await apiFetch<Array<{
  id: number;
  name: string;
  email: string | null;
  finalRank: number | null;    // 1 = best candidate
  overallScore: number;        // 0–100, pipeline-computed (20/25/30/25 weighted)

  // Per-stage scores (0–100 each)
  semanticScore: number | null;    // Stage 1: CV match
  assessmentScore: number | null;  // Stage 2: online test
  technicalScore: number | null;   // Stage 3: technical interview
  hrScore: number | null;          // Stage 4: HR interview

  // Decision fields
  recommendation: string;     // see Recommendation Values below
  hireProbability: number;    // 0–100 integer
  applicationStatus: string;  // "Applied" | "Shortlisted" | "Offer Extended"

  // Red-flag detection — ALWAYS check this first
  redFlag: boolean;
  redFlagReason: string | null;

  // SHAP explanation
  shapSummary: string | null;
}>>(`/api/v1/jobs/${jobId}/final-ranking`);
```

**`recommendation` → badge colors:**
| Value | Color | When |
|---|---|---|
| `"Top Candidate"` | Green | overallScore ≥ 90 |
| `"Strong Hire"` | Teal | overallScore ≥ 80 |
| `"Hire"` | Blue | overallScore ≥ 65 |
| `"Maybe"` | Yellow | overallScore ≥ 50 |
| `"No Hire"` | Red | overallScore < 50 or redFlag === true |

**Critical UI rule for red flags:**
```ts
// ALWAYS override recommendation display if redFlag is true
function renderRecommendation(candidate) {
  if (candidate.redFlag) {
    return (
      <div>
        <Badge color="red">No Hire</Badge>
        <Tooltip content={candidate.redFlagReason}>
          <WarningIcon color="red" />
        </Tooltip>
      </div>
    );
  }
  return <Badge color={getBadgeColor(candidate.recommendation)}>
    {candidate.recommendation}
  </Badge>;
}
```

**Red flag rules (backend applies these — display only):**
- **RF-1:** `semanticScore ≥ 80 AND technicalScore < 40` → "Interview Collapse"
- **RF-2:** `assessmentScore − technicalScore > 30` → "Cheating Suspicion"

### Step 4 — SHAP report drawer (per candidate)
```ts
// GET /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shap-report
const shap = await apiFetch<{
  candidateId: number;
  candidateName: string;
  overallScore: number;
  finalRank: number | null;
  shapScreening: number | null;     // φ value: signed contribution of Stage 1
  shapAssessment: number | null;    // φ value: signed contribution of Stage 2
  shapTechInterview: number | null; // φ value: signed contribution of Stage 3
  shapHrInterview: number | null;   // φ value: signed contribution of Stage 4
  shapSummary: string | null;       // human-readable narrative
  weightScreening: number | null;   // 0.20
  weightAssessment: number | null;  // 0.25
  weightTechInterview: number | null; // 0.30
  weightHrInterview: number | null;   // 0.25
  redFlag: boolean;
  redFlagReason: string | null;
}>(`/api/v1/jobs/${jobId}/final-ranking/${candidateId}/shap-report`);

// Render a horizontal waterfall bar chart:
// Each bar = shapXxx value, positive = green (above baseline), negative = red
// Baseline assumption: a 50/100 average candidate (φ = 0)
// Label each bar: "Screening (20%)", "Assessment (25%)", etc.
```

**Waterfall chart data:**
```ts
const waterfallBars = [
  { label: "CV Screening",        weight: "20%", phi: shap.shapScreening },
  { label: "Assessment",          weight: "25%", phi: shap.shapAssessment },
  { label: "Technical Interview", weight: "30%", phi: shap.shapTechInterview },
  { label: "HR Interview",        weight: "25%", phi: shap.shapHrInterview },
];
// phi > 0 → candidate performed above average in this stage (green bar)
// phi < 0 → candidate performed below average in this stage (red bar)
// phi = null → stage was not completed / not in scope
```

### Shortlist a candidate
```ts
// POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shortlist
const result = await apiFetch<{ status: "Shortlisted" }>(
  `/api/v1/jobs/${jobId}/final-ranking/${candidateId}/shortlist`,
  {
    method: "POST",
    body: JSON.stringify({
      note: "Top pick — excellent technical depth and culture fit.",
    }),
  }
);
// Update applicationStatus in local state to "Shortlisted"
```

### Send offer
```ts
// POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/offer
const result = await apiFetch<{
  message: string;
  candidateId: number;
  applicationStatus: "Offer Extended";
}>(`/api/v1/jobs/${jobId}/final-ranking/${candidateId}/offer`, {
  method: "POST",
  body: JSON.stringify({
    position: "Senior Backend Engineer",
    salary: "25,000 EGP / month",
    startDate: "2026-05-15",
    department: "Engineering",
    reportingTo: "CTO",
    benefits: "Health insurance, annual bonus, remote work options",
    contractType: "Full-time",
    location: "Cairo, Egypt",
    notes: "We look forward to having you on the team!",
  }),
});
// Show success toast: "Offer sent to candidate's email"
// Update applicationStatus to "Offer Extended"
```

---

## Page 9 — Shortlist (`/shortlist`)

### Load all shortlisted candidates (company-wide)
```ts
// GET /api/v1/shortlist?page=1&page_size=20
// Optional filter: ?job_id=42

const data = await apiFetch<{
  count: number;
  results: Array<{
    id: number;
    name: string;
    email: string | null;
    phone: string | null;
    experience: string | null;
    education: string | null;
    summary: string | null;
    projects: string[];
    score: number | null;
    match: string | null;
    skills: string[];
    jobTitle: string;
    shortlistedFrom: string;    // "Final Ranking" | "Semantic Analysis" etc.
    shortlistedDate: string;    // "YYYY-MM-DD"
    shortlistNote: string | null;
  }>;
}>("/api/v1/shortlist?page=1&page_size=20");
```

### Remove from shortlist
```ts
// DELETE /api/v1/shortlist/{candidateId}
await apiFetch(`/api/v1/shortlist/${candidateId}`, { method: "DELETE" });
// Returns 204 — remove row from local state
```

---

## Page 10 — Analytics (`/analytics`)

### Load company-wide analytics
```ts
// GET /api/v1/analytics/overview
const analytics = await apiFetch<{
  overview: {
    totalApplications: number;
    totalHires: number;
    avgTimeToHire: number;      // days
    pipelineEfficiency: number; // 0–100
    offerAcceptanceRate: number;
  };
  applicationSources: Array<{ source: string; count: number }>;
  assessmentMetrics: {
    sent: number;
    completed: number;
    avgScore: number;
    passRate: number;
  };
  interviewMetrics: {
    technical: number;
    hr: number;
    avgScore: number;
    satisfaction: number;
  };
  hiringBreakdown: {
    total: number;
    avgDaysToHire: number;
    acceptanceRate: number;
    byDepartment: Array<{ department: string; count: number }>;
  };
  efficiency: {
    hoursSaved: number;
    automationRate: number;
    manualTasksReduced: number;
    productivityGain: number;
  };
  trends: {
    monthlyApplications: number[];  // last 12 months
    monthlyHires: number[];
    satisfactionScores: number[];
    efficiencyGains: number[];
  };
}>("/api/v1/analytics/overview");
```

---

## Error Handling (global)

Add this wrapper around `apiFetch` or use an interceptor:

```ts
try {
  const data = await apiFetch("/api/v1/...");
} catch (err) {
  const message = (err as Error).message;

  if (message.includes("401") || message.includes("Unauthorized")) {
    localStorage.removeItem("token");
    router.push("/login");
    return;
  }

  if (message.includes("404")) {
    showToast("Resource not found.", "warning");
    return;
  }

  showToast(`Error: ${message}`, "error");
}
```

---

## Complete Page Flow: Final Ranking (end-to-end)

This is the most complex flow. Here is the exact sequence of calls:

```ts
async function finalRankingPageLoad(jobId: number) {
  // 1. Load job to check current status
  const job = await apiFetch<JobListItem>(`/api/v1/jobs/${jobId}`);

  if (job.status !== "Final Stage") {
    // Show "Trigger Ranking" button — do not auto-trigger
    return { status: "not_ready", job };
  }

  // 2. Load ranked list
  const ranking = await apiFetch<FinalRankingItem[]>(
    `/api/v1/jobs/${jobId}/final-ranking`
  );

  return { status: "ready", job, ranking };
}

async function onTriggerRankingClick(jobId: number) {
  // 1. Trigger pipeline
  const trigger = await apiFetch<TriggerRankingResponse>(
    `/api/v1/jobs/${jobId}/trigger-ranking`,
    { method: "POST" }
  );

  if (trigger.status === "already_running") {
    showToast("Pipeline already running. Please wait.");
    startPolling(jobId);
    return;
  }

  showToast("Ranking pipeline started. This may take 1–3 minutes.");
  startPolling(jobId);
}

function startPolling(jobId: number) {
  setLoading(true);
  const poll = setInterval(async () => {
    const job = await apiFetch<JobListItem>(`/api/v1/jobs/${jobId}`);
    if (job.status === "Final Stage") {
      clearInterval(poll);
      setLoading(false);
      const ranking = await apiFetch<FinalRankingItem[]>(
        `/api/v1/jobs/${jobId}/final-ranking`
      );
      setRanking(ranking);
    }
  }, 5000);
}

async function onViewSHAP(jobId: number, candidateId: number) {
  const shap = await apiFetch<SHAPReportResponse>(
    `/api/v1/jobs/${jobId}/final-ranking/${candidateId}/shap-report`
  );
  openSHAPDrawer(shap);
}

async function onShortlist(jobId: number, candidateId: number, note: string) {
  await apiFetch(`/api/v1/jobs/${jobId}/final-ranking/${candidateId}/shortlist`, {
    method: "POST",
    body: JSON.stringify({ note }),
  });
  // update local state: candidate.applicationStatus = "Shortlisted"
}

async function onSendOffer(jobId: number, candidateId: number, offerDetails: SendOfferRequest) {
  const result = await apiFetch(
    `/api/v1/jobs/${jobId}/final-ranking/${candidateId}/offer`,
    {
      method: "POST",
      body: JSON.stringify(offerDetails),
    }
  );
  // update local state: candidate.applicationStatus = "Offer Extended"
  showToast(result.message);
}
```

---

## Important Rules (read before writing any component)

1. **Never compute `overallScore` on the frontend.** It comes from the DB, already weighted 20/25/30/25. Just display `candidate.overallScore`.

2. **All final ranking stage scores are 0–100.** `semanticScore`, `assessmentScore`, `technicalScore`, `hrScore` are all on a 100-point scale. Display them as-is.

3. **AI model raw scores in interview pages are 0–1 floats.** `codebertScore`, `emotionScore`, etc. — multiply by 100 only for display labels (e.g., show `(codebert * 100).toFixed(1) + "%"`).

4. **Red flag always overrides recommendation.** If `candidate.redFlag === true`, display `"No Hire"` with a red warning badge, regardless of what `recommendation` says. Always show `redFlagReason` below the badge.

5. **`hasTranscript === false` means AI scores will be null.** Show an "Upload Transcript" button. AI scores (`codebertScore`, `emotionScore`, etc.) will be `null` until a transcript is uploaded AND trigger-ranking is called.

6. **Trigger ranking once, then poll.** Never call `trigger-ranking` more than once. If `status === "already_running"`, just start polling.

7. **SHAP φ values can be negative.** A negative SHAP value means that stage pulled the overall score *down* from the baseline (average candidate). Render positive as green bars, negative as red bars in the waterfall chart.
