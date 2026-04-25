from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base


class JrKnowledgeGap(Base):
    __tablename__ = "jr_knowledge_gaps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    jr_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False)
    tool_name = Column(String(200), nullable=False)
    status = Column(String(100), nullable=False, default="Missing in DB - LLM Generated")
    created_at = Column(DateTime, server_default=func.now())

    job_requisition = relationship("JobRequisition", back_populates="knowledge_gaps")
