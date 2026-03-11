from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from database.connection import Base

class FinalRanking(Base):
    __tablename__ = "final_rankings"

    ranking_id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.application_id"), nullable=False, unique=True)
    posting_id = Column(Integer, ForeignKey("job_postings.posting_id"), nullable=False)
    semantic_score = Column(Numeric(6, 2), nullable=True)
    assessment_score = Column(Numeric(6, 2), nullable=True)
    technical_interview_score = Column(Numeric(6, 2), nullable=True)
    hr_interview_score = Column(Numeric(6, 2), nullable=True)
    weighted_total_score = Column(Numeric(6, 2), nullable=True)
    final_rank = Column(Integer, nullable=True)
    final_recommendation = Column(String(100), nullable=True)
    final_status = Column(String(50), nullable=True)
    generated_at = Column(DateTime, server_default=func.now())

    application = relationship("Application", back_populates="final_ranking")
    posting = relationship("JobPosting", back_populates="final_rankings")
