"""
Compatibility shim — all enums live in enums/ (the project root enums package).
Import from enums/ directly for new code.
"""
from enums import (  # noqa: F401
    RequisitionStatus,
    ApplicationStatus,
    InterviewStatus,
    ProcessingStatus,
    EmploymentType,
    PostingState,
    MatchType,
    SeniorityLevel,
)
from enums.auth_errors import AuthErrorMessages, HTTPStatusCodes, AuthErrorDetails  # noqa: F401
# Legacy exports kept for backwards compat
from .ResponseEnums import ResponseSignal  # noqa: F401
from .ProcessingEnums import ProcessingEnums  # noqa: F401
