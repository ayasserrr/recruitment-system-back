import enum


class ProcessingStatus(str, enum.Enum):
    IDLE       = "idle"
    PROCESSING = "processing"
    ERROR      = "error"
