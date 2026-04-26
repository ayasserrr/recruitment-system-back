"""
Pipeline Status Constants
─────────────────────────
Single source of truth for every status string in the recruitment system.

WHY: Raw string literals scattered across scanners, workers, routes, and
controllers are the #1 source of silent failures.  A typo like "Ranked"
instead of "ranked" bypasses the Celery Beat scanner without any error.

USAGE:
    from core.status_constants import JRStatus, AppStatus, AssessmentStatus

    jr.status = JRStatus.RANKED         # not "ranked"
    app.status = AppStatus.SHORTLISTED  # not "Shortlisted"

The string VALUES are preserved exactly as stored in PostgreSQL so existing
rows remain compatible.  Only the access pattern changes.

NORMALIZATION MAP (used by PATCH endpoints):
    JRStatus.normalize("Published") → "published"
    JRStatus.normalize("active")    → "Active"
"""

from __future__ import annotations


class JRStatus:
    """job_requisitions.status values."""
    DRAFT               = "Draft"
    PUBLISHED           = "published"      # set by LinkedIn publish task
    ACTIVE              = "Active"         # set by recruiter dashboard PATCH
    RANKED              = "ranked"
    ASSESSMENT_SENT     = "assessment_sent"
    ASSESSMENT_RANKED   = "assessment_ranked"
    INTERVIEW_PENDING   = "interview_pending"
    RANKING_COMPLETE    = "ranking_complete"
    CLOSED              = "Closed"

    # Statuses that make a JR eligible for CV ranking by the Beat scanner
    RANKABLE: frozenset[str] = frozenset({PUBLISHED, ACTIVE})

    # Complete set used in status-machine assertions
    ALL: frozenset[str] = frozenset({
        DRAFT, PUBLISHED, ACTIVE, RANKED, ASSESSMENT_SENT,
        ASSESSMENT_RANKED, INTERVIEW_PENDING, RANKING_COMPLETE, CLOSED,
    })

    # ── Normalization map ──────────────────────────────────────────────────
    # Keys: every common frontend variation (lowercase).
    # Values: the canonical DB string.
    _NORMALIZE: dict[str, str] = {
        "draft":               DRAFT,
        "published":           PUBLISHED,
        "publish":             PUBLISHED,
        "active":              ACTIVE,
        "live":                ACTIVE,
        "open":                ACTIVE,
        "ranked":              RANKED,
        "assessment_sent":     ASSESSMENT_SENT,
        "assessment_ranked":   ASSESSMENT_RANKED,
        "interview_pending":   INTERVIEW_PENDING,
        "ranking_complete":    RANKING_COMPLETE,
        "closed":              CLOSED,
        "close":               CLOSED,
    }

    @classmethod
    def normalize(cls, raw: str) -> str:
        """Return the canonical status string for any frontend input.
        Falls back to the original (stripped) value for unknown inputs.
        """
        return cls._NORMALIZE.get(raw.strip().lower(), raw.strip())


class ProcessingStatus:
    """job_requisitions.processing_status values."""
    IDLE        = "idle"
    PROCESSING  = "processing"
    ERROR       = "error"

    RESTARTABLE: frozenset[str] = frozenset({IDLE, ERROR})


class AppStatus:
    """applications.status values."""
    APPLIED         = "Applied"
    SHORTLISTED     = "Shortlisted"
    NOT_SHORTLISTED = "Not Shortlisted"
    WITHDRAWN       = "Withdrawn"


class AssessmentStatus:
    """candidate_assessments.status values."""
    PENDING   = "Pending"
    SUBMITTED = "Submitted"
    EXPIRED   = "Expired"


class InterviewStatus:
    """technical_interview_sessions / hr_interview_sessions status values."""
    SCHEDULED   = "Scheduled"
    IN_PROGRESS = "In Progress"
    COMPLETED   = "Completed"
    FAILED      = "Failed"
    NO_SHOW     = "No-Show"


class FinalStatus:
    """final_rankings.final_status values."""
    SELECTED     = "Selected"
    NOT_SELECTED = "Not Selected"
