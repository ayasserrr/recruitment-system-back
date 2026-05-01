import enum


class InterviewStatus(str, enum.Enum):
    SCHEDULED  = "Scheduled"
    IN_PROGRESS = "InProgress"
    COMPLETED  = "Completed"    # what the DB and livekit_agent actually store
    NO_SHOW    = "No-show"
    CANCELLED  = "Cancelled"

    # Convenience set used in terminal checks across the codebase
    @classmethod
    def terminal_set(cls) -> frozenset[str]:
        return frozenset({cls.COMPLETED, cls.NO_SHOW, cls.CANCELLED})
