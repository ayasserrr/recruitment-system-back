from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from database.connection import Base

class SemanticMatchedSkill(Base):
    __tablename__ = "semantic_matched_skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(Integer, ForeignKey("semantic_analysis_reports.report_id"), nullable=False)
    skill_name = Column(String(100), nullable=False)
    match_type = Column(String(50), nullable=True)

    report = relationship("SemanticAnalysisReport", back_populates="matched_skills")
