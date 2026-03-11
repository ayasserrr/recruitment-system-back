import enum

class RequisitionStatus(str, enum.Enum):
    DRAFT = "Draft"
    ACTIVE = "Active"
    CLOSED = "Closed"
