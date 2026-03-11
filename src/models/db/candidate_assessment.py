from sqlalchemy import Column, Integer, String, DateTime, Boolean, Numeric, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class CandidateAssessment(Base):
    __tablename__ = "candidate_assessments"

    assessment_id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.application_id"), nullable=False, unique=True)
    config_id = Column(Integer, ForeignKey("technical_assessment_configs.config_id"), nullable=False)
    template_id = Column(Integer, ForeignKey("assessment_templates.template_id"), nullable=True)
    status = Column(String(50), nullable=False, default="Pending")
    started_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    attempt_number = Column(Integer, default=1)
    total_score = Column(Numeric(6, 2), nullable=True)
    passing_score = Column(Numeric(6, 2), nullable=True)
    passed = Column(Boolean, nullable=True)

    application = relationship("Application", back_populates="candidate_assessment")
    config = relationship("TechnicalAssessmentConfig", back_populates="candidate_assessments")
    template = relationship("AssessmentTemplate", back_populates="candidate_assessments")
    answers = relationship("AssessmentAnswer", back_populates="assessment")
    assessment_report = relationship("AssessmentReport", back_populates="assessment", uselist=False)
