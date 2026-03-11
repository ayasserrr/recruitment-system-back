import enum

class PipelineStatus(str, enum.Enum):
    PASSED = "Passed"
    FAILED = "Failed"
    PENDING = "Pending"
    SKIPPED = "Skipped"
