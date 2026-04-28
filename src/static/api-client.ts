/**
 * Recruitment System — TypeScript API Client
 *
 * 4-Stage Pipeline: Screening (20%) / Assessment (25%) / Tech Interview (30%) / HR Interview (25%)
 *
 * Usage:
 *   const api = new RecruitmentAPI("https://your-backend.com", token);
 *   const ranking = await api.getFinalRanking(42);
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface FinalRankingItem {
  id: number;
  name: string;
  email: string | null;
  finalRank: number | null;
  overallScore: number;
  // Per-stage scores (0–100 scale)
  semanticScore: number | null;       // Stage 1: CV screening
  assessmentScore: number | null;     // Stage 2: Online assessment
  technicalScore: number | null;      // Stage 3: Technical interview
  hrScore: number | null;             // Stage 4: HR interview
  // Decision
  recommendation: string;             // "Top Candidate" | "Strong Hire" | "Hire" | "Maybe" | "No Hire"
  hireProbability: number;            // 0–100
  applicationStatus: string;
  // Red-flag detection
  redFlag: boolean;
  redFlagReason: string | null;
  // SHAP explainability
  shapSummary: string | null;
}

export interface SHAPReportResponse {
  candidateId: number;
  candidateName: string;
  overallScore: number;
  finalRank: number | null;
  shapScreening: number | null;
  shapAssessment: number | null;
  shapTechInterview: number | null;
  shapHrInterview: number | null;
  shapSummary: string | null;
  weightScreening: number | null;
  weightAssessment: number | null;
  weightTechInterview: number | null;
  weightHrInterview: number | null;
  redFlag: boolean;
  redFlagReason: string | null;
}

export interface TriggerRankingResponse {
  message: string;
  requisitionId: number;
  status: "started" | "already_running" | "completed";
}

export interface ShortlistRequest {
  note?: string;
}

export interface SendOfferRequest {
  position: string;
  salary: string;
  startDate: string;
  department: string;
  reportingTo: string;
  benefits: string;
  contractType: string;
  location: string;
  notes?: string;
}

// ── Assessment Types ──────────────────────────────────────────────────────────

export interface AssessmentOverview {
  id: number;
  jobTitle: string;
  totalCandidates: number;
  sent: number;
  completed: number;
  pending: number;
  deadline: string | null;
  status: string;
  avgScore: number;
  duration: string;
  questions: number;
  passingScore: number;
}

export interface AssessmentCandidate {
  id: number;
  name: string;
  score: number;
  technical: string;
  problemSolving: string;
  timeSpent: string;
  status: string;
  codingScore: number | null;
  theoryScore: number | null;
  completed: string | null;
  shapSummary: string | null;
  email: string | null;
  phone: string | null;
  experience: string | null;
  education: string | null;
  summary: string | null;
  projects: string[];
  skills: string[];
}

// ── Technical Interview Types ─────────────────────────────────────────────────

export interface TechInterviewOverview {
  id: number;
  jobTitle: string;
  scheduled: number;
  completed: number;
  pending: number;
  avgScore: number;
  nextInterview: string | null;
  interviewers: string[];
  duration: string;
  passingScore: number;
  status: string;
}

export interface TechInterviewCandidate {
  id: number;
  name: string;
  // Human sub-scores
  technicalScore: number | null;
  problemSolving: number | null;
  systemDesign: number | null;
  coding: number | null;
  communication: number | null;
  overall: number | null;
  // AI ensemble scores (0–1 scale per model, combined to 0–100 for the pipeline)
  codebertScore: number | null;
  robertaDepthScore: number | null;
  nliTechnicalScore: number | null;
  tfidfTechnicalScore: number | null;
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
}

export interface ScheduleInterviewRequest {
  candidateId: number;
  scheduledDate: string; // "YYYY-MM-DD"
  scheduledTime: string; // "HH:MM"
  interviewerName: string;
  type?: string;
}

export interface SubmitTechScoresRequest {
  candidateId: number;
  technicalScore: number;
  problemSolving: number;
  systemDesign: number;
  coding: number;
  communication: number;
  feedback?: string;
  transcript?: string;
}

// ── HR Interview Types ────────────────────────────────────────────────────────

export interface HRInterviewOverview {
  id: number;
  jobTitle: string;
  scheduled: number;
  completed: number;
  pending: number;
  avgScore: number;
  nextInterview: string | null;
  interviewer: string | null;
  duration: string;
  passingScore: number;
  status: string;
}

export interface HRInterviewCandidate {
  id: number;
  name: string;
  // Human sub-scores
  cultureFit: number | null;
  communication: number | null;
  leadership: number | null;
  motivation: number | null;
  teamwork: number | null;
  overall: number | null;
  // AI ensemble scores
  emotionScore: number | null;
  sentimentScore: number | null;
  nliAlignScore: number | null;
  semanticDepthScore: number | null;
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
}

export interface SubmitHRScoresRequest {
  candidateId: number;
  cultureFit: number;
  communication: number;
  leadership: number;
  motivation: number;
  teamwork: number;
  feedback?: string;
  transcript?: string;
}

// ── Client ────────────────────────────────────────────────────────────────────

export class RecruitmentAPI {
  private base: string;
  private headers: Record<string, string>;

  constructor(baseUrl: string, authToken: string) {
    this.base = baseUrl.replace(/\/$/, "");
    this.headers = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${authToken}`,
    };
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T> {
    const res = await fetch(`${this.base}${path}`, {
      method,
      headers: this.headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => res.statusText);
      throw new Error(`${method} ${path} → ${res.status}: ${detail}`);
    }
    return res.json() as Promise<T>;
  }

  // ── Stage 2: Assessment ────────────────────────────────────────────────────

  /** GET /api/v1/jobs/{jobId}/assessment */
  getAssessmentOverview(jobId: number): Promise<AssessmentOverview> {
    return this.request("GET", `/api/v1/jobs/${jobId}/assessment`);
  }

  /** GET /api/v1/jobs/{jobId}/assessment/candidates */
  getAssessmentCandidates(jobId: number): Promise<AssessmentCandidate[]> {
    return this.request("GET", `/api/v1/jobs/${jobId}/assessment/candidates`);
  }

  /** POST /api/v1/jobs/{jobId}/assessment/send-invitations */
  sendAssessmentInvitations(
    jobId: number,
    candidateIds: number[]
  ): Promise<{ sent: number; failed: number; message: string }> {
    return this.request("POST", `/api/v1/jobs/${jobId}/assessment/send-invitations`, {
      candidateIds,
    });
  }

  // ── Stage 3: Technical Interview ──────────────────────────────────────────

  /** GET /api/v1/jobs/{jobId}/technical-interview */
  getTechInterviewOverview(jobId: number): Promise<TechInterviewOverview> {
    return this.request("GET", `/api/v1/jobs/${jobId}/technical-interview`);
  }

  /** GET /api/v1/jobs/{jobId}/technical-interview/candidates */
  getTechInterviewCandidates(jobId: number): Promise<TechInterviewCandidate[]> {
    return this.request("GET", `/api/v1/jobs/${jobId}/technical-interview/candidates`);
  }

  /** POST /api/v1/jobs/{jobId}/technical-interview/schedule */
  scheduleTechInterview(
    jobId: number,
    req: ScheduleInterviewRequest
  ): Promise<{ message: string; sessionId: number }> {
    return this.request("POST", `/api/v1/jobs/${jobId}/technical-interview/schedule`, req);
  }

  /**
   * POST /api/v1/jobs/{jobId}/technical-interview/submit-scores
   * Optionally include `transcript` to enable CodeBERT + RoBERTa-QA AI analysis
   * during the next ranking run.
   */
  submitTechScores(
    jobId: number,
    req: SubmitTechScoresRequest
  ): Promise<{ message: string; candidateId: number; overall: number }> {
    return this.request("POST", `/api/v1/jobs/${jobId}/technical-interview/submit-scores`, req);
  }

  /**
   * PATCH /api/v1/jobs/{jobId}/technical-interview/{candidateId}/transcript
   * Upload or replace the raw interview transcript for AI scoring.
   */
  updateTechTranscript(
    jobId: number,
    candidateId: number,
    transcript: string
  ): Promise<{ message: string; candidateId: number }> {
    return this.request(
      "PATCH",
      `/api/v1/jobs/${jobId}/technical-interview/${candidateId}/transcript`,
      { transcript }
    );
  }

  // ── Stage 4: HR Interview ─────────────────────────────────────────────────

  /** GET /api/v1/jobs/{jobId}/hr-interview */
  getHRInterviewOverview(jobId: number): Promise<HRInterviewOverview> {
    return this.request("GET", `/api/v1/jobs/${jobId}/hr-interview`);
  }

  /** GET /api/v1/jobs/{jobId}/hr-interview/candidates */
  getHRInterviewCandidates(jobId: number): Promise<HRInterviewCandidate[]> {
    return this.request("GET", `/api/v1/jobs/${jobId}/hr-interview/candidates`);
  }

  /** POST /api/v1/jobs/{jobId}/hr-interview/schedule */
  scheduleHRInterview(
    jobId: number,
    req: ScheduleInterviewRequest
  ): Promise<{ message: string; sessionId: number }> {
    return this.request("POST", `/api/v1/jobs/${jobId}/hr-interview/schedule`, req);
  }

  /**
   * POST /api/v1/jobs/{jobId}/hr-interview/submit-scores
   * Optionally include `transcript` to enable Go-Emotions + Sentiment AI analysis.
   */
  submitHRScores(
    jobId: number,
    req: SubmitHRScoresRequest
  ): Promise<{ message: string; candidateId: number; overall: number }> {
    return this.request("POST", `/api/v1/jobs/${jobId}/hr-interview/submit-scores`, req);
  }

  /**
   * PATCH /api/v1/jobs/{jobId}/hr-interview/{candidateId}/transcript
   * Upload or replace the raw HR interview transcript for AI scoring.
   */
  updateHRTranscript(
    jobId: number,
    candidateId: number,
    transcript: string
  ): Promise<{ message: string; candidateId: number }> {
    return this.request(
      "PATCH",
      `/api/v1/jobs/${jobId}/hr-interview/${candidateId}/transcript`,
      { transcript }
    );
  }

  // ── Final Ranking ─────────────────────────────────────────────────────────

  /**
   * GET /api/v1/jobs/{jobId}/final-ranking
   * Returns candidates ranked by 20/25/30/25 weighted pipeline score.
   * Includes red-flag detection and SHAP summary.
   */
  getFinalRanking(jobId: number): Promise<FinalRankingItem[]> {
    return this.request("GET", `/api/v1/jobs/${jobId}/final-ranking`);
  }

  /**
   * GET /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shap-report
   * Returns per-phase SHAP contributions (φ values) for a single candidate.
   */
  getSHAPReport(jobId: number, candidateId: number): Promise<SHAPReportResponse> {
    return this.request(
      "GET",
      `/api/v1/jobs/${jobId}/final-ranking/${candidateId}/shap-report`
    );
  }

  /**
   * POST /api/v1/jobs/{jobId}/trigger-ranking
   * Kicks off the 8-node LangGraph pipeline in the background.
   * The pipeline computes ensemble scores, SHAP values, red-flags, and ranks.
   */
  triggerRanking(jobId: number): Promise<TriggerRankingResponse> {
    return this.request("POST", `/api/v1/jobs/${jobId}/trigger-ranking`);
  }

  /**
   * POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/shortlist
   */
  shortlistCandidate(
    jobId: number,
    candidateId: number,
    note?: string
  ): Promise<{ status: string }> {
    return this.request(
      "POST",
      `/api/v1/jobs/${jobId}/final-ranking/${candidateId}/shortlist`,
      { note }
    );
  }

  /**
   * POST /api/v1/jobs/{jobId}/final-ranking/{candidateId}/offer
   */
  sendOffer(
    jobId: number,
    candidateId: number,
    req: SendOfferRequest
  ): Promise<{ message: string; candidateId: number; applicationStatus: string }> {
    return this.request(
      "POST",
      `/api/v1/jobs/${jobId}/final-ranking/${candidateId}/offer`,
      req
    );
  }
}
