"""
enums — single source of truth for all status/type enumerations.

Import from here everywhere. Do NOT import from models/enums/.
"""

from .auth_errors import AuthErrorMessages, HTTPStatusCodes, AuthErrorDetails
from .requisition_status import RequisitionStatus
from .application_status import ApplicationStatus
from .interview_status import InterviewStatus
from .processing_status import ProcessingStatus
from .employment_type import EmploymentType
from .posting_state import PostingState
from .match_type import MatchType
from .seniority_level import SeniorityLevel

__all__ = [
    "AuthErrorMessages",
    "HTTPStatusCodes",
    "AuthErrorDetails",
    "RequisitionStatus",
    "ApplicationStatus",
    "InterviewStatus",
    "ProcessingStatus",
    "EmploymentType",
    "PostingState",
    "MatchType",
    "SeniorityLevel",
]
