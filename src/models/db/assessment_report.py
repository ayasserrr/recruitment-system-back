from sqlalchemy import Column, Integer, Text, Numeric, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class AssessmentReport(Base):
    __tablename__ = "assessment_reports"

    report_id = Column(Integer, primary_key=True, autoincrement=True)
    assessment_id = Column(Integer, ForeignKey("candidate_assessments.assessment_id"), nullable=False, unique=True)
    overall_score = Column(Numeric(6, 2), nullable=True)
    ai_feedback = Column(Text, nullable=True)
    strengths = Column(Text, nullable=True)
    weaknesses = Column(Text, nullable=True)
    rank_in_pool   = Column(Integer,  nullable=True)
    recommendation = Column(Text,     nullable=True)
    # ── Ensemble SHAP explanation ─────────────────────────────────────────────
    shap_summary   = Column(Text,     nullable=True)   # human-readable SHAP narrative
    generated_at   = Column(DateTime, server_default=func.now())

    assessment = relationship("CandidateAssessment", back_populates="assessment_report")
