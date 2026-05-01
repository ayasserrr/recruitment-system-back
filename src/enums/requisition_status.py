import enum


class RequisitionStatus(str, enum.Enum):
    # Recruiter-managed states
    DRAFT          = "Draft"
    ACTIVE         = "Active"
    CLOSED         = "Closed"

    # Automated pipeline states (set by Celery workers)
    PUBLISHED            = "published"
    RANKED               = "ranked"
    ASSESSMENT_COMPLETE  = "assessment_complete"
    ASSESSMENT_SENT      = "assessment_sent"
    INTERVIEW_PENDING    = "interview_pending"
    HR_INTERVIEW_PENDING = "hr_interview_pending"
    RANKING_COMPLETE     = "ranking_complete"
