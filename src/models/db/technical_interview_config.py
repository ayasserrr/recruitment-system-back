from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class TechnicalInterviewConfig(Base):
    __tablename__ = "technical_interview_configs"

    config_id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False, unique=True)
    interview_type = Column(String(100), nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    ai_feedback_level = Column(String(50), nullable=True)
    scoring_system = Column(String(100), nullable=True)
    candidates_to_advance = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    requisition = relationship("JobRequisition", back_populates="technical_interview_config")
    criteria = relationship("InterviewEvaluationCriterion", back_populates="config")
    sessions = relationship("TechnicalInterviewSession", back_populates="config")
