from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class TechnicalInterviewReport(Base):
    __tablename__ = "technical_interview_reports"

    report_id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("technical_interview_sessions.session_id"), nullable=False, unique=True)
    interview_summary = Column(Text, nullable=True)
    detailed_assessment = Column(Text, nullable=True)
    strengths = Column(Text, nullable=True)
    weaknesses = Column(Text, nullable=True)
    recommendation = Column(String(100), nullable=True)
    rank_in_pool = Column(Integer, nullable=True)
    generated_at = Column(DateTime, server_default=func.now())

    session = relationship("TechnicalInterviewSession", back_populates="report")
