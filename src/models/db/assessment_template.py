from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class AssessmentTemplate(Base):
    __tablename__ = "assessment_templates"

    template_id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.company_id"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    company = relationship("Company", back_populates="assessment_templates")
    questions = relationship("AssessmentTemplateQuestion", back_populates="template")
    assessment_configs = relationship("TechnicalAssessmentConfig", back_populates="template")
    candidate_assessments = relationship("CandidateAssessment", back_populates="template")
