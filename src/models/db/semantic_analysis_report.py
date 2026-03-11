from sqlalchemy import Column, Integer, Text, Numeric, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class SemanticAnalysisReport(Base):
    __tablename__ = "semantic_analysis_reports"

    report_id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.application_id"), nullable=False, unique=True)
    match_percentage = Column(Numeric(5, 2), nullable=True)
    ai_insights = Column(Text, nullable=True)
    recommendation_summary = Column(Text, nullable=True)
    strengths = Column(Text, nullable=True)
    weaknesses = Column(Text, nullable=True)
    rank_in_pool = Column(Integer, nullable=True)
    generated_at = Column(DateTime, server_default=func.now())

    application = relationship("Application", back_populates="semantic_report")
    matched_skills = relationship("SemanticMatchedSkill", back_populates="report")
