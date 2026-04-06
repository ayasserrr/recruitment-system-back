from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class TechnicalAssessmentConfig(Base):
    __tablename__ = "technical_assessment_configs"

    config_id = Column(Integer, primary_key=True, autoincrement=True)
    requisition_id = Column(Integer, ForeignKey("job_requisitions.requisition_id"), nullable=False, unique=True)
    template_id = Column(Integer, ForeignKey("assessment_templates.template_id"), nullable=True)
    assessment_type = Column(String(100), nullable=True)
    time_limit_minutes = Column(Integer, nullable=True)
    candidates_to_advance = Column(Integer, nullable=True)
    max_attempts = Column(Integer, default=1)
    assessment_language = Column(String(50), nullable=True)
    sample_task = Column(Text, nullable=True)
    assessment_questions = Column(Text, nullable=True)   # newline-joined custom questions
    created_at = Column(DateTime, server_default=func.now())

    requisition = relationship("JobRequisition", back_populates="technical_assessment_config")
    template = relationship("AssessmentTemplate", back_populates="assessment_configs")
    candidate_assessments = relationship("CandidateAssessment", back_populates="config")
