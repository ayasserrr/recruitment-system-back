import enum

class PostingState(str, enum.Enum):
    OPEN = "Open"
    PAUSED = "Paused"
    CLOSED = "Closed"
