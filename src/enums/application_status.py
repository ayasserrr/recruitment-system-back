import enum


class ApplicationStatus(str, enum.Enum):
    APPLIED              = "Applied"
    SCREENING            = "Screening"
    RANKED               = "ranked"
    SHORTLISTED          = "shortlisted"
    ASSESSMENT           = "Assessment"
    ASSESSMENT_PENDING   = "assessment_pending"
    ASSESSMENT_COMPLETED = "assessment_completed"
    ASSESSMENT_EXPIRED   = "assessment_expired"
    TECHNICAL_INTERVIEW  = "Technical Interview"
    HR_INTERVIEW         = "HR Interview"
    OFFER                = "Offer"
    REJECTED             = "Rejected"
    WITHDRAWN            = "Withdrawn"
