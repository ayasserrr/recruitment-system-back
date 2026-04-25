from datetime import datetime
from pydantic import BaseModel


class MissingKnowledgeItem(BaseModel):
    tool_name: str
    occurrence_count: int
    last_requested_at: datetime
    affected_jrs: list[int]
    high_priority: bool  # True when occurrence_count > 5

    class Config:
        from_attributes = True


class MissingKnowledgeResponse(BaseModel):
    total_missing_tools: int
    high_priority_count: int
    generated_at: datetime
    items: list[MissingKnowledgeItem]
