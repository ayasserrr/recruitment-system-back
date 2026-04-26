"""
Pydantic schemas for all frontend-facing API endpoints.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


# ── Helpers ────────────────────────────────────────────────────────────────────

def match_label(score: float) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Very Good"
    if score >= 55:
        return "Good"
    if score >= 40:
        return "Average"
    return "Fair"


def jr_status_to_display(status: str, end_date_passed: bool = False) -> str:
    if end_date_passed and status not in ("ranking_complete", "Closed"):
        return "Closed"
    mapping = {
        "Draft": "Posted",
        "Active": "CV Collection",
        "published": "CV Collection",
        "failed": "CV Collection",
        "ranked": "CV Collection",
        "assessment_sent": "In Progress",
        "assessment_ranked": "In Progress",
        "interview_pending": "In Progress",
        "ranking_complete": "Final Stage",
        "Closed": "Closed",
    }
    return mapping.get(status, "Posted")


# ── Full candidate profile (shared across all 4 pipeline stage endpoints) ──────

class CandidateProfile(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    experience: Optional[str] = None
    education: Optional[str] = None
    summary: Optional[str] = None
    projects: List[str] = []
    skills: List[str] = []


# ── Jobs List / Detail ─────────────────────────────────────────────────────────

class JobListItem(BaseModel):
    id: int
    jobTitle: str
    department: Optional[str] = None
    posted: Optional[str] = None
    status: str
    cvs: int
    newToday: int
    semantic: int
    assessment: int
    techInterview: int
    hrInterview: int
    finalCandidates: int
    selectedPlatforms: List[str]
    postingStartDate: Optional[str] = None
    postingEndDate: Optional[str] = None
    requiredSkills: List[str] = []
    preferredSkills: List[str] = []
    assessmentCandidatesToAdvance: Optional[int] = None
    technicalInterviewCandidatesToAdvance: Optional[int] = None
    hrInterviewCandidatesToAdvance: Optional[int] = None
    technicalInterviewDuration: Optional[str] = None
    hrInterviewDuration: Optional[str] = None


class JobListResponse(BaseModel):
    count: int
    results: List[JobListItem]


class JobStatusUpdate(BaseModel):
    status: str


# ── Pipeline (Job Post Page timeline) ─────────────────────────────────────────

class PipelineStage(BaseModel):
    step: str
    status: str          # "completed" | "active" | "pending"
    date: Optional[str] = None
    time: Optional[str] = None
    platforms: Optional[List[str]] = None
    count: Optional[int] = None
    newToday: Optional[int] = None
    note: Optional[str] = None
    scheduledDate: Optional[str] = None


class PipelineResponse(BaseModel):
    jobId: int
    jobTitle: str
    postingEndDate: Optional[str] = None
    stages: List[PipelineStage]


# ── Apply (public) ─────────────────────────────────────────────────────────────

class ApplyResponse(BaseModel):
    applicationId: int
    message: str


class CandidateApplicationItem(BaseModel):
    applicationId: int
    jobTitle: str
    company: str
    appliedDate: str
    status: str


# ── Semantic Analysis ──────────────────────────────────────────────────────────

class SemanticStats(BaseModel):
    totalCandidates: int
    processed: int
    highMatch: int
    mediumMatch: int
    lowMatch: int
    avgScore: float


class SemanticCandidate(BaseModel):
    id: int
    name: str
    score: float
    match: str
    skills: List[str] = []
    email: Optional[str] = None
    phone: Optional[str] = None
    experience: Optional[str] = None
    education: Optional[str] = None
    summary: Optional[str] = None
    projects: List[str] = []


class SemanticResponse(BaseModel):
    jobId: int
    processingTime: Optional[str] = None
    stats: SemanticStats
    candidates: List[SemanticCandidate]


class SemanticRunResponse(BaseModel):
    taskId: str
    status: str


class SemanticStatusResponse(BaseModel):
    status: str
    progress: int


# ── Technical Assessment ───────────────────────────────────────────────────────

class AssessmentOverview(BaseModel):
    id: int
    jobTitle: str
    totalCandidates: int
    sent: int
    completed: int
    pending: int
    deadline: Optional[str] = None
    status: str
    avgScore: float
    duration: str
    questions: int
    passingScore: float


class AssessmentCandidate(BaseModel):
    # pipeline stage scores
    id: int
    name: str
    score: float
    technical: str
    problemSolving: str
    timeSpent: str
    status: str
    codingScore: Optional[float] = None
    theoryScore: Optional[float] = None
    completed: Optional[str] = None
    # full candidate profile
    email: Optional[str] = None
    phone: Optional[str] = None
    experience: Optional[str] = None
    education: Optional[str] = None
    summary: Optional[str] = None
    projects: List[str] = []
    skills: List[str] = []


class SendInvitationsRequest(BaseModel):
    candidateIds: List[int]


class SendInvitationsResponse(BaseModel):
    sent: int
    failed: int
    message: str


# ── Technical Interview ────────────────────────────────────────────────────────

class TechInterviewOverview(BaseModel):
    id: int
    jobTitle: str
    scheduled: int
    completed: int
    pending: int
    avgScore: float
    nextInterview: Optional[str] = None
    interviewers: List[str] = []
    duration: str
    passingScore: float
    status: str


class TechInterviewCandidate(BaseModel):
    id: int
    name: str
    technicalScore: Optional[float] = None
    problemSolving: Optional[float] = None
    systemDesign: Optional[float] = None
    coding: Optional[float] = None
    communication: Optional[float] = None
    overall: Optional[float] = None
    status: str
    interviewer: Optional[str] = None
    date: Optional[str] = None
    feedback: Optional[str] = None
    # full candidate profile
    email: Optional[str] = None
    phone: Optional[str] = None
    experience: Optional[str] = None
    education: Optional[str] = None
    summary: Optional[str] = None
    projects: List[str] = []
    skills: List[str] = []


class ScheduleInterviewRequest(BaseModel):
    candidateId: int
    scheduledDate: str
    scheduledTime: str
    interviewerName: str
    type: str = "ai-conducted"


class SubmitTechScoresRequest(BaseModel):
    candidateId: int
    technicalScore: float
    problemSolving: float
    systemDesign: float
    coding: float
    communication: float
    feedback: Optional[str] = None


# ── HR Interview ───────────────────────────────────────────────────────────────

class HRInterviewOverview(BaseModel):
    id: int
    jobTitle: str
    scheduled: int
    completed: int
    pending: int
    avgScore: float
    nextInterview: Optional[str] = None
    interviewer: Optional[str] = None
    duration: str
    passingScore: float
    status: str


class HRInterviewCandidate(BaseModel):
    id: int
    name: str
    cultureFit: Optional[float] = None
    communication: Optional[float] = None
    leadership: Optional[float] = None
    motivation: Optional[float] = None
    teamwork: Optional[float] = None
    overall: Optional[float] = None
    status: str
    interviewer: Optional[str] = None
    date: Optional[str] = None
    feedback: Optional[str] = None
    # full candidate profile
    email: Optional[str] = None
    phone: Optional[str] = None
    experience: Optional[str] = None
    education: Optional[str] = None
    summary: Optional[str] = None
    projects: List[str] = []
    skills: List[str] = []


class SubmitHRScoresRequest(BaseModel):
    candidateId: int
    cultureFit: float
    communication: float
    leadership: float
    motivation: float
    teamwork: float
    feedback: Optional[str] = None


# ── Final Ranking ──────────────────────────────────────────────────────────────

class FinalRankingItem(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    overallScore: float
    semanticScore: Optional[float] = None
    assessmentScore: Optional[float] = None
    technicalScore: Optional[float] = None
    cultureFitScore: Optional[float] = None
    recommendation: str
    hireProbability: int
    applicationStatus: str


class ShortlistRequest(BaseModel):
    note: Optional[str] = None


class ShortlistResponse(BaseModel):
    status: str


class SendOfferRequest(BaseModel):
    position: str
    salary: str
    startDate: str
    department: str
    reportingTo: str
    benefits: str
    contractType: str
    location: str
    notes: Optional[str] = None


# ── Shortlist ──────────────────────────────────────────────────────────────────

class AddToShortlistRequest(BaseModel):
    candidateId: int
    jobId: int
    shortlistedFrom: str = "Semantic Analysis"
    note: Optional[str] = None


class AddToShortlistResponse(BaseModel):
    id: int
    status: str


class ShortlistItem(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    experience: Optional[str] = None
    education: Optional[str] = None
    summary: Optional[str] = None
    projects: List[str] = []
    score: Optional[float] = None
    match: Optional[str] = None
    skills: List[str] = []
    jobTitle: str
    shortlistedFrom: str
    shortlistedDate: str
    shortlistNote: Optional[str] = None


class ShortlistListResponse(BaseModel):
    count: int
    results: List[ShortlistItem]


# ── Analytics ──────────────────────────────────────────────────────────────────

class AnalyticsOverview(BaseModel):
    totalApplications: int
    totalHires: int
    avgTimeToHire: int
    pipelineEfficiency: int
    offerAcceptanceRate: int


class ApplicationSource(BaseModel):
    source: str
    count: int


class AssessmentMetrics(BaseModel):
    sent: int
    completed: int
    avgScore: float
    passRate: int


class InterviewMetrics(BaseModel):
    technical: int
    hr: int
    avgScore: float
    satisfaction: float


class DepartmentBreakdown(BaseModel):
    department: str
    count: int


class HiringBreakdown(BaseModel):
    total: int
    avgDaysToHire: int
    acceptanceRate: int
    byDepartment: List[DepartmentBreakdown]


class EfficiencyMetrics(BaseModel):
    hoursSaved: int
    automationRate: int
    manualTasksReduced: int
    productivityGain: int


class TrendData(BaseModel):
    monthlyApplications: List[int]
    monthlyHires: List[int]
    satisfactionScores: List[float]
    efficiencyGains: List[int]


class AnalyticsResponse(BaseModel):
    overview: AnalyticsOverview
    applicationSources: List[ApplicationSource]
    assessmentMetrics: AssessmentMetrics
    interviewMetrics: InterviewMetrics
    hiringBreakdown: HiringBreakdown
    efficiency: EfficiencyMetrics
    trends: TrendData
