import enum

class InterviewStatus(str, enum.Enum):
    SCHEDULED = "Scheduled"
    DONE = "Done"
    CANCELLED = "Cancelled"
    NO_SHOW = "No Show"
